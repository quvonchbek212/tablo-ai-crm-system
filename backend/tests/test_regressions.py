import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch


BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
os.environ.setdefault("APP_DEBUG", "false")

from app.agent_analyst import AIDataAnalystAgent
from app.database import DatabaseManager
from app.config import Settings
from app.dashboard_generator import DashboardGenerator
from app.models import (
    SchemaContext, TableMeta, TableColumnMeta,
    SemanticCatalog, SemanticTableDefinition, SemanticMetricDefinition,
    SemanticFilterDefinition, SemanticJoinDefinition,
)
from app.models import TableRelationship
from app.relationship_guard import RelationshipGuard
from app.data_quality import DataQualityAuditor
from app.models import DataQualityAuditRequest
from app.models import GoldEvaluationRequest, GoldQueryCase, NaturalQueryResponse
from app.gold_evaluator import GoldQueryEvaluator
from app.llm_service import LLMUnavailableError
from app.schema_profiler import SchemaProfiler
from app.schema_retriever import SchemaRetriever
from app.semantic_catalog import SemanticCatalogManager, SemanticCatalogError
from app.sql_validator import validate_and_sanitize_sql


class LargeSchemaCatalogRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Retrieval must not depend on the last customer database opened locally.
        names = ["organizations", "tax_debts", "investment_projects", "employees",
                 "inventory_balances", "customers", "production_plan", "production_actual",
                 "sales_plan", "sales_actual", "warehouse_income", "warehouse_outcome", "vehicles"]
        tables = [TableMeta(name=name, row_count=100, columns=[
            TableColumnMeta(name="id", data_type="integer", is_primary_key=True),
            TableColumnMeta(name="name", data_type="text"),
            TableColumnMeta(name="amount", data_type="numeric"),
            TableColumnMeta(name="event_date", data_type="date"),
        ]) for name in names]
        tables.extend(TableMeta(name=f"technical_object_{i}", columns=[
            TableColumnMeta(name="opaque_value", data_type="text")]) for i in range(80))
        cls.schema = SchemaContext(database_type="postgresql", database_name="synthetic_routing",
                                   total_tables=len(tables), schema_prompt="", tables=tables)

    def first_candidate(self, question: str) -> str:
        candidates = AIDataAnalystAgent.discover_candidate_tables(question, self.schema, top_k=3)
        self.assertTrue(candidates, question)
        return candidates[0].name

    def test_routing_returns_only_connected_catalog_objects(self):
        known = {table.name for table in self.schema.tables}
        questions = (
            "Bazada jami nechta tashkilot bor?",
            "Soliq qarzi eng ko'p 10 ta tashkilot",
            "Faol investitsiya loyihalari soni",
            "Faol xodimlar soni",
            "Mahsulotlar bo'yicha ombor qoldig'i",
        )
        for question in questions:
            with self.subTest(question=question):
                candidates = AIDataAnalystAgent.discover_candidate_tables(question, self.schema, top_k=4)
                self.assertTrue(candidates)
                self.assertTrue({table.name for table in candidates}.issubset(known))

    def test_follow_up_keeps_previous_metric(self):
        resolved = AIDataAnalystAgent.resolve_analytical_prompt(
            "Endi faqat Samarqand bo'yicha",
            [{"role": "user", "content": "Mahsulotlar bo'yicha ombor qoldig'ini ko'rsat"}],
        )
        self.assertIn("ombor qoldig'ini", resolved)
        self.assertIn("Samarqand", resolved)
        self.assertTrue(AIDataAnalystAgent.discover_candidate_tables(resolved, self.schema, top_k=4))

    def test_clarification_preserves_chained_filters_and_never_uses_assistant_facts(self):
        history = [{"role": "user", "content": "Sotuvni oylar bo'yicha ko'rsat"},
                   {"role": "assistant", "content": "Tayyor."},
                   {"role": "user", "content": "Endi faqat 2026-yil"},
                   {"role": "assistant", "content": "Qaysi shahar bo‘yicha?"}]
        resolve = AIDataAnalystAgent.resolve_analytical_prompt
        result = resolve("Samarqand", history)
        for grounded in ("Sotuvni oylar", "2026-yil", "shahar: Samarqand"):
            self.assertIn(grounded, result)
        for independent in ("Salom", "Samarqanddagi mijozlar soni", "Yalpi marja nima degani?", "Rahmat"):
            self.assertEqual(resolve(independent, history), independent)
        history[-1]["content"] = "Jami 600 dona."
        self.assertEqual(resolve("Samarqand", history), "Samarqand")
        history[-1].update(content="Ombor yoki jadval nomini kiriting. Namuna 600.", clarification=True)
        self.assertNotIn("600", resolve("warehouse_stocks", history))

    def test_standalone_location_question_is_not_contaminated_by_history(self):
        current = "Samarqand viloyatidagi mijozlar soni"
        resolved = AIDataAnalystAgent.resolve_analytical_prompt(
            current,
            [{"role": "user", "content": "Ombor qoldig'ini ko'rsat"}],
        )
        self.assertEqual(current, resolved)

    def test_follow_up_replaces_single_year_without_requesting_both_periods(self):
        resolved = AIDataAnalystAgent.resolve_analytical_prompt(
            "2025-yilda-chi?", [{"role": "user", "content": "2024-yilda jami nechta buyurtma bor?"}])
        self.assertEqual(resolved, "2025-yilda jami nechta buyurtma bor?")

    def test_plan_actual_and_flow_questions_retrieve_both_sources(self):
        cases = (
            "Korxonalar kesimida ishlab chiqarish hajmi",
            "Rejadagi va amaldagi ishlab chiqarish farqi",
            "Rejadagi va amaldagi sotuv farqi",
            "Omborga kirim va chiqim miqdori",
            "Transportlar soni va yuk hajmi",
        )
        for question in cases:
            with self.subTest(question=question):
                names = {
                    table.name
                    for table in AIDataAnalystAgent.discover_candidate_tables(question, self.schema, top_k=4)
                }
                self.assertTrue(names, question)
                if "rejadagi va amaldagi sotuv" in question.lower():
                    self.assertTrue(any("plan" in name for name in names))
                    self.assertTrue(any("actual" in name for name in names))


class UniversalDatabaseRoutingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.schema = SchemaContext(
            database_type="postgresql",
            database_name="retail_demo",
            included_schemas="public",
            total_tables=4,
            schema_prompt="synthetic retail schema",
            tables=[
                TableMeta(name="customers", row_count=100, columns=[
                    TableColumnMeta(name="customer_id", data_type="integer", nullable=False),
                    TableColumnMeta(name="company_name", data_type="text"),
                ]),
                TableMeta(name="sales_orders", row_count=1000, columns=[
                    TableColumnMeta(name="order_id", data_type="integer", nullable=False),
                    TableColumnMeta(name="customer_id", data_type="integer"),
                    TableColumnMeta(name="order_total", data_type="numeric"),
                    TableColumnMeta(name="order_date", data_type="date"),
                ]),
                TableMeta(name="inventory_balances", row_count=300, columns=[
                    TableColumnMeta(name="product_id", data_type="integer"),
                    TableColumnMeta(name="warehouse_id", data_type="integer"),
                    TableColumnMeta(name="quantity", data_type="numeric"),
                ]),
                TableMeta(name="employees", row_count=20, columns=[
                    TableColumnMeta(name="employee_id", data_type="integer", nullable=False),
                    TableColumnMeta(name="full_name", data_type="text"),
                ]),
            ],
        )

    def first(self, question):
        return AIDataAnalystAgent.discover_candidate_tables(question, self.schema, top_k=1)[0].name

    def test_unknown_database_uses_schema_semantics_not_kimyo_names(self):
        self.assertEqual("customers", self.first("Mijozlar soni nechta?"))
        self.assertEqual("sales_orders", self.first("Sotuv buyurtmalari summasi"))
        self.assertEqual("inventory_balances", self.first("Ombordagi mahsulot qoldig'i"))
        self.assertEqual("employees", self.first("Xodimlar soni"))

    def test_ambiguous_question_does_not_select_largest_table_by_volume(self):
        self.assertEqual(
            [],
            AIDataAnalystAgent.discover_candidate_tables(
                "Bu noma'lum ko'rsatkichni hisobla",
                self.schema,
                top_k=3,
            ),
        )

    def test_retrieval_searches_the_full_thousand_table_catalog(self):
        tables = [
            TableMeta(
                name=f"technical_object_{index}",
                columns=[TableColumnMeta(name="opaque_value", data_type="text")],
            )
            for index in range(999)
        ]
        tables.append(TableMeta(
            name="customer_accounts",
            object_type="view",
            columns=[TableColumnMeta(name="customer_id", data_type="integer")],
        ))
        schema = SchemaContext(
            database_type="postgresql",
            total_tables=len(tables),
            schema_prompt="",
            tables=tables,
        )
        matches = SchemaRetriever.rank("Mijozlar soni", schema, top_k=3)
        prompt = SchemaProfiler._build_prompt_representation("postgresql", tables)
        self.assertEqual("customer_accounts", matches[0].table.name)
        self.assertIn("technical_object_998", prompt)

    def test_rank_orders_at_order_grain_and_include_verified_branch_lookup(self):
        branches = TableMeta(name="branches", primary_key=["id"])
        orders = TableMeta(name="orders", columns=[
            TableColumnMeta(name=name, data_type="integer") for name in ("id", "branch_id", "total_amount")
        ])
        lines = TableMeta(name="order_items", row_count=1000000)
        invoices = TableMeta(name="invoices", columns=[
            TableColumnMeta(name=name, data_type="integer") for name in ("order_id", "branch_id")
        ])
        fk = TableRelationship(from_table="orders", from_column="branch_id", to_table="branches",
                               to_column="id", join_hint="orders.branch_id = branches.id",
                               source="foreign_key", verified=True)
        schema = SchemaContext(database_type="sqlite", total_tables=4, schema_prompt="",
                               tables=[branches, orders, lines, invoices], relationships=[fk])
        question = "2026-yil oktabrda yakunlangan buyurtmalar summasi eng katta bo'lgan uchta filial qaysi?"
        ranked = SchemaRetriever.rank(question, schema)
        self.assertEqual("orders", ranked[0].table.name)
        self.assertEqual([orders, branches], SchemaRetriever.grouped_sources(["filial"], ranked, schema))
        schema.relationships[0].verified = False
        self.assertEqual([], SchemaRetriever.grouped_sources(["filial"], ranked, schema))

    def test_focused_prompt_includes_eight_safe_values_but_no_sensitive_samples(self):
        table = TableMeta(name="orders", columns=[
            TableColumnMeta(name="status", data_type="text",
                            sample_values=["cancelled", "completed", "confirmed", "new", "paid",
                                           "processing", "returned", "shipped", "extra_value"]),
            TableColumnMeta(name="account_status", data_type="text", sample_values=["private_value"]),
        ])
        schema = SchemaContext(database_type="sqlite", total_tables=1, schema_prompt="", tables=[table])
        prompt = AIDataAnalystAgent._build_focused_schema_prompt(table, schema)
        self.assertIn("'shipped'", prompt)
        self.assertNotIn("extra_value", prompt)
        self.assertNotIn("private_value", prompt)

    def test_equipment_margin_selects_only_fact_at_product_grain(self):
        from app.schema_retriever import TableMatch
        catalog = TableMeta(name="catalog_refs", semantic_aliases=["uskuna"], business_role="dimension",
                            certified_source=True)
        lines = TableMeta(name="transaction_positions", semantic_aliases=["marja"], business_role="fact")
        headers = TableMeta(name="journal", semantic_aliases=["daromad"], business_role="fact")
        fk = TableRelationship(from_table=lines.name, from_column="asset_ref", to_table=catalog.name,
                               to_column="key", join_hint="transaction_positions.asset_ref=catalog_refs.key",
                               source="foreign_key", verified=True)
        schema = SchemaContext(database_type="sqlite", total_tables=3, schema_prompt="",
                               tables=[headers, catalog, lines], relationships=[fk])
        question = "Qaysi IT uskunalar eng ko‘p daromad va yuqori marja bergan?"
        matches = SchemaRetriever.rank(question, schema)
        self.assertEqual([lines, catalog], SchemaRetriever.grouped_sources(["IT uskunalar"], matches, schema))
        self.assertEqual([lines, catalog], SchemaRetriever.grouped_sources([], matches, schema, "IT uskunalar"))
        alternative = TableMeta(name="other_positions", semantic_aliases=["marja"])
        schema.tables.append(alternative)
        schema.relationships.append(fk.model_copy(update={"from_table": alternative.name}))
        matches.append(TableMatch(alternative, 100, ["semantic catalog alias:marja"]))
        self.assertEqual([], SchemaRetriever.grouped_sources(["IT uskunalar"], matches, schema))

    def test_soft_delete_metadata_is_inferred_for_any_table_name(self):
        schema = SchemaContext(
            database_type="postgresql",
            total_tables=4,
            schema_prompt="",
            tables=[
                TableMeta(name="crm_accounts", columns=[
                    TableColumnMeta(name="deleted_at", data_type="timestamp"),
                ]),
                TableMeta(name="erp_lines", columns=[
                    TableColumnMeta(name="is_deleted", data_type="boolean"),
                ]),
                TableMeta(name="legacy_rows", columns=[
                    TableColumnMeta(name="deleted", data_type="integer"),
                ]),
                TableMeta(name="workflow_rows", columns=[
                    TableColumnMeta(name="deleted", data_type="varchar"),
                ]),
            ],
        )
        SchemaProfiler._normalize_context_metadata(schema)
        self.assertEqual("is_null", schema.tables[0].soft_delete_mode)
        self.assertEqual("not_true", schema.tables[1].soft_delete_mode)
        self.assertEqual("zero_or_null", schema.tables[2].soft_delete_mode)
        self.assertIsNone(schema.tables[3].soft_delete_column)


class AmbiguityHandlingTests(unittest.IsolatedAsyncioTestCase):
    async def test_ambiguous_chat_question_returns_clarification_without_sql(self):
        from tests.plan_fixtures import grounded_plan
        planner_patch = patch('app.agent_analyst.plan_question', side_effect=grounded_plan)
        planner_patch.start()
        self.addCleanup(planner_patch.stop)
        schema = SchemaContext(
            database_type="postgresql",
            database_name="retail_demo",
            total_tables=1,
            schema_prompt="",
            tables=[
                TableMeta(
                    name="sales_orders",
                    row_count=100,
                    columns=[TableColumnMeta(name="order_id", data_type="integer")],
                )
            ],
        )
        response = await AIDataAnalystAgent.execute_analyst_agent(
            "Bu noma'lum ko'rsatkichni hisobla",
            schema,
            history=[],
        )
        self.assertFalse(response.success)
        self.assertEqual("", response.sql_query)
        self.assertEqual("clarification_required", response.error)


