"""Live universal analyst check on an isolated synthetic SQLite fixture.

Changes the active connection only when it is initially the dealership demo.
Always attempts to restore that demo connection. Makes one paid AI question.
Never opens a user database or prints credentials/provider response bodies.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys
import time
import uuid

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--assistant", action="store_true", help="Exercise the unified assistant command endpoint")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    work = root / ".codex_run"
    work.mkdir(exist_ok=True)
    fixture = work / f"universal-fixture-{uuid.uuid4().hex}.db"
    demo = root / "backend" / "data" / "dealership.db"
    evidence = {"test": "live universal synthetic SQLite", "switched": False, "restored": False,
                "expected_total": 425000, "passed": False}
    started = time.monotonic()
    with httpx.Client(base_url=args.base_url, timeout=30) as client:
        response = client.get("/api/platform/sources")
        response.raise_for_status()
        if response.json().get("active_connection", {}).get("is_demo") is not True:
            print("SKIPPED: active source is not the demo; no connection changed.")
            return 2
        with closing(sqlite3.connect(fixture)) as con:
            con.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, order_date DATE NOT NULL, status TEXT NOT NULL, total_amount INTEGER NOT NULL)")
            con.executemany("INSERT INTO orders VALUES (?,?,?,?)", [
                (1, "2026-01-14", "completed", 100000),
                (2, "2026-03-20", "completed", 250000),
                (3, "2026-11-01", "completed", 75000),
                (4, "2026-05-10", "cancelled", 999999),
                (5, "2025-12-10", "completed", 50000),
            ])
            con.commit()
            expected = con.execute("SELECT SUM(total_amount) FROM orders WHERE status='completed' AND order_date BETWEEN '2026-01-01' AND '2026-12-31'").fetchone()[0]
        try:
            # Mark before the request: timeout may still leave a successful switch.
            evidence["switched"] = True
            connection = client.post("/api/database/connect", json={"connection_url": f"sqlite:///{fixture.as_posix()}"})
            evidence["connection_status"] = connection.status_code
            if connection.status_code != 200:
                raise AssertionError("Fixture connection failed")
            evidence["profile_complete"] = connection.json().get("profile_complete")
            prompt = "2026-01-01 dan 2026-12-31 gacha status='completed' bo'lgan orders jadvalidagi total_amount ustuni yig'indisi qancha?"
            endpoint = "/api/platform/command" if args.assistant else "/api/query/chat"
            payload = {"prompt": prompt, "history": []}
            if args.assistant:
                payload["source"] = "external"
            answer = client.post(endpoint, json=payload, timeout=150)
            evidence["query_http_status"] = answer.status_code
            if answer.status_code != 200:
                raise AssertionError("Universal analyst HTTP request failed")
            result = answer.json()
            if args.assistant:
                evidence["assistant_steps"] = result.get("steps", [])
                analyses = [item["data"] for item in result.get("artifacts", []) if item.get("type") == "analysis"]
                if not analyses:
                    raise AssertionError("Assistant did not return an external analysis artifact")
                result = analyses[0]
            evidence["query_success"] = result.get("success")
            evidence["error_code"] = result.get("error")
            evidence["response_type"] = result.get("response_type")
            evidence["generated_sql"] = result.get("sql_query")
            result_rows = result.get("rows") or []
            evidence["rows"] = result_rows
            evidence["validation_warnings"] = result.get("validation_warnings", [])
            values = list(result_rows[0].values()) if len(result_rows) == 1 else []
            evidence["passed"] = result.get("success") is True and any(value == expected for value in values)
        except Exception as exc:
            evidence["failure_type"] = type(exc).__name__
        finally:
            try:
                restored = client.post("/api/database/connect", json={"connection_url": f"sqlite:///{demo.as_posix()}"}, timeout=30)
                check = client.get("/api/platform/sources", timeout=15)
                evidence["restored"] = restored.status_code == 200 and check.status_code == 200 and check.json().get("active_connection", {}).get("is_demo") is True
            except Exception as exc:
                evidence["restore_failure_type"] = type(exc).__name__
            evidence["passed"] = evidence["passed"] and evidence["restored"]
    evidence["elapsed_seconds"] = round(time.monotonic() - started, 2)
    out = work / ("assistant-external-acceptance.json" if args.assistant else "universal-sqlite-acceptance.json")
    out.write_text(json.dumps(evidence, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(evidence, ensure_ascii=True, indent=2))
    return 0 if evidence["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
