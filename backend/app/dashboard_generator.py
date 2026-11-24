import datetime
import logging
import uuid
import re
import time
from typing import List, Dict, Any, Optional
from app.models import DashboardResponse, KpiMetric, ChartSpec, SchemaContext, BusinessDomain
from app.schema_profiler import SchemaProfiler
from app.llm_service import llm_service
from app.query_engine import SelfHealingQueryEngine
from app.config import settings
from app.schema_retriever import SchemaRetriever

logger = logging.getLogger(__name__)

# Generic schema-name fragments only. No connected customer's concrete table
# name is treated as a privileged source of truth.
BUSINESS_TERM_LABELS = {
    "order": "Buyurtmalar", "customer": "Mijozlar", "client": "Mijozlar",
    "sale": "Sotuvlar", "invoice": "Hisob-fakturalar", "payment": "To'lovlar",
    "product": "Mahsulotlar", "inventory": "Zaxiralar", "stock": "Qoldiqlar",
    "warehouse": "Omborlar", "employee": "Xodimlar", "staff": "Xodimlar",
    "transport": "Transport", "vehicle": "Transport", "trip": "Qatnovlar",
    "production": "Ishlab chiqarish", "project": "Loyihalar",
    "contract": "Shartnomalar", "region": "Hududlar", "district": "Hududlar",
}

def humanize_name(name: str) -> str:
    """Converts technical snake_case names into professional business labels."""
    if not name:
        return ""
    clean = name.lower().strip()
    for token, label in BUSINESS_TERM_LABELS.items():
        if token in re.split(r"[_\.]+", clean):
            return label
    words = clean.replace("_", " ").split()
    return " ".join(w.capitalize() for w in words)


