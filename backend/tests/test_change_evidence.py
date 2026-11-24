import sqlite3
import time
import unittest
from contextlib import closing
from unittest.mock import AsyncMock, patch

from app.agent_analyst import AIDataAnalystAgent as Agent
from app.models import SchemaContext, TableColumnMeta, TableMeta
from tests.plan_fixtures import grounded_plan


QUESTION = "2026-yil sentabrdan oktabrgacha tushum nega kamaydi?"
SQL = """SELECT SUBSTR(paid_on, 1, 7) AS period, SUM(amount) AS metric_value
         FROM receipts GROUP BY period ORDER BY period DESC"""


class ChangeEvidenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = self.enterContext(closing(sqlite3.connect(':memory:')))
        self.db.row_factory = sqlite3.Row
        self.db.execute("CREATE TABLE receipts (paid_on TEXT, amount INTEGER, branch TEXT)")
        self.db.executemany("INSERT INTO receipts VALUES (?,?,?)", [
            ('2026-09-05', 30, 'A'), ('2026-10-05', 40, 'A'), ('2026-10-13', 60, 'B')])

    def result(self, sql=SQL, question=QUESTION):
        rows = [dict(row) for row in self.db.execute(sql)]
        unverified = "Reklama kamaygani uchun sotuv tushgan"
        return Agent._build_response(question, rows, {'title': unverified, 'explanation': unverified},
                                     sql, 'receipts', time.time())

    def test_disproves_decline_from_two_periods_without_inventing_cause(self):
        result = self.result()
        self.assertTrue(result.success)
        self.assertIn('70 ga oshgan', result.explanation)
        self.assertIn('2026-09 da 30', result.explanation)
        self.assertIn('2026-10 da 100', result.explanation)
        self.assertIn('qo‘shimcha dalil', result.explanation)
        self.assertNotIn('Reklama', result.explanation + result.chart.title)

    def test_segment_missing_in_first_period_is_never_filled_with_zero(self):
        result = self.result(SQL.replace('FROM receipts', ', branch AS segment FROM receipts')
                             .replace('GROUP BY period', 'GROUP BY period, branch'))
        self.assertIn('A —', result.explanation)
        self.assertIn('10 ga oshgan', result.explanation)
        self.assertIn('1 ta guruhda', result.explanation)
        self.assertNotIn('60 ga oshgan', result.explanation)

    def test_single_fact_sum_decomposition_reconciles_using_only_non_null_rows(self):
        self.db.execute("INSERT INTO receipts VALUES ('2026-09-18',NULL,'A')")
        schema = SchemaContext(database_name='demo', database_type='sqlite', schema_prompt='',
                               total_tables=1, tables=[TableMeta(name='receipts', business_role='fact',
                               columns=[TableColumnMeta(name='amount', data_type='integer')])])
        enriched, column = Agent._change_observation_count(SQL, schema, add=True)
        self.assertEqual('amount', column)
        self.assertEqual((enriched, column), Agent._change_observation_count(enriched, schema, add=False))
        rows = [dict(row) for row in self.db.execute(enriched)]
        result = Agent._build_response(QUESTION, rows, {}, enriched, 'receipts', time.time(), observation_column=column)
        self.assertEqual([2, 1], [r['observation_count'] for r in result.rows])
        self.assertIn('yozuvlar soni +40.00', result.explanation)
        self.assertIn('o‘rtacha qiymat +30.00', result.explanation)
        self.assertIn('jami +70.00', result.explanation)
        for variant in [SQL.replace('SUM(amount)', 'AVG(amount)'), SQL.replace('amount)', 'DISTINCT amount)'),
                        SQL.replace('GROUP BY period', 'GROUP BY period, branch'),
                        SQL.replace('FROM receipts', 'FROM receipts JOIN (SELECT 1 AS x) t ON t.x=1')]:
            self.assertEqual((variant, None), Agent._change_observation_count(variant, schema, add=True))
        altered = enriched.replace('COUNT(amount)', 'COUNT(*)')
        self.assertEqual((altered, None), Agent._change_observation_count(altered, schema, add=False))

    def test_no_baseline_does_not_claim_change(self):
        result = self.result(SQL.replace('GROUP BY', "WHERE paid_on > '2026-10-01' GROUP BY"))
        self.assertIn('2026-10 uchun hisoblangan qiymat 100', result.explanation)
        self.assertNotIn('ga kamaygan', result.explanation)

    def test_invented_cte_constant_is_not_evidence(self):
        sql = ("WITH fabricated AS (SELECT paid_on, 999 AS amount FROM receipts) " +
               SQL.replace('FROM receipts', 'FROM fabricated'))
        result = self.result(sql)
        self.assertFalse(result.success)
        self.assertEqual('clarification_required', result.error)
        self.assertFalse(result.rows)

    async def test_period_unspecified_gets_clarification_without_sql_generation(self):
        question = "Nima uchun savdo pasaydi?"
        schema = SchemaContext(database_name='test', database_type='sqlite', tables=[],
                               total_tables=0, schema_prompt='')
        with (patch('app.agent_analyst.plan_question', new=grounded_plan),
              patch('app.agent_analyst.llm_service.generate_completion', new_callable=AsyncMock) as model):
            result = await Agent.execute_analyst_agent(question, schema)
        model.assert_not_awaited()
        self.assertEqual('clarification_required', result.error)
        self.assertIn('ikki davr', result.explanation)

    def test_ordinary_grouping_by_recorded_reason_is_unaffected(self):
        question = "Bekor qilish sabablari kesimida buyurtmalar sonini kamayish tartibida ko‘rsat"
        self.assertFalse(Agent._asks_why_metric_changed(question))
