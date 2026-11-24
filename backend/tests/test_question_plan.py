import json
import unittest
from unittest.mock import AsyncMock, patch

from app.question_plan import QuestionPlan, plan_question
from app.agent_analyst import AIDataAnalystAgent
from app.models import SchemaContext
from app.models import TableMeta, TableColumnMeta
from app.schema_retriever import SchemaRetriever
from app.intent_guard import validate_explicit_intent


QUESTION = 'Count orders by region top 3 descending'


def payload():
    return dict(entity=dict(meaning='orders', evidence='orders'),
                measure=dict(meaning='number of orders', evidence='Count'), operation='count',
                dimensions=[dict(meaning='region', evidence='by region')], filters=[], time=[],
                ordering=[dict(meaning='descending', evidence='descending')], limit=3,
                limit_evidence='top 3', ambiguities=[])


class PlanTests(unittest.IsolatedAsyncioTestCase):
    def test_requested_derived_dimension_is_not_monthly_grain(self):
        schema = SchemaContext(database_name='demo', database_type='postgresql', total_tables=1,
            schema_prompt='', tables=[TableMeta(name='facts', columns=[
                TableColumnMeta(name=n, data_type='integer') for n in ['month', 'currency_id', 'amount']])])
        question = 'chorak va currency_id kesimida'
        expression = 'CASE WHEN month <= 3 THEN 1 ELSE 2 END'
        sql = f'SELECT {expression} AS quarter,currency_id,SUM(amount) FROM facts GROUP BY {expression},currency_id'
        self.assertTrue(validate_explicit_intent(question, sql, schema)[0])
        self.assertFalse(validate_explicit_intent(question, sql + ',month', schema)[0])

    def test_explicit_underscore_table_outranks_semantic_aliases(self):
        target = TableMeta(name='sales_events', columns=[TableColumnMeta(name='amount', data_type='numeric')])
        distractor = TableMeta(name='stock_totals', semantic_aliases=['miqdor', 'jami'], certified_source=True)
        schema = SchemaContext(database_name='other', database_type='sqlite', total_tables=2,
                               schema_prompt='', tables=[target, distractor])
        matches = SchemaRetriever.rank('sales_events jadvalida jami yozuvlar soni, miqdor emas', schema)
        self.assertEqual('sales_events', matches[0].table.name)
        self.assertFalse(SchemaRetriever.ambiguous(matches))

    def test_qualified_column_is_not_an_explicit_source_table(self):
        schema = SchemaContext(database_name='other', database_type='sqlite', total_tables=2,
            schema_prompt='', tables=[TableMeta(name='sales_actual'), TableMeta(name='revenue')])
        matches = SchemaRetriever.rank('sales_actual.revenue yigindisi', schema)
        self.assertEqual('sales_actual', matches[0].table.name)
        for match in matches:
            if match.table.name == 'revenue':
                self.assertNotIn('explicit identifier match', match.evidence)

    def test_evidence_presentation_normalization(self):
        plan = QuestionPlan(**payload())
        plan.entity.evidence = 'ORDERS'
        plan.dimensions[0].evidence = 'by   region'
        plan.check_evidence(QUESTION)

    async def test_bad_limit_retried_without_weakening_guard(self):
        bad = payload()
        bad['limit'] = 500
        bad['limit_evidence'] = None
        mock = AsyncMock(side_effect=[json.dumps(bad), json.dumps(payload())])
        with patch('app.question_plan.llm_service.generate_completion', new=mock):
            result = await plan_question(QUESTION)
        self.assertEqual(3, result.limit)
        self.assertEqual(2, mock.await_count)

    async def test_genuine_ambiguity_is_preserved_after_review(self):
        data = payload()
        data['ambiguities'] = ['Which definition of active is intended?']
        mock = AsyncMock(return_value=json.dumps(data))
        with patch('app.question_plan.llm_service.generate_completion', new=mock):
            result = await plan_question(QUESTION)
        self.assertEqual(data['ambiguities'], result.ambiguities)
        self.assertEqual(2, mock.await_count)

    async def test_planner_sees_scoped_schema_hints_but_evidence_remains_from_question(self):
        hints = [{"table": "asset_entries", "columns": ["asset_type", "net_amount"]}]
        imagined = payload()
        imagined["measure"]["evidence"] = "net_amount"
        model = AsyncMock(side_effect=[json.dumps(imagined), json.dumps(payload())])
        with patch('app.question_plan.llm_service.generate_completion', new=model):
            plan = await plan_question(QUESTION, hints)
        self.assertEqual([], plan.ambiguities)
        self.assertEqual(2, model.await_count)
        packet = json.loads(model.await_args_list[0].args[1])
        self.assertEqual({"question": QUESTION, "schema_hints": hints}, packet)

    def test_reviewed_label_includes_related_tables(self):
        from app.models import TableRelationship
        table = TableMeta(name='records', columns=[TableColumnMeta(name='area_id', data_type='integer')])
        dimension = TableMeta(name='areas', default_label_column='display_text',
                              columns=[TableColumnMeta(name='id', data_type='integer')])
        schema = SchemaContext(database_name='other', database_type='sqlite', total_tables=2,
            schema_prompt='', tables=[table, dimension], relationships=[TableRelationship(
                from_table='records', from_column='area_id', to_table='areas', to_column='id',
                source='foreign_key', verified=True, join_hint='records.area_id=areas.id')])
        prompt = AIDataAnalystAgent._build_focused_schema_prompt(table, schema)
        self.assertIn('areas.display_text', prompt)

    def test_duplicate_threshold_cannot_be_omitted_or_hidden_under_or(self):
        data = payload()
        data.update(limit=None, limit_evidence=None, ordering=[], dimensions=[dict(meaning='name', evidence='nom')])
        question = QUESTION + ' bir xil nom ikki yoki undan ko‘p takrorlangan'
        plan = QuestionPlan(**data)
        plan.check_evidence(question)
        base = 'SELECT name,COUNT(*) n FROM arbitrary_table GROUP BY name'
        self.assertFalse(plan.validate_sql(base, 'postgresql')[0])
        self.assertFalse(plan.validate_sql(base + ' HAVING COUNT(*)>=2 OR 1=1', 'postgresql')[0])
        self.assertTrue(plan.validate_sql(base + ' HAVING COUNT(*)>=2', 'postgresql')[0])
        self.assertTrue(plan.validate_sql(base + ' HAVING COUNT(*)>1', 'sqlite')[0])

    async def test_grounded_plan(self):
        with patch('app.question_plan.llm_service.generate_completion', new=AsyncMock(return_value=json.dumps(payload()))):
            plan = await plan_question(QUESTION)
        self.assertEqual(3, plan.limit)

    async def test_invented_evidence_rejected(self):
        data = payload()
        data['time'] = [dict(meaning='2025', evidence='2025')]
        with patch('app.question_plan.llm_service.generate_completion', new=AsyncMock(return_value=json.dumps(data))):
            with self.assertRaises(ValueError):
                await plan_question(QUESTION)

    def test_limit_must_match_evidence(self):
        data = payload()
        data['limit'] = 30
        with self.assertRaises(ValueError):
            QuestionPlan(**data).check_evidence(QUESTION)

    def test_uzbek_spelled_out_top_count(self):
        plan = QuestionPlan(**{**payload(), "limit_evidence": "uchta"})
        plan.check_evidence(QUESTION.replace("top 3", "uchta"))
        with self.assertRaises(ValueError):
            plan.check_evidence(QUESTION.replace("top 3", "yigirma uchta"))

    def test_rank_uses_aggregate_and_requested_direction(self):
        plan = QuestionPlan(**payload())
        plan.check_evidence(QUESTION)
        base = "SELECT region AS label, COUNT(*) n FROM orders GROUP BY region ORDER BY "
        for key in ['n DESC', '2 DESC', 'COUNT(*) DESC, region', 'COALESCE(n,0) DESC']:
            self.assertTrue(plan.validate_sql(base + key + ' LIMIT 3', 'sqlite')[0], key)
        for key in ['label DESC', '1 DESC', 'region DESC, n DESC', 'n ASC']:
            self.assertFalse(plan.validate_sql(base + key + ' LIMIT 3', 'sqlite')[0], key)

    def test_named_month_constrains_the_aggregate(self):
        plan = QuestionPlan(**payload())
        plan.check_evidence(QUESTION + ' 2026-yil oktabrda')
        query = 'SELECT region, COUNT(*) AS n FROM orders WHERE {} GROUP BY region ORDER BY n DESC LIMIT 3'
        for predicate in ["ordered >= '2026-10-01' AND ordered < '2026-11-01'",
                          "date(ordered) BETWEEN '2026-10-01' AND '2026-10-31'",
                          "strftime('%Y-%m', ordered) = '2026-10'",
                          "strftime('%Y', ordered) = '2026' AND strftime('%m', ordered) = '10'"]:
            self.assertTrue(plan.validate_sql(query.format(predicate), 'sqlite')[0], predicate)
        for predicate in ["ordered >= '2026-10-01'", "ordered LIKE '2026-09%'",
                          "ordered >= '2026-10-01' AND paid < '2026-11-01'",
                          "ordered LIKE '2026-10%' OR 1=1"]:
            self.assertFalse(plan.validate_sql(query.format(predicate), 'sqlite')[0], predicate)
        base = "WITH monthly AS (SELECT * FROM records WHERE ordered LIKE '2026-10%') "
        self.assertTrue(plan.validate_sql(base + query.replace('orders WHERE {}', 'monthly'), 'sqlite')[0])
        self.assertFalse(plan.validate_sql(base + query.format('1=1'), 'sqlite')[0])

    def test_explicit_status_filter_cannot_be_dropped(self):
        plan = QuestionPlan(**{**payload(), 'filters': [dict(meaning='completed', evidence="status='completed'")]})
        plan.check_evidence(QUESTION + " status='completed'")
        query = 'SELECT region, COUNT(*) n FROM orders WHERE {} GROUP BY region ORDER BY n DESC LIMIT 3'
        self.assertTrue(plan.validate_sql(query.format("orders.status='completed'"), 'sqlite')[0])
        for predicate in ["orders.status='pending'", "notes='completed'", "status='completed' OR 1=1"]:
            self.assertFalse(plan.validate_sql(query.format(predicate), 'sqlite')[0], predicate)

    def test_nested_group_does_not_justify_outer_scalar_total(self):
        plan = QuestionPlan(**payload())
        sql = ('WITH groups AS (SELECT region, COUNT(*) n FROM orders GROUP BY region) '
               'SELECT COUNT(*) n FROM groups ORDER BY n DESC LIMIT 3')
        self.assertFalse(plan.validate_sql(sql, 'postgresql')[0])

    def test_scalar_count_rejects_grouped_empty_result(self):
        data = payload()
        data.update(dimensions=[], ordering=[], limit=None, limit_evidence=None)
        plan = QuestionPlan(**data)
        self.assertFalse(plan.validate_sql('SELECT COUNT(*) FROM records WHERE 1=0 GROUP BY id', 'sqlite')[0])
        self.assertTrue(plan.validate_sql('SELECT COUNT(*) FROM records WHERE 1=0', 'sqlite')[0])

    def test_null_count_is_not_non_null_count(self):
        data = payload()
        data.update(dimensions=[], ordering=[], limit=None, limit_evidence=None)
        plan = QuestionPlan(**data)
        plan.check_evidence(QUESTION + " amount NULL bo'lgan yozuvlar sonini bitta qatorda")
        self.assertFalse(plan.validate_sql('SELECT COUNT(*),COUNT(amount) FROM records', 'postgresql')[0])
        for expression in ['COUNT(*) FILTER(WHERE r.amount IS NULL)', 'COUNT(*)-COUNT(amount)',
                           'SUM(CASE WHEN amount IS NULL THEN 1 ELSE 0 END)']:
            self.assertTrue(plan.validate_sql('SELECT COUNT(*),' + expression + ' FROM records r', 'postgresql')[0])

    def test_different_schemas_and_dialects(self):
        plan = QuestionPlan(**payload())
        for dialect, table, dimension in [('sqlite', 'purchases', 'area'), ('postgresql', 'orders', 'region')]:
            sql = f'SELECT {dimension},COUNT(*) n FROM {table} GROUP BY {dimension} ORDER BY n DESC LIMIT 3'
            self.assertTrue(plan.validate_sql(sql, dialect)[0])
            self.assertFalse(plan.validate_sql(f'SELECT COUNT(*) FROM {table}', dialect)[0])
            self.assertFalse(plan.validate_sql(sql.replace('COUNT(*)', 'SUM(amount)'), dialect)[0])
            self.assertFalse(plan.validate_sql(sql.replace('LIMIT 3', 'LIMIT 4'), dialect)[0])

    async def test_ambiguity_stops_before_retrieval_and_database(self):
        data = payload()
        data['ambiguities'] = ['Qaysi davr kerak?']
        schema = SchemaContext(database_name='demo', database_type='sqlite', total_tables=0, schema_prompt='', tables=[])
        with (
            patch('app.agent_analyst.plan_question', new=AsyncMock(return_value=QuestionPlan(**data))),
            patch.object(AIDataAnalystAgent, 'discover_candidate_tables') as discover,
            patch('app.agent_analyst.db_manager.execute_query') as execute,
        ):
            result = await AIDataAnalystAgent.execute_analyst_agent(QUESTION, schema)
        self.assertEqual('clarification_required', result.error)
        discover.assert_not_called()
        execute.assert_not_called()

    async def test_malformed_plan_never_executes(self):
        schema = SchemaContext(database_name='demo', database_type='sqlite', total_tables=0, schema_prompt='', tables=[])
        with (
            patch('app.question_plan.llm_service.generate_completion', new=AsyncMock(return_value='{}')),
            patch('app.agent_analyst.db_manager.execute_query') as execute,
        ):
            result = await AIDataAnalystAgent.execute_analyst_agent(QUESTION, schema)
        self.assertEqual('question_plan_invalid', result.error)
        execute.assert_not_called()
