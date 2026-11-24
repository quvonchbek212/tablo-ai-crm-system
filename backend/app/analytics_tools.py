"""Small typed analytics tools: models choose parameters, never SQL text."""
from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app import platform_service as service


class AnalysisQuery(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation: Literal["sales", "payments", "inventory", "customers"] = "sales"
    metric: Literal["revenue", "units", "gross_profit", "average_price", "receivable", "receipts", "stock_value", "customer_count"] = "revenue"
    group_by: Literal["none", "day", "month", "year", "branch", "category", "brand", "model", "employee", "customer_type", "city", "status", "method", "fuel_type"] = "none"
    start_date: str
    end_date: str
    branch_id: int | None = Field(default=None, ge=1, strict=True)
    branch_city: str | None = Field(default=None, min_length=1, max_length=80)
    category: Literal["passenger", "truck"] | None = None
    brand: str | None = Field(default=None, min_length=1, max_length=80)
    model: str | None = Field(default=None, min_length=1, max_length=80)
    employee_id: int | None = Field(default=None, ge=1, strict=True)
    fuel_type: str | None = Field(default=None, max_length=40)
    status: Literal["available", "reserved", "sold"] | None = None
    method: str | None = Field(default=None, max_length=40)
    order: Literal["asc", "desc"] = "desc"
    sort_by: Literal["metric", "label"] = "metric"
    limit: int = Field(default=20, ge=1, le=50, strict=True)

    @model_validator(mode="after")
    def valid_operation(self):
        service.Filters(self.start_date, self.end_date, self.branch_id, self.category)
        metrics = {"sales": {"revenue", "units", "gross_profit", "average_price", "receivable", "customer_count"},
                   "payments": {"receipts"}, "inventory": {"units", "stock_value", "average_price"},
                   "customers": {"customer_count"}}
        groups = {"sales": {"none", "day", "month", "year", "branch", "category", "brand", "model", "employee", "customer_type", "city", "fuel_type"},
                  "payments": {"none", "day", "month", "year", "branch", "category", "brand", "model", "employee", "method", "customer_type", "city", "fuel_type"},
                  "inventory": {"none", "branch", "category", "brand", "model", "status", "fuel_type"},
                  "customers": {"none", "day", "month", "year", "customer_type", "city"}}
        if self.metric not in metrics[self.operation] or self.group_by not in groups[self.operation]:
            raise ValueError("Tanlangan ko'rsatkich yoki guruh ushbu tahlilga mos emas.")
        if self.status is not None and self.operation != "inventory":
            raise ValueError("Ombor holati faqat ombor tahliliga tegishli.")
        if self.method is not None and self.operation != "payments":
            raise ValueError("To'lov usuli faqat tushum tahliliga tegishli.")
        if self.employee_id is not None and self.operation in {"inventory", "customers"}:
            raise ValueError("Bu tahlilda xodim filtri mavjud emas.")
        if self.operation == "customers" and any([self.branch_id, self.branch_city, self.category, self.brand, self.model, self.fuel_type]):
            raise ValueError("Yangi mijozlar ro'yxatida filial/avtomobil filtri yo'q. Xaridorlar uchun sales/customer_count tanlanadi.")
        if self.operation == "inventory" and self.end_date != service.AS_OF.isoformat():
            raise ValueError("Ombor tarixi saqlanmagan; faqat 2026-11-12 holati mavjud.")
        return self


class AnalysisPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["analysis", "clarification", "conversation"]
    queries: list[AnalysisQuery] = Field(default_factory=list, max_length=4)
    message: str = Field(default="", max_length=1000)

    @model_validator(mode="after")
    def valid_queries(self):
        if self.kind == "analysis" and not self.queries:
            raise ValueError("Tahlil rejasi bo'sh.")
        if self.kind != "analysis" and (self.queries or not self.message.strip()):
            raise ValueError("Aniqlashtirish xabari noto'g'ri.")
        return self


LABELS = {"revenue": "Sotuv summasi", "units": "Avtomobillar soni", "gross_profit": "Yalpi foyda",
          "average_price": "O'rtacha narx", "receivable": "To'lanmagan qoldiq", "receipts": "Pul tushumi",
          "stock_value": "Zaxira tannarxi", "customer_count": "Mijozlar soni"}
DEFINITIONS = {
    "revenue": "Yakunlangan sotuvlarning summasi; kassaga kelgan pul emas.",
    "gross_profit": "Yakunlangan sotuv summasi minus shu avtomobillar tannarxi; operatsion xarajatlar va soliqlar ayirilmagan.",
    "receivable": "Tanlangan davrdagi yakunlangan sotuvlar minus davr oxirigacha shu sotuvlardan tushgan to'lovlar; kechikkan qarz emas.",
    "receipts": "Tanlangan sanalarda haqiqatan qayd etilgan to'lovlar; sotuv sanasi davrdan oldin bo'lishi mumkin.",
    "stock_value": "2026-11-12 holatidagi mavjud/band avtomobillarning tannarxi; tarixiy zaxira emas.",
}


def catalog():
    with service.source_connection() as con:
        return {"branches": service.rows(con, "SELECT id,name,city FROM branches ORDER BY id"),
                "models": service.rows(con, "SELECT brand,model,category,fuel_type FROM vehicle_models ORDER BY brand,model"),
                "employees": service.rows(con, "SELECT id,name,branch_id,role FROM employees ORDER BY id"),
                "payment_methods": [r[0] for r in con.execute("SELECT DISTINCT method FROM payments ORDER BY method")],
                "data_start": service.START.isoformat(), "data_end": service.AS_OF.isoformat()}


def _validate_names(con, query):
    service.validate_branch(con, service.Filters(query.start_date, query.end_date, query.branch_id, query.category))
    for field, table, column in [("brand", "vehicle_models", "brand"), ("model", "vehicle_models", "model"), ("branch_city", "branches", "city"),
                                 ("fuel_type", "vehicle_models", "fuel_type"), ("method", "payments", "method")]:
        value = getattr(query, field)
        if value and not con.execute(f"SELECT 1 FROM {table} WHERE {column}=? COLLATE NOCASE LIMIT 1", (value,)).fetchone():
            raise ValueError(f"Bazada bunday {field} topilmadi: {value}")
    if query.employee_id and not con.execute("SELECT 1 FROM employees WHERE id=?", (query.employee_id,)).fetchone():
        raise ValueError("Xodim topilmadi.")


def execute(query: AnalysisQuery) -> dict:
    """Only hard-coded SQL fragments become identifiers; every user value is bound."""
    q = query
    values, where = [], []
    date_col = {"sales": "s.sale_date", "payments": "p.payment_date", "customers": "c.created_at"}.get(q.operation)
    if date_col:
        where.append(f"{date_col} BETWEEN ? AND ?")
        values.extend([q.start_date, q.end_date])
    if q.operation in {"sales", "payments"}:
        where.append("s.status='completed'")
        joins = "sales s JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id JOIN branches b ON b.id=s.branch_id JOIN employees e ON e.id=s.employee_id JOIN customers c ON c.id=s.customer_id"
        if q.operation == "payments":
            joins = "payments p JOIN sales s ON s.id=p.sale_id JOIN vehicles v ON v.id=s.vehicle_id JOIN vehicle_models m ON m.id=v.model_id JOIN branches b ON b.id=s.branch_id JOIN employees e ON e.id=s.employee_id JOIN customers c ON c.id=s.customer_id"
        if q.metric == "receivable":
            joins += " LEFT JOIN (SELECT sale_id,SUM(amount) paid FROM payments WHERE payment_date<=? GROUP BY sale_id) paid ON paid.sale_id=s.id"
        metrics = {"revenue": "COALESCE(SUM(s.sale_price),0)", "units": "COUNT(*)",
                   "gross_profit": "COALESCE(SUM(s.sale_price-s.cost_price),0)", "average_price": "AVG(s.sale_price)",
                   "customer_count": "COUNT(DISTINCT s.customer_id)", "receipts": "COALESCE(SUM(p.amount),0)",
                   "receivable": "COALESCE(SUM(s.sale_price-COALESCE(paid.paid,0)),0)"}
        branch_col = "s.branch_id"
    elif q.operation == "inventory":
        joins = "vehicles v JOIN vehicle_models m ON m.id=v.model_id JOIN branches b ON b.id=v.branch_id"
        where.append("v.status=?" if q.status else "v.status IN ('available','reserved')")
        if q.status:
            values.append(q.status)
        metrics = {"units": "COUNT(*)", "stock_value": "COALESCE(SUM(v.cost_price),0)", "average_price": "AVG(v.list_price)"}
        branch_col = "v.branch_id"
    else:
        joins, metrics, branch_col = "customers c", {"customer_count": "COUNT(*)"}, None
    for field, expression in [("branch_id", branch_col), ("branch_city", "b.city"), ("category", "m.category"), ("brand", "m.brand"),
                              ("model", "m.model"), ("employee_id", "s.employee_id"), ("fuel_type", "m.fuel_type"), ("method", "p.method")]:
        value = getattr(q, field)
        if value is not None:
            where.append(f"{expression}=? COLLATE NOCASE")
            values.append(value)
    groups = {"branch": ("b.id", "b.name"), "category": ("m.category", "m.category"),
              "brand": ("m.brand", "m.brand"), "model": ("m.id", "m.brand || ' ' || m.model"),
              "employee": ("e.id", "e.name"), "customer_type": ("c.customer_type", "c.customer_type"),
              "city": ("c.city", "c.city"), "status": ("v.status", "v.status"), "method": ("p.method", "p.method"),
              "fuel_type": ("m.fuel_type", "m.fuel_type")}
    if date_col:
        groups.update({"day": (date_col, date_col), "month": (f"substr({date_col},1,7)", f"substr({date_col},1,7)"),
                       "year": (f"substr({date_col},1,4)", f"substr({date_col},1,4)")})
    grouping = groups.get(q.group_by)
    select = f"{grouping[1]} AS label," if grouping else ""
    sql = f"SELECT {select}{metrics[q.metric]} AS value FROM {joins} WHERE {' AND '.join(where) or '1=1'}"
    if grouping:
        sql += f" GROUP BY {grouping[0]},{grouping[1]}"
        sql += f" ORDER BY label {q.order.upper()}" if q.sort_by == "label" else f" ORDER BY value {q.order.upper()},label ASC"
    sql += " LIMIT ?"
    params = ([q.end_date] if q.metric == "receivable" else []) + values + [q.limit + 1]
    with service.source_connection() as con:
        _validate_names(con, q)
        result = service.rows(con, sql, params)
    truncated = len(result) > q.limit
    result = result[:q.limit]
    definition = DEFINITIONS.get(q.metric, "")
    if q.metric == "customer_count":
        definition = "Tanlangan davrda ro'yxatdan o'tgan mijozlar." if q.operation == "customers" else "Tanlangan davrda yakunlangan sotuvi bo'lgan noyob xaridorlar. Guruhlar orasida bir xaridor takrorlanishi mumkin; guruhlar yig'indisi umumiy noyob son emas."
    if q.operation == "inventory":
        definition = f"2026-11-12 holati. Holat: {q.status or 'available va reserved'}. Sana filtri tarixiy omborni hisoblamaydi. " + definition
        if q.status == "sold" and q.metric == "stock_value":
            definition = "2026-11-12 holatiga sotilgan avtomobillarning qayd etilgan tannarxi; bu joriy zaxira qiymati emas."
    if q.metric == "average_price":
        definition = "O'rtacha ro'yxat narxi (UZS)." if q.operation == "inventory" else "Yakunlangan sotuvlarning o'rtacha narxi (UZS)."
    if q.metric == "units" and q.operation == "sales":
        definition = "Tanlangan davrdagi yakunlangan sotuvlar soni; har bir sotuv bitta avtomobil."
    return {"operation": q.operation, "metric": q.metric, "metric_label": LABELS[q.metric], "group_by": q.group_by,
            "filters": q.model_dump(exclude={"operation", "metric", "group_by", "limit", "order", "sort_by"}),
            "columns": ["label", "value"] if grouping else ["value"], "rows": result, "truncated": truncated,
            "limit": q.limit, "order": q.order, "sort_by": q.sort_by, "unit": "UZS" if q.metric not in {"units", "customer_count"} else "count",
            "definition": definition, "source": service.source_receipt()}
