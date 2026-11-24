"""Iterative model-tool protocol exercised against a real isolated SQLite DB."""
import asyncio
import copy
import json
import sqlite3
import sys
import tempfile
import unittest
from contextlib import ExitStack, closing
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import data_agent, relationship_guard
from app.database import DatabaseManager
from app.models import SchemaContext, TableColumnMeta, TableMeta
from app.llm_service import LLMServiceError


def turn(name, arguments, call_id="call_1", reasoning=False):
    output = [{"type": "function_call", "name": name, "arguments": json.dumps(arguments), "call_id": call_id}]
    if reasoning:
        output.insert(0, {"type": "reasoning", "id": "reasoning_1", "summary": [], "encrypted_content": "opaque-provider-state"})
    return {"id": "response_" + call_id, "status": "completed", "output": output}


def query(sql, title="Hisoblash", purpose="Ma'lumotlarni hisoblash", call_id="query_1"):
    return turn("query_data", {"sql": sql, "purpose": purpose, "title": title}, call_id)


def finish(ids=None, charts=None, call_id="finish_1", status="completed", limitations=None, answer="Hisoblangan natijalar tayyor."):
    return turn("finish_analysis", {"status": status, "answer": answer, "result_ids": ids or ["q1"],
                                    "charts": charts or [], "limitations": limitations or []}, call_id)


class DataAgentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "retail.db"
        with closing(sqlite3.connect(self.path)) as connection:
            connection.executescript("""
                CREATE TABLE orders(id INTEGER PRIMARY KEY, order_date TEXT, product TEXT, amount NUMERIC, status TEXT);
                INSERT INTO orders VALUES(1,'2024-01-10','Books',120,'completed');
                INSERT INTO orders VALUES(2,'2024-02-10','Tools',80,'completed');
                INSERT INTO orders VALUES(3,'2023-02-10','Tools',999,'completed');
                INSERT INTO orders VALUES(4,'2024-02-10','Books',500,'cancelled');
                INSERT INTO orders VALUES(5,'2024-03-10','Books',NULL,'pending');
                CREATE TABLE samples(id INTEGER PRIMARY KEY, label TEXT, value NUMERIC);
            """)
            connection.executemany("INSERT INTO samples VALUES(?,?,?)", [(i, f"item-{i}", 0 if i == 1 else i) for i in range(1, 603)])
            connection.commit()
        self.manager = DatabaseManager()
        self.manager.set_connection(f"sqlite:///{self.path.as_posix()}")
        self.stack = ExitStack()
        self.stack.enter_context(patch.object(data_agent, "db_manager", self.manager))
        self.stack.enter_context(patch.object(relationship_guard, "db_manager", self.manager))
        self.stack.enter_context(patch.object(relationship_guard.RelationshipGuard, "_cache", {}))
        self.reviewer = self.stack.enter_context(patch.object(data_agent.llm_service, "generate_completion",
            new=AsyncMock(return_value='{"approved":true,"issues":[]}')))
        self.schema = SchemaContext(database_type="sqlite", database_name="retail", total_tables=2, schema_prompt="",
            connection_fingerprint=self.manager.connection_fingerprint, schema_fingerprint="fixture-schema",
            tables=[TableMeta(name="orders", row_count=5, primary_key=["id"], columns=[
                TableColumnMeta(name="id", data_type="INTEGER", is_primary_key=True),
                TableColumnMeta(name="order_date", data_type="TEXT"),
                TableColumnMeta(name="product", data_type="TEXT"),
                TableColumnMeta(name="amount", data_type="NUMERIC"),
                TableColumnMeta(name="status", data_type="TEXT", sample_values=["completed", "cancelled", "pending"])]),
                    TableMeta(name="samples", row_count=602, primary_key=["id"], columns=[
                TableColumnMeta(name="id", data_type="INTEGER", is_primary_key=True),
                TableColumnMeta(name="label", data_type="TEXT"), TableColumnMeta(name="value", data_type="NUMERIC")])])

    def tearDown(self):
        self.manager._engine.dispose()
        self.stack.close()
        self.temp.cleanup()

    def execute(self, turns, prompt="Ma'lumotlarni tahlil qil", guard=None, *, multiple_charts=False, history=None):
        snapshots = []
        remaining = list(turns)

        async def fake_turn(instructions, input_items, tools, **kwargs):
            snapshots.append(copy.deepcopy(input_items))
            if not remaining:
                raise AssertionError("Unexpected extra model call")
            return remaining.pop(0)

        with patch.object(data_agent.llm_service, "generate_tool_turn", new=AsyncMock(side_effect=fake_turn)):
            result = asyncio.run(data_agent.execute_data_agent(prompt, self.schema, history=history, assert_current=guard,
                                                               multiple_charts=multiple_charts))
        return result, snapshots

    def test_chat_selects_one_chart_and_dashboard_retains_both_recorded_results(self):
        turns = [query("SELECT product, SUM(amount) AS total FROM orders GROUP BY product"),
                 query("SELECT status, SUM(amount) AS total FROM orders GROUP BY status", call_id="status"),
                 finish(["q1", "q2"], [
                     {"query_id": "q1", "title": "Mahsulotlar", "chart_type": "pie", "x_axis": "product", "y_axis": "total"},
                     {"query_id": "q2", "title": "Holatlar", "chart_type": "bar", "x_axis": "status", "y_axis": "total"},
                 ])]
        chat, _ = self.execute(turns)
        self.assertEqual(chat.charts, [chat.chart])
        self.assertEqual((chat.chart.query_id, chat.chart.chart_type), ("q1", "bar"))
        self.assertEqual(len(chat.query_results), 2)
        dashboard, _ = self.execute(turns, multiple_charts=True)
        self.assertEqual([chart.query_id for chart in dashboard.charts], ["q1", "q2"])

    def test_count_report_requires_direct_total_in_addition_to_breakdown(self):
        scope = " FROM orders WHERE status='completed' AND order_date >= '2024-01-01' AND order_date <= '2024-12-31'"
        result, snapshots = self.execute([
            query("SELECT product, COUNT(*) AS count" + scope + " GROUP BY product", call_id="groups"),
            finish(["q1"], call_id="incomplete_count"),
            query("SELECT COUNT(*) AS total" + scope, call_id="total"),
            finish(["q2", "q1"], call_id="complete_count"),
        ], "2024-yilda yakunlangan buyurtmalar soni boyicha Excel hisobot tayyorla")
        self.assertEqual(result.agent_status, "completed")
        self.assertEqual(result.rows, [{"total": 2}])
        self.assertTrue(any(step["tool"] == "finish_analysis" and step["status"] == "failed" for step in result.agent_trace))
        feedback = [item for item in snapshots[2] if item.get("type") == "function_call_output"][-1]
        self.assertIn("overall COUNT", feedback["output"])

    def test_grouped_count_answers_the_requested_grain_without_extra_total_query(self):
        for question in ["Har bir mahsulot uchun nechta buyurtma bor?",
                         "Mahsulotlar bo‘yicha buyurtmalar sonini ko‘rsat",
                         "Order count by product"]:
            with self.subTest(question=question):
                result, _ = self.execute([
                    query("SELECT product, COUNT(*) AS orders FROM orders GROUP BY product ORDER BY product"),
                    finish(answer="Books uchun 3 ta, Tools uchun 2 ta buyurtma qayd etilgan."),
                ], question)
                self.assertEqual(result.agent_status, "completed")
                self.assertEqual(result.rows, [{"product": "Books", "orders": 3}, {"product": "Tools", "orders": 2}])

    def test_review_uses_same_conversation_context_as_agent_for_followup(self):
        history = [{"role": "user", "content": "2024-yilda yakunlangan buyurtmalar soni qancha?"}]
        result, calls = self.execute([
            query("SELECT product, COUNT(*) AS n FROM orders WHERE status='completed' "
                  "AND order_date >= '2024-01-01' AND order_date < '2025-01-01' GROUP BY product"),
            finish(answer="Books va Tools mahsulotlaridan bittadan buyurtma yakunlangan."),
        ], "Endi mahsulotlar kesimida-chi?", history=history)
        self.assertEqual(result.agent_status, "completed")
        review = json.loads(self.reviewer.call_args.args[1])
        self.assertEqual(review["recent_conversation"], calls[0][:-1])
        self.assertEqual(review["recent_conversation"], history)

    def test_completed_count_cannot_drop_observed_status_filter_or_use_or_bypass(self):
        for wrong in ["SELECT COUNT(*) AS total FROM orders",
                      "SELECT COUNT(*) AS total FROM orders WHERE status='completed' OR 1=1"]:
            with self.subTest(wrong=wrong):
                result, _ = self.execute([
                    query(wrong, call_id="unfiltered"),
                    query("SELECT COUNT(*) AS total FROM orders WHERE status='completed'", call_id="filtered"),
                    finish(),
                ], "Yakunlangan buyurtmalar soni")
                self.assertEqual(result.rows, [{"total": 3}])
                self.assertEqual(len(result.query_results), 1)
                self.assertEqual(result.agent_trace[0]["status"], "failed")

    def test_iterates_inspects_and_queries_again_using_actual_results(self):
        sql = "SELECT SUM(amount) AS total FROM orders WHERE status='completed' AND order_date >= '2024-01-01' AND order_date <= '2024-12-31'"
        monthly = "SELECT substr(order_date,1,7) AS month, SUM(amount) AS revenue FROM orders WHERE status='completed' AND order_date >= '2024-01-01' AND order_date <= '2024-12-31' GROUP BY month ORDER BY month"
        result, snapshots = self.execute([
            turn("search_schema", {"query": "orders revenue"}, "search", reasoning=True),
            turn("inspect_tables", {"tables": ["orders"]}, "inspect"),
            query(sql, "Jami sotuv", "2024-yil yakunlangan sotuv jami", "total"),
            query(monthly, "Oylar kesimi", "2024-yil oylar kesimi", "monthly"),
            finish(["q1", "q2"], [{"query_id": "q2", "title": "Oylar", "chart_type": "line", "x_axis": "month", "y_axis": "revenue"}]),
        ], "2024-yil yakunlangan sotuvni jami va oylar bo'yicha tahlil qil")
        self.assertTrue(result.success)
        self.assertEqual(result.agent_status, "completed")
        self.assertEqual(result.rows, [{"total": 200}])
        self.assertEqual([row["revenue"] for row in result.charts[0].data], [120, 80])
        self.assertEqual(len(result.query_results), 2)
        self.assertTrue(all(step["status"] == "completed" for step in result.agent_trace))
        self.assertIn("q1", result.explanation)
        replay = snapshots[1]
        self.assertIn({"type": "reasoning", "id": "reasoning_1", "summary": [], "encrypted_content": "opaque-provider-state"}, replay)
        outputs = [item for item in snapshots[3] if item.get("type") == "function_call_output"]
        self.assertEqual(outputs[-1]["call_id"], "total")
        self.assertEqual(json.loads(outputs[-1]["output"])["result"]["rows"], [{"total": 200}])
        self.assertNotIn("opaque-provider-state", str(result.model_dump()))

    def test_failed_sql_is_feedback_and_next_turn_repairs_it(self):
        result, snapshots = self.execute([
            query("SELECT SUM(nonexistent) AS total FROM orders", call_id="bad"),
            query("SELECT COUNT(*) AS total FROM orders", call_id="good"),
            finish(),
        ])
        self.assertTrue(result.success)
        self.assertEqual(result.rows, [{"total": 5}])
        feedback = json.loads(next(item["output"] for item in snapshots[1] if item.get("call_id") == "bad" and item.get("type") == "function_call_output"))
        self.assertFalse(feedback["success"])
        self.assertIn("nonexistent", feedback["error"])
        self.assertEqual(result.agent_trace[0]["status"], "failed")
        self.assertEqual(len(result.query_results), 1)

    def test_forbidden_write_never_executes(self):
        result, snapshots = self.execute([
            query("DELETE FROM orders", call_id="delete"),
            turn("ask_clarification", {"question": "Qaysi davrni tahlil qilay?"}, "clarify"),
        ])
        self.assertFalse(result.success)
        self.assertEqual(result.agent_status, "clarification")
        self.assertEqual(self.manager.execute_query("SELECT COUNT(*) AS total FROM orders"), [{"total": 5}])
        self.assertEqual(result.query_results, [])
        self.assertIn("prohibited", snapshots[1][-1]["output"])

    def test_unknown_result_reference_and_chart_columns_require_repair(self):
        result, snapshots = self.execute([
            query("SELECT product, SUM(amount) AS total FROM orders GROUP BY product"),
            finish(["q999"], call_id="unknown"),
            finish(charts=[{"query_id": "q1", "title": "Products", "chart_type": "bar", "x_axis": "product", "y_axis": "invented"}], call_id="axis"),
            finish(charts=[{"query_id": "q1", "title": "Products", "chart_type": "bar", "x_axis": "product", "y_axis": "total"}], call_id="fixed"),
        ])
        self.assertTrue(result.success)
        evidence = result.query_results[0]["rows"]
        self.assertEqual(result.charts[0].data, sorted(evidence, key=lambda row: row["total"], reverse=True))
        self.assertEqual(result.rows, evidence)
        self.assertEqual(result.charts[0].query_id, "q1")
        self.assertEqual([step["status"] for step in result.agent_trace], ["completed", "failed", "failed", "completed", "completed"])
        self.assertIn("recorded query IDs", snapshots[2][-1]["output"])
        self.assertIn("absent", snapshots[3][-1]["output"])

    def test_budget_exhaustion_returns_real_partial_evidence(self):
        with patch.object(data_agent, "MAX_ROUNDS", 2):
            result, _ = self.execute([
                query("SELECT COUNT(*) AS total FROM orders"),
                turn("search_schema", {"query": "orders"}, "search"),
            ])
        self.assertTrue(result.success)
        self.assertEqual(result.agent_status, "partial")
        self.assertEqual(result.error, "agent_budget_exhausted")
        self.assertEqual(result.rows, [{"total": 5}])
        self.assertTrue(result.validation_warnings)

    def test_source_changed_callback_propagates_without_querying(self):
        checked = 0

        def guard():
            nonlocal checked
            checked += 1
            if checked >= 3:
                raise ValueError("source changed")

        with patch.object(self.manager, "execute_query", wraps=self.manager.execute_query) as execute:
            with self.assertRaisesRegex(ValueError, "source changed"):
                self.execute([query("SELECT COUNT(*) AS total FROM orders")], guard=guard)
            execute.assert_not_called()

    def test_large_results_keep_500_rows_and_model_preview_is_explicit(self):
        result, snapshots = self.execute([
            query("SELECT id,label,value FROM samples ORDER BY id"), finish(),
        ])
        self.assertTrue(result.success)
        self.assertEqual(result.returned_count, 500)
        self.assertTrue(result.is_truncated)
        self.assertEqual(result.rows[0]["value"], 0)
        feedback = json.loads(snapshots[1][-1]["output"])["result"]
        self.assertEqual(len(feedback["rows"]), data_agent.MAX_MODEL_ROWS)
        self.assertEqual(feedback["returned_count"], 500)
        self.assertTrue(feedback["preview_is_truncated"])
        self.assertTrue(result.validation_warnings)

    def test_null_and_zero_are_preserved(self):
        result, _ = self.execute([query("SELECT amount FROM orders WHERE id=5"), finish()])
        self.assertEqual(result.rows, [{"amount": None}])
        self.assertFalse(result.is_truncated)
        self.assertTrue(result.success)

    def test_finish_cannot_ignore_explicit_user_year(self):
        result, snapshots = self.execute([
            query("SELECT COUNT(*) AS total FROM orders WHERE order_date >= '2023-01-01' AND order_date <= '2023-12-31'", call_id="wrong_year"),
            finish(call_id="rejected"),
            query("SELECT COUNT(*) AS total FROM orders WHERE order_date >= '2024-01-01' AND order_date <= '2024-12-31'", call_id="right_year"),
            finish(["q2"], call_id="fixed"),
        ], "2024-yil yozuvlar soni")
        self.assertEqual(result.rows, [{"total": 4}])
        self.assertIn("2024", snapshots[2][-1]["output"])
        self.assertEqual(result.agent_trace[1]["status"], "failed")

    def test_literal_select_is_not_database_evidence(self):
        with patch.object(data_agent, "MAX_ROUNDS", 1):
            result, _ = self.execute([query("SELECT 58500 AS revenue")])
        self.assertFalse(result.success)
        self.assertEqual(result.query_results, [])
        self.assertEqual(result.agent_status, "failed")

    def test_sql_query_budget_prevents_additional_execution(self):
        with patch.object(data_agent, "MAX_QUERIES", 1):
            result, snapshots = self.execute([
                query("SELECT COUNT(*) AS total FROM orders", call_id="one"),
                query("SELECT COUNT(*) AS total FROM samples", call_id="two"), finish(),
            ])
        self.assertEqual(len(result.query_results), 1)
        self.assertEqual(result.rows, [{"total": 5}])
        self.assertIn("budget exhausted", snapshots[2][-1]["output"])

    def test_profile_from_another_connection_fails_before_model_call(self):
        self.schema.connection_fingerprint = "different-source"
        with patch.object(data_agent.llm_service, "generate_tool_turn", new=AsyncMock()) as model:
            with self.assertRaisesRegex(RuntimeError, "mos emas"):
                asyncio.run(data_agent.execute_data_agent("count", self.schema))
            model.assert_not_called()

    def test_uncertainty_caveat_does_not_mark_completed_work_partial(self):
        caveat = "Bu bog'liqlik tashqi sababni isbotlamaydi."
        result, _ = self.execute([query("SELECT COUNT(*) AS total FROM orders"), finish(limitations=[caveat])])
        self.assertEqual(result.agent_status, "completed")
        self.assertIn(caveat, result.validation_warnings)
        self.assertIn(caveat, result.explanation)
        partial, _ = self.execute([query("SELECT COUNT(*) AS total FROM orders"),
                                  finish(status="partial", limitations=["Mahsulotlar kesimi hali bajarilmadi."])])
        self.assertEqual(partial.agent_status, "partial")

    def test_inclusive_user_end_accepts_equivalent_half_open_sql(self):
        prompt = "2024-01-01 dan 2024-12-31 gacha yakunlangan sotuvni hisobla"
        sql = "SELECT SUM(amount) AS total FROM orders WHERE status='completed' AND order_date >= '2024-01-01' AND order_date < '2025-01-01'"
        result, _ = self.execute([query(sql, purpose=prompt), finish()], prompt)
        self.assertTrue(result.success)
        self.assertEqual(result.agent_status, "completed")
        self.assertEqual(result.rows, [{"total": 200}])

    def test_multistep_half_open_range_preserves_actual_end_semantics(self):
        prompt = "2024-01-01 dan 2024-12-31 gacha yakunlangan sotuvni jami va oylar kesimida ko'rsat"
        scope = "FROM orders WHERE status='completed' AND order_date >= '2024-01-01' AND order_date < '2025-01-01'"
        result, _ = self.execute([
            query("SELECT SUM(amount) AS total " + scope, call_id="total"),
            query("SELECT substr(order_date,1,7) AS month, SUM(amount) AS total " + scope + " GROUP BY month", call_id="month"),
            finish(["q1", "q2"]),
        ], prompt)
        self.assertTrue(result.success)
        self.assertTrue(data_agent.DataAgentRun.has_exclusive_end("SELECT id FROM orders WHERE '2025-01-01' > order_date", "2024-12-31"))
        self.assertFalse(data_agent.DataAgentRun.has_exclusive_end("SELECT id FROM orders WHERE order_date <= '2025-01-01'", "2024-12-31"))
        self.assertFalse(data_agent.DataAgentRun.has_exclusive_end("SELECT id FROM orders WHERE order_date < '2025-01-02'", "2024-12-31"))

    def test_rejected_constancy_claim_leads_to_period_evidence_and_review(self):
        self.reviewer.side_effect = [
            '{"approved":false,"issues":["Whole-period product totals do not prove the assortment was unchanged. Query products by month or remove that claim."]}',
            '{"approved":true,"issues":[]}',
        ]
        scope = "FROM orders WHERE status='completed' AND order_date >= '2024-01-01' AND order_date < '2025-01-01'"
        result, snapshots = self.execute([
            query("SELECT product,SUM(amount) AS total " + scope + " GROUP BY product", call_id="products"),
            finish(answer="Assortiment o'zgarmadi.", call_id="unsupported"),
            query("SELECT substr(order_date,1,7) AS month,product,SUM(amount) AS total " + scope + " GROUP BY month,product", call_id="temporal"),
            finish(["q2", "q1"], answer="Yanvarda Books 120, fevralda Tools 80. Bu tashqi sababni isbotlamaydi.", call_id="supported"),
        ], "2024-yilda mahsulotlar bo'yicha o'zgarishni tekshir")
        self.assertEqual(result.agent_status, "completed")
        self.assertEqual(len(result.query_results), 2)
        self.assertEqual(result.rows[0]["month"], "2024-01")
        self.assertNotIn("Assortiment o'zgarmadi", result.explanation)
        reviews = [step for step in result.agent_trace if step["tool"] == "verify_answer"]
        self.assertEqual([step["status"] for step in reviews], ["failed", "completed"])
        self.assertIn("Whole-period product totals", snapshots[2][-1]["output"])
        first_review = json.loads(self.reviewer.call_args_list[0].args[1])
        second_review = json.loads(self.reviewer.call_args_list[1].args[1])
        self.assertNotIn("month", first_review["selected_query_evidence"][0]["columns"])
        self.assertIn("month", second_review["selected_query_evidence"][1]["columns"])
        self.assertTrue(first_review["host_creates_requested_file_exports"])

    def test_reviewer_requires_direct_total_in_narrative_even_when_query_exists(self):
        self.reviewer.side_effect = [
            '{"approved":false,"issues":["The user requested one total. Grouped product counts are supplementary; query the overall count and answer it directly. Do not offer the already requested Excel file."]}',
            '{"approved":true,"issues":[]}',
        ]
        result, _ = self.execute([
            query("SELECT product,COUNT(*) AS count FROM orders GROUP BY product", call_id="groups"),
            query("SELECT COUNT(*) AS total FROM orders", call_id="directtotal"),
            finish(["q2", "q1"], answer="Mahsulotlar kesimi tayyor. Excel kerak bo'lsa so'rang.", call_id="missingtotal"),
            finish(["q2", "q1"], answer="Jami 5 ta buyurtma. Hisobot tahlilga biriktiriladi.", call_id="directanswer"),
        ], "Barcha mahsulotlar bo'yicha jami buyurtma soni va Excel hisobot")
        self.assertEqual(result.rows, [{"total": 5}])
        self.assertEqual(result.agent_status, "completed")
        self.assertIn("Jami 5", result.explanation)
        self.assertEqual(self.reviewer.await_count, 2)

    def test_unavailable_or_invalid_reviewer_fails_closed_with_actual_evidence(self):
        for response in [LLMServiceError("Unavailable"), '{"approved":"yes","issues":[]}']:
            with self.subTest(response=response):
                self.reviewer.side_effect = response if isinstance(response, Exception) else None
                if isinstance(response, str):
                    self.reviewer.return_value = response
                result, _ = self.execute([
                    query("SELECT COUNT(*) AS total FROM orders"),
                    finish(answer="Noto'g'ri tekshirilmagan xulosa: 999 ta."),
                ])
                self.assertEqual(result.agent_status, "partial")
                self.assertEqual(result.error, "agent_review_unavailable")
                self.assertEqual(result.rows, [{"total": 5}])
                self.assertNotIn("999", result.explanation)
                self.assertEqual(result.agent_trace[-1]["tool"], "verify_answer")
                self.assertEqual(result.agent_trace[-1]["status"], "failed")

    def test_source_change_during_final_review_discards_the_answer(self):
        changed = False

        def guard():
            if changed:
                raise ValueError("source changed during review")

        async def reviewer(*args, **kwargs):
            nonlocal changed
            changed = True
            return '{"approved":true,"issues":[]}'

        self.reviewer.side_effect = reviewer
        with self.assertRaisesRegex(ValueError, "source changed during review"):
            self.execute([query("SELECT COUNT(*) AS total FROM orders"), finish()], guard=guard)

    def test_repeated_rejections_are_bounded_and_cannot_claim_completion(self):
        self.reviewer.return_value = '{"approved":false,"issues":["Unsupported statement; correct it."]}'
        with patch.object(data_agent, "MAX_FINAL_REVIEWS", 2):
            result, _ = self.execute([
                query("SELECT COUNT(*) AS total FROM orders"),
                finish(call_id="draft1"), finish(call_id="draft2"), finish(call_id="draft3"),
            ])
        self.assertEqual(self.reviewer.await_count, 2)
        self.assertEqual(result.agent_status, "partial")
        self.assertEqual(result.rows, [{"total": 5}])
        self.assertEqual(result.error, "agent_review_unavailable")


if __name__ == "__main__":
    unittest.main()
