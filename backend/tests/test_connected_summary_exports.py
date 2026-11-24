import csv
import io
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import platform_service as service
from app.database_understanding import build_understanding
from app.models import SchemaContext, TableMeta, TableColumnMeta, TableRelationship
from app.platform_api import CommandBody


class UnderstandingTests(unittest.TestCase):
    def test_counts_real_schema_without_sample_rows_or_credentials(self):
        schema = SchemaContext(database_type="sqlite", database_name="shop", total_tables=2, schema_prompt="hidden",
            connection_fingerprint="source-a", schema_fingerprint="schema-a",
            tables=[TableMeta(name="customers", row_count=8, columns=[TableColumnMeta(name="id", data_type="INTEGER")],
                              sample_rows=[{"secret": "not-for-summary"}]),
                    TableMeta(name="orders", row_count=20, columns=[TableColumnMeta(name="customer_id", data_type="INTEGER")])],
            relationships=[TableRelationship(from_table="orders", from_column="customer_id", to_table="customers",
                                             to_column="id", join_hint="", source="foreign_key")])
        result = build_understanding(schema)
        self.assertEqual((result["table_count"], result["column_count"], result["relationship_count"]), (2, 2, 1))
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["tables"][0]["row_count_estimate"], 8)
        self.assertEqual(result["connection_fingerprint"], "source-a")
        self.assertNotIn("not-for-summary", str(result))
        self.assertNotIn("schema_prompt", result)
        self.assertIn('"customers"', result["suggested_questions"][0])

    def test_empty_and_incomplete_profiles_never_claim_ready(self):
        schema = SchemaContext(database_type="sqlite", total_tables=0, tables=[], schema_prompt="")
        self.assertEqual(build_understanding(schema)["status"], "empty")
        schema.profile_complete = False
        schema.profile_warnings = ["Some tables could not be inspected"]
        result = build_understanding(schema)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["warnings"], schema.profile_warnings)
        self.assertEqual(result["suggested_questions"], [])

    def test_large_schema_summary_is_bounded_but_counts_remain_complete(self):
        columns = [TableColumnMeta(name=f"c{i}", data_type="TEXT") for i in range(35)]
        schema = SchemaContext(database_type="sqlite", total_tables=101,
                               tables=[TableMeta(name=f"t{i}", columns=columns) for i in range(101)], schema_prompt="")
        result = build_understanding(schema)
        self.assertEqual(result["column_count"], 3535)
        self.assertEqual(len(result["tables"]), 100)
        self.assertTrue(result["tables_truncated"])
        self.assertTrue(result["tables"][0]["columns_truncated"])


class ConnectedExportTests(unittest.TestCase):
    def test_saved_external_report_exports_actual_rows_with_safe_strings(self):
        report = {"kind": "external", "title": "Retail <script>alert(1)</script>", "prompt": "2024 sales",
                  "source": {"name": "Retail 2024", "dialect": "sqlite", "connection_fingerprint": "retail-a"},
                  "analysis": {"columns": ["customer", "amount"],
                               "rows": [{"customer": "=1+1", "amount": -250}, {"customer": "<img src=x onerror=alert(1)>", "amount": 58500}],
                               "explanation": "<script>bad</script>", "sql_query": "SELECT customer, amount FROM orders",
                               "is_truncated": True, "charts": [{"title": "Monthly", "data": [{"month": "2024-01", "total": 29500}]}]}}
        with tempfile.TemporaryDirectory() as folder, patch.object(service, "STATE_PATH", Path(folder) / "state.db"):
            saved = service.save_artifact("report", report)
            csv_content, mime = service.export_report(saved["id"], "csv")
            rows = list(csv.reader(io.StringIO(csv_content.lstrip("\ufeff"))))
            self.assertIn(["'=1+1", "-250"], rows)
            self.assertIn(["2024-01", "29500"], rows)
            self.assertIn(["Manba", "Retail 2024"], rows)
            self.assertIn(["Natija cheklangan", "Ha"], rows)
            self.assertTrue(mime.startswith("text/csv"))
            html_content, _ = service.export_report(saved["id"], "html")
            self.assertIn("&lt;script&gt;", html_content)
            self.assertNotIn("<script>", html_content)
            self.assertNotIn("<img", html_content)
            self.assertNotIn("Atlas", html_content + csv_content)
            self.assertIn("58500", html_content)
            self.assertIn("29500", html_content)
            self.assertIn("retail-a", html_content)
            with self.assertRaises(ValueError):
                service.export_report(saved["id"], "pdf")


class CommandScopeTests(unittest.TestCase):
    def test_external_dates_have_no_demo_year_limit_or_implicit_dates(self):
        body = CommandBody(prompt="sales", source="external")
        self.assertIsNone(body.filters().start_date)
        dates = CommandBody(prompt="sales", source="external", start_date="2024-01-01", end_date="2024-12-31").filters()
        self.assertEqual(dates.start_date, "2024-01-01")
        with self.assertRaises(ValueError):
            CommandBody(prompt="sales", source="external", start_date="2024-12-31", end_date="2024-01-01").filters()
        with self.assertRaises(ValueError):
            CommandBody(prompt="sales", source="external", start_date="2024-02-30").filters()
        self.assertEqual(CommandBody(prompt="sales").filters().start_date, "2026-01-01")


if __name__ == "__main__":
    unittest.main()