class SemanticCatalogTests(unittest.TestCase):
    def setUp(self):
        self.schema = SchemaContext(
            database_type="postgresql",
            database_name="generic_retail",
            total_tables=2,
            schema_prompt="",
            schema_fingerprint="abc123",
            tables=[
                TableMeta(name="sales_events", columns=[
                    TableColumnMeta(name="id", data_type="integer", is_primary_key=True),
                    TableColumnMeta(name="customer_id", data_type="integer"),
                    TableColumnMeta(name="total_amount", data_type="numeric"),
                    TableColumnMeta(name="status", data_type="text"),
                    TableColumnMeta(name="event_date", data_type="date"),
                ], primary_key=["id"]),
                TableMeta(name="client_registry", columns=[
                    TableColumnMeta(name="id", data_type="integer", is_primary_key=True),
                    TableColumnMeta(name="display_name", data_type="text"),
                ], primary_key=["id"]),
            ],
        )
        self.catalog = SemanticCatalog(
            catalog_key="",
            database_name="generic_retail",
            schema_fingerprint="abc123",
            reviewed=True,
            tables=[SemanticTableDefinition(
                table="sales_events",
                aliases=["savdo hodisalari", "tushum manbasi"],
                description="Tasdiqlangan sotuv faktlari",
                role="fact",
                grain="one row per sale",
                default_time_column="event_date",
                measures=["total_amount"],
                dimensions=["status"],
                certified=True,
            )],
            metrics=[SemanticMetricDefinition(
                id="paid_revenue",
                name="To'langan tushum",
                aliases=["jami tushum"],
                table="sales_events",
                aggregation="sum",
                column="total_amount",
                filters=[SemanticFilterDefinition(column="status", operator="eq", value="PAID")],
                unit="UZS",
                certified=True,
            )],
            relationships=[SemanticJoinDefinition(
                from_table="sales_events",
                from_columns=["customer_id"],
                to_table="client_registry",
                to_columns=["id"],
                cardinality="many-to-one",
                approved=True,
            )],
        )

    def test_reviewed_catalog_drives_routing_and_relationships(self):
        SemanticCatalogManager.apply_to_context(self.schema, self.catalog)
        matches = SchemaRetriever.rank("Jami tushum qancha?", self.schema, top_k=2)
        self.assertTrue(self.schema.semantic_catalog_reviewed)
        self.assertEqual("sales_events", matches[0].table.name)
        self.assertTrue(matches[0].table.certified_source)
        self.assertTrue(any(
            relationship.source == "semantic_catalog" and relationship.verified
            for relationship in self.schema.relationships
        ))

    def test_certified_metric_enforces_aggregation_column_and_filter(self):
        SemanticCatalogManager.apply_to_context(self.schema, self.catalog)
        valid, reason = SemanticCatalogManager.validate_metric_sql(
            "Jami tushum qancha?",
            "SELECT SUM(total_amount) FROM sales_events WHERE status = 'PAID'",
            self.schema,
        )
        invalid, invalid_reason = SemanticCatalogManager.validate_metric_sql(
            "Jami tushum qancha?",
            "SELECT COUNT(*) FROM sales_events",
            self.schema,
        )
        wrong_filter, wrong_filter_reason = SemanticCatalogManager.validate_metric_sql(
            "Jami tushum qancha?",
            "SELECT SUM(total_amount) FROM sales_events WHERE status != 'PAID'",
            self.schema,
        )
        self.assertTrue(valid, reason)
        self.assertFalse(invalid)
        self.assertIn("SUM", invalid_reason)
        self.assertFalse(wrong_filter)
        self.assertIn("status eq", wrong_filter_reason)

    def test_certified_metric_is_compiled_without_llm_guessing(self):
        SemanticCatalogManager.apply_to_context(self.schema, self.catalog)
        with patch(
            "app.semantic_catalog.db_manager.quote_identifier",
            side_effect=lambda value: ".".join(f'"{part}"' for part in value.split(".")),
        ):
            compiled = SemanticCatalogManager.compile_matching_metric_sql(
                "Jami tushum qancha?",
                self.schema,
            )
        self.assertIsNotNone(compiled)
        metric, sql = compiled
        self.assertEqual("paid_revenue", metric.id)
        self.assertIn('SUM("sales_events"."total_amount")', sql)
        self.assertIn('"sales_events"."status" = \'PAID\'', sql)
        self.assertNotIn("invented", sql)

    def test_region_typo_uses_reviewed_metric_and_verified_join_path(self):
        from tests.catalog_fixtures import reviewed_warehouse_fixture
        schema, catalog = reviewed_warehouse_fixture()
        SemanticCatalogManager.apply_to_context(schema, catalog)
        with patch(
            "app.semantic_catalog.db_manager.quote_identifier",
            side_effect=lambda value: ".".join(f'"{part}"' for part in value.split(".")),
        ):
            compiled = SemanticCatalogManager.compile_matching_metric_sql(
                "Qashqadartoda nechta vakolatli ombor?",
                schema,
            )
        self.assertIsNotNone(compiled)
        metric, sql = compiled
        self.assertEqual("authorized_warehouse_count", metric.id)
        self.assertIn('LEFT JOIN "district"', sql)
        self.assertIn('LEFT JOIN "region"', sql)
        self.assertIn('"region"."name_uz" = \'Qashqadaryo viloyati\'', sql)
        self.assertIn('"warehouse"."working_factory_id" IS NOT NULL', sql)
        self.assertIn("%Chiqim Ombor%", sql)
        self.assertNotIn("warehouse_status", sql)

    def test_each_region_phrase_compiles_to_reviewed_grouping(self):
        from tests.catalog_fixtures import reviewed_warehouse_fixture
        schema, catalog = reviewed_warehouse_fixture()
        SemanticCatalogManager.apply_to_context(schema, catalog)
        with patch(
            "app.semantic_catalog.db_manager.quote_identifier",
            side_effect=lambda value: ".".join(f'"{part}"' for part in value.split(".")),
        ):
            compiled = SemanticCatalogManager.compile_matching_metric_sql(
                "har bitta viloyatda vakolatli omborlar nechta?",
                schema,
            )
        self.assertIsNotNone(compiled)
        metric, sql = compiled
        self.assertEqual("authorized_warehouse_count", metric.id)
        self.assertIn('"region"."name_uz" AS "viloyat"', sql)
        self.assertIn('GROUP BY "region"."name_uz"', sql)
        self.assertIn('ORDER BY "authorized_warehouse_count" DESC', sql)
        self.assertNotIn("warehouse_status", sql)

    def test_catalog_rejects_unknown_columns(self):
        self.catalog.metrics[0].column = "invented_amount"
        with self.assertRaises(SemanticCatalogError):
            SemanticCatalogManager.validate_catalog(self.catalog, self.schema)

    def test_catalog_is_scoped_and_saved_outside_the_database(self):
        with tempfile.TemporaryDirectory() as folder, patch(
            "app.semantic_catalog.CATALOG_DIRECTORY", folder
        ):
            saved = SemanticCatalogManager.save_for_context(self.catalog, self.schema)
            loaded = SemanticCatalogManager.load_for_context(self.schema)
        self.assertEqual(SemanticCatalogManager.catalog_key(self.schema), saved.catalog_key)
        self.assertIsNotNone(loaded)
        self.assertEqual("paid_revenue", loaded.metrics[0].id)


