import unittest
import json
import httpx
from unittest.mock import patch, AsyncMock, MagicMock
from app.agent_analyst import AIDataAnalystAgent
from app.llm_service import VLLMService, LLMServiceError
from app.intent_guard import validate_explicit_intent, normalize_explicit_null_order, requested_group_columns
from app.models import SchemaContext, TableMeta, TableColumnMeta
from app.models import SemanticFilterDefinition
from app.models import SemanticMetricDefinition
from app.models import TableRelationship
from app.semantic_catalog import SemanticCatalogManager
import sqlglot
from sqlglot import exp


class ExplicitIntentTests(unittest.TestCase):
    def setUp(self):
        self.schema = SchemaContext(database_name='other_database', database_type='postgresql', total_tables=1, schema_prompt='', tables=[
            TableMeta(name='events', columns=[
                TableColumnMeta(name=name, data_type='text')
                for name in ('currency_id', 'status', 'date_created', 'name')]),
        ])

    def check(self, prompt, sql):
        return validate_explicit_intent(prompt, sql, self.schema)[0]

    def test_year_in_projection_does_not_satisfy_filter(self):
        self.assertFalse(self.check('2025-yil jami', 'SELECT 2025, COUNT(*) FROM events'))
        self.assertTrue(self.check('2025-yil jami', 'SELECT COUNT(*) FROM events WHERE year=2025'))

    def test_date_boundaries_required(self):
        self.assertFalse(self.check("2024-01-01 dan 2025-01-01 gacha", "SELECT SUM(amount) FROM events WHERE date_created >= '2024-01-01'"))

    def test_measure_fallback_is_not_grouping(self):
        self.schema.tables[0].columns.append(TableColumnMeta(name='quantity', data_type='numeric'))
        self.assertEqual(set(), requested_group_columns("real_quantity NULL bo'lsa quantity bo'yicha hisobla", self.schema))

    def test_inner_limit_cannot_satisfy_outer_top(self):
        self.assertFalse(self.check('top 4', 'SELECT * FROM (SELECT * FROM events LIMIT 4) t ORDER BY name'))
        self.assertTrue(self.check('top 4', 'SELECT name FROM events ORDER BY name LIMIT 4'))

    def test_dimension_must_be_grouped_and_returned(self):
        question = 'har bir currency_id uchun revenue yigindisi'
        self.assertFalse(self.check(question, 'SELECT SUM(revenue) FROM events'))
        self.assertFalse(self.check(question, 'SELECT SUM(revenue) FROM events GROUP BY currency_id'))
        self.assertTrue(self.check(question, 'SELECT currency_id,SUM(revenue) FROM events GROUP BY currency_id'))

    def test_explicit_null_filter(self):
        self.assertFalse(self.check('events.name IS NOT NULL', 'SELECT COUNT(*) FROM events'))
        self.assertTrue(self.check('events.name IS NOT NULL', 'SELECT COUNT(*) FROM events WHERE name IS NOT NULL'))

    def test_latest_date_is_not_a_requested_grouping(self):
        question = "date_created bo'yicha shu jadvaldagi eng oxirgi umumiy sanani ol"
        self.assertTrue(self.check(question, 'SELECT * FROM events WHERE date_created=(SELECT MAX(date_created) FROM events)'))
        self.assertFalse(self.check(question, "SELECT * FROM events WHERE date_created=(SELECT MAX(date_created) FROM events WHERE name='x')"))

    def test_snapshot_scope_failure_even_if_numbers_happen_to_match(self):
        self.assertFalse(self.check("eng so'nggi umumiy snapshot", 'WITH d AS (SELECT MAX(date_created) FROM events GROUP BY name) SELECT * FROM d'))

    def filter_ok(self, condition, operator, value=None):
        return SemanticCatalogManager._required_filter_present(
            sqlglot.parse_one('SELECT * FROM events WHERE ' + condition).find(exp.Where),
            SemanticFilterDefinition(column='status', operator=operator, value=value))

    def test_reviewed_nullable_boolean_filter(self):
        self.assertTrue(self.filter_ok('COALESCE(e.status,FALSE)=FALSE', 'not_true'))
        self.assertTrue(self.filter_ok('status IS NOT TRUE', 'not_true'))
        self.assertFalse(self.filter_ok('status=FALSE', 'not_true'))
        self.assertFalse(self.filter_ok('COALESCE(status,FALSE)=FALSE OR 1=1', 'not_true'))

    def test_reviewed_string_exclusion(self):
        self.assertTrue(self.filter_ok("COALESCE(e.status,'') NOT ILIKE '%test%' AND COALESCE(e.status,'') NOT ILIKE '%old%'", 'not_ilike_or_null', ['%test%', '%old%']))
        self.assertFalse(self.filter_ok("COALESCE(status,'') NOT ILIKE '%test%'", 'not_ilike_or_null', ['%test%', '%old%']))
        self.assertTrue(self.filter_ok("status ILIKE '%paid%'", 'ilike', '%paid%'))

    def test_required_filter_under_or_is_not_enforced(self):
        self.assertFalse(self.filter_ok("status='PAID' OR name='someone'", 'eq', 'PAID'))
        self.assertTrue(self.filter_ok("status='PAID' AND name='someone'", 'eq', 'PAID'))
        self.assertTrue(self.filter_ok("status='PAID'", 'is_not_null'))
        self.assertFalse(self.filter_ok("status='PAID' OR status IS NULL", 'is_not_null'))

    def test_explicit_order_direction(self):
        self.assertFalse(self.check("soni kamayish, nomi o'sish", 'SELECT name,COUNT(*) n FROM events GROUP BY name ORDER BY n ASC,name DESC'))
        self.assertTrue(self.check("soni kamayish, nomi o'sish", 'SELECT name,COUNT(*) n FROM events GROUP BY name ORDER BY n DESC,name ASC'))

    def test_requested_null_order_survives_rendering(self):
        sql = 'SELECT status FROM events ORDER BY status ASC NULLS FIRST'
        question = 'status oshish tartibida NULL oxirida'
        self.assertFalse(self.check(question, sql))
        normalized = normalize_explicit_null_order(question, sql)
        self.assertTrue(self.check(question, normalized))
        self.assertFalse(self.check(question, "SELECT status FROM events ORDER BY COALESCE(status,'NULL') ASC"))

    def test_missing_fact_periods_are_not_invented(self):
        self.schema.tables[0].business_role = 'fact'
        sql = 'SELECT c.month,COALESCE(SUM(e.revenue),0) FROM calendar c LEFT JOIN events e ON c.id=e.month_id GROUP BY c.month'
        self.assertFalse(self.check('amaldagi tushum oylar kesimida', sql))
        self.assertTrue(self.check('barcha oy uchun, mavjud emaslari nol bilan', sql))

    def test_extra_grouping_changes_result_grain(self):
        self.assertFalse(self.check('currency_id kesimida', 'SELECT currency_id,name,SUM(amount) FROM events GROUP BY currency_id,name'))
        self.assertFalse(self.check('currency_id kesimida', 'WITH a AS (SELECT currency_id,name,SUM(amount) FROM events GROUP BY currency_id,name) SELECT * FROM a'))
        self.assertTrue(self.check('currency_id kesimida', 'SELECT currency_id,year,SUM(amount) FROM events WHERE year=2025 GROUP BY currency_id,year'))

    def test_declared_foreign_key_allows_dependent_labels(self):
        self.schema.tables.append(TableMeta(name='currencies', primary_key=['id'], columns=[
            TableColumnMeta(name='id', data_type='integer', is_primary_key=True),
            TableColumnMeta(name='code', data_type='text')]))
        relation = TableRelationship(from_table='events', from_column='currency_id', to_table='currencies',
                                     to_column='id', source='foreign_key', verified=True, join_hint='')
        self.schema.relationships = [relation]
        sql = 'SELECT e.currency_id,c.code,SUM(amount) FROM events e LEFT JOIN currencies c ON e.currency_id=c.id GROUP BY e.currency_id,c.code'
        self.assertTrue(self.check('currency_id kesimida', sql))
        relation.verified = False
        self.assertFalse(self.check('currency_id kesimida', sql))


class CertifiedRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_protocol_disconnect_retries_once(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': 'SELECT 1'}}]}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.side_effect = [httpx.RemoteProtocolError('closed'), response]
        with patch('app.llm_service.httpx.AsyncClient', return_value=client):
            self.assertEqual('SELECT 1', await VLLMService().generate_completion('system', 'question'))
        self.assertEqual(2, client.post.await_count)
    async def test_truncated_output_is_not_sql(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'choices': [{'finish_reason': 'length', 'message': {'content': 'SELECT'}}]}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        with patch('app.llm_service.httpx.AsyncClient', return_value=client):
            with self.assertRaisesRegex(LLMServiceError, 'truncated'):
                await VLLMService().generate_completion('system', 'question')

    async def test_reasoning_is_not_final_answer(self):
        response = MagicMock(status_code=200)
        response.json.return_value = {'choices': [{'finish_reason': 'stop', 'message': {'content': '', 'reasoning': 'SELECT 123'}}]}
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.post.return_value = response
        with patch('app.llm_service.httpx.AsyncClient', return_value=client):
            result = await VLLMService().generate_completion('system', 'question')
        self.assertEqual('', result)

    async def test_modified_metric_cannot_execute_unmodified_template(self):
        from tests.plan_fixtures import grounded_plan
        table = TableMeta(name='events', columns=[TableColumnMeta(name='year', data_type='integer')])
        metric = SemanticMetricDefinition(id='event_count', name='Jami events soni', table='events', aggregation='count', certified=True)
        schema = SchemaContext(database_name='unrelated', database_type='sqlite', total_tables=1,
                               schema_prompt='', tables=[table], semantic_metrics=[metric])
        completion = AsyncMock(return_value=json.dumps({'sql_query': 'SELECT COUNT(*) n FROM events WHERE year=2025'}))
        execution = AsyncMock(return_value=(True, [{'n': 2}], 'SELECT COUNT(*) n FROM events WHERE year=2025', None))
        with (
            patch('app.agent_analyst.plan_question', side_effect=grounded_plan),
            patch('app.agent_analyst.SemanticCatalogManager.compile_matching_metric_sql', return_value=(metric, 'SELECT COUNT(*) n FROM events')),
            patch('app.agent_analyst.db_manager.execute_query') as direct_execute,
            patch('app.agent_analyst.SchemaProfiler.enrich_safe_enum_samples'),
            patch('app.agent_analyst.SchemaRetriever.ambiguous', return_value=False),
            patch('app.agent_analyst.llm_service.generate_completion', new=completion),
            patch('app.agent_analyst.SelfHealingQueryEngine.execute_with_self_healing', new=execution),
        ):
            result = await AIDataAnalystAgent.execute_analyst_agent('2025-yil Jami events soni', schema)
        self.assertTrue(result.success)
        direct_execute.assert_not_called()
        completion.assert_awaited_once()
        self.assertIn('CERTIFIED BASE FORMULA', completion.call_args.args[1])
        guard = execution.call_args.kwargs['semantic_validator']
        self.assertFalse(guard('SELECT COUNT(*) n FROM events')[0])
        self.assertTrue(guard('SELECT COUNT(*) n FROM events WHERE year=2025')[0])


if __name__ == '__main__':
    unittest.main()
