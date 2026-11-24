"""Curated, read-only questions for the built-in 1C enterprise sample."""
from dataclasses import dataclass
import re
from time import monotonic
from textwrap import dedent


@dataclass(frozen=True)
class EnterpriseQuestion:
    question: str
    title: str
    label: str
    metric: str
    answer_line: str
    sql: str
    calculation_note: str = ""


QUESTIONS = [
    EnterpriseQuestion(
        question="Qaysi besh mijozdan eng ko‘p pul tushgan va ularning umumiy tushumdagi ulushi qancha?",
        title="Mijozlardan kelib tushgan to‘lovlar",
        label="Mijoz", metric="Tushum (UZS)",
        answer_line="{Mijoz} — {Tushum (UZS)}, umumiy tushumdagi ulushi {Umumiy tushumdagi ulush (%)}.",
        sql="""
        SELECT c.name AS "Mijoz", COUNT(DISTINCT i.order_id) AS "To'lov tushgan buyurtmalar",
               SUM(p.amount) AS "Tushum (UZS)",
               ROUND(100.0 * SUM(p.amount) / NULLIF((SELECT SUM(amount) FROM payments), 0), 2)
                   AS "Umumiy tushumdagi ulush (%)"
        FROM payments p
        JOIN clients c ON c.id = p.client_id
        JOIN invoices i ON i.id = p.invoice_id
        GROUP BY c.id, c.name
        ORDER BY SUM(p.amount) DESC, c.id
        LIMIT 5
    """),
    EnterpriseQuestion(
        question="Yakunlangan buyurtmalarda qaysi besh mahsulot eng katta sotuv summasini bergan va ularning marjasi qancha?",
        title="Mahsulotlar bo‘yicha yakunlangan sotuvlar",
        label="Mahsulot", metric="Chegirmadan keyingi sotuv (UZS)",
        answer_line="{Mahsulot} — {Miqdor} {Birlik}, sotuv {Chegirmadan keyingi sotuv (UZS)}, marja {Buyurtma narxidagi yalpi marja (%)}.",
        sql="""
        SELECT p.name AS "Mahsulot", pc.name AS "Toifa", SUM(oi.quantity) AS "Miqdor",
               p.unit AS "Birlik", SUM(oi.total_amount) AS "Chegirmadan keyingi sotuv (UZS)",
               ROUND(100.0 * SUM(oi.total_amount - oi.quantity * oi.cost_price)
                     / NULLIF(SUM(oi.total_amount), 0), 1) AS "Buyurtma narxidagi yalpi marja (%)"
        FROM order_items oi
        JOIN orders o ON o.id = oi.order_id
        JOIN products p ON p.id = oi.product_id
        JOIN product_categories pc ON pc.id = p.category_id
        WHERE o.status = 'completed'
        GROUP BY p.id, p.name, pc.name, p.unit
        ORDER BY SUM(oi.total_amount) DESC, p.id
        LIMIT 5
    """),
    EnterpriseQuestion(
        question="Muddati o‘tgan qarzi eng katta besh mijoz kim va ularning qarzi qancha?",
        title="Mijozlar bo‘yicha muddati o‘tgan qarz",
        label="Mijoz", metric="Muddati o'tgan qarz (UZS)",
        answer_line="{Mijoz} — {Muddati o'tgan qarz (UZS)} muddati o‘tgan, eng uzoq kechikish {Eng uzoq kechikish (kun)} kun.",
        sql="""
        SELECT c.name AS "Mijoz", SUM(ds.balance_receivable) AS "Jami qarz (UZS)",
               SUM(ds.overdue_receivable) AS "Muddati o'tgan qarz (UZS)",
               MAX(CASE WHEN ds.overdue_receivable > 0 THEN ds.overdue_days END)
                   AS "Eng uzoq kechikish (kun)",
               CASE MAX(CASE ds.risk_level WHEN 'critical' THEN 2 WHEN 'warning' THEN 1 ELSE 0 END)
                   WHEN 2 THEN 'critical' WHEN 1 THEN 'warning' ELSE 'normal' END AS "Eng yuqori xavf"
        FROM debt_settlements ds
        JOIN clients c ON c.id = ds.client_id
        GROUP BY c.id, c.name
        HAVING SUM(ds.overdue_receivable) > 0
        ORDER BY SUM(ds.overdue_receivable) DESC, c.id
        LIMIT 5
    """),
    EnterpriseQuestion(
        question="Har bir omborda qancha tovar bor va qoldiqning ulgurji narxdagi qiymati qancha?",
        title="Ombor qoldiqlarining ulgurji qiymati",
        label="Ombor", metric="Ulgurji qiymat (UZS)",
        answer_line="{Ombor} — {Qoldiq}, band qilingan qismi {Band qilingan}, ulgurji qiymati {Ulgurji qiymat (UZS)}.",
        sql="""
        WITH stock_by_unit AS (
            SELECT s.warehouse_id, p.unit, SUM(s.quantity) AS quantity,
                   SUM(s.reserved_quantity) AS reserved, SUM(s.quantity * p.wholesale_price) AS value
            FROM warehouse_stocks s
            JOIN products p ON p.id = s.product_id
            GROUP BY s.warehouse_id, p.unit
        )
        SELECT w.name AS "Ombor", w.address AS "Manzil",
               GROUP_CONCAT(s.quantity || ' ' || s.unit, ', ') AS "Qoldiq",
               GROUP_CONCAT(s.reserved || ' ' || s.unit, ', ') AS "Band qilingan",
               COALESCE(SUM(s.value), 0) AS "Ulgurji qiymat (UZS)"
        FROM warehouses w
        LEFT JOIN stock_by_unit s ON s.warehouse_id = w.id
        GROUP BY w.id, w.name, w.address
        ORDER BY SUM(s.value) DESC, w.id
    """),
    EnterpriseQuestion(
        question="Bazadagi oxirgi buyurtma oyidan oldingi oyda qaysi besh menejer eng ko‘p sotuv qilgan va rejasini necha foizga bajargan?",
        title="Menejerlar bo‘yicha oylik sotuv",
        label="Menejer", metric="Sotuv (UZS)",
        answer_line="{Menejer} — {Oy} oyida {Sotuv (UZS)} sotuv, reja {Oylik reja (UZS)}, bajarilishi {Reja bajarilishi (%)}.",
        sql="""
        WITH period AS (
            SELECT DATE(MAX(order_date), 'start of month', '-1 month') AS start_date,
                   DATE(MAX(order_date), 'start of month') AS end_date
            FROM orders
        )
        SELECT m.full_name AS "Menejer", m.role AS "Lavozim",
               SUBSTR(period.start_date, 1, 7) AS "Oy",
               COUNT(o.id) AS "Yakunlangan buyurtmalar",
               COALESCE(SUM(o.total_amount), 0) AS "Sotuv (UZS)",
               m.monthly_target_uzs AS "Oylik reja (UZS)",
               ROUND(100.0 * COALESCE(SUM(o.total_amount), 0)
                     / NULLIF(m.monthly_target_uzs, 0), 1) AS "Reja bajarilishi (%)"
        FROM managers m CROSS JOIN period
        LEFT JOIN orders o ON o.manager_id = m.id AND o.status = 'completed'
             AND o.order_date >= period.start_date AND o.order_date < period.end_date
        WHERE period.start_date IS NOT NULL
        GROUP BY m.id, m.full_name, m.role, period.start_date, m.monthly_target_uzs
        ORDER BY COALESCE(SUM(o.total_amount), 0) DESC, m.id
        LIMIT 5
    """),
]