class SqlGuardTests(unittest.TestCase):
    def test_postgres_sessions_are_forced_read_only(self):
        args = DatabaseManager._postgres_connect_args(7)
        self.assertEqual(7, args["connect_timeout"])
        self.assertIn("default_transaction_read_only=on", args["options"])
        self.assertIn("statement_timeout=15000", args["options"])

    def test_ssh_settings_connection_reports_success(self):
        manager = DatabaseManager()
        manager.connect_with_ssh = MagicMock(return_value=True)
        with (
            patch("app.database.settings.SSH_HOST", "ssh.example"),
            patch("app.database.settings.DB_NAME", "analytics"),
            patch("app.database.settings.DB_USER", "reader"),
        ):
            self.assertTrue(manager.connect_from_settings())
        manager.connect_with_ssh.assert_called_once()

    def test_startup_connection_error_is_reported_without_password(self):
        manager = DatabaseManager()
        manager.connect_with_ssh = MagicMock(
            side_effect=RuntimeError("authentication failed for super-secret")
        )
        with (
            patch("app.database.settings.SSH_HOST", "ssh.example"),
            patch("app.database.settings.DB_NAME", "analytics"),
            patch("app.database.settings.DB_USER", "reader"),
            patch("app.database.settings.DB_PASSWORD", "super-secret"),
            patch("app.database.settings.SSH_PKEY_PASSPHRASE", None),
        ):
            self.assertFalse(manager.connect_from_settings())
        info = manager.test_connection()
        self.assertEqual("disconnected", info["status"])
        self.assertIn("authentication failed", info["error"])
        self.assertNotIn("super-secret", info["error"])

    def test_failed_database_switch_preserves_previous_connection(self):
        manager = DatabaseManager()
        previous_engine = MagicMock()
        previous_tunnel = MagicMock()
        manager._engine = previous_engine
        manager._ssh_tunnel = previous_tunnel

        with patch.object(
            manager,
            "_build_engine",
            side_effect=RuntimeError("new database unavailable"),
        ):
            with self.assertRaises(RuntimeError):
                manager.set_connection("postgresql://reader@db.example/analytics")

        self.assertIs(previous_engine, manager._engine)
        self.assertIs(previous_tunnel, manager._ssh_tunnel)
        previous_engine.dispose.assert_not_called()
        previous_tunnel.stop.assert_not_called()

    def test_explicit_database_url_has_priority_for_direct_connection(self):
        configured = Settings()
        configured.SSH_HOST = None
        configured.DATABASE_URL = "sqlite:///C:/data/read-only.db"
        configured.DB_HOST = "ignored.example"
        configured.DB_NAME = "ignored"
        self.assertEqual(
            "sqlite:///C:/data/read-only.db",
            configured.effective_database_url,
        )

    def test_schema_identifier_injection_is_rejected(self):
        manager = DatabaseManager()
        manager._include_schemas = "public'); DROP TABLE organization; --"
        with self.assertRaises(ValueError):
            manager.get_schemas_list()

    def test_sqlite_connection_is_query_only(self):
        handle = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        handle.close()
        try:
            connection = sqlite3.connect(handle.name)
            connection.execute("CREATE TABLE facts (id INTEGER PRIMARY KEY, amount REAL)")
            connection.execute("INSERT INTO facts VALUES (1, 12.5)")
            connection.commit()
            connection.close()

            manager = DatabaseManager()
            manager.set_connection(f"sqlite:///{handle.name}")
            self.assertTrue(manager.test_connection()["read_only"])
            self.assertEqual([{"id": 1, "amount": 12.5}], manager.execute_query("SELECT * FROM facts"))
            self.assertEqual([{"value": "12.5"}], manager.execute_query("SELECT REPLACE('12,5', ',', '.') AS value"))
            with self.assertRaises(PermissionError):
                manager.execute_query("UPDATE facts SET amount = 99")
            with manager._engine.connect() as conn:
                self.assertEqual('DELETE', conn.exec_driver_sql("SELECT 'DELETE'").scalar())
                with self.assertRaises(Exception):
                    conn.exec_driver_sql("REPLACE INTO facts VALUES (1, 99)")
                with self.assertRaises(Exception):
                    conn.exec_driver_sql("UPDATE facts SET amount = 99")
            manager._engine.dispose()
        finally:
            try:
                os.unlink(handle.name)
            except PermissionError:
                pass

    def test_dangerous_select_functions_are_rejected(self):
        for sql in (
            "SELECT pg_sleep(30)",
            "SELECT pg_read_file('/etc/passwd')",
            "SELECT nextval('orders_id_seq')",
            "SELECT load_file('/etc/passwd')",
            "SELECT * INTO copied_orders FROM orders",
            "SELECT * FROM orders FOR UPDATE",
        ):
            with self.subTest(sql=sql):
                valid, _ = validate_and_sanitize_sql(sql)
                self.assertFalse(valid)

    def test_soft_delete_filters_are_scoped(self):
        valid, sql = validate_and_sanitize_sql(
            "SELECT * FROM warehouse w LEFT JOIN news n ON w.id = n.id",
            soft_delete_rules={
                "warehouse": {"column": "is_deleted", "mode": "not_true"},
                "news": {"column": "is_deleted", "mode": "not_true"},
            },
        )
        self.assertTrue(valid, sql)
        self.assertEqual(1, sql.lower().count("w.is_deleted"))
        self.assertEqual(1, sql.lower().count("n.is_deleted"))
        self.assertIn("on w.id = n.id and coalesce(n.is_deleted, false) = false", sql.lower())

    def test_soft_delete_rules_support_timestamp_and_arbitrary_tables(self):
        valid, sql = validate_and_sanitize_sql(
            "SELECT * FROM crm_accounts a",
            soft_delete_rules={
                "crm_accounts": {"column": "deleted_at", "mode": "is_null"},
            },
        )
        self.assertTrue(valid, sql)
        self.assertIn("a.deleted_at is null", sql.lower())

    def test_soft_delete_rules_support_integer_flags_without_dropping_zero_rows(self):
        valid, sql = validate_and_sanitize_sql(
            "SELECT * FROM legacy_rows r",
            soft_delete_rules={
                "legacy_rows": {"column": "deleted", "mode": "zero_or_null"},
            },
        )
        self.assertTrue(valid, sql)
        self.assertIn("coalesce(r.deleted, 0) = 0", sql.lower())

    def test_integrity_audit_can_disable_only_soft_delete_rewrite(self):
        valid, sql = validate_and_sanitize_sql(
            "SELECT * FROM warehouse w",
            apply_soft_delete=False,
        )
        self.assertTrue(valid, sql)
        self.assertNotIn("is_deleted", sql.lower())

    def test_nested_query_does_not_reference_inner_alias_outside_scope(self):
        valid, sql = validate_and_sanitize_sql(
            "SELECT * FROM (SELECT * FROM warehouse) q"
        )
        self.assertTrue(valid, sql)
        outer_sql = sql.lower().split("from (", 1)[0]
        self.assertNotIn("warehouse.is_deleted", outer_sql)


