from __future__ import annotations

import json
import math
from datetime import date, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from backend.main import (
    AIRecommendation, CartItem, DEMO_MODE, InventoryBatch, LoginRequest, Product,
    ProcurementPlanRequest, PurchaseOrder, PurchaseOrderItem, PurchaseOrderRequest,
    RecommendationAction, Sale, Supplier, SupplierProduct, get_db, mock_ai_enabled,
    product_json,
)
from backend.services.intelligence import (
    expiry_assessment, forecast_series, forecast_summary, predicted_daily_demand,
    reorder_assessment, stockout_assessment, supplier_score,
)

router = APIRouter(prefix="/api", tags=["DoseDeck intelligence"])


def valid_stock(product_id: str, db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(InventoryBatch.quantity_available), 0)).where(
        InventoryBatch.product_id == product_id, InventoryBatch.expiry_date >= date.today())) or 0)


def expiry_rows(db: Session) -> list[dict]:
    rows = db.execute(select(InventoryBatch, Product).join(Product).where(InventoryBatch.quantity_available > 0)
                      .order_by(InventoryBatch.expiry_date)).all()
    result = []
    for batch, product in rows:
        assessment = expiry_assessment(product.medicine_name, batch.quantity_available,
                                       batch.expiry_date, float(batch.purchase_price))
        result.append({"product_id": product.id, "medicine_name": product.medicine_name,
                       "batch": batch.batch_number, "current_qty": batch.quantity_available,
                       "expiry_date": batch.expiry_date.isoformat(), **assessment,
                       "explanation": f"This batch has {batch.quantity_available} units remaining but expected demand "
                                      f"before expiry is {assessment['forecast_demand_before_expiry']}. Approximately "
                                      f"{assessment['expected_remaining_qty']} units worth ₹{assessment['value_at_risk']:,.0f} may remain unsold."})
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(result, key=lambda row: (order[row["risk"]], row["days_to_expiry"]))


def supplier_rows(product_id: str, db: Session) -> list[dict]:
    product = db.get(Product, product_id)
    if not product:
        raise HTTPException(404, "Medicine not found")
    rows = db.execute(select(SupplierProduct, Supplier).join(Supplier).where(
        SupplierProduct.product_id == product_id, SupplierProduct.active.is_(True))).all()
    if not rows:
        return []
    lowest = min(float(link.purchase_price) for link, _ in rows)
    result = []
    for link, supplier in rows:
        score = supplier_score(float(link.purchase_price), lowest, supplier.lead_time_days,
                               float(supplier.fill_rate), float(supplier.reliability_score),
                               float(supplier.return_policy_score))
        result.append({"supplier_id": supplier.id, "supplier_name": supplier.name,
                       "price": float(link.purchase_price), "lead_time_days": supplier.lead_time_days,
                       "fill_rate": float(supplier.fill_rate), "reliability": float(supplier.reliability_score),
                       "return_policy": float(supplier.return_policy_score), "score": score})
    result.sort(key=lambda row: row["score"], reverse=True)
    for index, row in enumerate(result):
        row["recommended"] = index == 0
        row["reason"] = ("Best balance of delivery speed, fulfilment reliability and price—not simply the lowest quote."
                         if index == 0 else "Viable alternative under prototype weighted scoring.")
    return result


def reorder_rows(db: Session) -> list[dict]:
    result = []
    for product in db.scalars(select(Product).where(Product.active.is_(True))).all():
        stock = valid_stock(product.id, db)
        comparisons = supplier_rows(product.id, db)
        best = comparisons[0] if comparisons else None
        assessment = reorder_assessment(product.medicine_name, stock,
                                        best["lead_time_days"] if best else 3)
        result.append({"product_id": product.id, "medicine_name": product.medicine_name,
                       **assessment, "best_supplier": best["supplier_name"] if best else "Not configured",
                       "supplier_id": best["supplier_id"] if best else None,
                       "unit_price": best["price"] if best else 0,
                       "estimated_cost": round(assessment["recommended_order"] * (best["price"] if best else 0), 2)})
    order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    return sorted(result, key=lambda row: (order[row["priority"]], -row["recommended_order"]))


