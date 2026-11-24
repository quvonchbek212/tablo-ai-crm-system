"""Source-backed dealership analytics. Demo records never mix with external sources."""
from __future__ import annotations

import calendar
import csv
import html
import io
import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.dealership_seed import ensure_demo_database

AS_OF = date(2026, 11, 12)
START = date(2025, 1, 1)
STATE_PATH = Path(__file__).resolve().parents[1] / "data" / "platform_state.sqlite3"
CATEGORY_LABELS = {"passenger": "Yengil avtomobillar", "truck": "Yuk mashinalari"}


@dataclass(frozen=True)
class Filters:
    start_date: str = "2026-01-01"
    end_date: str = "2026-11-12"
    branch_id: int | None = None
    category: str | None = None

    def __post_init__(self):
        try:
            start, end = date.fromisoformat(self.start_date), date.fromisoformat(self.end_date)
        except (TypeError, ValueError) as exc:
            raise ValueError("Sana YYYY-MM-DD formatida bo'lishi kerak.") from exc
        if start > end:
            raise ValueError("Boshlanish sanasi tugash sanasidan keyin bo'lishi mumkin emas.")
        if start < START or end > AS_OF:
            raise ValueError("Namuna ma'lumotlar davri: 2025-01-01 — 2026-11-12.")
        if self.category not in (None, "passenger", "truck"):
            raise ValueError("Avtomobil turi passenger yoki truck bo'lishi kerak.")
        if self.branch_id is not None and (isinstance(self.branch_id, bool) or self.branch_id < 1):
            raise ValueError("Filial identifikatori musbat son bo'lishi kerak.")


@contextmanager
def source_connection():
    path = ensure_demo_database()
    con = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    try:
        yield con
    finally:
        con.close()


def rows(con, sql, params=()):
    return [dict(row) for row in con.execute(sql, params).fetchall()]


def scalar(con, sql, params=()):
    row = con.execute(sql, params).fetchone()
    return row[0] if row and row[0] is not None else 0


def scope(filters: Filters, alias="s", date_column="sale_date", *, period=True):
    clauses, values = [], []
    if period:
        clauses.append(f"{alias}.{date_column} BETWEEN ? AND ?")
        values.extend([filters.start_date, filters.end_date])
    if filters.branch_id:
        clauses.append(f"{alias}.branch_id = ?")
        values.append(filters.branch_id)
    if filters.category:
        clauses.append("m.category = ?")
        values.append(filters.category)
    return " AND ".join(clauses) or "1=1", values


SALES_FROM = "FROM sales s JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id"


def validate_branch(con, filters):
    if filters.branch_id and not scalar(con, "SELECT COUNT(*) FROM branches WHERE id=?", (filters.branch_id,)):
        raise ValueError("Bunday filial mavjud emas.")


def totals(con, filters, start=None, end=None):
    where, params = scope(filters)
    if start is not None:
        params[:2] = [start, end]
    result = rows(con, f"SELECT COUNT(*) units, COALESCE(SUM(s.sale_price),0) revenue, COALESCE(SUM(s.sale_price-s.cost_price),0) gross_profit {SALES_FROM} WHERE s.status='completed' AND {where}", params)[0]
    cutoff = end or filters.end_date
    result["receivable"] = scalar(con, f"SELECT COALESCE(SUM(s.sale_price-COALESCE(p.paid,0)),0) {SALES_FROM} LEFT JOIN (SELECT sale_id,SUM(amount) paid FROM payments WHERE payment_date<=? GROUP BY sale_id) p ON p.sale_id=s.id WHERE s.status='completed' AND {where}", [cutoff, *params])
    lead_where, lead_params = scope(filters, "l", "created_at")
    if start is not None:
        lead_params[:2] = [start, end]
    leads = rows(con, f"SELECT COUNT(*) total,COALESCE(SUM(CASE WHEN l.status='won' THEN 1 ELSE 0 END),0) won FROM leads l JOIN vehicle_models m ON m.id=l.model_id WHERE {lead_where}", lead_params)[0]
    result["conversion"] = round(100 * leads["won"] / leads["total"], 2) if leads["total"] else 0
    return result


