"""Repeatable live HTTP acceptance checks. Defaults to read-only local checks.

--live-ai explicitly enables three small paid model conversations against synthetic
data. No credentials, provider response bodies or customer records are printed.
The script never changes the active external source or writes business records.
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
    parser.add_argument("--live-ai", action="store_true", help="Send three paid OpenAI demo analysis requests")
    parser.add_argument("--output", type=Path, help="Optional JSON evidence summary (contains no secrets)")
    args = parser.parse_args()
    results = []

    def record(name, check):
        try:
            check()
            results.append({"name": name, "passed": True})
            print("PASS:", name)
        except Exception as exc:
            # Avoid HTTP URL/auth/provider bodies in logs. Descriptions are ours.
            reason = str(exc) if isinstance(exc, AssertionError) else type(exc).__name__
            results.append({"name": name, "passed": False, "reason": reason})
            print("FAIL:", name, "-", reason)

    with httpx.Client(base_url=args.base_url.rstrip("/"), timeout=150) as client:
        def get(path, **params):
            response = client.get(path, params=params)
            require(response.status_code == 200, f"Expected HTTP 200 for {path}; received {response.status_code}")
            return response.json()

        def health():
            app = get("/")
            require(app.get("app") == "TabloAI", "Unexpected application on selected port")
            source = get("/api/platform/sources")
            demo = next(s for s in source["sources"] if s["id"] == "dealership-demo")
            require(demo["synthetic"] and demo["status"] == "connected", "Demo source unavailable")
            require(demo["row_count"] >= 50000 and demo["table_count"] >= 12, "Demo dataset incomplete")

        def metric(data, key):
            return next(k["value"] for k in data["kpis"] if k["key"] == key)

        def dashboard():
            filters = {"start_date": "2026-10-01", "end_date": "2026-10-31", "branch_id": 2}
            all_data = get("/api/platform/overview", **filters)
            parts = [get("/api/platform/overview", **filters, category=kind) for kind in ("passenger", "truck")]
            for key in ("units", "revenue", "gross_profit", "receivable", "inventory"):
                require(metric(all_data, key) == sum(metric(d, key) for d in parts), f"Category partition mismatch: {key}")
            require(metric(all_data, "revenue") == sum(row["revenue"] for row in all_data["trend"]), "Monthly series does not reconcile")
            require(metric(all_data, "units") == sum(row["units"] for row in all_data["branches"]), "Branch series does not reconcile")
            require(all_data["filters"]["branch_id"] == 2, "Branch selection was ignored")

        def inventory():
            first = get("/api/platform/inventory", category="truck", status="available", page=1, page_size=5)
            second = get("/api/platform/inventory", category="truck", status="available", page=2, page_size=5)
            require(first["total"] > 5 and len(first["rows"]) == 5, "Inventory pagination fixture missing")
            require(not ({r["id"] for r in first["rows"]} & {r["id"] for r in second["rows"]}), "Inventory pages overlap")
            require(all(r["category"] == "truck" and r["status"] == "available" for r in first["rows"]), "Inventory filters ignored")
            found = get("/api/platform/inventory", search=first["rows"][0]["vin"])
            require(found["total"] == 1, "Exact VIN search failed")

        def artifacts():
            reports = get("/api/platform/reports")["reports"]
            agents = get("/api/platform/agents")
            require(len(agents["agents"]) == 4, "Agent definitions incomplete")
            if reports:
                report = get("/api/platform/reports/" + reports[0]["id"])
                require(report["summary"]["revenue"] == sum(r["revenue"] for r in report["overview"]["trend"]), "Saved report totals differ")
                for fmt in ("csv", "html"):
                    response = client.get(f"/api/platform/reports/{report['id']}/export", params={"format": fmt})
                    require(response.status_code == 200 and len(response.content) > 500, f"{fmt} export failed")
                    require(report["filters"]["start_date"] in response.text, f"{fmt} export lost scope")

        def errors():
            cases = ["/api/platform/overview?start_date=2026-10-31&end_date=2026-10-01",
                     "/api/platform/inventory?page=0", "/api/platform/inventory?category=invalid",
                     "/api/platform/reports/does-not-exist"]
            for path in cases:
                require(client.get(path).status_code in (400, 404, 422), "Invalid request incorrectly accepted")
            require(client.post("/api/platform/chat", json={"prompt": "  "}).status_code == 400, "Empty chat accepted")

        for name, check in [("app and source", health), ("dashboard reconciliation", dashboard),
                            ("inventory search and pagination", inventory), ("saved reports and agent definitions", artifacts),
                            ("invalid request handling", errors)]:
            record(name, check)

        if args.live_ai:
            db = Path(__file__).resolve().parents[1] / "data" / "dealership.db"
            cases = [
                ("historical units", "2025-yilda jami nechta avtomobil sotilgan?", "sales", "units",
                 "SELECT COUNT(*) FROM sales WHERE status='completed' AND sale_date BETWEEN '2025-01-01' AND '2025-12-31'"),
                ("model filtered revenue", "2026-yil oktabr oyida Chevrolet Cobalt sotuv summasi qancha?", "sales", "revenue",
                 "SELECT COALESCE(SUM(s.sale_price),0) FROM sales s JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id WHERE s.status='completed' AND s.sale_date BETWEEN '2026-10-01' AND '2026-10-31' AND m.brand='Chevrolet' AND m.model='Cobalt'"),
                ("cash receipts distinct from sales", "2026-yil oktabr oyida jami pul tushumi qancha?", "payments", "receipts",
                 "SELECT COALESCE(SUM(p.amount),0) FROM payments p JOIN sales s ON s.id=p.sale_id WHERE s.status='completed' AND p.payment_date BETWEEN '2026-10-01' AND '2026-10-31'"),
            ]
            for name, prompt, operation, measure, sql in cases:
                def check(prompt=prompt, operation=operation, measure=measure, sql=sql):
                    with closing(sqlite3.connect(db.as_uri() + "?mode=ro", uri=True)) as con:
                        expected = con.execute(sql).fetchone()[0]
                    response = client.post("/api/platform/chat", json={"prompt": prompt})
                    require(response.status_code == 200, f"AI HTTP status {response.status_code}")
                    data = response.json()
                    require(data.get("mode") == "openai" and bool(data.get("answer", "").strip()), "AI did not return a live answer")
                    analysis = next((a for a in data.get("analyses", []) if a["operation"] == operation and a["metric"] == measure), None)
                    require(analysis is not None, "AI did not execute requested analytical operation")
                    require(len(analysis["rows"]) == 1, "AI changed requested total to a breakdown")
                    require(analysis["rows"][0]["value"] == expected, "AI result differs from independent reference SQL")
                record("live AI: " + name, check)

    summary = {"passed": all(r["passed"] for r in results), "checks": results, "live_ai": args.live_ai,
               "limits": "HTTP checks do not validate browser layout, physical microphone, external PostgreSQL or every possible question."}
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"{sum(r['passed'] for r in results)}/{len(results)} checks passed")
    return 0 if summary["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
