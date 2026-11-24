import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app import assistant_commands as commands, platform_api, platform_service as service


class CalculatorTests(unittest.TestCase):
    def test_uzbek_year_suffixes_keep_requested_report_period(self):
        for prompt in ["2025-yilda hisobot", "2025 yildagi jami sotuv", "2025-yilgi hisobot", "2025 yil sotuvlari"]:
            with self.subTest(prompt=prompt):
                self.assertEqual(commands.explicit_period(prompt), {"start_date": "2025-01-01", "end_date": "2025-12-31"})

    def test_decimal_arithmetic_and_bounds(self):
        self.assertEqual(commands.calculate("1250000*12+500000")["value"], 15500000)
        self.assertEqual(commands.calculate("0.1 + 0.2")["formatted"], "0.3")
        self.assertEqual(commands.calculate("(125 + 75) * 12 / 100")["value"], 24)
        for expression in ["__import__('os').system('whoami')", "[1]*10000", "True+1", "10**1000000", "1/0", "1e999", "(1).__class__", "2**2**12", "0**-1"]:
            with self.subTest(expression=expression), self.assertRaises(ValueError):
                commands.calculate(expression)


class CommandRouteTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.state = patch.object(service, "STATE_PATH", Path(self.folder.name) / "state.db")
        self.state.start()
        app = FastAPI()
        app.include_router(platform_api.router)
        self.client = TestClient(app)
        self.key = patch.object(commands.llm_service, "api_key", "")
        self.key.start()

    def tearDown(self):
        self.key.stop()
        self.state.stop()
        self.folder.cleanup()

    def post(self, prompt, **kwargs):
        return self.client.post("/api/platform/command", json={"prompt": prompt, **kwargs})

    def test_demo_dashboard_and_report_use_requested_month_and_persist(self):
        response = self.post("2026-yil oktabr dashboard va hisobot yarat")
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual(result["filters"]["start_date"], "2026-10-01")
        self.assertEqual(result["filters"]["end_date"], "2026-10-31")
        self.assertEqual([item["type"] for item in result["artifacts"]], ["dashboard", "report"])
        self.assertTrue(all(step["status"] == "completed" for step in result["steps"]))
        overview, report = [item["data"] for item in result["artifacts"]]
        revenue = next(item["value"] for item in overview["kpis"] if item["key"] == "revenue")
        self.assertEqual(report["summary"]["revenue"], revenue)
        self.assertEqual(service.get_report(report["id"])["filters"], result["filters"])
        self.assertEqual(self.client.get(report["exports"]["csv"]).status_code, 200)

    def test_agent_is_really_executed(self):
        result = self.post("Omborni tekshir va muammolarni top").json()
        run = result["artifacts"][0]["data"]
        self.assertEqual(run["agent_id"], "inventory")
        self.assertTrue(run["findings"])
        self.assertEqual(service.agents()["runs"][0]["id"], run["id"])

    def test_calculation_and_missing_integration_never_claim_database_execution(self):
        result = self.post("1250000*12+500000 ni hisobla").json()
        self.assertEqual(result["artifacts"][0]["data"]["value"], 15500000)
        self.assertEqual(result["sources"], [])
        denied = self.post("Barcha sotuvlarni o'chir").json()
        self.assertEqual(denied["artifacts"], [])
        self.assertEqual(denied["steps"], [])
        self.assertEqual(denied["response_type"], "clarification")

    def test_invalid_dates_and_blank_prompt(self):
        self.assertEqual(self.post("2024-yil dashboard yarat").status_code, 400)
        self.assertEqual(self.post("dashboard", start_date="2026-10-31", end_date="2026-10-01").status_code, 400)
        self.assertEqual(self.post("   ").status_code, 400)

    def test_external_never_substitutes_demo_or_persists_demo_artifacts(self):
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool="report"), commands.Action(tool="agent")])
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)), patch.object(service, "create_report") as report, patch.object(service, "run_agent") as agent:
            result = self.post("Hisobot va tekshiruv", source="external").json()
        report.assert_not_called()
        agent.assert_not_called()
        self.assertEqual(result["artifacts"], [])
        self.assertEqual(result["sources"], [])
        self.assertTrue(all(step["status"] == "unavailable" for step in result["steps"]))

    def test_external_analytics_returns_real_result_shape(self):
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool="analytics", prompt="orders summasi")])
        data = {"success": True, "explanation": "Jami 425000", "columns": ["total"], "rows": [{"total": 425000}]}
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)), patch.object(commands, "external_analysis", new=AsyncMock(return_value=data)):
            result = self.post("orders summasi", source="external").json()
        self.assertEqual(result["artifacts"][0]["data"], data)
        self.assertFalse(result["sources"][0]["synthetic"])

    def test_model_plan_is_bounded_and_calculator_rejects_code(self):
        with self.assertRaises(ValueError):
            commands.CommandPlan(kind="execute", actions=[commands.Action(tool="dashboard")] * 4)
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool="calculation", expression="__import__('os')")])
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)):
            result = self.post("hisobla").json()
        self.assertEqual(result["steps"][0]["status"], "failed")
        self.assertEqual(result["artifacts"], [])

    def test_live_router_conversation_and_explicit_filters(self):
        plan = {"kind": "execute", "actions": [{"tool": "dashboard"}], "filters": {"start_date": "2025-01-01", "end_date": "2025-12-31", "category": "truck"}}
        with patch.object(commands.llm_service, "api_key", "test-key"), patch.object(commands.llm_service, "generate_completion", new=AsyncMock(return_value=json.dumps(plan))) as generate:
            result = self.post("2025-yil Toshkent yuk mashinalari dashboardini yarat").json()
        self.assertEqual(result["filters"]["start_date"], "2025-01-01")
        self.assertEqual(result["filters"]["category"], "truck")
        self.assertEqual(generate.await_count, 1)
        conversation = {"kind": "conversation", "message": "Yalpi foyda — sotuv summasi minus tannarx.", "actions": []}
        with patch.object(commands.llm_service, "api_key", "test-key"), patch.object(commands.llm_service, "generate_completion", new=AsyncMock(return_value=json.dumps(conversation))):
            result = self.post("Yalpi foyda nima?").json()
        self.assertEqual(result["artifacts"], [])
        self.assertIn("tannarx", result["answer"])

    def test_explicit_tools_do_not_depend_on_model_routing(self):
        with patch.object(commands.llm_service, "api_key", "test-key"), patch.object(commands.llm_service, "generate_completion", new=AsyncMock(side_effect=AssertionError("Router must not guess an explicit tool"))):
            result = self.post("2026-yil oktabr sotuv dashboardini yarat va shu davr hisobotini saqla.").json()
        self.assertEqual([item["type"] for item in result["artifacts"]], ["dashboard", "report"])
        self.assertEqual(result["filters"]["start_date"], "2026-10-01")
        for prompt in ["Cobalt uchun dashboard yarat", "Toshkent uchun dashboard yarat", "Oktabr emas sentabr dashboardini yarat", "2025 va 2026-yil dashboardlarini yarat"]:
            self.assertIsNone(commands.explicit_command_plan(prompt, service.Filters(), [], "demo"), prompt)
        self.assertIsNone(commands.explicit_command_plan("Shu davr hisobotini saqla", service.Filters(), [{"role": "user", "content": "2025-yil"}], "demo"))

    def test_cash_receipts_contract_preserves_original_question(self):
        prompt = "2026-yil oktabr oyida jami pul tushumi qancha?"
        plan = commands.explicit_command_plan(prompt, service.Filters(), [], "demo")
        self.assertEqual(plan.actions[0].tool, "analytics")
        self.assertEqual(plan.actions[0].prompt, prompt)
        result_data = {"answer": "Tushum hisoblandi", "analyses": [{"operation": "payments", "metric": "receipts", "rows": [{"value": 138957100000}]}], "response_type": "analysis"}
        with patch.object(commands.llm_service, "api_key", "test-key"), patch.object(commands.platform_ai, "chat", new=AsyncMock(return_value=result_data)) as analysis, patch.object(commands.llm_service, "generate_completion", new=AsyncMock(side_effect=AssertionError("No redundant router"))):
            result = self.post(prompt).json()
        self.assertEqual(result["artifacts"][0]["type"], "analysis")
        self.assertEqual(analysis.call_args.args[0], prompt)

    def test_invalid_model_plan_gets_one_error_directed_repair(self):
        invalid = {"kind": "execute", "actions": [{"tool": "dashboard", "title": None}]}
        valid = {"kind": "execute", "actions": [{"tool": "dashboard", "title": "Filiallar"}]}
        with patch.object(commands.llm_service, "api_key", "test-key"), patch.object(commands.llm_service, "generate_completion", new=AsyncMock(side_effect=[json.dumps(invalid), json.dumps(valid)])) as model:
            response = self.post("Filiallar bo'yicha interaktiv ko'rinish yarat")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(model.await_count, 2)
        repair = json.loads(model.call_args_list[1].args[1])
        self.assertEqual(repair["VALIDATION_ERRORS"], [{"path": ["actions", 0, "title"], "type": "string_type"}])
        self.assertEqual(response.json()["artifacts"][0]["type"], "dashboard")
        with patch.object(commands.llm_service, "api_key", "test-key"), patch.object(commands.llm_service, "generate_completion", new=AsyncMock(return_value=json.dumps(invalid))) as model:
            response = self.post("Filiallar bo'yicha interaktiv ko'rinish yarat")
        self.assertEqual(response.status_code, 502)
        self.assertEqual(model.await_count, 2)

    def test_explicit_month_overrides_incorrect_model_dates_without_losing_other_filters(self):
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool="dashboard"), commands.Action(tool="report")],
            filters=commands.FilterOverrides(start_date="2026-01-01", end_date="2026-11-12", branch_id=2, category="truck"))
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)):
            result = self.post("2026-yil oktabr uchun sotuv dashboardini yarat va hisobotni saqla").json()
        expected = {"start_date": "2026-10-01", "end_date": "2026-10-31", "branch_id": 2, "category": "truck"}
        self.assertEqual(result["filters"], expected)
        self.assertEqual([artifact["data"]["filters"] for artifact in result["artifacts"]], [expected, expected])

    def test_period_contract_does_not_replace_followup_or_mixed_period_scope(self):
        self.assertIsNone(commands.explicit_period("Shu davr uchun dashboard ham yarat"))
        self.assertIsNone(commands.explicit_period("2025 va 2026 yilni solishtir"))
        self.assertIsNone(commands.explicit_period("Oktabr emas, sentabr uchun dashboard yarat"))
        self.assertIsNone(commands.explicit_period("Yanvar va oktabrni taqqosla"))
        self.assertEqual(commands.explicit_period("2025-yil dashboard"), {"start_date": "2025-01-01", "end_date": "2025-12-31"})
        self.assertEqual(commands.explicit_period("2026-09-03 dan 2026-10-20 gacha dashboard"), {"start_date": "2026-09-03", "end_date": "2026-10-20"})
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool="dashboard")],
            filters=commands.FilterOverrides(start_date="2026-09-01", end_date="2026-09-30"))
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)):
            result = self.post("Shu davr uchun dashboard ham yarat").json()
        self.assertEqual(result["filters"]["start_date"], "2026-09-01")

    def test_followup_report_reuses_executed_period_even_when_model_returns_wrong_year(self):
        first_prompt = "2025-yil uchun sotuv dashboardini yarat."
        first = self.post(first_prompt).json()
        history = [{"role": "user", "content": first_prompt}, {"role": "assistant", "content": first["answer"]}]
        wrong_plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool="report")],
            filters=commands.FilterOverrides(start_date="2026-01-01", end_date="2026-11-12", branch_id=2, category="truck"))
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=wrong_plan)):
            result = self.post("Shu davr uchun hisobot ham tayyorla.", history=history).json()
        self.assertEqual(result["filters"], {"start_date": "2025-01-01", "end_date": "2025-12-31", "branch_id": 2, "category": "truck"})
        self.assertEqual(result["artifacts"][0]["data"]["filters"], result["filters"])
        report_id = result["artifacts"][0]["data"]["id"]
        self.assertEqual(service.get_report(report_id)["filters"], result["filters"])

    def test_new_explicit_period_wins_and_standalone_question_does_not_inherit_history(self):
        history = [{"role": "user", "content": "2025-yil sotuv dashboardini yarat"},
                   {"role": "assistant", "content": "Dashboard tayyor. Davr: 2025-01-01 — 2025-12-31."}]
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool="report")],
            filters=commands.FilterOverrides(start_date="2026-01-01", end_date="2026-11-12"))
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)):
            explicit = self.post("2026-yil oktabr hisobotini ham tayyorla", history=history).json()
            standalone = self.post("Umumiy hisobot yarat", history=history).json()
        self.assertEqual(explicit["filters"]["start_date"], "2026-10-01")
        self.assertEqual(standalone["filters"]["start_date"], "2026-01-01")
        self.assertIsNone(commands.followup_period("Umumiy hisobot yarat", history))

    def test_followup_period_validates_history_and_uses_latest_reference(self):
        history = [{"role": "user", "content": "2025-yil sotuvlar"},
                   {"role": "assistant", "content": "Davr: 2025-01-01 — 2025-12-31."},
                   {"role": "user", "content": "2026-yil sentabrni ko'rsat"},
                   {"role": "assistant", "content": "Davr: 2026-09-01 — 2026-09-30. Ombor holati: 2026-11-12."}]
        self.assertEqual(commands.followup_period("O'sha davr uchun hisobot", history), {"start_date": "2026-09-01", "end_date": "2026-09-30"})
        self.assertEqual(commands.followup_period("Shu davr uchun hisobot", history[:1]), {"start_date": "2025-01-01", "end_date": "2025-12-31"})
        for invalid in ["Davr: 2025-12-31 — 2025-01-01.", "Davr: 2025-02-31 — 2025-03-31.", "Davr: 2024-01-01 — 2024-12-31."]:
            self.assertIsNone(commands.followup_period("Shu davr uchun hisobot", [{"role": "assistant", "content": invalid}]))
        self.assertIsNone(commands.followup_period("Shu davr uchun hisobot", [{"role": "assistant", "content": "2025-01-01 kuni yangi mijoz. 2025-12-31 kuni boshqa mijoz."}]))

    def test_failures_are_not_completed_or_secret_leaks(self):
        plan = commands.CommandPlan(kind="execute", actions=[commands.Action(tool="dashboard")])
        with patch.object(commands, "make_plan", new=AsyncMock(return_value=plan)), patch.object(service, "overview", side_effect=RuntimeError("secret-connection")):
            response = self.post("dashboard")
        self.assertEqual(response.json()["steps"][0]["status"], "failed")
        self.assertEqual(response.json()["artifacts"], [])
        self.assertNotIn("secret-connection", response.text)


if __name__ == "__main__":
    unittest.main()
