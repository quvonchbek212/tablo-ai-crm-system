import asyncio
import json
import sqlite3
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.analytics_tools import AnalysisPlan, AnalysisQuery, execute
from app import platform_ai, platform_service
from app.dealership_seed import ensure_demo_database
from app.llm_service import LLMServiceError


class AnalyticsToolsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.path = ensure_demo_database()

    def query(self, **kwargs):
        return AnalysisQuery(**{"start_date": "2025-01-01", "end_date": "2025-12-31", **kwargs})

    def independent(self, sql, params=()):
        with sqlite3.connect(self.path) as con:
            return con.execute(sql, params).fetchall()

    def test_year_brand_and_model_exact_sum(self):
        q = self.query(brand="Chevrolet", model="Cobalt", group_by="month", order="asc", sort_by="label", limit=50)
        result = execute(q)
        expected = self.independent("SELECT substr(s.sale_date,1,7), SUM(s.sale_price) FROM sales s JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id WHERE s.status='completed' AND s.sale_date BETWEEN '2025-01-01' AND '2025-12-31' AND m.brand='Chevrolet' AND m.model='Cobalt' GROUP BY 1 ORDER BY 1")
        self.assertEqual([(r["label"], r["value"]) for r in result["rows"]], expected)
        self.assertTrue(result["rows"])

    def test_model_rank_by_units_not_price(self):
        result = execute(self.query(metric="units", group_by="model", order="desc", limit=3))
        expected = self.independent("SELECT m.brand||' '||m.model,COUNT(*) n FROM sales s JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id WHERE s.status='completed' AND s.sale_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY m.id ORDER BY n DESC,1 ASC LIMIT 3")
        self.assertEqual([(r["label"], r["value"]) for r in result["rows"]], expected)
        self.assertTrue(result["truncated"])

    def test_receipts_use_payment_dates(self):
        result = execute(self.query(operation="payments", metric="receipts"))
        expected = self.independent("SELECT SUM(p.amount) FROM payments p JOIN sales s ON s.id=p.sale_id WHERE s.status='completed' AND p.payment_date BETWEEN '2025-01-01' AND '2025-12-31'")[0][0]
        self.assertEqual(result["rows"][0]["value"], expected)

    def test_daily_row_limit_truncates_chronologically_not_by_value(self):
        result = execute(self.query(group_by="day", order="asc", sort_by="label", limit=3))
        expected = self.independent("SELECT sale_date,SUM(sale_price) FROM sales WHERE status='completed' AND sale_date BETWEEN '2025-01-01' AND '2025-12-31' GROUP BY sale_date ORDER BY sale_date ASC LIMIT 3")
        self.assertEqual([(r["label"], r["value"]) for r in result["rows"]], expected)
        self.assertTrue(result["truncated"])

    def test_receivable_does_not_fan_out_payments(self):
        result = execute(self.query(metric="receivable", category="truck", branch_id=2))
        sales = self.independent("SELECT s.id,s.sale_price FROM sales s JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id WHERE s.status='completed' AND s.sale_date BETWEEN '2025-01-01' AND '2025-12-31' AND s.branch_id=2 AND m.category='truck'")
        paid = sum(self.independent("SELECT COALESCE(SUM(amount),0) FROM payments WHERE sale_id=? AND payment_date<='2025-12-31'", (sid,))[0][0] for sid, _ in sales)
        self.assertEqual(result["rows"][0]["value"], sum(price for _, price in sales) - paid)

    def test_employee_rank_has_source_names_and_units(self):
        result = execute(self.query(metric="units", group_by="employee", limit=50))
        self.assertTrue(result["rows"])
        self.assertGreater(result["rows"][0]["value"], 0)
        self.assertTrue(result["rows"][0]["label"])

    def test_new_customers_are_not_distinct_purchasers(self):
        result = execute(self.query(operation="customers", metric="customer_count"))
        expected = self.independent("SELECT COUNT(*) FROM customers WHERE created_at BETWEEN '2025-01-01' AND '2025-12-31'")[0][0]
        self.assertEqual(result["rows"][0]["value"], expected)
        buyers = execute(self.query(metric="customer_count"))
        expected_buyers = self.independent("SELECT COUNT(DISTINCT customer_id) FROM sales WHERE status='completed' AND sale_date BETWEEN '2025-01-01' AND '2025-12-31'")[0][0]
        self.assertEqual(buyers["rows"][0]["value"], expected_buyers)

    def test_snapshot_guard_and_status(self):
        with self.assertRaises(ValueError):
            self.query(operation="inventory", metric="units")
        q = self.query(operation="inventory", metric="units", end_date="2026-11-12", status="reserved", category="truck")
        actual = execute(q)["rows"][0]["value"]
        expected = self.independent("SELECT COUNT(*) FROM vehicles v JOIN vehicle_models m ON m.id=v.model_id WHERE v.status='reserved' AND m.category='truck'")[0][0]
        self.assertEqual(actual, expected)

    def test_invalid_arguments_do_not_turn_into_sql(self):
        for args in [{"group_by": "DROP TABLE sales"}, {"limit": 100000}, {"operation": "customers", "metric": "customer_count", "category": "truck"}, {"sql": "SELECT 1"}, {"operation": "payments", "metric": "units"}]:
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.query(**args)
        with self.assertRaises(ValueError):
            execute(self.query(brand="' OR 1=1 --"))


class PlannedChatTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_cash_receipts_repairs_wrong_revenue_plan(self):
        plan = {"kind": "analysis", "queries": [{"operation": "sales", "metric": "revenue", "start_date": "2026-10-01", "end_date": "2026-10-31"}]}
        completion = AsyncMock(side_effect=[json.dumps(plan), "Pul tushumi hisoblandi."])
        with patch.object(platform_ai.llm_service, "generate_completion", completion):
            result = await platform_ai._openai_chat("2026-yil oktabr oyida jami pul tushumi qancha?", platform_service.Filters(), [])
        self.assertEqual(result["analyses"][0]["operation"], "payments")
        self.assertEqual(result["analyses"][0]["metric"], "receipts")
        with platform_service.source_connection() as con:
            receipts = con.execute("SELECT SUM(amount) FROM payments WHERE payment_date BETWEEN '2026-10-01' AND '2026-10-31'").fetchone()[0]
        self.assertGreater(receipts, 0)
        self.assertEqual(result["analyses"][0]["rows"][0]["value"], receipts)
        self.assertEqual(result["filters"]["start_date"], "2026-10-01")
        payload = json.loads(completion.call_args_list[0].args[1])
        self.assertEqual(payload["EXPLICIT_METRIC_CONTRACT"]["metric"], "receipts")

    def test_cash_contract_does_not_override_negated_or_multi_metric_requests(self):
        for prompt in ["Pul tushumi emas, sotuv summasini ko'rsat", "Sotuv summasi va pul tushumini taqqosla", "Pul tushumi bilan yalpi foydani ko'rsat", "Nechta to'lov bo'lgan, pul tushumi qancha?"]:
            with self.subTest(prompt=prompt):
                self.assertIsNone(platform_ai.explicit_metric_contract(prompt))
        self.assertEqual(platform_ai.explicit_metric_contract("Sotuv summasi emas, to‘lovlar bo‘yicha hisobla")["metric"], "receipts")

    def test_cash_contract_rejects_incompatible_multi_query_plan(self):
        plan = AnalysisPlan.model_validate({"kind": "analysis", "queries": [{"operation": "sales", "metric": "revenue", "start_date": "2026-10-01", "end_date": "2026-10-31"}] * 2})
        with self.assertRaises(LLMServiceError):
            platform_ai.enforce_metric_contract(plan, platform_ai.explicit_metric_contract("Pul tushumi qancha?"))

    async def test_planned_brand_question_executes_verified_tool(self):
        plan = {"kind": "analysis", "queries": [{"operation": "sales", "metric": "units", "group_by": "model", "brand": "Chevrolet", "start_date": "2025-01-01", "end_date": "2025-12-31", "limit": 5}], "message": ""}
        fake = AsyncMock(side_effect=[json.dumps(plan), "Hisoblangan natija tayyor."])
        with patch.object(platform_ai.llm_service, "provider", "openai"), patch.object(platform_ai.llm_service, "api_key", "test-key"), patch.object(platform_ai.llm_service, "generate_completion", fake):
            result = await platform_ai.chat("2025 yilda Chevrolet modellari soni", platform_service.Filters())
        self.assertEqual(result["mode"], "openai")
        self.assertEqual(result["filters"]["start_date"], "2025-01-01")
        self.assertEqual(result["filters"]["brand"], "Chevrolet")
        self.assertTrue(result["table"]["rows"])
        self.assertTrue(all(r["label"].startswith("Chevrolet ") for r in result["table"]["rows"]))
        evidence = json.loads(fake.call_args_list[1].args[1])["EXECUTED_RESULTS"]
        self.assertEqual(evidence["analyses"], result["analyses"])

    async def test_bad_plan_does_not_execute_source_query(self):
        with patch.object(platform_ai.llm_service, "generate_completion", AsyncMock(return_value='{"kind":"analysis","queries":[{"sql":"DROP TABLE sales"}]}')), patch.object(platform_ai, "execute_analysis") as execute_mock:
            with self.assertRaises(LLMServiceError):
                await platform_ai._openai_chat("delete", platform_service.Filters(), [])
        execute_mock.assert_not_called()

    async def test_clarification_has_no_fake_table_or_second_ai_call(self):
        completion = AsyncMock(return_value=json.dumps({"kind": "clarification", "queries": [], "message": "Qaysi filial?"}))
        with patch.object(platform_ai.llm_service, "generate_completion", completion):
            result = await platform_ai._openai_chat("O'sha filial", platform_service.Filters(), [])
        self.assertEqual(result["response_type"], "clarification")
        self.assertNotIn("table", result)
        self.assertEqual(completion.await_count, 1)
