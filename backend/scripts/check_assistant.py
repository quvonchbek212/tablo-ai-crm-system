"""Exercise the chat command interface against the running local demo server.

Requires --live-ai because commands can call OpenAI. Creates demo report/agent
artifacts, but never writes to the business database or switches its connection.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

import httpx


def require(condition, message):
    if not condition:
        raise AssertionError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8015")
    parser.add_argument("--live-ai", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not args.live_ai:
        parser.error("Pass --live-ai to enable real model requests and demo artifact creation.")
    checks = []
    db = Path(__file__).resolve().parents[1] / "data" / "dealership.db"

    def scalar(sql):
        with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as connection:
            return connection.execute(sql).fetchone()[0]

    expected_revenue = scalar("SELECT SUM(sale_price) FROM sales WHERE status='completed' AND sale_date BETWEEN '2026-10-01' AND '2026-10-31'")
    expected_receipts = scalar("SELECT SUM(p.amount) FROM payments p JOIN sales s ON s.id=p.sale_id WHERE s.status='completed' AND p.payment_date BETWEEN '2026-10-01' AND '2026-10-31'")
    sales_count = scalar("SELECT COUNT(*) FROM sales")

    def record(name, operation):
        try:
            details = operation() or {}
            checks.append({"name": name, "passed": True, **details})
            print("PASS:", name, flush=True)
        except Exception as error:
            reason = str(error) if isinstance(error, AssertionError) else type(error).__name__
            checks.append({"name": name, "passed": False, "reason": reason})
            print("FAIL:", name, "-", reason, flush=True)

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=180) as client:
        def command(prompt, history=None):
            response = client.post("/api/platform/command", json={"prompt": prompt, "source": "demo", "history": history or []})
            require(response.status_code == 200, f"Command HTTP {response.status_code}")
            data = response.json()
            require(bool(data.get("answer", "").strip()), "Empty command answer")
            require(isinstance(data.get("artifacts"), list), "Missing artifact list")
            require(isinstance(data.get("steps"), list), "Missing execution trace")
            return data

        def artifact(data, kind):
            found = next((item for item in data["artifacts"] if item["type"] == kind), None)
            require(found is not None, f"Missing {kind} artifact")
            return found["data"]

        def october(data):
            require(data["filters"]["start_date"] == "2026-10-01", "Requested October start was ignored")
            require(data["filters"]["end_date"] == "2026-10-31", "Requested October end was ignored")

        def calculation():
            data = artifact(command("1250000 * 12 + 500000 ni hisobla"), "calculation")
            require(float(data["value"]) == 15500000, "Calculator result mismatch")
            return {"result": data["value"]}

        def dashboard():
            data = artifact(command("2026-yil oktabr uchun sotuv dashboardini yarat."), "dashboard")
            october(data)
            revenue = next(item["value"] for item in data["kpis"] if item["key"] == "revenue")
            require(revenue == expected_revenue, "Dashboard differs from reference SQL")
            require(sum(row["revenue"] for row in data["trend"]) == revenue, "Chart does not reconcile")
            return {"revenue": revenue}

        def report():
            data = artifact(command("2026-yil oktabr uchun sotuv hisobotini yarat va saqla."), "report")
            october(data)
            require(data["summary"]["revenue"] == expected_revenue, "Report differs from reference SQL")
            stored = client.get("/api/platform/reports/" + data["id"])
            require(stored.status_code == 200, "Report was not persisted")
            for fmt in ("csv", "html"):
                response = client.get(f"/api/platform/reports/{data['id']}/export", params={"format": fmt})
                require(response.status_code == 200 and len(response.content) > 300, f"Report {fmt} export failed")
            return {"saved": True, "exported": ["csv", "html"]}

        def agent():
            data = artifact(command("Omborni tekshir va muammolarni top."), "agent")
            require(bool(data.get("id")), "Agent run has no persisted ID")
            require(data.get("agent_id") == "inventory", "Warehouse request was sent to a different agent")
            require(isinstance(data.get("findings"), list), "Missing agent findings")
            return {"findings": len(data["findings"])}

        def receipts():
            data = artifact(command("2026-yil oktabr oyida jami pul tushumi qancha?"), "analysis")
            analysis = next((item for item in data.get("analyses", []) if item["operation"] == "payments" and item["metric"] == "receipts"), None)
            require(analysis is not None, "Cash receipts were confused with sales")
            require(analysis["rows"][0]["value"] == expected_receipts, "Receipts differ from reference SQL")
            return {"receipts": expected_receipts}

        def conversation():
            data = command("Salom! Sen nimalar qila olasan?")
            require(len(data["answer"]) > 30, "Conversation answer too short")
            require(not data["artifacts"], "Greeting unexpectedly executed a business command")

        def multi_action():
            data = command("2026-yil oktabr sotuv dashboardini yarat va shu davr hisobotini saqla.")
            october(artifact(data, "dashboard"))
            october(artifact(data, "report"))
            return {"artifact_types": [item["type"] for item in data["artifacts"]]}

        def followup():
            prompt = "2025-yil uchun sotuv dashboardini yarat."
            first = command(prompt)
            history = [{"role": "user", "content": prompt}, {"role": "assistant", "content": first["answer"]}]
            result = artifact(command("Shu davr uchun hisobot ham tayyorla.", history), "report")
            require(result["filters"]["start_date"] == "2025-01-01", "Follow-up lost the previous period start")
            require(result["filters"]["end_date"] == "2025-12-31", "Follow-up lost the previous period end")
            return {"filters": result["filters"]}

        def unsupported():
            data = command("Bazadagi barcha sotuvlarni o'chir.")
            require(not data["artifacts"], "Unsupported database mutation returned a completed artifact")
            require(scalar("SELECT COUNT(*) FROM sales") == sales_count, "Business rows changed")

        for name, operation in [("calculator", calculation), ("chat dashboard", dashboard),
                                ("saved report and exports", report), ("agent dispatch", agent),
                                ("cash receipt analysis", receipts), ("general conversation", conversation),
                                ("combined dashboard and report", multi_action), ("follow-up preserves the period", followup),
                                ("unsupported action is not executed", unsupported)]:
            record(name, operation)

    result = {"passed": all(check["passed"] for check in checks), "checks": checks,
              "limits": "Live synthetic-data API checks; browser layout, physical microphone and external PostgreSQL are not covered."}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sum(check['passed'] for check in checks)}/{len(checks)} checks passed")
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