def equipment_question(prompt):
    """Recognize an unqualified two-metric hardware comparison in the bundled sample."""
    words = " ".join(prompt.casefold().translate(str.maketrans("‘’ʻʼ`", "'''''")).strip(" ?!.").split())
    if not re.fullmatch(
        r"qaysi (?:it uskunalar(?:i)?|it qurilmalar(?:i)?) (?:eng ko'p|eng katta) "
        r"(?:daromad|sotuv summasi(?:ni)?) (?:va|hamda) (?:eng )?(?:yuqori|katta) "
        r"(?:yalpi )?marja(?:ni)? (?:bergan|keltirgan)", words
    ):
        return None
    return EnterpriseQuestion(
        question=prompt, title="IT uskunalar bo‘yicha sotuv va yalpi marja",
        label="Uskuna", metric="Sotuv (UZS)",
        calculation_note=(
            "Barcha davrdagi yakunlangan buyurtmalar hisobga olindi. Daromad — chegirmadan keyingi sotuv summasi. "
            "Yalpi marja — yalpi foydaning sotuv summasiga nisbati. "
            "Ro‘yxatda boshqa biror uskuna ham sotuvda, ham marjada ortda qoldirmagan modellar keltirilgan. "
            "O‘rinlar barcha sotilgan IT uskunalari orasida hisoblangan."
        ),
        answer_line="{Uskuna} — sotuv {Sotuv (UZS)} ({Sotuv o‘rni}-o‘rin), "
                    "yalpi marja {Marja (%)} ({Marja o‘rni}-o‘rin), yalpi foyda {Yalpi foyda (UZS)}.",
        sql="""
        WITH totals AS (
            SELECT p.id, p.name, SUM(oi.total_amount) AS sales,
                   SUM(oi.total_amount - oi.quantity * oi.cost_price) AS profit
            FROM order_items oi
            JOIN orders o ON o.id = oi.order_id
            JOIN products p ON p.id = oi.product_id
            JOIN product_categories pc ON pc.id = p.category_id
            WHERE o.status = 'completed'
              AND pc.code IN ('CAT-SRV','CAT-NET','CAT-STO','CAT-SEC','CAT-PWR','CAT-CLI','CAT-CAB')
            GROUP BY p.id, p.name
            HAVING SUM(oi.total_amount) > 0
        ), scored AS (
            SELECT totals.*, 100.0 * profit / sales AS margin_pct FROM totals
        ), ranked AS (
            SELECT scored.*, DENSE_RANK() OVER (ORDER BY sales DESC) AS sales_rank,
                   DENSE_RANK() OVER (ORDER BY margin_pct DESC) AS margin_rank FROM scored
        )
        SELECT r.name AS "Uskuna", r.sales AS "Sotuv (UZS)", r.profit AS "Yalpi foyda (UZS)",
               ROUND(r.margin_pct, 2) AS "Marja (%)", r.sales_rank AS "Sotuv o‘rni",
               r.margin_rank AS "Marja o‘rni"
        FROM ranked r
        WHERE NOT EXISTS (
            SELECT 1 FROM scored s WHERE s.sales >= r.sales AND s.margin_pct >= r.margin_pct
                AND (s.sales > r.sales OR s.margin_pct > r.margin_pct)
        )
        ORDER BY r.sales DESC, r.margin_pct DESC, r.id
        """
    )