class DashboardGenerator:
    """
    Orchestrates executive-grade BI dashboard generation:
    1. Reads full database schema & relationships.
    2. Utilizes Gemma with C-Level Chief Data & Analytics Officer prompts.
    3. Produces 20+ chart types, strategic executive summaries, and business domains.
    4. Executes every metric through the guarded query engine.
    """

    _cached_dashboard: Optional[DashboardResponse] = None
    _cached_key: Optional[str] = None
    _cached_time: float = 0.0
    _CACHE_TTL_SECONDS = 300.0

    @classmethod
    def clear_cache(cls):
        cls._cached_dashboard = None
        cls._cached_key = None
        cls._cached_time = 0.0

    @staticmethod
    def _semantic_validator(intent: str, schema_ctx: SchemaContext):
        """Use the same intent/source checks for dashboard and chat SQL."""
        from app.agent_analyst import AIDataAnalystAgent

        matches = SchemaRetriever.rank(intent or "", schema_ctx, top_k=8)
        candidates = [match.table for match in matches] or schema_ctx.tables
        return lambda sql: AIDataAnalystAgent._validate_sql_semantics(
            intent or "dashboard metric",
            sql,
            candidates,
            schema_ctx,
        )

    @classmethod
    async def generate_dashboard(cls, force_refresh: bool = False) -> DashboardResponse:
        # Step 1: Profile database
        schema_ctx: SchemaContext = SchemaProfiler.profile()
        cache_key = "|".join([
            schema_ctx.connection_fingerprint or "",
            schema_ctx.schema_fingerprint or "",
            schema_ctx.database_type,
            schema_ctx.database_name or "",
            schema_ctx.included_schemas or "",
            schema_ctx.saved_at or "",
        ])
        if (
            not force_refresh
            and cls._cached_dashboard is not None
            and cls._cached_key == cache_key
            and (time.time() - cls._cached_time) < cls._CACHE_TTL_SECONDS
        ):
            return cls._cached_dashboard
        if not schema_ctx.tables:
            return DashboardResponse(
                kpis=[],
                charts=[],
                schema_summary="Ma'lumotlar bazasi ulanmagan yoki unda jadvallar mavjud emas.",
                executive_summary="Tizimga ma'lumotlar bazasi ulanmagan. Iltimos, boshqaruv panelini shakllantirish uchun bazaga ulaning.",
                business_domains=[],
                suggested_questions=[],
                generated_at=datetime.datetime.now().isoformat(),
                llm_model=settings.VLLM_MODEL,
                success=False,
                message="Iltimos, avval ma'lumotlar bazangizga ulaning."
            )

        # Check LLM availability
        health = await llm_service.check_health()
        
        res: DashboardResponse
        if health["online"] and health["model_ready"]:
            try:
                res = await cls._generate_via_gemma(schema_ctx)
            except Exception as e:
                logger.error("Model dashboard generation failed; using evidence-only fallback: %s", e)
                res = await cls._generate_via_heuristics(
                    schema_ctx,
                    fallback_reason="AI model so'rovi bajarilmadi; faqat deterministik SQL ko'rsatkichlari ko'rsatildi.",
                )
        else:
            logger.info("Remote vLLM / Gemma not ready. Using automated smart schema analytical heuristics.")
            res = await cls._generate_via_heuristics(
                schema_ctx, 
                fallback_reason="AI model tayyor emas; faqat deterministik SQL ko'rsatkichlari ko'rsatildi."
            )

        cls._cached_dashboard = res
        cls._cached_key = cache_key
        cls._cached_time = time.time()
        return res

    @classmethod
    async def _generate_via_gemma(cls, schema_ctx: SchemaContext) -> DashboardResponse:
        system_prompt = (
            "Siz McKinsey, Gartner va Big-4 darajasidagi Bosh Tahlilchi (Chief Data & Analytics Officer)siz.\n"
            "Vazifangiz: Berilgan ma'lumotlar bazasi sxemasini chuqur tahlil qilib, korxona rahbariyati (C-Level / Boshqaruv kengashi) uchun "
            "yuqori darajadagi strategik boshqaruv paneli (Executive BI Dashboard) loyihalashtirish.\n"
            "\n"
            "QAT'IY QOIDALAR VA MEZONLAR:\n"
            "1. FORMAT: Faqat to'g'ri JSON formatida javob bering. Hech qanday kirish so'zi yoki markdown teglari (```json) yozmang.\n"
            "2. XAVFSIZLIK: Barcha SQL so'rovlar faqat xavfsiz SELECT bo'lishi shart (DROP, DELETE, UPDATE mutlaqo taqiqlanadi).\n"
            "3. BAZA BILAN TO'LIQ MOSLIK: Faqat sxemada aniq ko'rsatilgan jadvallar va ustunlardan foydalaning. Agregat ustunlarga doim 'AS' orqali aniq taxallus bering.\n"
            "4. DIAGRAMMA TURLARI (Kamida 6 ta turli xil ilg'or diagramma tanlang):\n"
            "   - 'composed': Bar + Line gibrid grafik (hajm va o'rtacha dinamikani birga ko'rsatish)\n"
            "   - 'area': Gradient to'lqinli vaqt dinamikasi\n"
            "   - 'stacked_area': Qatlamli hududiy dinamika\n"
            "   - 'horizontal_bar': Gorizontal tasma grafik (Top-5 yoki Top-10 reytinglar uchun eng qulay)\n"
            "   - 'bar': Vertikal ustunli taqqoslov\n"
            "   - 'grouped_bar': Guruhlangan ustunli taqqoslash\n"
            "   - 'donut': Halqasimon ulush taqsimoti (markazida umumiy ko'rsatkich bilan)\n"
            "   - 'pie': Sektorli doira grafik\n"
            "   - 'semi_donut': Spidometr/Yarim doiraviy samaradorlik ko'rsatkichi\n"
            "   - 'radar': Ko'p o'qli tahlil (3-6 ta mezon bo'yicha balans)\n"
            "   - 'radial_bar': Aylana progress ko'rsatkichi\n"
            "   - 'funnel': Bosqichma-bosqich jarayon yoki holatlar zanjiri (konversiya)\n"
            "   - 'stepped_line': Bosqichli o'zgarishlar dinamikasi\n"
            "   - 'line': Chiziqli dinamika\n"
            "   - 'treemap': Maydonli bloklar orqali nisbatlar\n"
            "   - 'table': Batafsil tahliliy hisobot jadvali\n"
            "5. NO ENGLISH DATABASE TABLE NAMES: 'title', 'description', 'insight', 'recommendation', 'badge' maydonlarida hech qachon inglizcha jadval yoki ustun nomlarini (masalan, 'trip jadvali', 'waybill table') ISHLATMANG! "
            "Ularning o'rniga sxema izohi va biznes ma'nosidan kelib chiqqan sof o'zbek atamalarini ishlating.\n"
            "6. ISBOTLANGAN NATIJA: SQL natijasidan oldin raqamli xulosa, trend, delta, target yoki tavsiya uydirmang. "
            "Ularni null qoldiring; ilova bajarilgan so'rov natijasidan faktik izoh tuzadi.\n"
            "7. TEZKOR SAVOLLAR: 'suggested_questions' ro'yxatida aniq 5 ta savol bering. Savollarda ALBATTA 'kechagi', 'haftalik', 'o''tgan oy' so'zlari qatnashishi SHART!\n"
            "8. BIZNES YO'NALISHLARI: 'business_domains' ro'yxatida bazadan kelib chiqib 3-4 ta o'zbekcha biznes modulini belgilang."
        )

        user_prompt = f"""
{schema_ctx.schema_prompt}

Quyidagi tuzilmada 100% O'ZBEK TILIDA toza JSON qaytaring:
{{
  "executive_summary": "Korxona asosiy faoliyati va ma'lumotlar tahlili bo'yicha 2-3 jumlali chuqur strategik xulosa.",
  "schema_summary": "Tizim arxitekturasi va ma'lumotlar oqimining qisqacha tavsifi.",
  "business_domains": [
    {{ "name": "Sxemadan aniqlangan biznes yo'nalishi", "description": "Faqat mavjud jadval va ustunlardan chiqarilgan tavsif", "status": "Faol" }}
  ],
  "suggested_questions": [
    "O'tgan oy bo'yicha asosiy ko'rsatkich dinamikasi qanday?",
    "Haftalik yozuvlar soni qanday o'zgardi?",
    "Kechagi kungi eng katta 5 ta ko'rsatkich qaysilar?",
    "O'tgan oydagi eng faol 5 ta toifa qaysilar?",
    "Haftalik holatlar bo'yicha taqsimot qanday?"
  ],
  "kpis": [
    {{
      "title": "Ko'rsatkich nomi (o'zbekcha)",
      "sql_query": "SELECT COUNT(*) AS value FROM ...",
      "unit": "ta",
      "delta": null,
      "delta_type": "positive",
      "target": null,
      "category": "Operatsion",
      "description": "Ko'rsatkich tavsifi"
    }}
  ],
  "charts": [
    {{
      "title": "Diagramma nomi (o'zbekcha)",
      "badge": "Vaqt Dinamikasi",
      "chart_type": "composed",
      "description": "Grafik maqsadi",
      "sql_query": "SELECT ... AS x, ... AS y FROM ... GROUP BY 1 ORDER BY 1 LIMIT 12",
      "x_axis": "x",
      "y_axis": "y",
      "secondary_y_axis": null,
      "insight": null,
      "recommendation": null
    }}
  ]
}}
"""
        response_text = await llm_service.generate_completion(system_prompt, user_prompt)
        parsed_json = llm_service.extract_json(response_text)

        # 1. Execute KPIs
        resolved_kpis: List[KpiMetric] = []
        for i, k in enumerate(parsed_json.get("kpis", [])[:6]):
            sql = k.get("sql_query", "")
            intent = f"KPI: {k.get('title')}"
            success, rows, final_sql, err = await SelfHealingQueryEngine.execute_with_self_healing(
                sql,
                schema_ctx.schema_prompt,
                user_intent=intent,
                schema_ctx=schema_ctx,
                semantic_validator=cls._semantic_validator(intent, schema_ctx),
            )
            if not success or len(rows) != 1 or len(rows[0]) != 1:
                logger.warning("Skipping failed dashboard KPI '%s': %s", k.get("title"), err)
                continue
            first_row = rows[0]
            val = list(first_row.values())[0] if first_row else 0

            resolved_kpis.append(
                KpiMetric(
                    id=f"kpi_{i+1}",
                    title=k.get("title", f"Ko'rsatkich {i+1}"),
                    value=val,
                    unit=k.get("unit", ""),
                    delta=None,
                    delta_type="neutral",
                    target=None,
                    category=k.get("category", "Asosiy"),
                    sparkline_data=[],
                    description=k.get("description", ""),
                    sql_query=final_sql
                )
            )

        # 2. Execute Charts
        resolved_charts: List[ChartSpec] = []
        palette_list = ["indigo", "emerald", "blue", "violet", "amber", "cyan"]
        raw_charts = parsed_json.get("charts", [])

        for i, c in enumerate(raw_charts[:8]):
            sql = c.get("sql_query", "")
            ctype = c.get("chart_type", "bar")
            intent = f"Chart: {c.get('title')}"

            success, rows, final_sql, err = await SelfHealingQueryEngine.execute_with_self_healing(
                sql,
                schema_ctx.schema_prompt,
                user_intent=intent,
                schema_ctx=schema_ctx,
                semantic_validator=cls._semantic_validator(intent, schema_ctx),
            )
            if not success or not rows:
                logger.warning("Skipping failed dashboard chart '%s': %s", c.get("title"), err)
                continue

            # Auto-detect x_axis and y_axis from returned row keys if not accurate
            x_axis = c.get("x_axis")
            y_axis = c.get("y_axis")
            data_keys = []
            if rows:
                keys = list(rows[0].keys())
                if x_axis not in keys and len(keys) >= 1:
                    x_axis = keys[0]
                if y_axis not in keys and len(keys) >= 2:
                    y_axis = keys[1]
                data_keys = [k for k in keys if k != x_axis]

            resolved_charts.append(
                ChartSpec(
                    id=f"chart_{i+1}",
                    title=c.get("title", f"Tahliliy Grafik {i+1}"),
                    badge=c.get("badge", "Tahlil"),
                    description=c.get("description", ""),
                    chart_type=ctype,
                    sql_query=final_sql,
                    x_axis=x_axis,
                    y_axis=y_axis,
                    secondary_y_axis=c.get("secondary_y_axis"),
                    data_keys=data_keys,
                    color_palette=palette_list[i % len(palette_list)],
                    insight=f"So'rov {len(rows)} ta natija qatorini qaytardi.",
                    recommendation=None,
                    data=rows
                )
            )

        # Business-domain labels come from actual table names, not model claims.
        business_domains = cls._derive_business_domains(schema_ctx)

        suggested = parsed_json.get("suggested_questions", [])
        if not suggested or len(suggested) < 3:
            suggested = cls._get_default_suggested_questions(schema_ctx)

        exec_summary = (
            f"Bajarilgan va tekshirilgan SQL so'rovlari asosida {len(resolved_kpis)} ta KPI "
            f"va {len(resolved_charts)} ta grafik tayyorlandi. Raqamli xulosalar faqat natija qatorlariga asoslanadi."
        )

        return DashboardResponse(
            kpis=resolved_kpis,
            charts=resolved_charts,
            schema_summary=(
                f"{schema_ctx.total_tables} ta profillangan jadval orasidan model taklif qilgan va "
                "validator tasdiqlagan so'rovlar bajarildi."
            ),
            executive_summary=exec_summary,
            business_domains=business_domains,
            suggested_questions=suggested[:5],
            generated_at=datetime.datetime.now().isoformat(),
            llm_model=settings.VLLM_MODEL,
            success=True,
            message="Executive Dashboard muvaffaqiyatli shakllantirildi."
        )

    @classmethod
    def _business_tables(cls, schema_ctx: SchemaContext) -> List:
        technical_tokens = (
            "_log", "_progress", "audit", "notification", "history", "temp_",
            "attachment", "message_log", "import_run", "import_page",
        )
        remaining = [
            table for table in schema_ctx.tables
            if not any(token in table.name.lower() for token in technical_tokens)
        ]
        return sorted(
            remaining,
            key=lambda table: (
                bool(table.comment),
                table.object_type in {"view", "materialized_view"},
                bool(table.primary_key or table.candidate_keys),
                table.row_count or 0,
                table.name,
            ),
            reverse=True,
        )

    @classmethod
    async def _generate_via_heuristics(cls, schema_ctx: SchemaContext, fallback_reason: str) -> DashboardResponse:
        """
        Smart heuristic generator: analyzes tables, joins, dates and numeric columns
        to construct a 6-chart multi-type executive dashboard in clean Uzbek.
        """
        tables = cls._business_tables(schema_ctx)
        if not tables:
            return DashboardResponse(
                kpis=[],
                charts=[],
                schema_summary="Tahlil uchun biznes jadvallari topilmadi.",
                executive_summary="Faqat texnik yoki bo'sh jadvallar mavjud.",
                business_domains=[],
                suggested_questions=[],
                generated_at=datetime.datetime.now().isoformat(),
                llm_model=settings.VLLM_MODEL,
                success=False,
                message=fallback_reason,
            )
        kpis: List[KpiMetric] = []
        charts: List[ChartSpec] = []
        
        # 1. Total counts for major tables with Uzbek labels
        for i, t in enumerate(tables[:4]):
            sql = f"SELECT COUNT(*) AS total_count FROM {t.name};"
            succ, rows, final_sql, _ = await SelfHealingQueryEngine.execute_with_self_healing(sql, schema_ctx.schema_prompt, schema_ctx=schema_ctx)
            if not succ or not rows:
                continue
            val = rows[0]["total_count"]
            
            t_label = humanize_name(t.name)
            kpis.append(
                KpiMetric(
                    id=f"kpi_{i+1}",
                    title=f"Jami {t_label}",
                    value=val,
                    unit="ta",
                    delta=None,
                    delta_type="neutral",
                    target=None,
                    category="Operatsion",
                    sparkline_data=[],
                    description=f"{t_label} bo'yicha jami ro'yxatdan o'tgan yozuvlar soni.",
                    sql_query=final_sql
                )
            )

        # 2. Identify date, numeric, and categorical columns
        date_col = None
        num_col = None
        cat_col = None
        main_table = tables[0].name

        for t in tables:
            for c in t.columns:
                cname = c.name.lower()
                ctype = c.data_type.lower()
                if not date_col and (
                    any(token in ctype for token in ["date", "time"])
                    or cname in {"date", "sana"}
                    or cname.endswith(("_date", "_at", "_time"))
                ):
                    date_col = (t.name, c.name)
                if not num_col and any(n in ctype for n in ["int", "num", "float", "dec"]) and not c.is_primary_key and not c.is_foreign_key:
                    num_col = (t.name, c.name)
                if not cat_col and any(ct in ctype for ct in ["varchar", "text", "char"]) and c.sample_values and not c.is_primary_key:
                    cat_col = (t.name, c.name)

        dialect = schema_ctx.database_type.lower()
        
        # Chart 1: Composed Chart (Vaqt Dinamikasi va Hajm)
        if date_col:
            dt_table, dt_col = date_col
            if "postgres" in dialect:
                time_sql = f"SELECT TO_CHAR({dt_col}, 'YYYY-MM') AS davr, COUNT(*) AS hajm FROM {dt_table} WHERE {dt_col} IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 10;"
            else:
                time_sql = f"SELECT substr({dt_col}, 1, 7) AS davr, COUNT(*) AS hajm FROM {dt_table} WHERE {dt_col} IS NOT NULL GROUP BY 1 ORDER BY 1 DESC LIMIT 10;"
            
            succ, rows, fsql, _ = await SelfHealingQueryEngine.execute_with_self_healing(time_sql, schema_ctx.schema_prompt, schema_ctx=schema_ctx)
            if succ and rows:
                charts.append(
                ChartSpec(
                    id="chart_composed_trend",
                    title=f"Oylar kesimida faollik dinamikasi ({humanize_name(dt_table)})",
                    badge="Vaqt Dinamikasi",
                    description="Qaytgan yozuvlar sonining oylar kesimidagi dinamikasi",
                    chart_type="composed",
                    sql_query=fsql,
                    x_axis="davr",
                    y_axis="hajm",
                    color_palette="indigo",
                    insight=f"So'rov {len(rows)} ta vaqt nuqtasini qaytardi.",
                    recommendation=None,
                    data=rows[::-1]
                )
            )

        # Chart 2: Horizontal Bar Chart (Top Reyting)
        if cat_col:
            tname, cname = cat_col
            hbar_sql = f"SELECT {cname} AS toifa, COUNT(*) AS soni FROM {tname} GROUP BY {cname} ORDER BY soni DESC LIMIT 8;"
            succ, rows, fsql, _ = await SelfHealingQueryEngine.execute_with_self_healing(hbar_sql, schema_ctx.schema_prompt, schema_ctx=schema_ctx)
            if succ and rows:
                charts.append(
                ChartSpec(
                    id="chart_horizontal_bar",
                    title=f"Yetakchi yo'nalishlar reytingi ({humanize_name(tname)})",
                    badge="Reyting va Yetakchilar",
                    description=f"{cname.replace('_', ' ').capitalize()} bo'yicha taqqoslama hajm",
                    chart_type="horizontal_bar",
                    sql_query=fsql,
                    x_axis="toifa",
                    y_axis="soni",
                    color_palette="emerald",
                    insight=f"So'rov {len(rows)} ta toifani qaytardi.",
                    recommendation=None,
                    data=rows
                )
            )

        # Chart 3: Donut Chart (Holat va Taqsimot Ulushi)
        if cat_col:
            tname, cname = cat_col
            donut_sql = f"SELECT {cname} AS status, COUNT(*) AS miqdor FROM {tname} WHERE {cname} IS NOT NULL GROUP BY {cname} ORDER BY miqdor DESC LIMIT 5;"
            succ, rows, fsql, _ = await SelfHealingQueryEngine.execute_with_self_healing(donut_sql, schema_ctx.schema_prompt, schema_ctx=schema_ctx)
            if succ and rows:
                charts.append(
                ChartSpec(
                    id="chart_donut",
                    title=f"Holatlar mutanosibligi ({humanize_name(tname)})",
                    badge="Taqsimot va Ulush",
                    description="Tizimdagi segmentlarning foizdagi ulushi",
                    chart_type="donut",
                    sql_query=fsql,
                    x_axis="status",
                    y_axis="miqdor",
                    color_palette="violet",
                    insight=f"So'rov {len(rows)} ta segmentni qaytardi.",
                    recommendation=None,
                    data=rows
                )
            )

        # Chart 4: Area Chart (Kunlik davr)
        if date_col:
            dt_table, dt_col = date_col
            area_sql = f"SELECT DATE({dt_col}) AS sana, COUNT(*) AS soni FROM {dt_table} GROUP BY 1 ORDER BY 1 DESC LIMIT 14;"
            succ, rows, fsql, _ = await SelfHealingQueryEngine.execute_with_self_healing(area_sql, schema_ctx.schema_prompt, schema_ctx=schema_ctx)
            if succ and rows:
                charts.append(
                    ChartSpec(
                        id="chart_area",
                        title=f"Kunlik yozuvlar dinamikasi ({humanize_name(dt_table)})",
                        badge="Kunlik Oqim",
                        description="Ma'lumot mavjud bo'lgan eng so'nggi 14 sana bo'yicha yozuvlar soni",
                        chart_type="area",
                        sql_query=fsql,
                        x_axis="sana",
                        y_axis="soni",
                        color_palette="blue",
                        insight=f"So'rov {len(rows)} ta kunlik nuqtani qaytardi.",
                        recommendation=None,
                        data=rows[::-1]
                    )
                )

        # Chart 5: privacy-safe sample table. Sensitive/identity fields are excluded.
        safe_columns = [
            column.name for column in tables[0].columns
            if not any(token in column.name.lower() for token in SchemaProfiler._SENSITIVE_TOKENS)
            and not any(token in column.data_type.lower() for token in ["binary", "bytea", "blob"])
        ][:8]
        table_sql = (
            f"SELECT {', '.join(safe_columns)} FROM {tables[0].name} LIMIT 10;"
            if safe_columns else ""
        )
        succ, rows, fsql, _ = (
            await SelfHealingQueryEngine.execute_with_self_healing(
                table_sql, schema_ctx.schema_prompt, schema_ctx=schema_ctx
            )
            if table_sql else (False, [], "", "No privacy-safe display columns")
        )
        if succ and rows:
            charts.append(
                ChartSpec(
                    id="chart_table",
                    title=f"Namunaviy yozuvlar ({humanize_name(tables[0].name)})",
                    badge="Hisobot Jadvali",
                    description="Maxfiy identifikator va aloqa maydonlarisiz dastlabki 10 qator",
                    chart_type="table",
                    sql_query=fsql,
                    insight=f"So'rov {len(rows)} ta yozuvni qaytardi.",
                    recommendation=None,
                    data=rows
                )
            )

        business_domains = cls._derive_business_domains(schema_ctx)

        return DashboardResponse(
            kpis=kpis,
            charts=charts,
            schema_summary=f"{len(tables)} ta biznes jadvali aniqlandi; faqat bajarilgan deterministik so'rovlar ko'rsatildi.",
            executive_summary=(
                f"{len(kpis)} ta KPI va {len(charts)} ta grafik read-only SQL natijalari asosida tayyorlandi. "
                "AI model tayyor bo'lmagani uchun strategik talqin berilmadi."
            ),
            business_domains=business_domains,
            suggested_questions=cls._get_default_suggested_questions(schema_ctx),
            generated_at=datetime.datetime.now().isoformat(),
            llm_model=settings.VLLM_MODEL,
            success=True,
            message=fallback_reason
        )

    @classmethod
    def _derive_business_domains(cls, schema_ctx: SchemaContext) -> List[BusinessDomain]:
        """Maps connected tables into executive Uzbek business modules."""
        domains: List[BusinessDomain] = []
        for t in cls._business_tables(schema_ctx)[:5]:
            clean = t.name.lower()
            name = humanize_name(clean)
            desc = t.comment or f"{name} bo'yicha operatsion ma'lumotlar oqimi"
            
            domains.append(
                BusinessDomain(
                    name=name,
                    description=desc,
                    record_count=t.row_count,
                    status="Faol"
                )
            )
        return domains

    @classmethod
    def _get_default_suggested_questions(cls, schema_ctx: SchemaContext) -> List[str]:
        """
        Dynamically derives 5 high-impact executive questions incorporating
        'kechagi', 'haftalik', and 'o'tgan oy' in natural Uzbek without raw English table names.
        """
        if not schema_ctx or not schema_ctx.tables:
            return []

        business_tables = cls._business_tables(schema_ctx)
        if not business_tables:
            return []
        t1 = humanize_name(business_tables[0].name)
        t2 = humanize_name(business_tables[1].name) if len(business_tables) > 1 else t1
        t3 = humanize_name(business_tables[2].name) if len(business_tables) > 2 else t1

        return [
            f"O'tgan oy bo'yicha jami {t1} dinamikasi va umumiy hajm",
            f"Haftalik {t2} yozuvlari soni va dinamikasi",
            f"Kechagi kungi {t1} yozuvlari soni",
            f"O'tgan oydagi {t3} bo'yicha top 5 ta toifa",
            f"Haftalik holatlar bo'yicha yozuvlar taqsimoti"
        ]
