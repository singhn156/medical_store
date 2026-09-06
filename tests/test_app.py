import os
import unittest
import io
from unittest.mock import patch
from datetime import date, timedelta

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["GROQ_API_KEY"] = ""  # Tests must never spend API credits or depend on a developer's .env.
os.environ["AI_MODE"] = "local"  # Legacy vision tests exercise the pluggable local-provider boundary.

from fastapi.testclient import TestClient
from backend.main import Base, Purchase, PurchaseItem, SessionLocal, app, engine
from sqlalchemy import func, select


class PharmacyFlowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        Base.metadata.drop_all(engine)
        cls.client_context = TestClient(app)
        cls.client = cls.client_context.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client_context.__exit__(None, None, None)

    def test_01_health_and_seed_data(self):
        self.assertEqual(self.client.get("/api/health").json()["status"], "ok")
        self.assertGreaterEqual(len(self.client.get("/api/products").json()), 6)

    def test_02_receive_then_fefo_sale(self):
        client = self.client
        product = client.get("/api/products?q=Dolo").json()[0]
        before = product["stock"]
        payload = {"supplier_name":"Test Supplier","invoice_number":"TEST-001","items":[{
            "product_id":product["id"],"batch_number":"EARLY","expiry_date":(date.today()+timedelta(days=40)).isoformat(),
            "quantity":3,"purchase_price":20,"mrp":35}]}
        self.assertEqual(client.post("/api/purchases/confirm", json=payload).status_code, 201)
        sale = {"items":[{"product_id":product["id"],"quantity":2,"unit_price":35}],"discount":0,"payment_method":"Cash"}
        self.assertEqual(client.post("/api/sales/complete", json=sale).status_code, 201)
        batches = client.get("/api/inventory").json()
        early = next(x for x in batches if x["batch_number"] == "EARLY")
        self.assertEqual(early["quantity_available"], 1)
        self.assertEqual(client.get("/api/products?q=Dolo").json()[0]["stock"], before + 1)


    def test_03_expired_stock_is_rejected_and_duplicate_invoice_blocked(self):
        client = self.client
        product = client.get("/api/products?q=Dolo").json()[0]
        payload = {"supplier_name":"Test","invoice_number":"TEST-001","items":[{
            "product_id":product["id"],"batch_number":"X","expiry_date":(date.today()+timedelta(days=30)).isoformat(),
            "quantity":1,"purchase_price":1,"mrp":2}]}
        self.assertEqual(client.post("/api/purchases/confirm", json=payload).status_code, 409)
        bad = payload | {"invoice_number":"EXPIRED-1"}
        bad["items"][0]["expiry_date"] = (date.today()-timedelta(days=1)).isoformat()
        self.assertEqual(client.post("/api/purchases/confirm", json=bad).status_code, 422)

    def test_04_csv_invoice_import_matches_products(self):
        csv_data = b"medicine_name,barcode,batch_number,expiry_date,quantity,purchase_price,mrp\nDolo 650,8901000000001,CSV1,2028-05,4,20,35\n"
        response = self.client.post("/api/purchases/import-csv", files={"file": ("invoice.csv", io.BytesIO(csv_data), "text/csv")})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["ready_count"], 1)
        self.assertIsNotNone(response.json()["rows"][0]["product_id"])

    def test_05_reconciliation_creates_adjustment(self):
        batch = self.client.get("/api/inventory").json()[0]
        target = batch["quantity_available"] + 2
        response = self.client.post("/api/inventory/reconcile", json={"batch_id": batch["id"], "physical_quantity": target, "reason": "Test count"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["difference"], 2)
        movements = self.client.get("/api/stock-movements").json()
        self.assertTrue(any(x["movement_type"] == "ADJUSTMENT" for x in movements))

    def test_06_sale_return_restocks_original_batch(self):
        product = self.client.get("/api/products?q=Limcee").json()[0]
        sale = self.client.post("/api/sales/complete", json={"items": [{"product_id": product["id"], "quantity": 1, "unit_price": product["default_mrp"]}], "discount": 0, "payment_method": "UPI"}).json()
        detail = self.client.get(f"/api/sales/{sale['id']}").json()
        returned = self.client.post(f"/api/sales/{sale['id']}/return", json={"sale_item_id": detail["items"][0]["id"], "quantity": 1, "reason": "Test return"})
        self.assertEqual(returned.status_code, 201)
        self.assertEqual(returned.json()["refund_amount"], product["default_mrp"])
        self.assertEqual(self.client.get(f"/api/sales/{sale['id']}/receipt").status_code, 200)

    def test_07_ai_requires_server_side_key(self):
        status = self.client.get("/api/ai/status").json()
        self.assertFalse(status["enabled"])
        response = self.client.post("/api/ai/product/recognize", files={"image": ("pack.jpg", b"fake", "image/jpeg")})
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("GROQ_API_KEY", str(response.request.headers))

    @patch("backend.main.groq_vision")
    def test_08_validated_vision_match_does_not_change_inventory(self, vision):
        vision.return_value = {"detected": True, "medicine_name": "Dolo 650", "brand_name": "Dolo",
                               "generic_name": "Paracetamol", "manufacturer": "Micro Labs", "strength": "650 mg",
                               "dosage_form": "Tablet", "pack_size": "15 tablets", "barcode": "8901000000001",
                               "batch_number": "DL1", "manufacturing_date": "06/2026", "expiry_date": "05/2028",
                               "mrp": "Rs. 35/-", "confidence": 95}
        before = self.client.get("/api/products?q=Dolo").json()[0]["stock"]
        with patch("backend.main.ai_ready", return_value=True):
            response = self.client.post("/api/ai/product/recognize", files={"image": ("pack.jpg", b"fake", "image/jpeg")})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["result"]["expiry_date"], "2028-05")
        self.assertEqual(body["result"]["confidence"], .95)
        self.assertEqual(body["candidates"][0]["medicine_name"], "Dolo 650")
        self.assertEqual(self.client.get("/api/products?q=Dolo").json()[0]["stock"], before)

    def test_09_intelligence_endpoints_are_data_backed(self):
        products = self.client.get("/api/products").json()
        self.assertGreaterEqual(len(products), 30)
        dolo = next(product for product in products if product["medicine_name"] == "Dolo 650")
        forecast = self.client.get(f"/api/forecasts/{dolo['id']}")
        self.assertEqual(forecast.status_code, 200)
        self.assertEqual(forecast.json()["forecast_14_days"], 44)
        reorder = next(row for row in self.client.get("/api/reorder").json() if row["product_id"] == dolo["id"])
        self.assertGreaterEqual(reorder["recommended_order"], 0)
        self.assertTrue(reorder["reasoning"])

    def test_10_expiry_and_supplier_scores(self):
        risks = self.client.get("/api/expiry-risk")
        self.assertEqual(risks.status_code, 200)
        augmentin = next(row for row in risks.json() if row["medicine_name"] == "Augmentin 625 Duo")
        self.assertGreater(augmentin["expected_remaining_qty"], 0)
        self.assertEqual(augmentin["risk"], "HIGH")
        product = self.client.get("/api/products?q=Dolo").json()[0]
        suppliers = self.client.get(f"/api/suppliers/compare/{product['id']}").json()
        self.assertGreaterEqual(len(suppliers), 3)
        self.assertTrue(suppliers[0]["recommended"])

    def test_11_mock_invoice_is_validated_and_requires_confirmation(self):
        with patch("backend.main.AI_MODE", "mock"):
            response = self.client.post("/api/ai/invoice/extract", files={"image": ("invoice.png", b"demo", "image/png")})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["requires_confirmation"])
        self.assertEqual(len(body["items"]), 3)
        self.assertEqual(body["items"][2]["status"], "review")

    def test_12_procurement_plan_requires_human_approval(self):
        dolo = self.client.get("/api/products?q=Dolo").json()[0]
        plan = self.client.post("/api/reorder/plan", json={"product_ids": [dolo["id"]], "quantities": {}})
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(plan.json()["status"], "AWAITING_HUMAN_APPROVAL")
        self.assertEqual(plan.json()["products"], 1)

    def test_13_full_invoice_purchase_inventory_billing_loop(self):
        before = self.client.get("/api/products?q=Dolo").json()[0]["stock"]
        with patch("backend.main.AI_MODE", "mock"):
            extracted = self.client.post("/api/ai/invoice/extract", files={"image": ("invoice.png", b"demo", "image/png")}).json()
            pack = self.client.post("/api/ai/batch-expiry/extract", files={"image": ("pack.jpg", b"demo", "image/jpeg")}).json()
        items = []
        for row in extracted["items"]:
            expiry = row["expiry_date"] or pack["result"]["expiry_date"]
            items.append({"product_id": row["product_id"], "batch_number": row["batch_number"],
                          "expiry_date": expiry + "-01", "quantity": row["quantity"],
                          "purchase_price": row["purchase_price"], "mrp": row["mrp"]})
        purchase = self.client.post("/api/purchases/confirm", json={"supplier_name": extracted["supplier_name"],
                                    "invoice_number": extracted["invoice_number"], "items": items})
        self.assertEqual(purchase.status_code, 201)
        self.assertEqual(purchase.json()["units_added"], 110)
        with SessionLocal() as db:
            purchase_record = db.scalar(select(Purchase).where(Purchase.invoice_number == extracted["invoice_number"]))
            self.assertIsNotNone(purchase_record)
            self.assertEqual(db.scalar(select(func.count(PurchaseItem.id)).where(
                PurchaseItem.purchase_id == purchase_record.id)), 3)
        dolo = self.client.get("/api/products?q=Dolo").json()[0]
        self.assertEqual(dolo["stock"], before + 50)
        sale = self.client.post("/api/sales/complete", json={"items": [{"product_id": dolo["id"],
                                "quantity": 1, "unit_price": dolo["default_mrp"]}],
                                "discount": 0, "payment_method": "UPI"})
        self.assertEqual(sale.status_code, 201)
        self.assertEqual(self.client.get("/api/products?q=Dolo").json()[0]["stock"], before + 49)

    @patch("backend.main.groq_catalog_matches")
    @patch("backend.main.groq_vision")
    def test_14_llm_catalog_candidates_and_new_medicine_path(self, vision, matcher):
        dolo = self.client.get("/api/products?q=Dolo").json()[0]
        vision.return_value = {"supplier_name": "Live Supplier", "invoice_number": "LIVE-MATCH-1",
                               "invoice_date": date.today().isoformat(), "confidence": .9, "items": [{
                                   "medicine_name": "D0L0 SIX FIFTY TAB", "manufacturer": "Micro",
                                   "batch_number": "LIVE1", "expiry_date": "2028-10", "quantity": 5,
                                   "purchase_price": 24, "mrp": 32, "confidence": .8}]}
        matcher.return_value = {0: {"candidate_ids": [dolo["id"]], "no_match": False,
                                    "reason": "OCR spelling variation of Dolo 650"}}
        with patch("backend.main.AI_MODE", "groq"):
            matched = self.client.post("/api/ai/invoice/extract", files={"image": ("invoice.jpg", b"x", "image/jpeg")}).json()
        self.assertEqual(matched["items"][0]["product_id"], dolo["id"])
        self.assertEqual(matched["items"][0]["match_candidates"][0]["id"], dolo["id"])
        self.assertEqual(matched["items"][0]["matching_provider"], "Groq Qwen")

        matcher.return_value = {0: {"candidate_ids": [], "no_match": True,
                                    "reason": "No equivalent medicine exists in Product Master"}}
        with patch("backend.main.AI_MODE", "groq"):
            unmatched = self.client.post("/api/ai/invoice/extract", files={"image": ("invoice.jpg", b"x", "image/jpeg")}).json()
        self.assertIsNone(unmatched["items"][0]["product_id"])
        self.assertTrue(unmatched["items"][0]["create_new_suggested"])

    @patch("backend.main.groq_catalog_matches")
    @patch("backend.main.groq_vision")
    def test_15_generic_and_strength_matches_cannot_be_hidden(self, vision, matcher):
        created = self.client.post("/api/products", json={"sku": "PARA-500-TEST", "medicine_name": "Paracetamol 500 mg",
                                   "generic_name": "Paracetamol", "strength": "500 mg", "manufacturer": "Generic",
                                   "category": "Pain Relief", "default_mrp": 20}).json()
        vision.return_value = {"supplier_name": "Supplier", "invoice_number": "PARA-INVOICE",
                               "confidence": .9, "items": [{"medicine_name": "PARACETAMOL500MG TAB",
                               "generic_name": "Paracetamol", "strength": "500 mg", "batch_number": "P500",
                               "expiry_date": "2028-12", "quantity": 10, "purchase_price": 10,
                               "mrp": 20, "gst": 12, "confidence": .88}]}
        matcher.return_value = {0: {"candidate_ids": [], "no_match": True, "reason": "No match"}}
        with patch("backend.main.AI_MODE", "groq"):
            item = self.client.post("/api/ai/invoice/extract",
                                    files={"image": ("invoice.jpg", b"x", "image/jpeg")}).json()["items"][0]
        names = [candidate["medicine_name"] for candidate in item["match_candidates"]]
        self.assertEqual(item["product_id"], created["id"])
        self.assertIn("Crocin Advance", names)
        self.assertIn("Calpol 500", names)


if __name__ == "__main__":
    unittest.main()