def answer_enterprise_question(preset, manager, identity):
    from app.chat_charts import chart_for_rows
    from app.models import NaturalQueryResponse

    started = monotonic()
    sql = dedent(preset.sql).strip()
    identity.assert_current()
    # Pin the execution to the engine whose identity authorized this preset.
    with identity.engine.connect() as connection:
        rows = manager.execute_query(sql, max_rows=501, connection=connection)
    identity.assert_current()
    truncated = len(rows) > 500
    rows = rows[:500]
    columns = list(rows[0]) if rows else []
    chart = chart_for_rows(preset.question, rows, title=preset.title, sql=sql, query_id="q1",
                           x_axis=preset.label, y_axis=preset.metric, truncated=truncated)

    answer = ["1C sun’iy namuna bazasi. " + preset.title + "."]
    if preset.calculation_note:
        answer.append(preset.calculation_note)
    for rank, row in enumerate(rows[:10], 1):
        formatted = {}
        for key, value in row.items():
            if value is None:
                formatted[key] = "hisoblanmagan"
            elif isinstance(value, (int, float)):
                unit = " so‘m" if key.endswith("(UZS)") else "%" if key.endswith("(%)") else ""
                formatted[key] = f"{value:,}".replace(",", " ") + unit
            else:
                formatted[key] = str(value)
        answer.append(f"{rank}. " + preset.answer_line.format_map(formatted))
    if not rows:
        answer.append("Savol shartlariga mos yozuv topilmadi.")
    if len(rows) > 10:
        answer.append("Qolgan natijalar jadvalda keltirilgan.")
    warnings = ["Natija 500 qator bilan cheklandi."] if truncated else []
    elapsed = round((monotonic() - started) * 1000, 2)
    summary = "\n".join(answer)
    evidence = {"id": "q1", "title": preset.title, "purpose": preset.question, "sql_query": sql,
                "columns": columns, "rows": rows, "returned_count": len(rows),
                "is_truncated": truncated, "execution_time_ms": elapsed}
    return NaturalQueryResponse(
        sql_query=sql, chart=chart, charts=[chart] if chart else [], explanation=summary,
        summary_text=summary, rows=rows, columns=columns, data_count=len(rows), returned_count=len(rows),
        is_truncated=truncated, validation_warnings=warnings, execution_time_ms=elapsed,
        query_results=[evidence], agent_status="completed", response_type="dashboard" if chart else "text",
    )