def source_receipt():
    return {"name": "Atlas Auto Group · sun'iy namuna baza", "as_of": AS_OF.isoformat(), "synthetic": True}


def overview(filters: Filters) -> dict:
    with source_connection() as con:
        validate_branch(con, filters)
        current = totals(con, filters)
        start, end = date.fromisoformat(filters.start_date), date.fromisoformat(filters.end_date)
        prev_end = start - timedelta(days=1)
        prev_start = prev_end - (end - start)
        previous = totals(con, filters, prev_start.isoformat(), prev_end.isoformat())
        comparison_complete = prev_start >= START
        where, params = scope(filters)
        inventory_where, inventory_params = scope(filters, "v", period=False)
        current["inventory"] = scalar(con, f"SELECT COUNT(*) FROM vehicles v JOIN vehicle_models m ON m.id=v.model_id WHERE v.status IN ('available','reserved') AND {inventory_where}", inventory_params)
        definitions = [("revenue", "Sotuv summasi", "currency"), ("units", "Sotilgan avtomobillar", "number"), ("gross_profit", "Yalpi foyda", "currency"), ("receivable", "Sotuvlar bo'yicha qarzdorlik", "currency"), ("inventory", "Ombordagi avtomobillar", "number"), ("conversion", "Murojaat konversiyasi", "percent")]
        kpis = []
        for key, label, fmt in definitions:
            prev = previous.get(key) if comparison_complete else None
            change = round((current[key] - prev) / abs(prev) * 100, 1) if prev else None
            kpis.append({"key": key, "label": label, "value": current[key], "previous_value": prev, "change_pct": change, "format": fmt})
        trend_rows = rows(con, f"SELECT substr(s.sale_date,1,7) month,SUM(s.sale_price) revenue,SUM(s.sale_price-s.cost_price) gross_profit,COUNT(*) units {SALES_FROM} WHERE s.status='completed' AND {where} GROUP BY month ORDER BY month", params)
        trend_map = {r["month"]: r for r in trend_rows}
        trend, cursor = [], start.replace(day=1)
        while cursor <= end:
            key = cursor.strftime("%Y-%m")
            trend.append(trend_map.get(key, {"month": key, "revenue": 0, "gross_profit": 0, "units": 0}))
            cursor = (cursor + timedelta(days=32)).replace(day=1)
        branch_rows = rows(con, f"SELECT b.id,b.name,SUM(s.sale_price) revenue,COUNT(*) units,SUM(s.sale_price-s.cost_price) gross_profit {SALES_FROM} JOIN branches b ON b.id=s.branch_id WHERE s.status='completed' AND {where} GROUP BY b.id,b.name ORDER BY revenue DESC", params)
        mixes = rows(con, f"SELECT m.category,SUM(s.sale_price) revenue,COUNT(*) units {SALES_FROM} WHERE s.status='completed' AND {where} GROUP BY m.category ORDER BY revenue DESC", params)
        for item in mixes:
            item["label"] = CATEGORY_LABELS[item["category"]]
        top_models = rows(con, f"SELECT m.brand,m.model,m.category,SUM(s.sale_price) revenue,COUNT(*) units {SALES_FROM} WHERE s.status='completed' AND {where} GROUP BY m.id,m.brand,m.model,m.category ORDER BY revenue DESC LIMIT 8", params)
        recent_sales = rows(con, f"SELECT s.id,s.sale_date,c.name customer,m.brand || ' ' || m.model vehicle,m.category,b.name branch,s.sale_price,s.status {SALES_FROM} JOIN customers c ON c.id=s.customer_id JOIN branches b ON b.id=s.branch_id WHERE {where} ORDER BY s.sale_date DESC,s.id DESC LIMIT 8", params)
        lead_where, lead_params = scope(filters, "l", "created_at")
        funnel = rows(con, f"SELECT l.status,COUNT(*) count FROM leads l JOIN vehicle_models m ON m.id=l.model_id WHERE {lead_where} GROUP BY l.status", lead_params)
        aged = scalar(con, f"SELECT COUNT(*) FROM vehicles v JOIN vehicle_models m ON m.id=v.model_id WHERE v.status='available' AND julianday(?)-julianday(v.received_at)>90 AND {inventory_where}", [AS_OF.isoformat(), *inventory_params])
        insights = [{"type": "warning" if aged else "success", "title": "Ombor aylanishi", "description": f"{aged:,} ta sotuvdagi avtomobil 90 kundan ortiq omborda turibdi. Holat: {AS_OF.isoformat()}."}]
        if branch_rows:
            best = branch_rows[0]
            insights.append({"type": "info", "title": "Yetakchi filial", "description": f"{best['name']}: tanlangan davrda {best['units']:,} ta avtomobil sotilgan."})
        if current["revenue"]:
            margin = current["gross_profit"] / current["revenue"] * 100
            insights.append({"type": "success", "title": "Yalpi foyda marjasi", "description": f"Sotuv summasining {margin:.1f}% i yalpi foyda. Operatsion xarajatlar ayirilmagan."})
        return {"metadata": {"company": "Atlas Auto Group", "synthetic": True, "as_of": AS_OF.isoformat(), "currency": "UZS", "period_label": f"{filters.start_date} — {filters.end_date}", "comparison": {"start_date": prev_start.isoformat(), "end_date": prev_end.isoformat(), "complete": comparison_complete}, "definitions": {"revenue": "Faqat yakunlangan sotuvlar summasi, pul tushumi emas.", "gross_profit": "Sotuv summasi minus sotilgan avtomobil tannarxi; operatsion xarajatlar ayirilmagan.", "receivable": "Tanlangan davrdagi sotuvlar minus davr oxirigacha tushgan to'lovlar.", "inventory": "2026-11-12 holatidagi mavjud va band qilingan avtomobillar; sana filtriga bog'liq emas.", "conversion": "Tanlangan davrda yaratilgan murojaatlarning hozirgi won ulushi."}}, "filters": asdict(filters), "options": {"branches": rows(con, "SELECT id,name,city FROM branches ORDER BY id"), "categories": [{"value": k, "label": v} for k, v in CATEGORY_LABELS.items()]}, "kpis": kpis, "trend": trend, "branches": branch_rows, "category_mix": mixes, "top_models": top_models, "recent_sales": recent_sales, "insights": insights, "funnel": funnel, "source": source_receipt()}


