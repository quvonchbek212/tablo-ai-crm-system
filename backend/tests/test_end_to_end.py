"""HTTP integration against the real app with isolated sources and no external calls."""
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import AsyncMock, patch
import importlib
import json
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi.testclient import TestClient
from app import main, platform_service, platform_ai, schema_profiler
from app.database import DatabaseManager
from app.dealership_seed import ensure_demo_database
from app.demo_seed import ensure_demo_database as ensure_enterprise_database
from app.enterprise_questions import QUESTIONS
from app.schema_profiler import SchemaProfiler
from app.dashboard_generator import DashboardGenerator
from app.relationship_guard import RelationshipGuard
from app.llm_service import LLMUnavailableError


class ApplicationAcceptanceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.stack = ExitStack()
        cls.manager = DatabaseManager()
        # Every imported reference must point at the same isolated source manager.
        for module_name in ["database", "main", "external_workspace", "schema_profiler", "query_engine", "agent_analyst",
                            "relationship_guard", "semantic_catalog", "data_quality", "gold_evaluator"]:
            module = importlib.import_module("app." + module_name)
            if hasattr(module, "db_manager"):
                cls.stack.enter_context(patch.object(module, "db_manager", cls.manager))
        cls.stack.enter_context(patch.object(main.settings, "WORKSPACE_MODE", "demo"))
        cls.stack.enter_context(patch.object(platform_service, "STATE_PATH", cls.root / "state.sqlite3"))
        cls.stack.enter_context(patch.object(schema_profiler, "SCHEMA_FILE_PATH", str(cls.root / "latest.json")))
        cls.stack.enter_context(patch.object(schema_profiler, "SCHEMA_CACHE_DIRECTORY", str(cls.root / "cache")))
        cls.stack.enter_context(patch.object(SchemaProfiler, "_cached_context", None))
        cls.stack.enter_context(patch.object(SchemaProfiler, "_cached_time", 0))
        cls.stack.enter_context(patch.object(DashboardGenerator, "_cached_dashboard", None))
        cls.stack.enter_context(patch.object(DashboardGenerator, "_cached_key", None))
        cls.stack.enter_context(patch.object(DashboardGenerator, "_cached_time", 0))
        cls.stack.enter_context(patch.object(RelationshipGuard, "_cache", {}))
        cls.stack.enter_context(patch.object(main.llm_service, "api_key", ""))
        cls.stack.enter_context(patch.object(main.llm_service, "check_health", new=AsyncMock(return_value={
            "model_ready": False, "provider": "openai", "online": False,
            "error": "API key is not configured for isolated test"})))
        # A mistaken unmocked AI path fails quickly and never reaches the Internet.
        cls.stack.enter_context(patch.object(main.llm_service, "generate_completion",
            new=AsyncMock(side_effect=LLMUnavailableError("AI disabled in acceptance tests"))))
        cls.stack.enter_context(patch.object(main.llm_service, "generate_tool_turn",
            new=AsyncMock(side_effect=LLMUnavailableError("AI tools disabled in isolated acceptance tests"))))
        cls.client = cls.stack.enter_context(TestClient(main.app))

    @classmethod
    def tearDownClass(cls):
        if cls.manager._engine is not None:
            cls.manager._engine.dispose()
        cls.stack.close()
        cls.temp.cleanup()

    def setUp(self):
        self.manager.set_connection(f"sqlite:///{ensure_demo_database().as_posix()}")
        SchemaProfiler.clear_cache(delete_file=False)

    def test_startup_health_and_live_schema_are_consistent(self):
        self.assertEqual(self.client.get("/").status_code, 200)
        health = self.client.get("/api/health").json()
        self.assertEqual(health["database"]["status"], "connected")
        self.assertTrue(health["database"]["read_only"])
        self.assertEqual(health["status"], "degraded")
        schema = self.client.get("/api/database/schema").json()
        self.assertTrue(schema["profile_complete"])
        self.assertIn("sales", {table["name"] for table in schema["tables"]})
        self.assertEqual(schema["total_tables"], 12)

    def test_chat_greeting_and_definition_do_not_start_sql_investigation(self):
        with patch.object(main, "iterative_agent_enabled", return_value=True), patch(
            "app.data_agent.execute_data_agent", new_callable=AsyncMock
        ) as investigate:
            with patch.object(SchemaProfiler, "profile") as profile:
                greeting = self.client.post("/api/query/chat", json={"prompt": "Assalomu alaykum!"}).json()
                self.assertTrue(greeting["success"])
                self.assertFalse(greeting["sql_query"])
                profile.assert_not_called()
            with patch.object(main.llm_service, "generate_completion", new=AsyncMock(
                    return_value="Yalpi marja sotuv summasidagi yalpi foyda ulushini bildiradi.")):
                definition = self.client.post("/api/query/chat", json={"prompt": "Yalpi marja nima degani?"}).json()
                self.assertTrue(definition["success"])
                self.assertIn("yalpi foyda", definition["explanation"])
                self.assertEqual(definition["charts"], [])
            investigate.assert_not_awaited()

    def test_chat_forwards_standalone_clarification_answer_with_original_metric(self):
        question = "Oktabrda jami qancha sotuv bo'lgan?"
        history = [
            {"role": "user", "content": question},
            {"role": "assistant", "content": "Qaysi hudud bo‘yicha?", "clarification": True},
        ]
        with patch.object(main, "iterative_agent_enabled", return_value=False), patch(
            "app.agent_analyst.plan_question", new=AsyncMock(side_effect=LLMUnavailableError("local test"))
        ) as planner:
            response = self.client.post("/api/query/chat", json={"prompt": "Toshkent", "history": history})
            self.assertEqual(response.status_code, 200)
            planner.assert_awaited_once()
            planned_question, hints = planner.await_args.args
            self.assertEqual(question + ". hudud: Toshkent", planned_question)
            self.assertIsInstance(hints, list)

    def test_enterprise_presets_from_chat_use_only_the_active_enterprise_database(self):
        self.assertEqual(self.client.get("/api/query/presets").json(), {"questions": []})
        ordinary = self.client.post("/api/query/chat", json={"prompt": QUESTIONS[0].question}).json()
        self.assertFalse(ordinary["success"])
        path = ensure_enterprise_database()
        self.manager.set_connection(f"sqlite:///{path.as_posix()}")
        SchemaProfiler.clear_cache(delete_file=False)
        listed = self.client.get("/api/query/presets").json()["questions"]
        self.assertEqual(listed, [preset.question for preset in QUESTIONS])
        with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as source:
            source.row_factory = sqlite3.Row
            for preset in QUESTIONS:
                with self.subTest(question=preset.question):
                    response = self.client.post("/api/query/chat", json={"prompt": preset.question})
                    self.assertEqual(response.status_code, 200, response.text)
                    answer = response.json()
                    self.assertTrue(answer["success"], answer["explanation"])
                    expected = [dict(row) for row in source.execute(preset.sql)]
                    self.assertEqual(answer["rows"], expected)
                    self.assertEqual(answer["query_results"][0]["rows"], expected)
                    self.assertEqual(answer["chart"]["query_id"], answer["query_results"][0]["id"])
                    self.assertEqual(answer["charts"], [answer["chart"]])
                    self.assertEqual(answer["chart"]["x_axis"], preset.label)
                    self.assertEqual(answer["chart"]["y_axis"], preset.metric)
                    self.assertIn(str(expected[0][preset.label]), answer["explanation"])
            modified = self.client.post("/api/query/chat", json={"prompt": listed[0] + " Faqat 2025-yil."}).json()
            self.assertFalse(modified["success"])

    def test_connect_external_sqlite_refreshes_schema_without_mixing_demo_panel(self):
        original = self.client.get("/api/platform/overview").json()["kpis"]
        external = self.root / "external-fixture.db"
        with closing(sqlite3.connect(external)) as con:
            con.execute("CREATE TABLE IF NOT EXISTS external_orders(id INTEGER PRIMARY KEY, total INTEGER NOT NULL)")
            if con.execute("SELECT COUNT(*) FROM external_orders").fetchone()[0] == 0:
                con.executemany("INSERT INTO external_orders VALUES (?,?)", [(1, 25), (2, 75)])
            con.commit()
        result = self.client.post("/api/database/connect", json={"connection_url": f"sqlite:///{external.as_posix()}"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["total_tables"], 1)
        schema = self.client.get("/api/database/schema").json()
        self.assertEqual([t["name"] for t in schema["tables"]], ["external_orders"])
        self.assertEqual(self.manager.execute_query("SELECT SUM(total) AS total FROM external_orders")[0]["total"], 100)
        with self.assertRaises(Exception):
            self.manager.execute_query("DELETE FROM external_orders")
        self.assertEqual(self.manager.execute_query("SELECT COUNT(*) AS n FROM external_orders")[0]["n"], 2)
        self.assertEqual(self.client.get("/api/platform/overview").json()["kpis"], original)
        with closing(sqlite3.connect(external)) as con:
            self.assertEqual(con.execute("SELECT COUNT(*) FROM external_orders").fetchone()[0], 2)

    def test_failed_connect_keeps_previous_source(self):
        before = self.manager.connection_fingerprint
        result = self.client.post("/api/database/connect", json={
            "connection_url": f"sqlite:///{(self.root / 'does-not-exist.db').as_posix()}"})
        self.assertEqual(result.status_code, 400)
        self.assertEqual(self.manager.connection_fingerprint, before)
        self.assertTrue(self.manager.is_connected)
        self.assertFalse((self.root / "does-not-exist.db").exists())

    def test_complete_report_and_monitor_workflow_persists_matching_scope(self):
        filters = {"start_date": "2026-09-01", "end_date": "2026-09-30", "branch_id": 4, "category": "truck"}
        overview = self.client.get("/api/platform/overview", params=filters).json()
        made = self.client.post("/api/platform/reports", json={**filters, "title": "Acceptance report"})
        self.assertEqual(made.status_code, 201)
        report = made.json()
        saved = self.client.get("/api/platform/reports/" + report["id"]).json()
        self.assertEqual(saved["filters"], filters)
        self.assertEqual(saved["overview"]["kpis"], overview["kpis"])
        for fmt in ("csv", "html"):
            exported = self.client.get(f"/api/platform/reports/{report['id']}/export", params={"format": fmt})
            self.assertEqual(exported.status_code, 200)
            self.assertIn("attachment", exported.headers["content-disposition"])
            self.assertIn("2026-09-01", exported.text)
            self.assertIn("truck", exported.text)
        run = self.client.post("/api/platform/agents/inventory/run", json=filters)
        self.assertEqual(run.status_code, 200, run.text)
        self.assertEqual(run.json()["filters"], filters)
        history = self.client.get("/api/platform/agents").json()["runs"]
        self.assertIn(run.json()["id"], {r["id"] for r in history})

    def test_empty_period_and_invalid_requests_are_explicit(self):
        response = self.client.get("/api/platform/overview", params={"start_date": "2025-01-01", "end_date": "2025-01-01"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(next(k["value"] for k in response.json()["kpis"] if k["key"] == "units"), 0)
        self.assertEqual(response.json()["recent_sales"], [])
        self.assertEqual(self.client.post("/api/platform/chat", json={"prompt": "  "}).status_code, 400)
        self.assertEqual(self.client.post("/api/platform/chat", json={"prompt": "x" * 4001}).status_code, 422)
        self.assertEqual(self.client.get("/api/platform/reports/no-such-id").status_code, 404)

    def test_demo_chat_never_needs_network(self):
        result = self.client.post("/api/platform/chat", json={"prompt": "Jami nechta avtomobil sotilgan?"})
        self.assertEqual(result.status_code, 200, result.text)
        self.assertEqual(result.json()["mode"], "demo")
        self.assertTrue(result.json()["sources"][0]["synthetic"])
        with closing(sqlite3.connect(ensure_demo_database())) as con:
            units = con.execute("SELECT COUNT(*) FROM sales WHERE status='completed' AND sale_date BETWEEN '2026-01-01' AND '2026-11-12'").fetchone()[0]
        self.assertGreater(units, 0)
        self.assertIn(f"{units:,} ta avtomobil", result.json()["answer"])

    def test_http_ai_plan_executes_historical_model_scope_before_synthesis(self):
        plan = {"kind": "analysis", "message": "", "queries": [{
            "operation": "sales", "metric": "revenue", "group_by": "none",
            "start_date": "2025-01-01", "end_date": "2025-12-31",
            "brand": "Chevrolet", "model": "Cobalt", "limit": 10,
        }]}
        completion = AsyncMock(side_effect=[json.dumps(plan), "Tekshirilgan namuna natijasi."])
        with patch.object(main.llm_service, "api_key", "test-only-key"), patch.object(
            main.llm_service, "generate_completion", new=completion
        ):
            result = self.client.post("/api/platform/chat", json={"prompt": "2025-yilda Cobalt sotuv summasi qancha?"})
        self.assertEqual(result.status_code, 200, result.text)
        payload = result.json()
        self.assertEqual(payload["mode"], "openai")
        self.assertEqual(payload["filters"]["start_date"], "2025-01-01")
        self.assertEqual(payload["filters"]["model"], "Cobalt")
        with closing(sqlite3.connect(ensure_demo_database())) as con:
            expected = con.execute("SELECT SUM(s.sale_price) FROM sales s JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id WHERE s.status='completed' AND s.sale_date BETWEEN '2025-01-01' AND '2025-12-31' AND m.brand='Chevrolet' AND m.model='Cobalt'").fetchone()[0]
        self.assertEqual(payload["analyses"][0]["rows"][0]["value"], expected)
        self.assertEqual(payload["table"]["rows"][0]["value"], expected)
        self.assertEqual(completion.await_count, 2)
        synthesis_input = json.loads(completion.await_args_list[1].args[1])
        self.assertEqual(synthesis_input["EXECUTED_RESULTS"]["analyses"][0]["rows"][0]["value"], expected)


if __name__ == "__main__":
    unittest.main()