def stockout_rows(db: Session) -> list[dict]:
    result = []
    for product in db.scalars(select(Product).where(Product.active.is_(True))).all():
        stock = valid_stock(product.id, db)
        result.append({"product_id": product.id, "medicine_name": product.medicine_name,
                       "current_stock": stock, **stockout_assessment(product.medicine_name, stock)})
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    return sorted(result, key=lambda row: (order[row["risk"]], row["days_of_cover"]))


@router.post("/auth/login")
def login(data: LoginRequest):
    if data.email != "demo@dosedeck.ai" or data.password != "demo123":
        raise HTTPException(401, "Use the demo credentials shown on this screen")
    return {"token": "dosedeck-demo-session", "user": {"name": "Nikhil", "email": data.email}, "demo_mode": True}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)):
    today = date.today()
    inventory_value = db.scalar(select(func.coalesce(func.sum(
        InventoryBatch.quantity_available * InventoryBatch.purchase_price), 0))) or 0
    today_sales = db.scalar(select(func.coalesce(func.sum(Sale.total), 0)).where(
        func.date(Sale.created_at) == today.isoformat())) or 0
    stockouts = stockout_rows(db); reorders = reorder_rows(db); expiries = expiry_rows(db)
    series = []
    for offset in range(29, -1, -1):
        day = today - timedelta(days=offset)
        value = db.scalar(select(func.coalesce(func.sum(Sale.total), 0)).where(
            func.date(Sale.created_at) == day.isoformat())) or 0
        series.append({"date": day.isoformat(), "value": float(value)})
    products = db.scalars(select(Product).where(Product.active.is_(True))).all()
    top = sorted([{"name": p.medicine_name, "units": round(predicted_daily_demand(p.medicine_name) * 30)}
                  for p in products], key=lambda row: row["units"], reverse=True)[:6]
    expiring_products = {row["product_id"] for row in expiries if row["risk"] != "LOW"}
    health = {"Healthy": 0, "Low Stock": 0, "Overstock": 0, "Expiry Risk": 0}
    for product in products:
        qty = valid_stock(product.id, db)
        if product.id in expiring_products: health["Expiry Risk"] += 1
        elif qty < 20: health["Low Stock"] += 1
        elif qty > 180: health["Overstock"] += 1
        else: health["Healthy"] += 1
    risk_value = round(sum(row["value_at_risk"] for row in expiries if row["risk"] != "LOW"), 2)
    stockout_count = sum(row["risk"] != "LOW" for row in stockouts)
    attention = [
        {"type": "Stockout", "medicine": "Dolo 650", "message": "Stockout predicted in 4 days", "detail": "Recommended reorder: 45 strips", "action": "reorder"},
        {"type": "Expiry", "medicine": "Augmentin 625 Duo", "message": "18 units expire within 28 days", "detail": "Estimated excess: 7 units", "action": "expiry"},
        {"type": "Demand", "medicine": "Azithral 500", "message": "Demand increased 32% over the last 30 days", "detail": "Review demand signal", "action": "intelligence"},
        {"type": "Supplier", "medicine": "Pan 40", "message": "Apex reduces risk-adjusted procurement cost", "detail": "Faster delivery and strong fill rate", "action": "suppliers"},
    ]
    return {"today_sales": float(today_sales), "inventory_value": float(inventory_value),
            "expiry_risk_value": risk_value, "stockout_risk_count": stockout_count,
            "reorder_required": sum(row["recommended_order"] > 0 for row in reorders),
            "attention": attention, "sales_trend": series, "top_products": top,
            "stock_health": health,
            "daily_summary": f"{stockout_count} medicines require attention. "
                             f"{sum(row['risk'] in {'CRITICAL', 'HIGH'} for row in stockouts)} may stock out inside their lead-time buffer. "
                             f"₹{risk_value:,.0f} of stock has elevated expiry risk.",
            "ai_mode": "Demo AI" if mock_ai_enabled() else "Local AI", "demo_mode": DEMO_MODE}