class GroundedResponseTests(unittest.TestCase):
    def test_result_cap_only_marks_truncation_when_an_extra_row_exists(self):
        for count in (500, 501):
            rows = [{"name": str(i)} for i in range(count)]
            result = AIDataAnalystAgent._build_response(
                "Nomlarni ko'rsat", rows, {}, "SELECT name FROM facts", "facts", 0)
            self.assertEqual(len(result.rows), 500)
            self.assertEqual(result.is_truncated, count > 500)

    def test_numbers_keep_their_executed_precision(self):
        text, _ = AIDataAnalystAgent._build_evidence_summary(
            [{"amount": 9007199254740993, "ratio": 0.00001234, "unknown": None}], "metrics")
        self.assertIn("9,007,199,254,740,993", text)
        self.assertIn("0.00001234", text)
        self.assertIn("qiymat mavjud emas", text)

    def test_questions_about_data_reach_the_analyst_even_after_greeting(self):
        classify = AIDataAnalystAgent.classify_user_intent
        self.assertEqual(classify("Salom!"), "GREETING")
        self.assertEqual(classify("Salom! Qaysi filial eng foydali?"), "DATA_QUERY")
        self.assertEqual(classify("Bizning sotuvlar nega kamaydi? Tavsiya ber."), "DATA_QUERY")
        self.assertEqual(classify("List shipments delayed over 30 days"), "DATA_QUERY")
        self.assertEqual(classify("Yalpi marja nima degani?"), "CONVERSATIONAL")

    def test_two_periods_can_be_compared_in_one_fact_table(self):
        orders = TableMeta(name="orders", columns=[TableColumnMeta(name="year", data_type="integer"),
                                                   TableColumnMeta(name="amount", data_type="numeric")])
        other = TableMeta(name="clients", columns=[TableColumnMeta(name="id", data_type="integer")])
        valid, error = AIDataAnalystAgent._validate_sql_semantics(
            "2023 va 2024 yil sotuvlarini solishtir",
            "SELECT year, SUM(amount) FROM orders WHERE year IN (2023, 2024) GROUP BY year",
            [orders, other])
        self.assertTrue(valid, error)

    def test_llm_number_is_not_used_as_result(self):
        response = AIDataAnalystAgent._build_response(
            prompt="Jami nechta?",
            rows=[{"jami": 12}],
            parsed={"explanation": "Tizimda 247 ta yozuv bor.", "title": "Jami"},
            sql="SELECT COUNT(*) AS jami FROM organization",
            table_used="organization",
            start_time=time.time(),
        )
        self.assertIn("12", response.explanation)
        self.assertNotIn("247", response.explanation)


class RelationshipSafetyTests(unittest.TestCase):
    def setUp(self):
        self.schema = SchemaContext(
            database_type="postgresql",
            database_name="generic",
            included_schemas="public",
            total_tables=2,
            schema_prompt="",
            tables=[
                TableMeta(
                    name="orders",
                    columns=[
                        TableColumnMeta(name="id", data_type="integer", is_primary_key=True, nullable=False),
                        TableColumnMeta(name="customer_id", data_type="integer"),
                    ],
                    primary_key=["id"],
                ),
                TableMeta(
                    name="customers",
                    columns=[
                        TableColumnMeta(name="id", data_type="integer", is_primary_key=True, nullable=False),
                    ],
                    primary_key=["id"],
                ),
            ],
            relationships=[
                TableRelationship(
                    from_table="orders",
                    from_column="customer_id",
                    to_table="customers",
                    to_column="id",
                    join_hint="LEFT JOIN customers ON orders.customer_id = customers.id",
                    source="foreign_key",
                    confidence=1.0,
                    verified=True,
                    cardinality="many-to-one",
                )
            ],
        )

    def test_declared_join_is_allowed(self):
        valid, error = RelationshipGuard.validate_query(
            "SELECT COUNT(DISTINCT o.id) FROM orders o JOIN customers c ON o.customer_id = c.id",
            self.schema,
        )
        self.assertTrue(valid, error)

    def test_approved_relationship_wins_over_earlier_naming_inference(self):
        self.schema.relationships.insert(0, TableRelationship(
            from_table="orders",
            from_column="customer_id",
            to_table="customers",
            to_column="id",
            join_hint="LEFT JOIN customers ON orders.customer_id = customers.id",
            source="naming_convention",
            confidence=0.7,
            verified=False,
            cardinality="many-to-one",
        ))
        valid, error = RelationshipGuard.validate_query(
            "SELECT COUNT(*) FROM orders o JOIN customers c ON o.customer_id = c.id",
            self.schema,
        )
        self.assertTrue(valid, error)

    def test_implicit_or_ambiguous_join_syntax_is_rejected(self):
        for sql in (
            "SELECT * FROM orders CROSS JOIN customers",
            "SELECT * FROM orders NATURAL JOIN customers",
            "SELECT * FROM orders JOIN customers USING (id)",
            "SELECT * FROM orders o JOIN customers c ON o.customer_id > c.id",
        ):
            with self.subTest(sql=sql):
                valid, error = RelationshipGuard.validate_query(sql, self.schema)
                self.assertFalse(valid)
                self.assertIn("join", (error or "").lower())

    def test_partial_composite_foreign_key_join_is_rejected(self):
        composite_schema = SchemaContext(
            database_type="postgresql",
            total_tables=2,
            schema_prompt="",
            tables=[
                TableMeta(name="ledger_entries", columns=[
                    TableColumnMeta(name="tenant_id", data_type="integer"),
                    TableColumnMeta(name="account_id", data_type="integer"),
                ]),
                TableMeta(name="accounts", columns=[
                    TableColumnMeta(name="tenant_id", data_type="integer"),
                    TableColumnMeta(name="id", data_type="integer"),
                ]),
            ],
            relationships=[TableRelationship(
                from_table="ledger_entries",
                from_column="tenant_id",
                to_table="accounts",
                to_column="tenant_id",
                from_columns=["tenant_id", "account_id"],
                to_columns=["tenant_id", "id"],
                constraint_name="fk_ledger_account",
                join_hint=(
                    "LEFT JOIN accounts ON ledger_entries.tenant_id = accounts.tenant_id "
                    "AND ledger_entries.account_id = accounts.id"
                ),
                source="foreign_key",
                verified=True,
            )],
        )
        partial, error = RelationshipGuard.validate_query(
            "SELECT * FROM ledger_entries l JOIN accounts a ON l.tenant_id = a.tenant_id",
            composite_schema,
        )
        complete, complete_error = RelationshipGuard.validate_query(
            "SELECT * FROM ledger_entries l JOIN accounts a "
            "ON l.tenant_id = a.tenant_id AND l.account_id = a.id",
            composite_schema,
        )
        self.assertFalse(partial)
        self.assertIn("composite", error.lower())
        self.assertTrue(complete, complete_error)

    def test_parent_measure_sum_after_one_to_many_join_is_rejected(self):
        valid, error = RelationshipGuard.validate_query(
            "SELECT SUM(c.id) FROM orders o JOIN customers c ON o.customer_id = c.id",
            self.schema,
        )
        self.assertFalse(valid)
        self.assertIn("blow-up", error)

    def test_sibling_facts_must_be_aggregated_before_join(self):
        payments = self.schema.tables[0].model_copy(deep=True)
        payments.name = "payments"
        key = self.schema.relationships[0].model_copy(update={"from_table": "payments"})
        self.schema.tables.append(payments)
        self.schema.relationships.append(key)
        joins = " FROM orders o JOIN customers c ON o.customer_id=c.id JOIN payments p ON p.customer_id=c.id"
        for agg in ("SUM(o.id)", "AVG(p.id)", "COUNT(o.id)"):
            for sql in ["SELECT " + agg + joins, "WITH q AS (SELECT " + agg + " AS v" + joins + ") SELECT * FROM q"]:
                valid, why = RelationshipGuard.validate_query(sql, self.schema)
                self.assertFalse(valid, sql)
                self.assertIn("blow-up", why)
        for sql in [
            "SELECT COUNT(DISTINCT o.id)" + joins,
            "WITH a AS (SELECT customer_id, SUM(id) AS v FROM orders GROUP BY customer_id), "
            "b AS (SELECT customer_id, SUM(id) AS v FROM payments GROUP BY customer_id) "
            "SELECT a.v,b.v FROM a JOIN b ON a.customer_id=b.customer_id",
        ]:
            valid, why = RelationshipGuard.validate_query(sql, self.schema)
            self.assertTrue(valid, why)

    def test_table_outside_profile_is_rejected(self):
        valid, error = RelationshipGuard.validate_query("SELECT * FROM pg_shadow", self.schema)
        self.assertFalse(valid)
        self.assertIn("outside", error)

    def test_unqualified_table_is_rejected_when_two_schemas_share_its_name(self):
        schema = SchemaContext(
            database_type="postgresql",
            included_schemas="sales,archive",
            total_tables=2,
            schema_prompt="",
            tables=[
                TableMeta(
                    name="sales.orders",
                    schema_name="sales",
                    columns=[TableColumnMeta(name="id", data_type="integer")],
                ),
                TableMeta(
                    name="archive.orders",
                    schema_name="archive",
                    columns=[TableColumnMeta(name="id", data_type="integer")],
                ),
            ],
        )
        ambiguous, error = RelationshipGuard.validate_query("SELECT * FROM orders", schema)
        qualified, qualified_error = RelationshipGuard.validate_query(
            "SELECT * FROM sales.orders",
            schema,
        )
        self.assertFalse(ambiguous)
        self.assertIn("outside", error)
        self.assertTrue(qualified, qualified_error)

    def test_unknown_qualified_column_is_rejected_before_database(self):
        valid, error = RelationshipGuard.validate_query(
            "SELECT o.imaginary_amount FROM orders o",
            self.schema,
        )
        self.assertFalse(valid)
        self.assertIn("unknown column", error)

    def test_naming_inference_is_not_mislabeled_as_declared_fk(self):
        relationships = SchemaProfiler.infer_relationships(self.schema.tables)
        inferred = next(rel for rel in relationships if rel.from_column == "customer_id")
        self.assertEqual("naming_convention", inferred.source)
        self.assertFalse(inferred.verified)
        orders_customer = next(
            column for column in self.schema.tables[0].columns if column.name == "customer_id"
        )
        self.assertFalse(orders_customer.is_foreign_key)


