"""Opt-in live acceptance for the connected database chatbot.

Run against an idle local development server; this temporarily changes its
global database connection. It refuses to touch a non-demo connection, uses
only a synthetic retail fixture, and restores the dealership demo in finally.
The --live-ai flag is mandatory because commands can make paid API calls.
Evidence contains check outcomes and synthetic reference totals, never server
error bodies, connection URLs, prompts, API credentials, or database rows.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import csv
from decimal import Decimal, InvalidOperation
import html
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


START, END = "2024-01-01", "2024-12-31"
METRIC = (
    "2024-01-01 dan 2024-12-31 gacha orders.order_date bo'yicha, "
    "faqat orders.status='completed' bo'lgan buyurtmalar uchun "
    "orders.total_amount yig'indisi"
)


class CheckFailure(Exception):
    """A controlled, safe-to-publish acceptance failure."""


def require(condition: Any, message: str) -> None:
    if not condition:
        raise CheckFailure(message)


def number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except (InvalidOperation, ValueError):
        return None


def create_fixture(path: Path) -> dict[str, Any]:
    with closing(sqlite3.connect(path)) as con:
        con.executescript("""
            PRAGMA foreign_keys=ON;
            CREATE TABLE customers (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL, city TEXT NOT NULL);
            CREATE TABLE products (
                id INTEGER PRIMARY KEY, name TEXT NOT NULL,
                category TEXT NOT NULL, unit_price INTEGER NOT NULL);
            CREATE TABLE orders (
                id INTEGER PRIMARY KEY,
                customer_id INTEGER NOT NULL REFERENCES customers(id),
                order_date DATE NOT NULL, status TEXT NOT NULL,
                total_amount INTEGER NOT NULL);
            CREATE TABLE order_items (
                id INTEGER PRIMARY KEY,
                order_id INTEGER NOT NULL REFERENCES orders(id),
                product_id INTEGER NOT NULL REFERENCES products(id),
                quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL);
        """)
        con.executemany("INSERT INTO customers VALUES (?,?,?)", [
            (1, "Retail Customer One", "Tashkent"),
            (2, "Retail Customer Two", "Samarkand"),
            (3, "Retail Customer Three", "Bukhara"),
        ])
        con.executemany("INSERT INTO products VALUES (?,?,?,?)", [
            (1, "Reading Lamp", "Lighting", 12500),
            (2, "Notebook", "Stationery", 5000),
            (3, "Storage Shelf", "Furniture", 22000),
            (4, "Coffee Mug", "Kitchen", 3500),
        ])
        con.executemany("INSERT INTO orders VALUES (?,?,?,?,?)", [
            (101, 1, "2024-01-15", "completed", 12500),
            (102, 2, "2024-01-25", "completed", 17000),
            (103, 1, "2024-02-10", "completed", 22000),
            (104, 3, "2024-02-20", "cancelled", 100000),
            (105, 3, "2024-03-12", "completed", 7000),
            (106, 2, "2023-12-30", "completed", 25000),
            (107, 1, "2024-03-15", "pending", 44000),
        ])
        con.executemany("INSERT INTO order_items VALUES (?,?,?,?,?)", [
            (1, 101, 1, 1, 12500), (2, 102, 2, 2, 5000),
            (3, 102, 4, 2, 3500), (4, 103, 3, 1, 22000),
            (5, 104, 2, 20, 5000), (6, 105, 4, 2, 3500),
            (7, 106, 1, 2, 12500), (8, 107, 3, 2, 22000),
        ])
        con.commit()
        where = "status='completed' AND order_date BETWEEN ? AND ?"
        total = con.execute(f"SELECT SUM(total_amount) FROM orders WHERE {where}", (START, END)).fetchone()[0]
        monthly = dict(con.execute(
            f"SELECT strftime('%Y-%m',order_date), SUM(total_amount) FROM orders WHERE {where} GROUP BY 1 ORDER BY 1",
            (START, END),
        ).fetchall())
        counts = {table: con.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
                  for table in ("customers", "products", "orders", "order_items")}
        require(not con.execute("PRAGMA foreign_key_check").fetchall(), "Fixture foreign keys are inconsistent")
        return {"total": total, "monthly": monthly, "table_rows": counts}


def json_response(response: httpx.Response) -> dict[str, Any]:
    require(response.status_code == 200, f"Unexpected HTTP status {response.status_code}")
    result = response.json()
    require(isinstance(result, dict), "Expected a JSON object")
    return result


def no_demo(value: Any) -> None:
    rendered = json.dumps(value, ensure_ascii=False).lower() if not isinstance(value, str) else value.lower()
    require(not any(word in rendered for word in ("atlas auto", "dealership", "synthetic demo")),
            "External result contains a dealership demo identity")


def replay_rows(path: Path, analysis: dict[str, Any]) -> None:
    """Check generated SQL against the isolated fixture, with read-only access."""
    query, actual = analysis.get("sql_query"), analysis.get("rows")
    require(isinstance(query, str) and bool(query.strip()), "Executed SQL is missing")
    require(isinstance(actual, list) and bool(actual), "Executed result rows are missing")
    permitted = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}

    def authorize(action: int, arg1: Any, arg2: Any, *_: Any) -> int:
        if action not in permitted:
            return sqlite3.SQLITE_DENY
        if action == sqlite3.SQLITE_FUNCTION and str(arg2 or arg1).lower() in {"load_extension", "readfile", "writefile"}:
            return sqlite3.SQLITE_DENY
        return sqlite3.SQLITE_OK

    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.set_authorizer(authorize)
        deadline = time.monotonic() + 5
        con.set_progress_handler(lambda: int(time.monotonic() > deadline), 1000)
        expected = [dict(row) for row in con.execute(query).fetchmany(1001)]
    require(len(expected) <= 1000, "Fixture query unexpectedly exceeded the result limit")
    require(expected == actual, "Returned rows differ from executing the supplied SQL on the fixture")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--live-ai", action="store_true", help="Explicitly allow paid AI calls and a temporary demo connection switch")
    parser.add_argument("--output", type=Path, help="Write sanitized JSON evidence to this path")
    args = parser.parse_args()
    if not args.live_ai:
        parser.error("--live-ai is required; no server was contacted")

    root = Path(__file__).resolve().parents[2]
    work = root / ".codex_run"
    output = args.output or work / "connected-workspace-acceptance.json"
    fixture = work / f"connected-retail-{uuid.uuid4().hex}.db"
    demo = root / "backend" / "data" / "dealership.db"
    evidence: dict[str, Any] = {
        "test": "connected workspace / synthetic retail SQLite",
        "live_ai": True, "switched": False, "restored": False,
        "checks": [], "passed": False,
    }
    started = time.monotonic()
    exit_code = 1

    def check(name: str, operation: Any) -> None:
        before = time.monotonic()
        entry: dict[str, Any] = {"name": name, "passed": False}
        try:
            details = operation()
            entry.update({"passed": True, "details": details or {}})
        except CheckFailure as exc:
            entry["failure"] = str(exc)
        except Exception as exc:
            # Deliberately omit exception text, response bodies and arbitrary SQL.
            entry["failure_type"] = type(exc).__name__
        entry["elapsed_seconds"] = round(time.monotonic() - before, 2)
        evidence["checks"].append(entry)
        print(f"{'PASS' if entry['passed'] else 'FAIL'} {name}", flush=True)

    try:
        with httpx.Client(base_url=args.base_url, timeout=30) as client:
            active = json_response(client.get("/api/platform/sources")).get("active_connection") or {}
            if active.get("is_demo") is not True:
                evidence["skipped"] = "Active connection is not the dealership demo; no connection changed"
                exit_code = 2
            else:
                require(demo.is_file(), "Demo database file is missing; a safe restoration is unavailable")
                work.mkdir(parents=True, exist_ok=True)
                reference = create_fixture(fixture)
                evidence["reference"] = reference
                state: dict[str, Any] = {}
                try:
                    # A timed-out request may still have changed server state.
                    evidence["switched"] = True
                    connected = json_response(client.post("/api/database/connect", json={"connection_url": f"sqlite:///{fixture.as_posix()}"}, timeout=90))
                    state["database_name"] = connected.get("database_name")

                    def understanding_check() -> dict[str, Any]:
                        require(connected.get("success") is True, "Connection did not report success")
                        require(Path(str(state["database_name"])).resolve() == fixture.resolve(), "Connection selected a database other than the fixture")
                        require(connected.get("dialect") == "sqlite", "Connection has the wrong database dialect")
                        require(connected.get("profile_complete") is True, "Fixture schema profile is incomplete")
                        summary = connected.get("understanding")
                        require(isinstance(summary, dict), "Connect response lacks automatic database understanding")
                        current = json_response(client.get("/api/database/understanding"))
                        for item in (summary, current):
                            require(item.get("database_name") == state["database_name"], "Understanding has the wrong source")
                            require(item.get("profile_complete") is True, "Understanding profile is incomplete")
                            require(item.get("table_count") == 4, "Understanding has the wrong table count")
                            require(item.get("column_count") == 17, "Understanding has the wrong column count")
                            require(item.get("relationship_count", 0) >= 3, "Understanding omitted declared relationships")
                            require(item.get("declared_relationship_count") == 3, "Understanding has the wrong declared relationship count")
                            require(item.get("read_only") is True, "Understanding does not declare read-only access")
                            tables = {table.get("name", "").split(".")[-1]: table for table in item.get("tables", [])}
                            require(set(tables) == set(reference["table_rows"]), "Understanding omitted or invented fixture tables")
                            for name, count in reference["table_rows"].items():
                                require(tables[name].get("row_count_estimate") == count, "Understanding row estimates disagree with fixture rows")
                            require(bool(item.get("schema_fingerprint")), "Understanding source fingerprint is missing")
                            require(bool(item.get("connection_fingerprint")), "Understanding connection fingerprint is missing")
                            no_demo(item)
                        require(summary["schema_fingerprint"] == current["schema_fingerprint"], "Understanding changed after connection")
                        require(summary["connection_fingerprint"] == current["connection_fingerprint"], "Understanding changed database connections")
                        state["schema_fingerprint"] = current["schema_fingerprint"]
                        state["connection_fingerprint"] = current["connection_fingerprint"]
                        return {"tables": 4, "columns": 17, "relationships": current["relationship_count"], "profile_complete": True}

                    check("automatic_database_understanding", understanding_check)

                    def source_check(source: Any) -> None:
                        require(isinstance(source, dict), "External source receipt is missing")
                        require(source.get("database_name") == state["database_name"], "External source receipt identifies another database")
                        require(source.get("synthetic") is False, "External source receipt is labelled as the demo")
                        require(bool(source.get("connection_fingerprint")), "External source connection fingerprint is missing")
                        require(bool(source.get("schema_fingerprint")), "External source schema fingerprint is missing")
                        if state.get("schema_fingerprint"):
                            require(source["schema_fingerprint"] == state["schema_fingerprint"], "Artifact schema differs from the connected schema")
                        if state.get("connection_fingerprint"):
                            require(source["connection_fingerprint"] == state["connection_fingerprint"], "Artifact belongs to a different database connection")
                        no_demo(source)

                    def command(prompt: str, kind: str, *, dated: bool = False) -> dict[str, Any]:
                        body: dict[str, Any] = {"source": "external", "prompt": prompt, "history": []}
                        if dated:
                            body.update(start_date=START, end_date=END)
                        result = json_response(client.post("/api/platform/command", json=body, timeout=300))
                        require(not any(step.get("status") in {"failed", "error"} for step in result.get("steps", [])), "Assistant reported a failed execution step")
                        matches = [item.get("data") for item in result.get("artifacts", []) if item.get("type") == kind]
                        require(len(matches) == 1 and isinstance(matches[0], dict), f"Expected exactly one {kind} artifact")
                        data = matches[0]
                        if kind != "calculation":
                            receipts = result.get("sources") or []
                            source_check(data.get("source") or (receipts[0] if receipts else None))
                            no_demo(data)
                        return data

                    def metric_check(data: dict[str, Any]) -> None:
                        analysis = data.get("analysis", data)
                        require(analysis.get("success") is True, "Analysis was not successful")
                        require(len(analysis.get("rows") or []) == 1, "Expected one aggregate metric row")
                        require(any(number(v) == reference["total"] for v in analysis["rows"][0].values()), "Completed 2024 revenue differs from independent SQL")
                        replay_rows(fixture, analysis)

                    def question_check() -> dict[str, Any]:
                        data = command(METRIC + " qancha? Bitta sonli natija qaytar.", "analysis")
                        metric_check(data)
                        return {"expected_total": reference["total"], "sql_replayed": True}

                    check("external_question_exact_revenue", question_check)

                    def specific_dashboard_check() -> dict[str, Any]:
                        data = command(METRIC + " asosida oylar kesimida dashboard yarat. Natija month (YYYY-MM) va revenue ustunlari bo'lsin.", "dashboard", dated=True)
                        require(data.get("kind") == "external", "Dashboard has the wrong kind")
                        analysis = data.get("analysis") or {}
                        require(analysis.get("success") is True, "Specific dashboard analysis failed")
                        replay_rows(fixture, analysis)
                        rows = analysis["rows"]
                        require(len(rows) == len(reference["monthly"]), "Dashboard did not preserve the requested monthly granularity")
                        for month, amount in reference["monthly"].items():
                            matching = [row for row in rows if any(str(value).startswith(month) for value in row.values())]
                            require(len(matching) == 1 and any(number(v) == amount for v in matching[0].values()), "Dashboard monthly totals differ from independent SQL")
                        require(bool(analysis.get("chart") or analysis.get("charts")), "Specific dashboard has no chart")
                        return {"months": len(rows), "total": reference["total"], "requested_start": START, "requested_end": END}

                    check("external_dashboard_2024_scope", specific_dashboard_check)

                    def generic_dashboard_check() -> dict[str, Any]:
                        data = command("Ulangan baza uchun umumiy dashboard yarat.", "dashboard")
                        dashboard = data.get("dashboard") or {}
                        require(data.get("kind") == "external" and dashboard.get("success") is True, "Generic dashboard failed")
                        charts, kpis = dashboard.get("charts") or [], dashboard.get("kpis") or []
                        require(bool(charts) and bool(kpis), "Generic dashboard has no charts or KPIs")
                        checked = 0
                        for chart in charts:
                            if chart.get("data"):
                                replay_rows(fixture, {"sql_query": chart.get("sql_query"), "rows": chart["data"]})
                                checked += 1
                        require(checked > 0, "Generic dashboard has no executed chart data")
                        return {"charts_with_verified_rows": checked, "kpis": len(kpis)}

                    check("external_generic_dashboard_actual_data", generic_dashboard_check)

                    def report_check() -> dict[str, Any]:
                        report = command(METRIC + " haqida hisobot tayyorla. Bitta umumiy revenue ko'rsatkichini hisobla.", "report", dated=True)
                        require(report.get("kind") == "external", "Report has the wrong kind")
                        metric_check(report)
                        report_id = report.get("id")
                        require(isinstance(report_id, str) and bool(re.fullmatch(r"[a-zA-Z0-9_-]+", report_id)), "Persistent report ID is missing or invalid")
                        saved = json_response(client.get(f"/api/platform/reports/{report_id}"))
                        require(saved.get("id") == report_id, "Report persistence returned another report")
                        source_check(saved.get("source"))
                        metric_check(saved)
                        require(saved.get("analysis") == report.get("analysis"), "Persisted report data changed")
                        exports = report.get("exports") or {}
                        export_contents: dict[str, str] = {}
                        for fmt in ("csv", "html"):
                            require(bool(exports.get(fmt)), "Report did not expose both export links")
                            download = client.get(f"/api/platform/reports/{report_id}/export", params={"format": fmt})
                            require(download.status_code == 200, f"{fmt.upper()} export failed")
                            content = download.text
                            no_demo(content)
                            require(str(saved["source"].get("name") or state["database_name"]) in html.unescape(content), "Export omitted the report source")
                            if fmt == "csv":
                                cells = [cell for row in csv.reader(io.StringIO(content.lstrip("\ufeff"))) for cell in row]
                            else:
                                cells = [html.unescape(cell).strip() for cell in re.findall(r">([^<>]+)<", content)]
                            require(any(number(cell.replace(",", "").replace("\u00a0", "").replace(" ", "")) == reference["total"] for cell in cells), "Export omitted the independently checked revenue")
                            export_contents[fmt] = content
                        state["report_id"] = report_id
                        state["report"] = saved
                        state["report_exports"] = export_contents
                        return {"persisted": True, "csv": True, "html": True, "expected_total": reference["total"]}

                    check("external_report_persistence_and_exports", report_check)

                    def calculator_check() -> dict[str, Any]:
                        data = command("Hisobla: (12500 + 7500) * 1.12", "calculation")
                        require(number(data.get("value")) == 22400, "Calculator returned an incorrect result")
                        return {"expected_value": 22400}

                    check("calculator_with_external_source", calculator_check)
                finally:
                    try:
                        restore = client.post("/api/database/connect", json={"connection_url": f"sqlite:///{demo.as_posix()}"}, timeout=90)
                        after = json_response(client.get("/api/platform/sources"))
                        evidence["restored"] = restore.status_code == 200 and (after.get("active_connection") or {}).get("is_demo") is True
                    except Exception as exc:
                        evidence["restore_failure_type"] = type(exc).__name__

                def saved_report_after_switch_check() -> dict[str, Any]:
                    require(evidence["restored"], "Demo restoration failed")
                    require(bool(state.get("report_id")), "No verified external report is available")
                    report_id = state["report_id"]
                    saved = json_response(client.get(f"/api/platform/reports/{report_id}"))
                    require(saved == state["report"], "Saved external report changed when the active source changed")
                    for fmt, previous in state["report_exports"].items():
                        download = client.get(f"/api/platform/reports/{report_id}/export", params={"format": fmt})
                        require(download.status_code == 200 and download.text == previous, "External export changed when the active source changed")
                    return {"saved_source_preserved": True, "csv_unchanged": True, "html_unchanged": True}

                check("saved_report_after_source_restore", saved_report_after_switch_check)
                # The command still requests source=external. Arithmetic must
                # work even though no external database is now connected.
                check("calculator_without_external_connection", calculator_check)
                evidence["passed"] = evidence["restored"] and len(evidence["checks"]) == 8 and all(item["passed"] for item in evidence["checks"])
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