def inventory(search="", status=None, category=None, branch_id=None, page=1, page_size=20):
    filters = Filters(branch_id=branch_id, category=category)
    if status not in (None, "available", "reserved", "sold"):
        raise ValueError("Noto'g'ri ombor holati.")
    if not 1 <= page_size <= 100 or page < 1:
        raise ValueError("Sahifa o'lchami 1–100 oralig'ida bo'lishi kerak.")
    where, params = scope(filters, "v", period=False)
    if status:
        where += " AND v.status=?"
        params.append(status)
    if search:
        where += " AND (m.brand || ' ' || m.model || ' ' || v.vin) LIKE ? ESCAPE '\\'"
        params.append("%" + search.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    joins = "FROM vehicles v JOIN vehicle_models m ON m.id=v.model_id JOIN branches b ON b.id=v.branch_id"
    with source_connection() as con:
        validate_branch(con, filters)
        total = scalar(con, f"SELECT COUNT(*) {joins} WHERE {where}", params)
        result = rows(con, f"SELECT v.*,m.brand,m.model,m.category,m.fuel_type,b.name branch,CAST(julianday(COALESCE((SELECT MAX(sale_date) FROM sales WHERE vehicle_id=v.id AND status='completed'),?))-julianday(v.received_at) AS INTEGER) days_in_stock {joins} WHERE {where} ORDER BY v.id DESC LIMIT ? OFFSET ?", [AS_OF.isoformat(), *params, page_size, (page - 1) * page_size])
    return {"rows": result, "total": total, "page": page, "page_size": page_size, "pages": (total + page_size - 1) // page_size, "source": source_receipt()}


def sources():
    from app.llm_service import llm_service
    from app.database import db_manager
    with source_connection() as con:
        tables = []
        for item in rows(con, "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"):
            name = item["name"]
            quoted = '"' + name.replace('"', '""') + '"'
            tables.append({"name": name, "rows": scalar(con, f"SELECT COUNT(*) FROM {quoted}"), "columns": [{"name": col["name"], "type": col["type"]} for col in rows(con, f"PRAGMA table_info({quoted})")]})
    provider = getattr(llm_service, "provider", "vllm").lower()
    configured = provider == "openai" and bool(llm_service.api_key)
    active_connection = None
    if db_manager.is_connected:
        is_demo = (db_manager.dialect_name == "sqlite" and
                   Path(db_manager._engine.url.database).resolve() == ensure_demo_database().resolve())
        active_connection = {"database_name": db_manager.current_db_name,
                             "connection_fingerprint": db_manager.connection_fingerprint,
                             "dialect": db_manager.dialect_name, "is_demo": is_demo}
    return {"active_connection": active_connection, "sources": [{"id": "dealership-demo", "name": "Atlas Auto Group", "type": "SQLite", "status": "connected", "synthetic": True, "as_of": AS_OF.isoformat(), "table_count": len(tables), "row_count": sum(t["rows"] for t in tables), "tables": tables}], "ai": {"provider": provider, "configured": configured, "model": llm_service.model}, "capabilities": {"demo_analytics": True, "postgresql": True, "sqlite": True, "mysql": False, "csv_import": False, "external_source_endpoint": "/api/database/connect", "note": "Avtosavdo paneli namuna bazaga tegishli. Tashqi manba bilan suhbat alohida universal tahlil orqali ishlaydi."}}


@contextmanager
def state_connection():
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(STATE_PATH, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE IF NOT EXISTS artifacts (id TEXT PRIMARY KEY,kind TEXT NOT NULL,created_at TEXT NOT NULL,payload TEXT NOT NULL)")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def save_artifact(kind, payload):
    payload = {"id": uuid.uuid4().hex, "created_at": datetime.now(timezone.utc).isoformat(), **payload}
    with state_connection() as con:
        con.execute("INSERT INTO artifacts VALUES (?,?,?,?)", (payload["id"], kind, payload["created_at"], json.dumps(payload, ensure_ascii=False)))
    return payload


def update_artifact(artifact_id, kind, changes):
    """Persist AI interpretation against the original completed check, atomically."""
    with state_connection() as con:
        row = con.execute("SELECT payload FROM artifacts WHERE id=? AND kind=?", (artifact_id, kind)).fetchone()
        if row is None:
            raise KeyError("Natija topilmadi.")
        payload = {**json.loads(row[0]), **changes}
        con.execute("UPDATE artifacts SET payload=? WHERE id=? AND kind=?",
                    (json.dumps(payload, ensure_ascii=False), artifact_id, kind))
    return payload


def artifacts(kind, limit=100):
    with state_connection() as con:
        return [json.loads(r["payload"]) for r in con.execute("SELECT payload FROM artifacts WHERE kind=? ORDER BY created_at DESC LIMIT ?", (kind, limit))]


AGENTS = [
    {"id": "sales", "name": "Sotuv tahlilchisi", "description": "Teng davrlar sotuvini solishtiradi va filial natijalarini tekshiradi.", "icon": "trending-up"},
    {"id": "inventory", "name": "Ombor kuzatuvchisi", "description": "90 kundan ortiq sotilmagan avtomobillar va bog'langan mablag'ni aniqlaydi.", "icon": "truck"},
    {"id": "receivables", "name": "To'lov nazoratchisi", "description": "Tanlangan sotuvlar bo'yicha to'lanmagan qoldiqni tekshiradi.", "icon": "wallet"},
    {"id": "quality", "name": "Ma'lumot tekshiruvchisi", "description": "Baza bog'lanishlari, takroriy VIN va to'lov summalarini tekshiradi.", "icon": "shield-check"},
]


def agents():
    runs = artifacts("agent", 30)
    return {"agents": [{**a, "kind": "rule_based", "status": "ready", "last_run": next((r for r in runs if r["agent_id"] == a["id"]), None)} for a in AGENTS], "runs": runs}


def run_agent(agent_id, filters):
    agent = next((a for a in AGENTS if a["id"] == agent_id), None)
    if agent is None:
        raise KeyError("Agent topilmadi.")
    data = overview(filters)
    metrics = {k["key"]: k for k in data["kpis"]}
    findings = []
    if agent_id == "sales":
        change = metrics["revenue"]["change_pct"]
        findings.append({"severity": "warning" if change is not None and change < 0 else "info", "title": "Sotuv dinamikasi", "description": f"Teng oldingi davrga nisbatan o'zgarish: {change:+.1f}%." if change is not None else "Oldingi teng davr uchun to'liq ma'lumot yoki taqqoslash bazasi mavjud emas.", "value": metrics["revenue"]["value"]})
        if data["branches"]:
            best = data["branches"][0]
            findings.append({"severity": "info", "title": best["name"], "description": f"Tanlangan davrda {best['units']} ta sotuv. Filiallar orasida sotuv summasi eng yuqori."})
    elif agent_id == "inventory":
        where, params = scope(filters, "v", period=False)
        with source_connection() as con:
            aged = rows(con, f"SELECT COUNT(*) units,COALESCE(SUM(v.cost_price),0) amount FROM vehicles v JOIN vehicle_models m ON m.id=v.model_id WHERE v.status='available' AND julianday(?)-julianday(v.received_at)>90 AND {where}", [AS_OF.isoformat(), *params])[0]
        findings.append({"severity": "warning" if aged["units"] else "success", "title": "90 kundan ortiq omborda", "description": f"{aged['units']:,} ta avtomobil; tannarx bo'yicha {aged['amount']:,} so'm. Narx va sotuv rejalarini ko'rib chiqing. Holat: {AS_OF}.", "value": aged["units"]})
    elif agent_id == "receivables":
        value = metrics["receivable"]["value"]
        findings.append({"severity": "warning" if value else "success", "title": "To'lanmagan qoldiq", "description": f"Tanlangan davrdagi sotuvlardan {filters.end_date} holatiga {value:,} so'm to'lanmagan. Bu shartnoma muddati o'tgan qarz degani emas.", "value": value})
    else:
        with source_connection() as con:
            fk_errors = len(con.execute("PRAGMA foreign_key_check").fetchall())
            dupes = scalar(con, "SELECT COUNT(*) FROM (SELECT vin FROM vehicles GROUP BY vin HAVING COUNT(*)>1)")
            overpaid = scalar(con, "SELECT COUNT(*) FROM (SELECT s.id FROM sales s JOIN payments p ON p.sale_id=s.id GROUP BY s.id HAVING SUM(p.amount)>MAX(s.sale_price))")
        for title, value in [("Bog'lanish xatolari", fk_errors), ("Takroriy VIN", dupes), ("Sotuvdan ortiq to'lov", overpaid)]:
            findings.append({"severity": "warning" if value else "success", "title": title, "description": f"{value} ta holat. Tekshiruv butun namuna bazasi bo'yicha bajarildi.", "value": value})
    return save_artifact("agent", {"agent_id": agent_id, "agent_name": agent["name"], "status": "completed", "kind": "rule_based", "summary": f"{len(findings)} ta tekshiruv yakunlandi", "findings": findings, "filters": asdict(filters), "source": source_receipt()})


def create_report(filters, title=None):
    data = overview(filters)
    summary = {k["key"]: k["value"] for k in data["kpis"]}
    return save_artifact("report", {"title": title or "Avtosavdo faoliyati hisoboti", "filters": asdict(filters), "summary": summary, "overview": data})


def list_reports():
    return {"reports": [{k: v for k, v in r.items() if k != "overview"} for r in artifacts("report")]}


def get_report(report_id):
    with state_connection() as con:
        row = con.execute("SELECT payload FROM artifacts WHERE kind='report' AND id=?", (report_id,)).fetchone()
    if row is None:
        raise KeyError("Hisobot topilmadi.")
    return json.loads(row[0])


def export_report(report_id, fmt):
    report = get_report(report_id)
    if report.get("kind") == "external":
        from app.report_exports import export_connected_report
        return export_connected_report(report, fmt)
    if fmt == "csv":
        output = io.StringIO(newline="")
        writer = csv.writer(output)
        writer.writerow(["Atlas Auto Group", "SYNTHETIC DEMO", report["filters"]["start_date"], report["filters"]["end_date"], "UZS"])
        writer.writerow(["Filial ID", report["filters"]["branch_id"] or "Barchasi", "Avtomobil turi", report["filters"]["category"] or "Barchasi"])
        writer.writerow(["Ombor holati sanasi", AS_OF.isoformat()])
        writer.writerow(["Ko'rsatkich", "Qiymat", "Birlik"])
        for kpi in report["overview"]["kpis"]:
            writer.writerow([kpi["label"], kpi["value"], "UZS" if kpi["format"] == "currency" else kpi["format"]])
        writer.writerow([])
        writer.writerow(["Oy", "Sotuv UZS", "Yalpi foyda UZS", "Avtomobillar"])
        for row in report["overview"]["trend"]:
            writer.writerow([row["month"], row["revenue"], row["gross_profit"], row["units"]])
        writer.writerow([])
        writer.writerow(["Ko'rsatkich", "Ta'rif"])
        for key, value in report["overview"]["metadata"]["definitions"].items():
            writer.writerow([key, value])
        return "\ufeff" + output.getvalue(), "text/csv; charset=utf-8"
    if fmt != "html":
        raise ValueError("Eksport formati csv yoki html bo'lishi kerak.")
    esc = lambda value: html.escape(str(value), quote=True)
    body = "".join(f"<tr><td>{esc(k['label'])}</td><td>{k['value']:,}</td><td>{'UZS' if k['format']=='currency' else esc(k['format'])}</td></tr>" for k in report["overview"]["kpis"])
    trend = "".join(f"<tr><td>{esc(r['month'])}</td><td>{r['revenue']:,}</td><td>{r['gross_profit']:,}</td><td>{r['units']}</td></tr>" for r in report["overview"]["trend"])
    definitions = "".join(f"<li>{esc(v)}</li>" for v in report["overview"]["metadata"]["definitions"].values())
    return f"<!doctype html><html lang='uz'><meta charset='utf-8'><title>{esc(report['title'])}</title><style>body{{font:16px system-ui;max-width:1000px;margin:48px auto;padding:20px;color:#172b35}}table{{width:100%;border-collapse:collapse;margin:24px 0}}td,th{{padding:12px;text-align:left;border-bottom:1px solid #ddd}}.tag{{color:#716014}}@media print{{button{{display:none}}}}</style><h1>{esc(report['title'])}</h1><p>Atlas Auto Group · {esc(report['filters']['start_date'])} — {esc(report['filters']['end_date'])}</p><p class='tag'>Sun'iy namuna ma'lumotlari · UZS · holat: {AS_OF}</p><p>Filial ID: {esc(report['filters']['branch_id'] or 'Barchasi')} · Tur: {esc(report['filters']['category'] or 'Barchasi')}</p><table><thead><tr><th>Ko'rsatkich</th><th>Qiymat</th><th>Birlik</th></tr></thead><tbody>{body}</tbody></table><h2>Oylar bo'yicha</h2><table><thead><tr><th>Oy</th><th>Sotuv UZS</th><th>Yalpi foyda UZS</th><th>Soni</th></tr></thead><tbody>{trend}</tbody></table><h2>Ko'rsatkich ta'riflari</h2><ul>{definitions}</ul></html>", "text/html; charset=utf-8"
