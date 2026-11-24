import asyncio
import csv
import io
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import platform_service as service
from app import platform_ai
from app.dealership_seed import ensure_demo_database
from app.llm_service import LLMServiceError
from app.platform_api import router
from fastapi import FastAPI
from fastapi.testclient import TestClient


class PlatformAnalyticsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ensure_demo_database()
        cls.temp = tempfile.TemporaryDirectory()
        cls.patch = patch.object(service, "STATE_PATH", Path(cls.temp.name) / "state.db")
        cls.patch.start()
        app = FastAPI()
        app.include_router(router)
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        cls.patch.stop()
        cls.temp.cleanup()

    def metric(self, data, key):
        return next(k["value"] for k in data["kpis"] if k["key"] == key)

    def test_revenue_and_receivables_independently_reconcile(self):
        filters = service.Filters("2026-01-01", "2026-06-30", 2, "truck")
        data = service.overview(filters)
        with sqlite3.connect(self.path) as con:
            sales = con.execute("SELECT s.id,s.sale_price,s.cost_price FROM sales s JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id WHERE s.status='completed' AND s.sale_date BETWEEN '2026-01-01' AND '2026-06-30' AND s.branch_id=2 AND m.category='truck'").fetchall()
            paid = sum(con.execute("SELECT COALESCE(SUM(amount),0) FROM payments WHERE sale_id=? AND payment_date<='2026-06-30'", (row[0],)).fetchone()[0] for row in sales)
        self.assertGreater(len(sales), 0)
        self.assertEqual(self.metric(data, "revenue"), sum(s[1] for s in sales))
        self.assertEqual(self.metric(data, "units"), len(sales))
        self.assertEqual(self.metric(data, "gross_profit"), sum(s[1] - s[2] for s in sales))
        self.assertEqual(self.metric(data, "receivable"), sum(s[1] for s in sales) - paid)
        self.assertEqual(sum(r["revenue"] for r in data["trend"]), self.metric(data, "revenue"))
        self.assertEqual(sum(r["revenue"] for r in data["branches"]), self.metric(data, "revenue"))
        self.assertEqual(sum(r["units"] for r in data["category_mix"]), len(sales))

    def test_category_partitions_reconcile(self):
        all_data = service.overview(service.Filters())
        passenger = service.overview(service.Filters(category="passenger"))
        truck = service.overview(service.Filters(category="truck"))
        for key in ("units", "revenue", "gross_profit", "receivable", "inventory"):
            self.assertEqual(self.metric(all_data, key), self.metric(passenger, key) + self.metric(truck, key))

    def test_incomplete_previous_period_has_no_misleading_change(self):
        data = service.overview(service.Filters("2025-01-01", "2025-03-31"))
        self.assertFalse(data["metadata"]["comparison"]["complete"])
        self.assertTrue(all(k["change_pct"] is None for k in data["kpis"]))

    def test_inventory_snapshot_and_literal_search(self):
        first = service.overview(service.Filters("2025-01-01", "2025-01-31"))
        second = service.overview(service.Filters())
        self.assertEqual(self.metric(first, "inventory"), self.metric(second, "inventory"))
        data = service.inventory(category="truck", status="available", branch_id=3, page_size=7)
        self.assertLessEqual(len(data["rows"]), 7)
        self.assertTrue(all(r["category"] == "truck" and r["branch_id"] == 3 and r["status"] == "available" for r in data["rows"]))
        self.assertEqual(service.inventory(search="%' OR 1=1 --")["total"], 0)

    def test_invalid_filters_and_pagination_rejected(self):
        for path in ["/overview?start_date=2026-12-01", "/overview?end_date=2024-01-01", "/overview?start_date=invalid", "/overview?branch_id=99999", "/inventory?page_size=10000", "/inventory?category=nope"]:
            self.assertIn(self.client.get("/api/platform" + path).status_code, (400, 422))

    def test_persisted_reports_export_matches_stored_scope(self):
        payload = {"start_date": "2026-10-01", "end_date": "2026-10-31", "category": "truck", "branch_id": 1, "title": "<script>alert(1)</script>"}
        response = self.client.post("/api/platform/reports", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        report = response.json()
        stored = self.client.get("/api/platform/reports/" + report["id"]).json()
        self.assertEqual(report, stored)
        self.assertEqual(stored["summary"]["revenue"], sum(t["revenue"] for t in stored["overview"]["trend"]))
        csv_response = self.client.get(f"/api/platform/reports/{report['id']}/export?format=csv")
        self.assertEqual(csv_response.status_code, 200)
        self.assertIn("truck", csv_response.text)
        self.assertIn("2026-11-12", csv_response.text)
        html_response = self.client.get(f"/api/platform/reports/{report['id']}/export?format=html")
        self.assertNotIn("<script>", html_response.text)
        self.assertIn("&lt;script&gt;", html_response.text)
        self.assertEqual(self.client.get("/api/platform/reports/unknown").status_code, 404)

    def test_agent_runs_are_persisted_and_honestly_labelled(self):
        for agent_id in ["sales", "inventory", "receivables", "quality"]:
            response = self.client.post(f"/api/platform/agents/{agent_id}/run", json={"category": "truck", "use_ai": False})
            self.assertEqual(response.status_code, 200, response.text)
            run = response.json()
            self.assertEqual(run["kind"], "rule_based")
            self.assertTrue(run["findings"])
        listing = self.client.get("/api/platform/agents").json()
        self.assertEqual(len(listing["agents"]), 4)
        self.assertTrue(all(a["last_run"] for a in listing["agents"]))
        self.assertEqual(self.client.post("/api/platform/agents/unknown/run", json={}).status_code, 404)

    def test_demo_chat_preserves_scope_and_refines_category(self):
        with patch.object(platform_ai.llm_service, "api_key", ""):
            response = self.client.post("/api/platform/chat", json={"prompt": "Yuk mashinalari sotuvlarini ko'rsat", "start_date": "2026-10-01", "end_date": "2026-10-31", "branch_id": 2})
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        self.assertEqual(data["mode"], "demo")
        self.assertEqual(data["filters"]["category"], "truck")
        self.assertEqual(data["filters"]["branch_id"], 2)
        self.assertEqual(data["filters"]["start_date"], "2026-10-01")
        self.assertTrue(data["sources"][0]["synthetic"])

    def test_openai_failures_are_not_fabricated_successes(self):
        with patch.object(platform_ai.llm_service, "provider", "openai", create=True), patch.object(platform_ai.llm_service, "api_key", "fake-test-key"), patch.object(platform_ai.llm_service, "generate_completion", new=AsyncMock(side_effect=LLMServiceError("secret-token-must-not-leak"))):
            response = self.client.post("/api/platform/chat", json={"prompt": "Sotuvni tahlil qil"})
        self.assertEqual(response.status_code, 502)
        self.assertNotIn("secret-token", response.text)

    def test_demo_unsupported_constraints_do_not_receive_unfiltered_answers(self):
        prompts = ["Eng kam sotilgan model qaysi?", "Toyota sotuvlarini ko'rsat", "Cobalt sotuvlari qancha?", "Oktabr oyida qancha sotuv bo'lgan?", "Toshkent filialida sotuv qancha?", "Sof foyda qancha?", "Eng ko'p sotilgan model qaysi?", "Yengil va yuk mashinalarini taqqosla"]
        with patch.object(platform_ai.llm_service, "api_key", ""):
            for prompt in prompts:
                with self.subTest(prompt=prompt):
                    response = self.client.post("/api/platform/chat", json={"prompt": prompt})
                    self.assertEqual(response.status_code, 200)
                    self.assertIn("barcha shartlarini hisoblab bo'lmaydi", response.json()["answer"])
                    self.assertEqual(response.json()["filters"]["start_date"], "2026-01-01")

    def test_demo_branch_quantity_ranking_uses_units(self):
        data = service.overview(service.Filters())
        data["branches"] = [{"name": "Expensive", "revenue": 10000, "units": 2}, {"name": "More units", "revenue": 5000, "units": 8}]
        answer = platform_ai.demo_answer("Qaysi filial eng ko'p sotgan?", data)
        self.assertIn("1. **More units**", answer)
        self.assertIn("avtomobil soni", answer)
        units_answer = platform_ai.demo_answer("Jami nechta avtomobil sotilgan?", data)
        self.assertIn(f"{self.metric(data, 'units'):,} ta avtomobil", units_answer)
        ui_answer = platform_ai.demo_answer("Bu davrda qancha avtomobil sotilgan?", data)
        self.assertEqual(ui_answer, units_answer)


if __name__ == "__main__":
    unittest.main()
