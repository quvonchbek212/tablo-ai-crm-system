"""Opt-in live acceptance for iterative data investigation and file exports.

This temporarily switches an idle local server from its dealership demo to a
small, synthetic retail database. It refuses an already-external source and
always attempts to restore the demo. --live-ai is required for paid model calls.
Only sanitized check summaries and known fixture reference totals are written.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
from html.parser import HTMLParser
import io
import json
from pathlib import Path
import re
import sqlite3
import sys
import time
from typing import Any
import uuid

import httpx
from openpyxl import load_workbook

from check_connected_workspace import CheckFailure, create_fixture, json_response, no_demo, number, require


INVESTIGATION = (
    "2024-yil yanvar-mart oralig'ida sotuvlar nega kamayganini tekshir. "
    "Faqat completed buyurtmalarni hisobga ol. Oylik jami sotuv, mahsulotlar "
    "va mijozlar kesimlarini alohida tekshir; jami summalarni solishtirib tasdiqla. "
    "Dalillarga tayangan xulosa va amaliy tavsiyalar ber. Bazada bo'lmagan tashqi "
    "sabablarni tasdiqlangan fakt sifatida yozma."
)


class HTMLTables(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.row: list[str] | None = None
        self.cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self.row = []
        elif tag in {"td", "th"} and self.row is not None:
            self.cell = []

    def handle_endtag(self, tag):
        if tag in {"td", "th"} and self.cell is not None and self.row is not None:
            self.row.append("".join(self.cell))
            self.cell = None
        elif tag == "tr" and self.row is not None:
            self.rows.append(self.row)
            self.row = None

    def handle_data(self, data):
        if self.cell is not None:
            self.cell.append(data)


def normalize(value):
    if value is None or value == "":
        return None
    numeric = number(value)
    return numeric if numeric is not None else str(value).strip()


def replay(path: Path, query: dict[str, Any]) -> None:
    sql, rows = query.get("sql_query"), query.get("rows")
    require(isinstance(sql, str) and sql.strip(), "Evidence lacks executed SQL")
    require(isinstance(rows, list), "Evidence rows are missing")
    allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

    def authorize(action, arg1, arg2, *_):
        if action not in allowed:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and str(arg2 or arg1).lower() in {"load_extension", "readfile", "writefile"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.set_authorizer(authorize)
        deadline = time.monotonic() + 5
        con.set_progress_handler(lambda: int(time.monotonic() >= deadline), 1000)
        actual = [dict(row) for row in con.execute(sql).fetchmany(501)]
    require(actual[:500] == rows, "Evidence differs from independent read-only SQL replay")
    require(query.get("returned_count", len(rows)) == len(rows), "Evidence row count is incorrect")
    require(bool(query.get("is_truncated")) == (len(actual) > 500), "Evidence truncation is incorrect")


def workbook_values(content: bytes) -> list[list[Any]]:
    workbook = load_workbook(io.BytesIO(content), data_only=False, read_only=True)
    try:
        require(len(workbook.sheetnames) >= 3, "Excel export lacks summary and separate evidence sheets")
        rows = []
        for sheet in workbook:
            for cells in sheet.iter_rows():
                require(not any(cell.data_type == "f" for cell in cells), "Excel export contains executable formulas")
                rows.append([cell.value for cell in cells])
        return rows
    finally:
        workbook.close()


def require_export_rows(exported: list[list[Any]], queries: list[dict[str, Any]], label: str) -> None:
    normalized = [list(map(normalize, row)) for row in exported]
    for query in queries:
        columns = query.get("columns") or list((query.get("rows") or [{}])[0])
        for row in query.get("rows") or []:
            values = [normalize(row.get(column)) for column in columns]
            require(any(candidate[:len(values)] == values for candidate in normalized),
                    f"{label} export omitted or changed a recorded evidence row")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--live-ai", action="store_true", help="Allow paid AI calls and a temporary synthetic source switch")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live_ai:
        parser.error("--live-ai is required; no server was contacted")

    root = Path(__file__).resolve().parents[2]
    work = root / ".codex_run"
    fixture = work / f"agent-retail-{uuid.uuid4().hex}.db"
    demo = root / "backend/data/dealership.db"
    output = args.output or work / "agent-investigation-live.json"
    evidence = {"test": "iterative data agent / synthetic retail", "live_ai": True,
                "switched": False, "restored": False, "checks": [], "passed": False}
    state: dict[str, Any] = {}
    started = time.monotonic()
    exit_code = 1

    def check(name, operation):
        before = time.monotonic()
        item = {"name": name, "passed": False}
        try:
            item.update(passed=True, details=operation() or {})
        except CheckFailure as exc:
            item.update(passed=False, failure=str(exc))
        except Exception as exc:
            item.update(passed=False, failure_type=type(exc).__name__)
        item["elapsed_seconds"] = round(time.monotonic() - before, 2)
        evidence["checks"].append(item)
        print(f"{'PASS' if item['passed'] else 'FAIL'} {name}", flush=True)

    try:
        with httpx.Client(base_url=args.base_url, timeout=httpx.Timeout(300, connect=15)) as client:
            active = json_response(client.get("/api/platform/sources")).get("active_connection") or {}
            if active.get("is_demo") is not True:
                evidence["skipped"] = "Active connection is external; no connection changed"
                exit_code = 2
            else:
                require(demo.is_file(), "Demo database required for restoration is missing")
                work.mkdir(parents=True, exist_ok=True)
                reference = create_fixture(fixture)
                evidence["reference"] = {"total": reference["total"], "monthly": reference["monthly"]}
                try:
                    evidence["switched"] = True
                    connected = json_response(client.post("/api/database/connect", json={"connection_url": f"sqlite:///{fixture.as_posix()}"}, timeout=90))
                    profile = connected.get("understanding") or {}
                    require(connected.get("success") and profile.get("profile_complete"), "Fixture could not be profiled completely")
                    require(Path(str(connected.get("database_name"))).resolve() == fixture.resolve(), "Wrong source was connected")
                    fingerprint = connected.get("connection_fingerprint")
                    require(isinstance(fingerprint, str) and fingerprint, "Expected connection identity is missing")
                    state.update(fingerprint=fingerprint, schema=profile.get("schema_fingerprint"), database_name=connected["database_name"])

                    def source_check(source):
                        require(isinstance(source, dict), "Source receipt is missing")
                        require(source.get("connection_fingerprint") == fingerprint, "Result connection identity changed")
                        require(source.get("schema_fingerprint") == state["schema"], "Result schema identity changed")
                        require(source.get("database_name") == state["database_name"], "Result belongs to another database")
                        require(source.get("synthetic") is False, "External fixture was substituted by the dealership demo")

                    def analysis_check(analysis):
                        require(isinstance(analysis, dict) and analysis.get("success"), "Investigation did not return an executed result")
                        require(analysis.get("agent_status") == "completed", "Investigation did not finish within its bounds")
                        queries = analysis.get("query_results") or []
                        require(len(queries) >= 2, "Agent returned fewer than two investigative query results")
                        require(len({query.get("id") for query in queries}) == len(queries), "Query evidence IDs are not unique")
                        require(len({query.get("sql_query") for query in queries}) >= 2, "Investigation repeated one SQL query")
                        for query in queries:
                            replay(fixture, query)
                        trace = analysis.get("agent_trace") or []
                        executions = [step for step in trace if step.get("tool") == "query_data" and step.get("status") == "completed"]
                        require(len(executions) >= 2, "Actual executed query trace is missing")
                        require(any(step.get("tool") == "verify_answer" and step.get("status") == "completed" for step in trace),
                                "Final answer did not pass a recorded evidence review")
                        answer = analysis.get("explanation") or analysis.get("summary_text") or ""
                        require(isinstance(answer, str) and len(answer.strip()) >= 100, "Evidence-backed narrative is missing")
                        no_demo(analysis)
                        # The fixture's three monthly values must appear in one
                        # actual returned result, not only in generated prose.
                        monthly_found = False
                        for query in queries:
                            rows = query.get("rows") or []
                            matched = {}
                            for row in rows:
                                months = [month for month in reference["monthly"] if any(str(value).startswith(month) for value in row.values())]
                                if not months:
                                    for column, value in row.items():
                                        if any(token in column.casefold() for token in ("month", "oy")) and number(value) in {1, 2, 3}:
                                            months.append(f"2024-{int(number(value)):02d}")
                                for month in months:
                                    if any(number(value) == number(reference["monthly"][month]) for value in row.values()):
                                        matched[month] = reference["monthly"][month]
                            monthly_found |= matched == reference["monthly"]
                        require(monthly_found, "Investigation did not reconcile the known monthly decline")
                        sql_context = " ".join(query["sql_query"] for query in queries).lower()
                        require(any(token in sql_context for token in ("products", "product_id"))
                                and any(token in sql_context for token in ("customers", "customer_id")),
                                "Investigation omitted requested product or customer evidence")
                        charts = analysis.get("charts") or []
                        by_id = {query["id"]: query for query in queries}
                        for chart in charts:
                            linked = by_id.get(chart.get("query_id"))
                            require(linked is not None, "Chart lacks a recorded query reference")
                            require(chart.get("data") == linked.get("rows"), "Chart data differs from its recorded query")
                            for axis in (chart.get("x_axis"), chart.get("y_axis")):
                                require(axis is None or axis in (linked.get("columns") or []), "Chart axis references an absent column")
                        return {"queries": len(queries), "query_steps": len(executions), "charts": len(charts), "monthly_reconciled": True}

                    def command(prompt, kind):
                        response = client.post("/api/platform/command", json={"source": "external", "prompt": prompt,
                                               "expected_connection_fingerprint": fingerprint})
                        result = json_response(response)
                        if not all(step.get("status") == "completed" for step in result.get("steps") or []):
                            diagnostics = {"steps": result.get("steps"), "answer": result.get("answer", "")[:500],
                                "analysis": [{"status": item.get("data", {}).get("analysis", {}).get("agent_status"),
                                              "error": item.get("data", {}).get("analysis", {}).get("error"),
                                              "trace": item.get("data", {}).get("analysis", {}).get("agent_trace", [])[-5:]}
                                             for item in result.get("artifacts", [])]}
                            raise CheckFailure("Command incomplete: " + json.dumps(diagnostics, ensure_ascii=True))
                        artifact = next((item for item in result.get("artifacts") or [] if item.get("type") == kind), None)
                        require(artifact is not None, f"Requested {kind} artifact is missing")
                        data = artifact.get("data") or {}
                        source_check(data.get("source"))
                        return data

                    def stale_source_check():
                        response = client.post("/api/platform/command", json={"source": "external", "prompt": "Sotuvlarni tekshir",
                                               "expected_connection_fingerprint": "different-source"})
                        require(response.status_code == 400, "Stale conversation source was not rejected")
                        require(not response.json().get("artifacts"), "Stale source returned artifacts")
                        return {"stale_source_rejected": True}

                    check("stale_source_is_rejected", stale_source_check)

                    def dashboard_check():
                        dashboard = command(INVESTIGATION + " Natijalarni grafiklari bilan dashboard qilib tayyorla.", "dashboard")
                        analysis = dashboard.get("analysis") or {}
                        details = analysis_check(analysis)
                        require(details["charts"] >= 1, "Dashboard did not include any recorded-result chart")
                        state["dashboard"] = dashboard
                        return details

                    check("iterative_investigation_dashboard", dashboard_check)

                    def report_check():
                        report = command(INVESTIGATION + " To'liq tekshiruv natijasidan yuklab olinadigan hisobot tayyorla.", "report")
                        details = analysis_check(report.get("analysis") or {})
                        require(isinstance(report.get("id"), str) and re.fullmatch(r"[A-Za-z0-9_-]+", report["id"]), "Saved report ID is missing")
                        saved = json_response(client.get(f"/api/platform/reports/{report['id']}"))
                        source_check(saved.get("source"))
                        require(saved.get("analysis") == report["analysis"], "Persisted report altered the evidence")
                        state["report"] = saved
                        state["export_links"] = report.get("exports") or {}
                        return {**details, "persisted": True}

                    check("iterative_investigation_report", report_check)

                    def exports_check():
                        saved = state.get("report")
                        require(saved is not None, "A verified persisted report is required for export checks")
                        require(set(state["export_links"]) >= {"csv", "html", "xlsx", "json"}, "Four report export links are not available")
                        queries = saved["analysis"]["query_results"]
                        downloads = {}
                        for fmt in ("csv", "html", "xlsx", "json"):
                            response = client.get(f"/api/platform/reports/{saved['id']}/export", params={"format": fmt})
                            require(response.status_code == 200, f"{fmt.upper()} download failed")
                            require(f".{fmt}" in response.headers.get("content-disposition", ""), "Export filename has the wrong extension")
                            downloads[fmt] = response.content
                            if fmt == "json":
                                exported = response.json()
                                require(exported.get("analysis") == saved["analysis"], "JSON export changed recorded evidence")
                                source_check(exported.get("source"))
                            elif fmt == "csv":
                                rows = list(csv.reader(io.StringIO(response.text.lstrip("\ufeff"))))
                                require_export_rows(rows, queries, "CSV")
                                no_demo(response.text)
                            elif fmt == "html":
                                document = HTMLTables()
                                document.feed(response.text)
                                require_export_rows(document.rows, queries, "HTML")
                                no_demo(response.text)
                            else:
                                rows = workbook_values(response.content)
                                require_export_rows(rows, queries, "Excel")
                                require(any(fingerprint in str(value) for row in rows for value in row), "Excel export omitted source provenance")
                                state["xlsx_rows"] = rows
                        state["downloads"] = downloads
                        return {"csv": True, "html": True, "xlsx": True, "json": True, "evidence_queries": len(queries)}

                    check("all_report_formats_preserve_evidence", exports_check)
                finally:
                    try:
                        restored = client.post("/api/database/connect", json={"connection_url": f"sqlite:///{demo.as_posix()}"}, timeout=90)
                        current = json_response(client.get("/api/platform/sources")).get("active_connection") or {}
                        evidence["restored"] = restored.status_code == 200 and current.get("is_demo") is True
                    except Exception as exc:
                        evidence["restore_failure_type"] = type(exc).__name__

                def persisted_after_switch():
                    require(evidence["restored"], "Demo restoration failed")
                    saved = state.get("report")
                    require(saved is not None and state.get("downloads"), "Verified report downloads are unavailable")
                    current = json_response(client.get(f"/api/platform/reports/{saved['id']}"))
                    require(current == saved, "Persisted report changed after switching active source")
                    for fmt, previous in state["downloads"].items():
                        response = client.get(f"/api/platform/reports/{saved['id']}/export", params={"format": fmt})
                        require(response.status_code == 200, "Stored report export failed after source switch")
                        if fmt == "xlsx":
                            require(workbook_values(response.content) == state["xlsx_rows"], "Excel content changed after source switch")
                        else:
                            require(response.content == previous, f"{fmt.upper()} content changed after source switch")
                    return {"source_and_evidence_preserved": True, "all_exports_preserved": True}

                check("persisted_report_after_source_restore", persisted_after_switch)
                evidence["passed"] = evidence["restored"] and len(evidence["checks"]) == 5 and all(check["passed"] for check in evidence["checks"])
                exit_code = 0 if evidence["passed"] else 1
    except CheckFailure as exc:
        evidence["failure"] = str(exc)
    except Exception as exc:
        evidence["failure_type"] = type(exc).__name__
    evidence["elapsed_seconds"] = round(time.monotonic() - started, 2)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=True, indent=2))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