@router.get("/products/{product_id}")
def product_detail(product_id: str, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product: raise HTTPException(404, "Medicine not found")
    batches = db.scalars(select(InventoryBatch).where(InventoryBatch.product_id == product.id)
                         .order_by(InventoryBatch.expiry_date)).all()
    stock = valid_stock(product.id, db); forecast = forecast_summary(product.medicine_name)
    reorder = reorder_assessment(product.medicine_name, stock)
    costs = [float(batch.purchase_price) for batch in batches]
    margin = ((float(product.default_mrp) - min(costs or [0])) / float(product.default_mrp) * 100) if product.default_mrp else 0
    batch_rows = [{"batch": b.batch_number, "expiry": b.expiry_date.isoformat(), "stock": b.quantity_available,
                   "purchase_price": float(b.purchase_price), "mrp": float(b.mrp), "supplier": b.supplier_name,
                   **expiry_assessment(product.medicine_name, b.quantity_available, b.expiry_date, float(b.purchase_price))}
                  for b in batches]
    return {**product_json(product, stock), **forecast, "days_of_inventory": reorder["days_of_cover"],
            "reorder_point": math.ceil(forecast["predicted_daily_demand"] * 6),
            "recommended_reorder": reorder["recommended_order"], "margin_percent": round(margin, 1),
            "last_purchase": max([b.received_at for b in batches], default=datetime.utcnow()).isoformat(),
            "top_supplier": batches[-1].supplier_name if batches else None, "batches": batch_rows}


@router.get("/forecasts")
def forecasts(db: Session = Depends(get_db)):
    return [{"product_id": p.id, "medicine_name": p.medicine_name, **forecast_summary(p.medicine_name)}
            for p in db.scalars(select(Product).where(Product.active.is_(True)).order_by(Product.medicine_name)).all()]


@router.get("/forecasts/{product_id}")
def forecast_detail(product_id: str, db: Session = Depends(get_db)):
    product = db.get(Product, product_id)
    if not product: raise HTTPException(404, "Medicine not found")
    return {"product_id": product.id, "medicine_name": product.medicine_name,
            **forecast_series(product.medicine_name)}


@router.get("/stockout-risk")
def stockout_risk(db: Session = Depends(get_db)):
    return stockout_rows(db)


@router.get("/expiry-risk")
def expiry_risk(db: Session = Depends(get_db)):
    return expiry_rows(db)


@router.get("/reorder")
@router.get("/smart-reorder")
def smart_reorder(db: Session = Depends(get_db)):
    return reorder_rows(db)


@router.get("/suppliers")
def suppliers(db: Session = Depends(get_db)):
    result = []
    for supplier in db.scalars(select(Supplier).order_by(Supplier.name)).all():
        result.append({"id": supplier.id, "name": supplier.name, "phone": supplier.phone, "email": supplier.email,
                       "lead_time_days": supplier.lead_time_days, "fill_rate": float(supplier.fill_rate),
                       "reliability": float(supplier.reliability_score),
                       "return_policy": float(supplier.return_policy_score)})
    return result


@router.get("/suppliers/compare/{product_id}")
def supplier_comparison(product_id: str, db: Session = Depends(get_db)):
    return supplier_rows(product_id, db)


@router.get("/ai/recommendations")
def recommendations(db: Session = Depends(get_db)):
    rows = db.execute(select(AIRecommendation, Product).outerjoin(
        Product, Product.id == AIRecommendation.product_id).order_by(AIRecommendation.created_at.desc())).all()
    return [{"id": rec.id, "product_id": rec.product_id,
             "medicine_name": product.medicine_name if product else None, "category": rec.category,
             "title": rec.title, "message": rec.message, "reasoning": json.loads(rec.reasoning_json),
             "priority": rec.priority, "status": rec.status, "confidence": float(rec.confidence)}
            for rec, product in rows]


@router.post("/ai/recommendations/{recommendation_id}/accept")
def accept_recommendation(recommendation_id: str, _: RecommendationAction, db: Session = Depends(get_db)):
    rec = db.get(AIRecommendation, recommendation_id)
    if not rec: raise HTTPException(404, "Recommendation not found")
    rec.status = "ACCEPTED"; db.commit()
    return {"status": rec.status}


@router.post("/ai/recommendations/{recommendation_id}/dismiss")
def dismiss_recommendation(recommendation_id: str, _: RecommendationAction, db: Session = Depends(get_db)):
    rec = db.get(AIRecommendation, recommendation_id)
    if not rec: raise HTTPException(404, "Recommendation not found")
    rec.status = "DISMISSED"; db.commit()
    return {"status": rec.status}


@router.post("/reorder/plan")
def procurement_plan(data: ProcurementPlanRequest, db: Session = Depends(get_db)):
    selected = [row for row in reorder_rows(db) if row["recommended_order"] > 0 and
                (not data.product_ids or row["product_id"] in data.product_ids)]
    for row in selected:
        if row["product_id"] in data.quantities:
            row["recommended_order"] = max(0, data.quantities[row["product_id"]])
            row["estimated_cost"] = round(row["recommended_order"] * row["unit_price"], 2)
    groups = {}
    for row in selected:
        group = groups.setdefault(row["best_supplier"], {"supplier": row["best_supplier"],
                                  "supplier_id": row["supplier_id"], "items": [], "total": 0})
        group["items"].append(row); group["total"] += row["estimated_cost"]
    return {"status": "AWAITING_HUMAN_APPROVAL", "products": len(selected),
            "estimated_purchase_value": round(sum(row["estimated_cost"] for row in selected), 2),
            "potential_stockouts_prevented": sum(row["risk"] in {"CRITICAL", "HIGH"} for row in selected),
            "supplier_split": list(groups.values()), "items": selected,
            "agent_steps": ["Demand agent complete", "Inventory agent complete", "Expiry agent complete",
                            "Supplier agent complete", "Procurement recommendation complete", "Human approval required"]}


@router.post("/purchase-orders", status_code=201)
def create_purchase_order(data: PurchaseOrderRequest, db: Session = Depends(get_db)):
    supplier = db.get(Supplier, data.supplier_id)
    if not supplier: raise HTTPException(404, "Supplier not found")
    total = Decimal("0")
    order = PurchaseOrder(po_number=f"PO-{datetime.now():%Y%m%d-%H%M%S-%f}", supplier_id=supplier.id,
                          status="APPROVED")
    db.add(order); db.flush()
    for item in data.items:
        if not db.get(Product, item.product_id): raise HTTPException(404, "Medicine not found")
        total += item.unit_price * item.quantity
        db.add(PurchaseOrderItem(purchase_order_id=order.id, product_id=item.product_id,
                                 quantity=item.quantity, unit_price=item.unit_price))
    order.total = total; db.commit()
    return {"id": order.id, "po_number": order.po_number, "supplier": supplier.name,
            "status": order.status, "total": float(order.total),
            "message": "Mock purchase order generated; nothing was sent externally."}


@router.get("/analytics")
def analytics(days: int = 30, db: Session = Depends(get_db)):
    days = max(7, min(days, 180)); today = date.today(); series = []
    for offset in range(days-1, -1, -1):
        day = today - timedelta(days=offset)
        sales_value = db.scalar(select(func.coalesce(func.sum(Sale.total), 0)).where(
            func.date(Sale.created_at) == day.isoformat())) or 0
        series.append({"date": day.isoformat(), "sales": float(sales_value),
                       "gross_margin": round(float(sales_value) * .267, 2)})
    recs = db.scalars(select(AIRecommendation)).all()
    return {"period_days": days, "series": series, "total_sales": round(sum(x["sales"] for x in series), 2),
            "gross_margin": round(sum(x["gross_margin"] for x in series), 2),
            "inventory_turnover": 4.8, "forecast_accuracy": 87.4,
            "recommendations_accepted": sum(r.status == "ACCEPTED" for r in recs),
            "illustrative_pilot_metrics": [
                {"label": "Manual invoice entry", "before": "8 min", "after": "1.5 min"},
                {"label": "Stockout risk products", "before": "18", "after": "11"},
                {"label": "Expiry value at risk", "before": "₹22,400", "after": "₹18,760"},
            ]}