class GroundedDashboardTests(unittest.IsolatedAsyncioTestCase):
    async def test_dashboard_drops_failed_queries_and_synthetic_metrics(self):
        schema = SchemaContext(
            database_type="postgresql",
            database_name="kimyosanoatdb",
            included_schemas="public",
            total_tables=1,
            tables=[TableMeta(name="organization", columns=[TableColumnMeta(name="id", data_type="integer")])],
            schema_prompt="organization(id integer)",
        )
        parsed = {
            "kpis": [
                {"title": "Jami", "sql_query": "SELECT COUNT(*) AS value FROM organization", "delta": "+99%", "target": "100"},
                {"title": "Xato", "sql_query": "SELECT missing FROM organization"},
            ],
            "charts": [
                {"title": "Taqsimot", "sql_query": "SELECT 1 AS x, 2 AS y", "x_axis": "x", "y_axis": "y", "insight": "Uydirma", "recommendation": "Uydirma"},
                {"title": "Xato grafik", "sql_query": "SELECT missing FROM organization"},
            ],
            "business_domains": [],
            "suggested_questions": [],
        }
        outcomes = [
            (True, [{"value": 12}], "SELECT COUNT(*) AS value FROM organization", None),
            (False, [], "SELECT missing FROM organization", "missing column"),
            (True, [{"x": "A", "y": 2}], "SELECT 1 AS x, 2 AS y", None),
            (False, [], "SELECT missing FROM organization", "missing column"),
        ]
        with (
            patch.object(DashboardGenerator, "_derive_business_domains", return_value=[]),
            patch("app.dashboard_generator.llm_service.generate_completion", new=AsyncMock(return_value="{}")),
            patch("app.dashboard_generator.llm_service.extract_json", return_value=parsed),
            patch("app.dashboard_generator.SelfHealingQueryEngine.execute_with_self_healing", new=AsyncMock(side_effect=outcomes)),
        ):
            result = await DashboardGenerator._generate_via_gemma(schema)

        self.assertEqual(1, len(result.kpis))
        self.assertEqual(12, result.kpis[0].value)
        self.assertIsNone(result.kpis[0].delta)
        self.assertIsNone(result.kpis[0].target)
        self.assertEqual([], result.kpis[0].sparkline_data)
        self.assertEqual(1, len(result.charts))
        self.assertIn("1 ta natija", result.charts[0].insight)
        self.assertIsNone(result.charts[0].recommendation)


class DataQualityAuditTests(unittest.TestCase):
    def test_schema_fingerprint_detects_added_columns(self):
        previous = SchemaContext(
            database_type="postgresql",
            database_name="generic",
            total_tables=1,
            schema_prompt="",
            tables=[TableMeta(name="facts", columns=[TableColumnMeta(name="id", data_type="integer")])],
        )
        current = [
            TableMeta(
                name="facts",
                columns=[
                    TableColumnMeta(name="id", data_type="integer"),
                    TableColumnMeta(name="amount", data_type="numeric"),
                ],
            )
        ]
        changes = SchemaProfiler._describe_schema_changes(previous, current)
        self.assertEqual(["column added: facts.amount"], changes)
        self.assertNotEqual(
            SchemaProfiler._fingerprint_tables(previous.tables),
            SchemaProfiler._fingerprint_tables(current),
        )

    def test_schema_fingerprint_detects_declared_relationship_change(self):
        tables = [
            TableMeta(name="facts", columns=[
                TableColumnMeta(name="dimension_id", data_type="integer", is_foreign_key=True),
            ]),
            TableMeta(name="dimensions", columns=[
                TableColumnMeta(name="id", data_type="integer", is_primary_key=True),
            ]),
        ]
        first = TableRelationship(
            from_table="facts",
            from_column="dimension_id",
            to_table="dimensions",
            to_column="id",
            join_hint="LEFT JOIN dimensions ON facts.dimension_id = dimensions.id",
            source="foreign_key",
            verified=True,
        )
        changed = first.model_copy(update={"to_column": "legacy_id"})
        self.assertNotEqual(
            SchemaProfiler._fingerprint_tables(tables, [first]),
            SchemaProfiler._fingerprint_tables(tables, [changed]),
        )

    def test_unvalidated_foreign_key_is_not_promoted_to_verified(self):
        schema = SchemaContext(
            database_type="postgresql",
            total_tables=0,
            tables=[],
            schema_prompt="",
            relationships=[TableRelationship(
                from_table="facts",
                from_column="dimension_id",
                to_table="dimensions",
                to_column="id",
                join_hint="",
                source="foreign_key",
                verified=False,
                constraint_validated=False,
            )],
        )
        SchemaProfiler._normalize_context_metadata(schema)
        self.assertFalse(schema.relationships[0].verified)

    def test_metadata_limit_fails_closed_instead_of_saving_partial_catalog(self):
        with patch("app.schema_profiler.db_manager") as fake_db:
            fake_db.execute_query.return_value = [{"id": 1}, {"id": 2}, {"id": 3}]
            with self.assertRaisesRegex(RuntimeError, "partial schema"):
                SchemaProfiler._metadata_query("SELECT 1", 2, "objects")
            fake_db.execute_query.assert_called_once_with("SELECT 1", max_rows=3)

    def test_foreign_table_name_is_schema_qualified_when_needed(self):
        self.assertEqual(
            "crm.customers",
            SchemaProfiler._qualified_object_name("crm", "customers", ["crm"]),
        )
        self.assertEqual(
            "public.customers",
            SchemaProfiler._qualified_object_name("public", "customers", ["public", "crm"]),
        )

    def test_audit_returns_only_aggregate_quality_evidence(self):
        schema = SchemaContext(
            database_type="postgresql",
            database_name="generic",
            included_schemas="public",
            total_tables=1,
            schema_prompt="",
            tables=[
                TableMeta(
                    name="facts",
                    row_count=2,
                    primary_key=["id"],
                    columns=[
                        TableColumnMeta(name="id", data_type="integer", is_primary_key=True, nullable=False),
                        TableColumnMeta(name="created_at", data_type="timestamp", nullable=False),
                        TableColumnMeta(name="amount", data_type="numeric"),
                    ],
                )
            ],
        )
        fake_db = MagicMock()
        fake_db.is_connected = True
        fake_db.current_db_name = "generic"
        fake_db.quote_identifier.side_effect = lambda value: f'"{value}"'

        def execute(sql, max_rows=500):
            if "duplicate_key_groups" in sql:
                return [{"duplicate_key_groups": 0}]
            if "sampled_rows" in sql:
                return [{"sampled_rows": 2, "null_0": 0, "null_1": 0}]
            if "latest_value" in sql:
                return [{"latest_value": "2026-10-24T10:00:00"}]
            raise AssertionError(sql)

        fake_db.execute_query.side_effect = execute
        with patch("app.data_quality.db_manager", fake_db):
            result = DataQualityAuditor.audit(
                schema,
                DataQualityAuditRequest(tables=["facts"], include_relationships=False),
            )

        self.assertTrue(result.read_only)
        self.assertEqual(["id"], result.tables[0].verified_candidate_key)
        self.assertEqual(0, result.tables[0].duplicate_key_groups)
        self.assertEqual("2026-10-24T10:00:00", result.tables[0].latest_timestamp)


class AgentAccuracyRegressionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.table = TableMeta(
            name="inventory_balances",
            row_count=20,
            columns=[
                TableColumnMeta(name="product_name", data_type="text"),
                TableColumnMeta(name="quantity", data_type="numeric"),
                TableColumnMeta(name="region", data_type="text"),
            ],
        )
        self.schema = SchemaContext(
            database_type="postgresql",
            database_name="retail_demo",
            total_tables=1,
            schema_prompt="",
            tables=[self.table],
        )

    def test_multirow_answer_contains_executed_values(self):
        text, truncated = AIDataAnalystAgent._build_evidence_summary(
            [
                {"Mahsulot": "Ammiak", "Miqdor": 1250},
                {"Mahsulot": "Karbamid", "Miqdor": 900},
            ],
            "inventory_balances",
        )
        self.assertIn("Ammiak", text)
        self.assertIn("1,250", text)
        self.assertIn("Karbamid", text)
        self.assertFalse(truncated)

    async def test_zero_result_is_not_retried_on_a_weaker_table(self):
        from tests.plan_fixtures import grounded_plan
        planner_patch = patch('app.agent_analyst.plan_question', side_effect=grounded_plan)
        planner_patch.start()
        self.addCleanup(planner_patch.stop)
        generated = {
            "sql_query": "SELECT SUM(quantity) AS total FROM inventory_balances",
            "chart_type": "metric",
            "title": "Jami qoldiq",
            "multi_charts": [],
        }
        completion = AsyncMock(return_value=__import__("json").dumps(generated))
        execution = AsyncMock(return_value=(
            True,
            [{"total": 0}],
            generated["sql_query"],
            None,
        ))
        with (
            patch("app.agent_analyst.llm_service.generate_completion", new=completion),
            patch(
                "app.agent_analyst.SelfHealingQueryEngine.execute_with_self_healing",
                new=execution,
            ),
        ):
            response = await AIDataAnalystAgent.execute_analyst_agent(
                "Ombordagi mahsulot qoldig'i jami qancha?",
                self.schema,
                history=[],
            )
        self.assertTrue(response.success)
        self.assertEqual([{"total": 0}], response.rows)
        self.assertEqual(1, completion.await_count)
        self.assertEqual(1, execution.await_count)

    def test_warehouse_count_label_is_uzbek(self):
        text, _ = AIDataAnalystAgent._build_evidence_summary(
            [{"warehouse_count": 24}],
            "warehouse",
        )
        self.assertIn("Omborlar soni", text)
        self.assertIn("24", text)

    def test_semantic_validator_rejects_non_aggregate_count_answer(self):
        valid, reason = AIDataAnalystAgent._validate_sql_semantics(
            "Mahsulotlar soni nechta?",
            "SELECT product_name FROM inventory_balances LIMIT 500",
            [self.table],
        )
        self.assertFalse(valid)
        self.assertIn("aggregate", reason)

    def test_semantic_validator_enforces_exact_top_n(self):
        invalid, _ = AIDataAnalystAgent._validate_sql_semantics(
            "Top 5 mahsulot",
            "SELECT product_name, SUM(quantity) AS total FROM inventory_balances GROUP BY 1 ORDER BY 2 DESC LIMIT 10",
            [self.table],
        )
        null_unsafe, _ = AIDataAnalystAgent._validate_sql_semantics(
            "Top 5 mahsulot",
            "SELECT product_name, SUM(quantity) AS total FROM inventory_balances "
            "GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
            [self.table],
        )
        valid, reason = AIDataAnalystAgent._validate_sql_semantics(
            "Top 5 mahsulot",
            "SELECT product_name, COALESCE(SUM(quantity), 0) AS total FROM inventory_balances "
            "WHERE product_name IS NOT NULL GROUP BY 1 ORDER BY 2 DESC NULLS LAST LIMIT 5",
            [self.table],
        )
        self.assertFalse(invalid)
        self.assertFalse(null_unsafe)
        self.assertTrue(valid, reason)

    def test_top_n_sql_is_normalized_before_execution(self):
        sql = AIDataAnalystAgent._normalize_top_n_sql(
            "Eng ko'p sotilgan top 5 mahsulot",
            "SELECT product_name, SUM(quantity) AS total FROM inventory_balances "
            "GROUP BY product_name ORDER BY total DESC LIMIT 5",
        )
        valid, reason = AIDataAnalystAgent._validate_sql_semantics(
            "Eng ko'p sotilgan top 5 mahsulot",
            sql,
            [self.table],
        )
        self.assertIn("NOT", sql.upper())
        self.assertIn("NULLS LAST", sql.upper())
        self.assertTrue(valid, reason)

        sanitized_ok, sanitized = validate_and_sanitize_sql(sql)
        self.assertTrue(sanitized_ok, sanitized)
        self.assertIn("NULLS LAST", sanitized.upper())

    def test_latin_geography_filter_uses_name_uz(self):
        warehouse = TableMeta(
            name="warehouse",
            columns=[TableColumnMeta(name="district_id", data_type="bigint")],
        )
        district = TableMeta(
            name="district",
            columns=[
                TableColumnMeta(name="name", data_type="text"),
                TableColumnMeta(name="name_uz", data_type="text"),
                TableColumnMeta(name="region_id", data_type="bigint"),
            ],
        )
        region = TableMeta(
            name="region",
            columns=[
                TableColumnMeta(name="name", data_type="text"),
                TableColumnMeta(name="name_uz", data_type="text"),
            ],
        )
        schema = SchemaContext(
            database_type="postgresql",
            database_name="kimyosanoatdb",
            total_tables=3,
            schema_prompt="",
            tables=[warehouse, district, region],
        )
        normalized = AIDataAnalystAgent._normalize_geographic_sql(
            "Qashqadaryo viloyatida nechta vakolatli ombor bor?",
            "SELECT COUNT(*) FROM warehouse w JOIN district d ON w.district_id=d.id "
            "JOIN region r ON d.region_id=r.id WHERE r.name = 'Qashqadaryo'",
            schema,
        )
        self.assertIn("r.name_uz = 'Qashqadaryo'", normalized)
        self.assertNotIn("ILIKE", normalized)
        self.assertNotIn("r.name =", normalized)

    def test_comparison_requires_both_sources(self):
        plan = TableMeta(
            name="sales_plan_monthly",
            columns=[TableColumnMeta(name="period_id", data_type="integer"), TableColumnMeta(name="planned", data_type="numeric")],
        )
        actual = TableMeta(
            name="sales_actual_monthly",
            columns=[TableColumnMeta(name="period_id", data_type="integer"), TableColumnMeta(name="actual", data_type="numeric")],
        )
        invalid, _ = AIDataAnalystAgent._validate_sql_semantics(
            "Reja va amaldagi sotuv farqini taqqosla",
            "SELECT SUM(planned) FROM sales_plan_monthly",
            [plan, actual],
        )
        valid, reason = AIDataAnalystAgent._validate_sql_semantics(
            "Reja va amaldagi sotuv farqini taqqosla",
            "WITH p AS (SELECT period_id, SUM(planned) planned FROM sales_plan_monthly GROUP BY period_id), "
            "a AS (SELECT period_id, SUM(actual) actual FROM sales_actual_monthly GROUP BY period_id) "
            "SELECT p.period_id, p.planned, a.actual, a.actual - p.planned AS difference "
            "FROM p LEFT JOIN a ON p.period_id = a.period_id",
            [plan, actual],
        )
        self.assertFalse(invalid)
        self.assertTrue(valid, reason)

    async def test_model_outage_is_not_reported_as_empty_data(self):
        with (
            patch.object(AIDataAnalystAgent, "discover_candidate_tables", return_value=[self.table]),
            patch(
                "app.agent_analyst.llm_service.generate_completion",
                new=AsyncMock(side_effect=LLMUnavailableError("offline")),
            ),
        ):
            response = await AIDataAnalystAgent.execute_analyst_agent(
                "Ombordagi mahsulotlar soni nechta?",
                self.schema,
                history=[],
            )
        self.assertFalse(response.success)
        self.assertEqual("llm_unavailable", response.error)
        self.assertNotIn("mos yozuv topilmadi", response.explanation)

    async def test_follow_up_is_resolved_before_table_discovery(self):
        from tests.plan_fixtures import grounded_plan
        planner_patch = patch('app.agent_analyst.plan_question', side_effect=grounded_plan)
        planner_patch.start()
        self.addCleanup(planner_patch.stop)
        history = [{"role": "user", "content": "Mahsulotlar bo'yicha ombor qoldig'ini ko'rsat"}]
        generated = {
            "sql_query": "SELECT SUM(quantity) AS total FROM inventory_balances WHERE region ILIKE '%Samarqand%'",
            "chart_type": "metric",
            "title": "Samarqand qoldig'i",
            "multi_charts": [],
        }
        with (
            patch.object(AIDataAnalystAgent, "discover_candidate_tables", return_value=[self.table]) as discover,
            patch(
                "app.agent_analyst.llm_service.generate_completion",
                new=AsyncMock(return_value=__import__("json").dumps(generated)),
            ),
            patch(
                "app.agent_analyst.SelfHealingQueryEngine.execute_with_self_healing",
                new=AsyncMock(return_value=(True, [{"total": 12}], generated["sql_query"], None)),
            ),
        ):
            response = await AIDataAnalystAgent.execute_analyst_agent(
                "Endi faqat Samarqand bo'yicha",
                self.schema,
                history=history,
            )
        resolved = discover.call_args.args[0]
        self.assertIn("ombor qoldig'ini", resolved)
        self.assertIn("Samarqand", resolved)
        self.assertTrue(response.success)
        self.assertIn("12", response.explanation)


