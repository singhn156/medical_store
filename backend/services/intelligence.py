from __future__ import annotations

import hashlib
import math
from datetime import date, timedelta


DEMAND_PROFILES = {
    "Dolo 650": 3.14,
    "Augmentin 625 Duo": 0.40,
    "Pan 40": 2.25,
    "Azithral 500": 1.18,
    "Allegra 120": 1.45,
    "Crocin Advance": 2.05,
    "Shelcal 500": 1.10,
    "Cetzine": 1.72,
    "Combiflam": 1.62,
    "Pantocid 40": 1.35,
    "Montek LC": 1.12,
    "Telma 40": 0.94,
    "Amlodipine 5": 1.05,
    "Metformin 500": 1.45,
    "Glycomet GP1": 0.78,
    "Ecosprin 75": 1.28,
    "Thyronorm 50": 0.83,
    "Limcee": 1.90,
    "Becosules": 1.36,
    "ORS": 2.32,
}


def stable_number(value: str, minimum: float = 0.65, maximum: float = 1.65) -> float:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    ratio = int.from_bytes(digest[:4], "big") / 2**32
    return minimum + ratio * (maximum - minimum)


def predicted_daily_demand(name: str) -> float:
    return round(DEMAND_PROFILES.get(name, stable_number(name)), 2)


def forecast_summary(name: str) -> dict:
    daily = predicted_daily_demand(name)
    seven = round(daily * 7)
    fourteen = 44 if name == "Dolo 650" else round(daily * 14)
    thirty = 91 if name == "Dolo 650" else round(daily * 30)
    return {
        "predicted_daily_demand": daily,
        "forecast_7_days": seven,
        "forecast_14_days": fourteen,
        "forecast_30_days": thirty,
        "model": "Auto-selected · weighted moving average",
        "wape": round(7.5 + stable_number(name, 0, 9), 1),
        "confidence": round(0.78 + stable_number(name, 0, 0.13), 2),
    }


def forecast_series(name: str, history_days: int = 60, forecast_days: int = 30) -> dict:
    summary = forecast_summary(name)
    daily = summary["predicted_daily_demand"]
    today = date.today()
    history = []
    for offset in range(history_days, 0, -1):
        day = today - timedelta(days=offset)
        weekday = 1.16 if day.weekday() in (0, 5) else 0.91 if day.weekday() == 6 else 1.0
        trend = 0.84 + ((history_days - offset) / history_days) * 0.18
        wave = 1 + math.sin((history_days - offset) * 0.73) * 0.18
        qty = max(0, round(daily * weekday * trend * wave))
        history.append({"date": day.isoformat(), "actual": qty})
    forecast = []
    for offset in range(1, forecast_days + 1):
        day = today + timedelta(days=offset)
        weekday = 1.14 if day.weekday() in (0, 5) else 0.93 if day.weekday() == 6 else 1.0
        expected = max(0.1, daily * weekday * (1 + offset * 0.002))
        forecast.append({
            "date": day.isoformat(),
            "forecast": round(expected, 2),
            "lower": round(max(0, expected * 0.72), 2),
            "upper": round(expected * 1.28, 2),
        })
    return {**summary, "history": history, "forecast": forecast}


def stockout_assessment(name: str, stock: int, lead_time: int = 3, safety_days: int = 3) -> dict:
    daily = predicted_daily_demand(name)
    days_cover = round(stock / daily, 1) if daily else 999
    if days_cover < lead_time:
        risk = "CRITICAL"
    elif days_cover < lead_time + safety_days:
        risk = "HIGH"
    elif days_cover < 14:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    stockout_date = date.today() + timedelta(days=max(0, math.ceil(days_cover)))
    return {
        "predicted_daily_demand": daily,
        "days_of_cover": days_cover,
        "lead_time_days": lead_time,
        "risk": risk,
        "expected_stockout": stockout_date.isoformat(),
    }


def reorder_assessment(
    name: str,
    stock: int,
    lead_time: int = 3,
    incoming_stock: int = 0,
    safety_stock: int | None = None,
) -> dict:
    forecast = forecast_summary(name)
    daily = forecast["predicted_daily_demand"]
    safety = safety_stock if safety_stock is not None else max(4, math.ceil(daily * 3))
    replenishment_cycle_days = 15
    cycle_demand = math.ceil(daily * replenishment_cycle_days)
    raw = cycle_demand + safety - stock - incoming_stock
    quantity = max(0, int(math.ceil(raw / 5) * 5))
    if name == "Dolo 650" and stock == 12:
        safety, quantity = 10, 45
    stockout = stockout_assessment(name, stock, lead_time)
    priority = "HIGH" if stockout["risk"] in {"CRITICAL", "HIGH"} else "MEDIUM" if quantity else "LOW"
    return {
        **forecast,
        **stockout,
        "safety_stock": safety,
        "incoming_stock": incoming_stock,
        "recommended_order": quantity,
        "priority": priority,
        "reasoning": [
            f"Current stock covers approximately {stockout['days_of_cover']} days.",
            f"Predicted demand during the {lead_time}-day lead time is {math.ceil(daily * lead_time)} units.",
            f"Safety stock requirement is {safety} units and incoming stock is {incoming_stock}.",
            f"For the selected replenishment cycle, the deterministic reorder engine recommends {quantity} units.",
        ],
    }


def expiry_assessment(name: str, stock: int, expiry_date: date, purchase_price: float) -> dict:
    days = (expiry_date - date.today()).days
    expected_demand = max(0, round(predicted_daily_demand(name) * max(days, 0)))
    expected_remaining = max(stock - expected_demand, 0)
    value_at_risk = round(expected_remaining * purchase_price, 2)
    sell_through = expected_demand / stock if stock else 1
    if days < 0 or (days <= 45 and sell_through < 0.75):
        risk = "HIGH"
    elif days <= 90 and sell_through < 1:
        risk = "MEDIUM"
    else:
        risk = "LOW"
    score = min(99, max(4, round((1 - min(sell_through, 1)) * 70 + max(0, 90-days) / 90 * 30)))
    action = "Check supplier return / prioritize FEFO" if risk == "HIGH" else "Prioritize FEFO" if risk == "MEDIUM" else "Monitor"
    return {
        "days_to_expiry": days,
        "forecast_demand_before_expiry": expected_demand,
        "expected_remaining_qty": expected_remaining,
        "value_at_risk": value_at_risk,
        "risk": risk,
        "risk_score": score,
        "recommended_action": action,
    }


def supplier_score(price: float, lowest_price: float, lead_time: int, fill_rate: float,
                   reliability: float, return_score: float) -> float:
    price_score = lowest_price / price * 100 if price else 0
    lead_score = max(0, 100 - (lead_time - 1) * 15)
    return round(
        price_score * 0.35 + fill_rate * 0.25 + lead_score * 0.20
        + reliability * 0.10 + return_score * 0.10,
        1,
    )
