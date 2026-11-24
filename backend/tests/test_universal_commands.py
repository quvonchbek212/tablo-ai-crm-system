"""Universal command tools against an isolated, non-dealership SQLite database."""
import asyncio
import importlib
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing
from dataclasses import dataclass
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import assistant_commands as commands, external_workspace, platform_api, platform_service as service, schema_profiler
from app.agent_analyst import AIDataAnalystAgent
from app.database import DatabaseManager
from app.dashboard_generator import DashboardGenerator
from app.models import NaturalQueryResponse
from app.schema_profiler import SchemaProfiler
from app.relationship_guard import RelationshipGuard


@dataclass(frozen=True)
class ExternalFilters:
    start_date: str | None = None
    end_date: str | None = None
    branch_id: int | None = None
    category: str | None = None


class UniversalCommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stack = ExitStack()
        self.manager = DatabaseManager()
        self.database = self.root / "retail.sqlite3"
        self.other = self.root / "different.sqlite3"
        for path, amount in [(self.database, 125), (self.other, 999)]:
            with closing(sqlite3.connect(path)) as con:
                con.executescript("""
                    CREATE TABLE customers (id INTEGER PRIMARY KEY, region TEXT);
                    CREATE TABLE products (id INTEGER PRIMARY KEY, category TEXT, price NUMERIC);
                    CREATE TABLE orders (id INTEGER PRIMARY KEY, customer_id INTEGER REFERENCES customers(id),
                        order_date DATE, status TEXT, total NUMERIC);
                    CREATE TABLE order_items (id INTEGER PRIMARY KEY, order_id INTEGER REFERENCES orders(id),
                        product_id INTEGER REFERENCES products(id), quantity INTEGER);
                    INSERT INTO customers VALUES (1, 'East'), (2, NULL);
                    INSERT INTO products VALUES (1, 'Books', 25), (2, 'Tools', 50);
                    INSERT INTO orders VALUES (2, 2, '2024-05-20', 'cancelled', 250);
                    INSERT INTO orders VALUES (3, 1, '2023-12-31', 'completed', 50);
                    INSERT INTO order_items VALUES (1, 1, 1, 5), (2, 2, 2, 5);
                """)
                con.execute("INSERT INTO orders VALUES (1, 1, '2024-05-03', 'completed', ?)", (amount,))
                con.commit()
        for name in ["database", "external_workspace", "schema_profiler", "query_engine", "agent_analyst",
                     "relationship_guard", "semantic_catalog", "data_quality"]:
            module = importlib.import_module("app." + name)
            if hasattr(module, "db_manager"):
                self.stack.enter_context(patch.object(module, "db_manager", self.manager))
        self.stack.enter_context(patch.object(service, "STATE_PATH", self.root / "state.sqlite3"))
        self.stack.enter_context(patch.object(schema_profiler, "SCHEMA_FILE_PATH", str(self.root / "latest.json")))
        self.stack.enter_context(patch.object(schema_profiler, "SCHEMA_CACHE_DIRECTORY", str(self.root / "schema_cache")))
        self.stack.enter_context(patch.object(importlib.import_module("app.semantic_catalog"), "CATALOG_DIRECTORY", str(self.root / "catalogs")))
        self.stack.enter_context(patch.object(SchemaProfiler, "_cached_context", None))
        self.stack.enter_context(patch.object(SchemaProfiler, "_cached_time", 0))
        self.stack.enter_context(patch.object(DashboardGenerator, "_cached_dashboard", None))
        self.stack.enter_context(patch.object(DashboardGenerator, "_cached_key", None))
        self.stack.enter_context(patch.object(DashboardGenerator, "_cached_time", 0))
        self.stack.enter_context(patch.object(RelationshipGuard, "_cache", {}))
        self.stack.enter_context(patch.object(commands.llm_service, "api_key", ""))
        self.stack.enter_context(patch.object(commands.llm_service, "check_health", new=AsyncMock(return_value={"online": False, "model_ready": False})))
        self.stack.enter_context(patch.object(commands.llm_service, "generate_completion", new=AsyncMock(side_effect=AssertionError("No network in isolated tests"))))
        self.manager.set_connection(f"sqlite:///{self.database.as_posix()}")
        self.schema = SchemaProfiler.profile(force_refresh=True)
        app = FastAPI()
        app.include_router(platform_api.router)
        self.client = TestClient(app)

    def tearDown(self):
        if self.manager._engine:
            self.manager._engine.dispose()
        self.stack.close()
        self.temp.cleanup()

    async def executed_analysis(self, prompt, schema, history):
        sql = "SELECT SUM(total) AS total FROM orders WHERE status = 'completed' AND order_date >= '2024-01-01' AND order_date <= '2024-12-31'"
        rows = self.manager.execute_query(sql)
        return NaturalQueryResponse(sql_query=sql, explanation=f"Jami {rows[0]['total']}", columns=["total"],
                                    rows=rows, data_count=len(rows), execution_time_ms=1)

    def run_command(self, prompt, tool="report", filters=None, **action):
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool=tool, prompt=prompt, **action)])
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)):
            return asyncio.run(commands.command(prompt, filters or ExternalFilters(), source="external"))

    def test_external_report_persists_executed_rows_and_exact_source(self):
        prompt = "2024-yil bajarilgan buyurtmalar jami summasi hisobotini tayyorla"
        with patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock(side_effect=self.executed_analysis)):
            result = self.run_command(prompt)
        report = result["artifacts"][0]["data"]
        self.assertEqual(report["kind"], "external")
        self.assertEqual(report["analysis"]["rows"], [{"total": 125}])
        self.assertEqual(report["source"]["connection_fingerprint"], self.manager.connection_fingerprint)
        self.assertEqual(report["source"]["schema_fingerprint"], self.schema.schema_fingerprint)
        self.assertEqual(report["source"]["database_name"], str(self.database).replace("\\", "/"))
        self.assertFalse(report["source"]["synthetic"])
        self.assertEqual(service.get_report(report["id"])["analysis"], report["analysis"])
        self.assertEqual(set(report["exports"]), {"csv", "html", "xlsx", "json"})
        self.assertEqual(result["sources"], [report["source"]])

    def test_external_2024_http_filters_reach_query_without_demo_bounds(self):
        with patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock(side_effect=self.executed_analysis)) as analyst:
            response = self.client.post("/api/platform/command", json={"source": "external",
                "prompt": "Bajarilgan buyurtmalar summasi hisobotini tayyorla", "start_date": "2024-01-01", "end_date": "2024-12-31"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["artifacts"][0]["data"]["filters"]["start_date"], "2024-01-01")
        self.assertIn("boshlanish sanasi 2024-01-01", analyst.call_args.args[0])

    def test_router_cannot_replace_original_question_with_unrequested_grouping(self):
        question = "2024-yilda yakunlangan buyurtmalar jami summasi bo'yicha hisobot tayyorla"
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(
            tool="report", prompt="2024-yilda barcha filiallar kesimida hisobot tayyorla")])
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)), patch.object(
                AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock(side_effect=self.executed_analysis)) as analyst:
            result = asyncio.run(commands.command(question, ExternalFilters(), source="external"))
        self.assertTrue(result["artifacts"])
        self.assertEqual(analyst.call_args.args[0], question)
        self.assertEqual(result["artifacts"][0]["data"]["prompt"], question)

    def test_failed_report_analysis_never_saves_artifact(self):
        failure = NaturalQueryResponse(sql_query="", explanation="Ko'rsatkichni aniqlashtiring", rows=[], columns=[],
                                       success=False, error="clarification_required", data_count=0, execution_time_ms=0)
        with patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock(return_value=failure)):
            result = self.run_command("Foyda hisobotini tayyorla")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(service.artifacts("report"), [])
        self.assertIn("aniqlashtiring", result["answer"])

    def test_conversation_without_executed_sql_is_not_saved_as_report(self):
        response = NaturalQueryResponse(sql_query="", explanation="Hisobot haqida tushuntirish", success=True,
                                        data_count=0, execution_time_ms=0, response_type="text")
        with patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock(return_value=response)):
            result = self.run_command("Hisobot tayyorla")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(service.artifacts("report"), [])

    def test_generic_dashboard_runs_multiple_actual_queries(self):
        result = self.run_command("Ulangan baza bo'yicha umumiy dashboard yarat", tool="dashboard")
        artifact = result["artifacts"][0]
        self.assertEqual(artifact["type"], "dashboard")
        self.assertEqual(artifact["data"]["kind"], "external")
        dashboard = artifact["data"]["dashboard"]
        self.assertGreaterEqual(len(dashboard["kpis"]), 3)
        self.assertGreaterEqual(len(dashboard["charts"]), 1)
        for metric in dashboard["kpis"]:
            actual = self.manager.execute_query(metric["sql_query"])[0]
            self.assertEqual(metric["value"], next(iter(actual.values())))
        self.assertEqual(artifact["data"]["source"]["connection_fingerprint"], self.manager.connection_fingerprint)

    def test_specific_dashboard_keeps_question_and_dates(self):
        prompt = "2024-yil bajarilgan buyurtmalar summasi dashboardini yarat"
        with patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock(side_effect=self.executed_analysis)) as analyst, patch.object(DashboardGenerator, "generate_dashboard", new=AsyncMock()) as general:
            result = self.run_command(prompt, tool="dashboard")
        general.assert_not_called()
        self.assertEqual(analyst.call_args.args[0], prompt)
        self.assertEqual(result["artifacts"][0]["data"]["analysis"]["rows"], [{"total": 125}])

    def test_incomplete_profile_prevents_sql_and_persistence(self):
        incomplete = self.schema.model_copy(update={"profile_complete": False, "profile_warnings": ["Partial catalog"]})
        with patch.object(SchemaProfiler, "profile", return_value=incomplete), patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock()) as analyst:
            result = self.run_command("Hisobot tayyorla")
        analyst.assert_not_called()
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(service.artifacts("report"), [])
        self.assertIn("to'liq", result["answer"])

    def test_source_changed_during_analysis_discards_and_does_not_persist(self):
        async def reconnect(*args):
            result = await self.executed_analysis(*args)
            self.manager.set_connection(f"sqlite:///{self.other.as_posix()}")
            return result
        with patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock(side_effect=reconnect)):
            result = self.run_command("Hisobot tayyorla")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(result["sources"], [])
        self.assertEqual(service.artifacts("report"), [])
        self.assertIn("almashdi", result["answer"])

    def test_source_changed_during_router_cannot_start_query(self):
        async def reconnect(*args):
            self.manager.set_connection(f"sqlite:///{self.other.as_posix()}")
            return commands.CommandPlan(kind="execute", actions=[commands.Action(tool="report")])
        with patch.object(commands, "make_plan", new=AsyncMock(side_effect=reconnect)), patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock()) as analyst:
            result = asyncio.run(commands.command("Hisobot tayyorla", ExternalFilters(), source="external"))
        analyst.assert_not_called()
        self.assertEqual(result["artifacts"], [])
        self.assertIn("almashdi", result["answer"])

    def test_schema_changed_during_analysis_is_rejected(self):
        async def change_schema(*args):
            result = await self.executed_analysis(*args)
            SchemaProfiler._cached_context = self.schema.model_copy(update={"schema_fingerprint": "different-schema"})
            return result
        with patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock(side_effect=change_schema)):
            result = self.run_command("Hisobot tayyorla")
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(service.artifacts("report"), [])
        self.assertIn("tuzilmasi", result["answer"])

    def test_quality_agent_audits_real_schema_without_model_or_demo(self):
        with patch.object(service, "run_agent", side_effect=AssertionError("No dealership checks")):
            result = self.run_command("Ma'lumotlar sifatini tekshir", tool="agent", agent_id="quality")
        data = result["artifacts"][0]["data"]
        self.assertEqual(data["kind"], "external")
        self.assertEqual(data["audit"]["tables_checked"], 4)
        self.assertTrue(data["audit"]["read_only"])
        self.assertEqual({row["table"] for row in data["audit"]["tables"]}, {"customers", "products", "orders", "order_items"})
        self.assertEqual(self.manager.execute_query("SELECT COUNT(*) AS n FROM orders")[0]["n"], 3)

    def test_calculation_does_not_require_connected_source(self):
        with patch.object(self.manager, "_engine", None):
            result = asyncio.run(commands.command("125 * 12", ExternalFilters(), source="external"))
        self.assertEqual(result["artifacts"][0]["type"], "calculation")
        self.assertEqual(result["artifacts"][0]["data"]["value"], 1500)
        self.assertEqual(result["sources"], [])

    def test_external_fallback_does_not_use_demo_period_parser(self):
        with patch.object(commands, "_fallback_filters", side_effect=AssertionError("No dealership filters")):
            plan = asyncio.run(commands.make_plan("2024-yil bajarilgan buyurtmalar summasi", [], ExternalFilters(), "external"))
        self.assertEqual(plan.actions[0].tool, "analytics")
        self.assertIsNone(plan.filters)

    def test_stale_browser_source_is_rejected_before_planner_or_query(self):
        with patch.object(commands, "make_plan", new=AsyncMock()) as planner, patch.object(AIDataAnalystAgent, "execute_analyst_agent", new=AsyncMock()) as analyst:
            with self.assertRaisesRegex(external_workspace.SourceChangedError, "boshqa oynada"):
                asyncio.run(commands.command("Hisobot tayyorla", ExternalFilters(), source="external",
                                             expected_connection_fingerprint="previous-source"))
        planner.assert_not_called()
        analyst.assert_not_called()
        self.assertEqual(service.artifacts("report"), [])


if __name__ == "__main__":
    unittest.main()