class GoldEvaluationTests(unittest.IsolatedAsyncioTestCase):
    async def test_clarifications_and_quota_are_scored_separately(self):
        schema = SchemaContext(database_type="sqlite", total_tables=0, tables=[], schema_prompt="")
        cases = [GoldQueryCase(name=str(i), question="Which metric?", expected_error="clarification_required")
                 for i in range(4)]
        base = dict(sql_query="", explanation="Qaysi davr?", data_count=0, execution_time_ms=0)
        answer = AsyncMock(side_effect=[
            NaturalQueryResponse(**base, success=False, error="clarification_required"),
            NaturalQueryResponse(**base, success=True),
            NaturalQueryResponse(**base, success=False, error="llm_rate_limited"),
        ])
        report = await GoldQueryEvaluator.evaluate(schema, GoldEvaluationRequest(cases=cases), answer=answer)
        self.assertEqual([r.outcome for r in report.results], ["correct", "wrong_answer", "error", "not_run"])
        self.assertEqual(answer.await_count, 3)

    def test_rank_and_column_meanings_survive_alias_comparison(self):
        rows = [{"branch": "A", "sales": 5, "cost": 3}, {"branch": "B", "sales": 4, "cost": 2}]
        renamed = [dict(zip(["filial", "sotuv", "tannarx"], row.values())) for row in rows]
        match = GoldQueryEvaluator._results_match
        self.assertTrue(match(renamed, rows, ordered=True))
        self.assertFalse(match(renamed[::-1], rows, ordered=True))
        self.assertTrue(match(renamed[::-1], rows, ordered=False))
        renamed[0].update(sotuv=3, tannarx=5)
        self.assertFalse(match(renamed, rows, ordered=False))

    def test_repository_gold_cases_are_select_only(self):
        payload = __import__("json").loads((BACKEND_ROOT / "gold_cases.json").read_text(encoding="utf-8"))
        self.assertTrue(payload.get("database_name"))
        self.assertTrue(payload.get("schema_fingerprint"))
        self.assertGreaterEqual(len(payload["cases"]), 8)
        for case in payload["cases"]:
            valid, error = validate_and_sanitize_sql(case["gold_sql"])
            self.assertTrue(valid, (case["name"], error))

    async def test_gold_suite_for_another_schema_is_rejected_before_queries(self):
        schema = SchemaContext(
            database_type="postgresql",
            database_name="database_b",
            schema_fingerprint="fingerprint_b",
            total_tables=1,
            schema_prompt="",
            tables=[TableMeta(name="facts", columns=[
                TableColumnMeta(name="id", data_type="integer")
            ])],
        )
        request = GoldEvaluationRequest(
            database_name="database_a",
            schema_fingerprint="fingerprint_a",
            cases=[GoldQueryCase(
                name="count",
                question="Jami nechta?",
                gold_sql="SELECT COUNT(*) FROM facts",
            )],
        )
        with self.assertRaises(ValueError):
            await GoldQueryEvaluator.evaluate(schema, request)

    async def test_gold_evaluation_compares_values_and_multiplicity(self):
        schema = SchemaContext(
            database_type="postgresql",
            database_name="generic",
            total_tables=1,
            schema_prompt="",
            tables=[TableMeta(name="facts", columns=[TableColumnMeta(name="id", data_type="integer")])],
        )
        generated = NaturalQueryResponse(
            sql_query="SELECT COUNT(*) AS total FROM facts",
            explanation="total: 2",
            rows=[{"total": 2}],
            columns=["total"],
            data_count=1,
            execution_time_ms=1,
            success=True,
        )
        request = GoldEvaluationRequest(cases=[
            GoldQueryCase(
                name="fact count",
                question="Jami nechta?",
                gold_sql="SELECT COUNT(*) AS total FROM facts",
            )
        ])
        with (
            patch("app.gold_evaluator.db_manager.execute_query", return_value=[{"total": 2}]),
            patch(
                "app.gold_evaluator.AIDataAnalystAgent.execute_analyst_agent",
                new=AsyncMock(return_value=generated),
            ),
        ):
            result = await GoldQueryEvaluator.evaluate(schema, request)
        self.assertEqual(1, result.passed)
        self.assertEqual(0, result.failed)


if __name__ == "__main__":
    unittest.main()
