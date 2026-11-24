"""Export contract checks for saved multi-query agent evidence."""
import copy
import csv
import io
import json
import sys
import unittest
from pathlib import Path

from openpyxl import load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.report_exports import export_connected_report


def report_fixture():
    first = {
        "id": "q1", "title": "Sotuv / hududlar: [2024] * natijalar juda uzun nom",
        "purpose": "Sotuv summasini hisoblash", "sql_query": "SELECT region, revenue, missing FROM revenue",
        "columns": ["region", "revenue", "missing"],
        "rows": [{"region": "=1+1", "revenue": 58500, "missing": None},
                 {"region": "<img src=x onerror=alert(1)>", "revenue": -250.5, "missing": None}],
        "returned_count": 2, "is_truncated": False, "execution_time_ms": 1.25,
    }
    second = {
        "id": "q2", "title": first["title"], "purpose": "Buyurtma sonlarini tekshirish",
        "sql_query": "SELECT month, units FROM orders", "columns": ["month", "units"],
        "rows": [{"month": "2024-01", "units": 12}, {"month": "2024-02", "units": 7}],
        "returned_count": 2, "is_truncated": True, "execution_time_ms": 2.5,
    }
    analysis = {key: copy.deepcopy(first[key]) for key in ("sql_query", "columns", "rows", "returned_count", "is_truncated")}
    analysis.update({
        "success": True, "agent_status": "completed", "summary_text": "Ikki manba tekshirildi.",
        "explanation": "<script>not executable</script>", "validation_warnings": ["Cheklangan namunada tekshirildi"],
        "query_results": [first, second, copy.deepcopy(first)],
        "charts": [{"id": "c1", "title": "Hududlar", "sql_query": first["sql_query"], "data": copy.deepcopy(first["rows"])},
                   {"id": "c2", "query_id": "q2", "title": "Oylar", "data": copy.deepcopy(second["rows"])}],
    })
    return {"id": "report-123", "kind": "external", "title": "Savdo <hisoboti>",
            "created_at": "2026-11-12T12:00:00Z", "prompt": "Savdoni tahlil qil va hisobot yarat",
            "source": {"name": "Retail <A>", "database_name": "retail", "dialect": "sqlite",
                       "connection_fingerprint": "connection-A", "schema_fingerprint": "schema-A"},
            "analysis": analysis}


class AgentExportsTests(unittest.TestCase):
    def test_csv_contains_each_executed_result_once_and_marks_partial_evidence(self):
        report = report_fixture()
        content, content_type = export_connected_report(report, "csv")
        rows = list(csv.reader(io.StringIO(content.lstrip("\ufeff"))))
        self.assertEqual(rows.count(["'=1+1", "58500", ""]), 1)
        self.assertEqual(rows.count(["2024-01", "12"]), 1)
        self.assertIn(["Natija bo'limlari", "2"], rows)
        self.assertIn(["Natija cheklangan", "Ha"], rows)
        self.assertIn(["Natija ID", "q1"], rows)
        self.assertIn(["Natija ID", "q2"], rows)
        self.assertIn(["Ulanish identifikatori", "connection-A"], rows)
        self.assertIn("to'liq baza eksporti emas", content)
        self.assertTrue(content_type.startswith("text/csv"))

    def test_html_keeps_narrative_source_and_all_evidence_without_executable_cells(self):
        content, content_type = export_connected_report(report_fixture(), "html")
        self.assertEqual(content.count("<section>"), 2)
        for value in ("58500", "-250.5", "2024-01", "connection-A", "schema-A", "Ikki manba tekshirildi."):
            self.assertIn(value, content)
        self.assertIn("Retail &lt;A&gt;", content)
        self.assertNotIn("<script>", content)
        self.assertNotIn("<img ", content)
        self.assertIn("&lt;script&gt;", content)
        self.assertTrue(content_type.startswith("text/html"))

    def test_xlsx_has_real_numeric_cells_all_queries_safe_unique_sheet_names_and_filters(self):
        content, content_type = export_connected_report(report_fixture(), "xlsx")
        self.assertIsInstance(content, bytes)
        self.assertTrue(content_type.endswith("spreadsheetml.sheet"))
        book = load_workbook(io.BytesIO(content), data_only=False)
        self.assertEqual(len(book.worksheets), 3)
        self.assertEqual(book.sheetnames[0], "Hisobot")
        self.assertEqual(len(set(name.casefold() for name in book.sheetnames)), 3)
        for name in book.sheetnames:
            self.assertLessEqual(len(name), 31)
            self.assertFalse(set("[]:*?/\\") & set(name))
        first, second = book.worksheets[1:]
        self.assertEqual(first["A9"].value, "=1+1")
        self.assertEqual(first["A9"].data_type, "s")
        self.assertEqual(first["B9"].value, 58500)
        self.assertEqual(first["B9"].data_type, "n")
        self.assertEqual(first["B10"].value, -250.5)
        self.assertIsNone(first["C9"].value)
        self.assertEqual(second["B9"].value, 12)
        self.assertEqual(first.freeze_panes, "A9")
        self.assertEqual(first.auto_filter.ref, "A8:C10")
        self.assertEqual(first["A8"].fill.fgColor.rgb, "00245BD9")
        self.assertTrue(all(cell.data_type != "f" for sheet in book for row in sheet for cell in row))
        summary = dict(book.worksheets[0].iter_rows(min_row=2, values_only=True))
        self.assertEqual(summary["Manba"], "Retail <A>")
        self.assertEqual(summary["Natija cheklangan"], "Ha")

    def test_xlsx_preserves_long_integer_precision_and_control_characters_explicitly(self):
        report = report_fixture()
        report["analysis"]["query_results"][0]["rows"][0].update({"region": "A\x00B", "revenue": 1234567890123456789})
        # Select evidence only so the intentionally different old main view does not add another section.
        report["analysis"].update(sql_query="", columns=[], rows=[], charts=[])
        content, _ = export_connected_report(report, "xlsx")
        book = load_workbook(io.BytesIO(content))
        first = book.worksheets[1]
        self.assertEqual(first["A9"].value, "A\\u0000B")
        self.assertEqual(first["B9"].value, "1234567890123456789")
        notes = [str(cell.value) for row in book.worksheets[0] for cell in row]
        self.assertTrue(any("aniqlikni saqlash" in note for note in notes))
        self.assertTrue(any("boshqaruv belgilar" in note for note in notes))

    def test_xlsx_rejects_overlong_text_instead_of_silently_truncating(self):
        report = report_fixture()
        report["analysis"]["query_results"][0]["rows"][0]["region"] = "x" * 32768
        with self.assertRaisesRegex(ValueError, "32767"):
            export_connected_report(report, "xlsx")

    def test_json_is_exact_saved_snapshot_and_legacy_reports_still_export(self):
        report = report_fixture()
        content, content_type = export_connected_report(report, "json")
        self.assertEqual(json.loads(content), report)
        self.assertTrue(content_type.startswith("application/json"))
        report["analysis"].pop("query_results")
        for fmt in ("csv", "html", "xlsx", "json"):
            content, _ = export_connected_report(report, fmt)
            self.assertTrue(content)
        with self.assertRaises(ValueError):
            export_connected_report(report, "pdf")


if __name__ == "__main__":
    unittest.main()
