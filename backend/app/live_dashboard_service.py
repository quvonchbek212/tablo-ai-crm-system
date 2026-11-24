"""Read the enterprise dashboard's actuals from one selected database connection."""
import calendar
from datetime import date, timedelta
from typing import Any, Dict

from app.database import db_manager


USD_RATE = 12800.0  # Fixed reporting rate for the synthetic dataset.
CATEGORY_FILTERS = {
    "all": "1=1", "servers": "p.category_id IN (1,3)",
    "network": "p.category_id IN (2,4)", "infrastructure": "p.category_id IN (5,6,7)",
}
PALETTE = ["#1D4ED8", "#2563EB", "#3B82F6", "#60A5FA", "#93C5FD", "#BFDBFE", "#DBEAFE"]


def period_buckets(period, anchor):
    month = anchor.replace(day=1)
    if period == "month":
        end = month.replace(day=calendar.monthrange(month.year, month.month)[1])
        return [(month.replace(day=day), min(month.replace(day=day) + timedelta(days=6), end))
                for day in range(1, end.day + 1, 7)]
    first = month.replace(month=1 if period == "year" else 1 + 3 * ((month.month - 1) // 3))
    width, count = (3, 4) if period == "year" else (1, 3)
    buckets = []
    for index in range(count):
        start = first.replace(month=first.month + index * width)
        last_month = start.month + width - 1
        end = date(start.year, last_month, calendar.monthrange(start.year, last_month)[1])
        buckets.append((start, end))
    return buckets


class LiveDashboardService:
    def __init__(self, connection, dept):
        self.connection = connection
        self.category_filter = CATEGORY_FILTERS[dept]

    def query(self, sql, **params):
        return db_manager.execute_query(sql, connection=self.connection, parameters=params)

    @classmethod
    def get_dashboard_data(cls, period="quarter", dept="all", currency="USD"):
        engine = db_manager._engine
        if engine is None:
            raise ValueError("1C ma'lumotlar bazasi ulanmagan.")
        with engine.connect() as connection:
            service = cls(connection, dept)
            result = service._dashboard(period, dept, currency)
        if engine is not db_manager._engine:
            raise ValueError("Tahlil davomida ulanish almashdi. Panelni yangilang.")
        return result

    def _dashboard(self, period, dept, currency):
        latest = self.query("SELECT MAX(order_date) AS latest FROM orders")[0]["latest"]
        if not latest:
            raise ValueError("Buyurtma sanalari mavjud emas.")
        anchor = date.fromisoformat(str(latest)[:10])
        buckets = period_buckets(period, anchor)
        start = buckets[0][0]
        params = {"start": start.isoformat(), "end": anchor.isoformat()}
        rate = USD_RATE if currency == "USD" else 1
        facts = """FROM order_items oi JOIN orders o ON o.id=oi.order_id
                   JOIN products p ON p.id=oi.product_id"""
        where = f"""WHERE o.order_date BETWEEN :start AND :end AND o.status <> 'cancelled'
                    AND {self.category_filter}"""
        daily = self.query(f"""SELECT o.order_date, SUM(oi.total_amount) AS revenue,
                          SUM(oi.quantity * oi.cost_price) AS cogs, COUNT(DISTINCT o.id) AS orders
                          {facts} {where} GROUP BY o.order_date ORDER BY o.order_date""", **params)
        expenses = self.query("""SELECT expense_date, SUM(amount) AS amount FROM pnl_expenses
                               WHERE expense_date BETWEEN :start AND :end GROUP BY expense_date""", **params)
        # There is no product-category allocation key for overhead. P&L always covers the company.
        company_costs = daily if dept == "all" else self.query("""
            SELECT order_date, SUM(total_amount) AS revenue, SUM(total_cost) AS cogs FROM orders
            WHERE order_date BETWEEN :start AND :end AND status <> 'cancelled' GROUP BY order_date""", **params)
        values = {"revenue": [], "profit": [], "orders": []}
        pnl = {"labels": [], "cogs": [], "opex": [], "net_profit": []}
        labels = []
        for begin, end in buckets:
            # Keep actuals through the last recorded day. Projections have a separate series.
            label = f"{begin:%d.%m}–{end:%d.%m}"
            labels.append(label)
            rows = [row for row in daily if begin.isoformat() <= str(row["order_date"])[:10] <= end.isoformat()]
            rev = sum(row["revenue"] for row in rows)
            cost = sum(row["cogs"] for row in rows)
            values["revenue"].append(rev if begin <= anchor else None)
            values["profit"].append(rev - cost if begin <= anchor else None)
            values["orders"].append(sum(row["orders"] for row in rows) if begin <= anchor else None)
            if begin <= anchor:
                company = [r for r in company_costs if begin.isoformat() <= str(r["order_date"])[:10] <= end.isoformat()]
                overhead = sum(r["amount"] for r in expenses if begin.isoformat() <= str(r["expense_date"])[:10] <= end.isoformat())
                company_rev = sum(r["revenue"] for r in company)
                company_cogs = sum(r["cogs"] for r in company)
                pnl["labels"].append(label)
                pnl["cogs"].append(round(company_cogs / rate, 2))
                pnl["opex"].append(round(overhead / rate, 2))
                pnl["net_profit"].append(round((company_rev - company_cogs - overhead) / rate, 2))

        totals = {metric: sum(value or 0 for value in series) for metric, series in values.items()}
        trend = {"labels": labels}
        for metric, series in values.items():
            divisor = 1 if metric == "orders" else rate
            decimals = 0 if metric == "orders" else 2
            pace = totals[metric] / ((anchor - start).days + 1)
            projected = []
            for actual, (begin, end) in zip(series, buckets):
                remaining = max(0, (end - max(anchor + timedelta(days=1), begin)).days + 1)
                projected.append(round(((actual or 0) + remaining * pace) / divisor, decimals) if remaining else None)
            trend[metric] = {"actual": [round(v / divisor, decimals) if v is not None else None for v in series],
                             "forecast": projected}

        def grouped(dimension, joins, group, limit=""):
            return self.query(f"""SELECT {dimension}, SUM(oi.total_amount) AS amount,
                               SUM(oi.cost_price * oi.quantity) AS cost, COUNT(DISTINCT o.id) AS orders
                               {facts} {joins} {where} GROUP BY {group} ORDER BY amount DESC, {group} {limit}""", **params)

        revenue = totals["revenue"]
        categories = grouped("pc.name", "JOIN product_categories pc ON pc.id=p.category_id", "pc.name")
        segments = [{"name": row["name"], "amount": round(row["amount"] / rate, 2),
                     "share": round(100 * row["amount"] / revenue, 1) if revenue else 0,
                     "color": PALETTE[i % len(PALETTE)]} for i, row in enumerate(categories)]
        regions = grouped("b.region", "JOIN branches b ON b.id=o.branch_id", "b.region", "LIMIT 5")
        clients = grouped("c.id,c.name,c.city,cs.name AS segment,c.status",
                          "JOIN clients c ON c.id=o.client_id JOIN client_segments cs ON cs.id=c.segment_id",
                          "c.id,c.name,c.city,cs.name,c.status", "LIMIT 8")
        return {
            "period": period, "dept": dept, "currency": currency, "rate": rate, "as_of": anchor.isoformat(),
            "kpis": {"rev": round(revenue / rate, 2), "orders": totals["orders"],
                     "gross_profit": round(totals["profit"] / rate, 2),
                     "margin": round(100 * totals["profit"] / revenue, 1) if revenue else None},
            "trend": trend, "pnl": pnl, "segments": segments,
            "debt": self._get_debt_summary(rate), "stock": self._get_stock_summary(rate),
            "regions": [{"region": r["region"], "amount": round(r["amount"] / rate, 2)} for r in regions],
            "top_contracts": [{"id": str(r["id"]), "name": r["name"], "segment": r["segment"], "region": r["city"],
                               "revenue": round(r["amount"] / rate, 2), "margin": round(100 * (1 - r["cost"] / r["amount"]), 1) if r["amount"] else 0,
                               "orders_count": r["orders"], "status": r["status"]} for r in clients],
        }

    def _get_debt_summary(self, rate: float) -> Dict[str, Any]:
        """Calculates total accounts receivable, overdue debt, and top debtor accounts."""
        sql_summary = """
            SELECT 
                SUM(balance_receivable) as total_debt,
                SUM(overdue_receivable) as overdue_debt,
                SUM(total_invoiced) as invoiced,
                SUM(total_paid) as paid
            FROM debt_settlements
        """
        summary_row = self.query(sql_summary)[0]
        total_debt = float(summary_row["total_debt"] or 0)
        overdue_debt = float(summary_row["overdue_debt"] or 0)
        invoiced = float(summary_row["invoiced"] or 1)
        paid = float(summary_row["paid"] or 0)

        collection_rate = round(paid / invoiced * 100, 1)

        sql_top = """
            SELECT 
                cl.name as client_name,
                cs.name as segment_name,
                cl.city as city,
                ds.balance_receivable,
                ds.overdue_receivable,
                ds.overdue_days,
                ds.risk_level
            FROM debt_settlements ds
            JOIN clients cl ON cl.id = ds.client_id
            JOIN client_segments cs ON cs.id = cl.segment_id
            WHERE ds.balance_receivable > 0
            ORDER BY ds.balance_receivable DESC
            LIMIT 6
        """
        top_rows = self.query(sql_top)
        top_debtors = []
        for r in top_rows:
            top_debtors.append({
                "client": r["client_name"],
                "segment": r["segment_name"],
                "city": r["city"],
                "debt": round(float(r["balance_receivable"]) / rate),
                "overdue": round(float(r["overdue_receivable"]) / rate),
                "days": int(r["overdue_days"] or 0),
                "risk": r["risk_level"]
            })

        return {
            "total_debt": round(total_debt / rate),
            "overdue_debt": round(overdue_debt / rate),
            "healthy_debt": round((total_debt - overdue_debt) / rate),
            "collection_rate": collection_rate,
            "top_debtors": top_debtors
        }

    def _get_stock_summary(self, rate: float) -> Dict[str, Any]:
        """Calculates total warehouse stock value, units, and warehouse breakdown."""
        sql_warehouses = f"""
            SELECT 
                w.name as warehouse_name,
                w.code,
                w.warehouse_type,
                p.unit,
                SUM(ws.quantity) as units,
                SUM(ws.quantity * p.wholesale_price) as stock_val
            FROM warehouse_stocks ws
            JOIN warehouses w ON w.id = ws.warehouse_id
            JOIN products p ON p.id = ws.product_id
            WHERE {self.category_filter}
            GROUP BY w.id, w.name, w.code, w.warehouse_type, p.unit
            ORDER BY w.code, p.unit
        """
        rows = self.query(sql_warehouses)
        warehouses = {}
        by_unit = {}
        total_value = 0
        for r in rows:
            units, value, unit = r["units"], r["stock_val"], r["unit"]
            warehouse = warehouses.setdefault(r["code"], {"name": r["warehouse_name"], "code": r["code"],
                "type": r["warehouse_type"], "units": 0, "value": 0, "by_unit": {}})
            warehouse["by_unit"][unit] = units
            warehouse["units"] += units
            warehouse["value"] += value
            by_unit[unit] = by_unit.get(unit, 0) + units
            total_value += value
        for warehouse in warehouses.values():
            warehouse["value"] = round(warehouse["value"] / rate, 2)

        return {
            "total_value": round(total_value / rate, 2),
            "total_units": sum(by_unit.values()),
            "by_unit": by_unit,
            "warehouses": sorted(warehouses.values(), key=lambda w: w["value"], reverse=True)
        }
