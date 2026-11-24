import asyncio
import logging
import re
import time
from datetime import date
from decimal import Decimal, localcontext
from typing import Dict, Any, List, Optional, Tuple
import sqlglot
from sqlglot import exp
from sqlglot.lineage import lineage
from app.chat_charts import chart_for_rows
from app.database import db_manager
from app.llm_service import llm_service, LLMRateLimitError, LLMServiceError, LLMUnavailableError
from app.query_engine import SelfHealingQueryEngine
from app.relationship_guard import RelationshipGuard
from app.schema_profiler import SchemaProfiler
from app.schema_retriever import SchemaRetriever
from app.semantic_catalog import SemanticCatalogError, SemanticCatalogManager
from app.monthly_metric import compile_monthly_metric
from app.sql_validator import validate_and_sanitize_sql
from app.intent_guard import validate_explicit_intent, normalize_explicit_null_order, requested_group_columns
from app.question_plan import normalize_evidence, plan_question
from app.models import (
    NaturalQueryResponse, SchemaContext, TableMeta
)

logger = logging.getLogger("ai_analytics.agent")
class AIDataAnalystAgent:
    """
    Autonomous Data Analyst Agent (ReAct: Reason + Act + Probe + Verify)
    Selects sources from live metadata and reviewed semantics, validates generated
    SQL before execution, and synthesizes only the rows actually returned.
    """

    @classmethod
    def discover_candidate_tables(cls, prompt: str, schema_ctx: SchemaContext, top_k: int = 3) -> List[TableMeta]:
        """Rank tables from live metadata and reviewed semantic aliases only."""
        return [
            match.table
            for match in SchemaRetriever.rank(prompt, schema_ctx, top_k=top_k)
        ]

    @classmethod
    def resolve_analytical_prompt(
        cls,
        prompt: str,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> str:
        """Turn a short analytical follow-up into a standalone question."""
        current = (prompt or "").strip()
        if not current or not history:
            return current

        lower = normalize_evidence(current)
        history = history[-8:]
        last = history[-1]
        reply = last.get("content")
        clarification_text = normalize_evidence(reply[:1500]) if last.get("role") == "assistant" and isinstance(reply, str) else ""
        awaiting_answer = clarification_text and (
            last.get("clarification") is True or "aniqlashtiring" in clarification_text
            or re.match(r"(?:qaysi|qanday|which)\b[^\n?]{1,160}\?\s*$", clarification_text)
        )
        standalone = re.search(r"\b(?:qancha|nechta\w*|qaysi|qanday|nega|nima|soni|how|what|why|"
                               r"ko'rsat\w*|hisobla\w*|taqqosla\w*|solishtir\w*)\b", lower)
        clarification_answer = (awaiting_answer and len(current.split()) <= 8 and len(current) <= 160
                                and "?" not in current and not standalone and cls.classify_user_intent(current) == "DATA_QUERY"
                                and lower.strip(".!") not in {"rahmat", "tashakkur", "xo'p", "ok", "thanks"})
        markers = (
            "endi ", "faqat ", "shulardan", "ulardan", "shu ", "o'sha ",
            "o'tgan savol", "avvalgi", "yana ",
        )
        is_follow_up = len(current.split()) <= 14 and (lower.startswith(markers)
            or bool(re.search(r"(?:-chi|bo'lsa-chi)[?!.]*$", lower))
            or bool(re.fullmatch(r"(?:nega|nima uchun|sababi nima|why)[?!.]*", lower)))
        if not (is_follow_up or clarification_answer):
            return current

        for index in range(len(history) - 1, -1, -1):
            item = history[index]
            previous = item.get("content")
            if item.get("role") == "user" and isinstance(previous, str) and previous.strip():
                previous = cls.resolve_analytical_prompt(previous[:3000], history[:index])
                # A stand-alone replacement year must not leave both years in
                # the question passed to date validation. Keep actual ranges
                # and explicit comparison questions for the planner.
                year_change = re.fullmatch(
                    r"(?:endi\s+)?(?:faqat\s+)?((?:19|20)\d{2})(?:[ -]?yil\w*)?(?:-chi)?[?!.]*", lower)
                years = set(re.findall(r"\b(?:19|20)\d{2}\b", previous))
                if year_change and len(years) == 1 and not re.search(r"\d{4}-\d\d-\d\d", previous):
                    return re.sub(r"\b(?:19|20)\d{2}\b", year_change.group(1), previous)
                if clarification_answer:
                    scope = re.search(r"\bqaysi (hudud|filial|shahar|davr|oy|yil|jadval|mahsulot)\b", clarification_text)
                    label = scope.group(1) if scope else "Aniqlashtirish"
                    return f"{previous}. {label}: {current}"
                return f"{previous}. Keyingi savol (yangi shart avvalgisini almashtiradi): {current}"
        return current

    @classmethod
    def _build_universal_sql_system_prompt(
        cls,
        candidate: TableMeta,
        schema_ctx: SchemaContext,
        candidates: Optional[List[TableMeta]] = None,
    ) -> str:
        """Database-independent SQL contract used for every analytical question."""
        dialect = (schema_ctx.database_type or "sql").lower()
        allowed_sources = candidates or [candidate]
        source_names = ", ".join(table.name for table in allowed_sources)
        return (
            "You are a cautious analytics SQL planner. Return one valid JSON object only.\n"
            f"SQL dialect: {dialect}. Primary candidate table: {candidate.name}. "
            f"Allowed focused source tables: {source_names}.\n"
            "Security rules:\n"
            "1. Produce exactly one read-only SELECT or WITH ... SELECT statement. Never use DDL, DML, COPY, CALL, SET, PRAGMA, filesystem, network, sleep, lock, or administrative functions.\n"
            "2. Use only tables and columns shown in the supplied focused schema.\n"
            "3. JOIN only through a DECLARED FOREIGN KEY or an explicitly reviewed SEMANTIC CATALOG relationship. If the required relationship is absent, return an empty sql_query and a short clarification question.\n"
            "Correctness rules:\n"
            "4. Preserve grain. After a one-to-many join, count entities with COUNT(DISTINCT primary_key); pre-aggregate child facts before joining when measures could multiply.\n"
            "4a. Prefer LEFT JOIN for dimensions so orphan dimension keys do not silently remove fact rows; use INNER JOIN only when the question explicitly requires matched records.\n"
            "4b. Compare periods or segments of one fact table using that table. When distinct sources are needed, pre-aggregate each at the requested grain before joining. Never join two raw fact tables directly.\n"
            "5. Never invent status codes, enum values, units, currencies, filters, targets, deltas, or dates. Use only values present in the schema context or requested by the user.\n"
            "5a. Choose label and localization columns only when they are present in the supplied schema; never assume a particular language column exists.\n"
            "6. Distinguish zero from NULL. Use explicit date boundaries and do not silently choose a time period.\n"
            "6a. Drive actual-results queries from the fact table. Do not generate missing calendar periods or COALESCE missing facts to zero unless explicitly requested.\n"
            "6b. If a global latest snapshot is requested, compute MAX(date) over the entire source table without organization/type filters, then filter the results.\n"
            "Use a scalar subquery comparison for the latest date, never CROSS JOIN the latest-date CTE.\n"
            "6c. Preserve explicitly requested dimension IDs (including currency_id) in the result; labels may be additional, never a replacement.\n"
            "6d. Return exactly the requested grain and columns, dimensions first then measures. Do not add organization_id grouping when only currency totals are requested. Prefer reviewed default_label_column for natural-language entity labels.\n"
            "7. For non-aggregate detail queries add LIMIT 500. Use stable ORDER BY for top-N queries.\n"
            "7a. Every explicit qualifier in the question (time period, region, organization, status, product, top-N) must appear in SQL as a filter, grouping, ordering, or limit.\n"
            "7b. For top-N queries exclude NULL category labels with IS NOT NULL and put nullable aggregate metrics last with COALESCE or NULLS LAST.\n"
            "8. explanation and recommendation must not contain numeric claims; the application builds factual prose after SQL execution.\n"
            "Output JSON keys: sql_query, chart_type, badge, title, x_axis, y_axis, table_used, explanation, recommendation, clarification, multi_charts.\n"
            "multi_charts must be empty unless the user explicitly requested multiple independent metrics; every subquery follows the same rules.\n"
            "Reasoning rule: Keep internal thinking concise and directly output the final JSON enclosed in ```json ... ``` without any text outside the JSON block."
        )

    @staticmethod
    def _clarification_response(prompt: str, start_time: float, reason: str) -> NaturalQueryResponse:
        return NaturalQueryResponse(
            sql_query="",
            chart=None,
            charts=[],
            explanation=reason,
            summary_text=reason,
            table_used=None,
            columns=[],
            rows=[],
            data_count=0,
            execution_time_ms=(time.time() - start_time) * 1000,
            success=False,
            error="clarification_required",
            response_type="text",
        )

    @staticmethod
    def _failure_response(start_time: float, reason: str, error_code: str) -> NaturalQueryResponse:
        return NaturalQueryResponse(
            sql_query="",
            chart=None,
            charts=[],
            explanation=reason,
            summary_text=reason,
            table_used=None,
            columns=[],
            rows=[],
            data_count=0,
            returned_count=0,
            execution_time_ms=(time.time() - start_time) * 1000,
            success=False,
            error=error_code,
            response_type="text",
        )

    @staticmethod
    def _greeting_response(start_time: float) -> NaturalQueryResponse:
        text = (
            "Assalomu alaykum! Men TabloAI tahlilchisiman. Ulangan bazangiz bo‘yicha savol bering. "
            "Masalan, qaysi filial eng ko‘p sotganini yoki tushum oyma-oy qanday o‘zgarganini so‘rashingiz mumkin."
        )
        return NaturalQueryResponse(
            sql_query="",
            chart=None,
            charts=[],
            explanation=text,
            summary_text=text,
            table_used=None,
            columns=[],
            rows=[],
            data_count=0,
            execution_time_ms=(time.time() - start_time) * 1000,
            success=True,
            response_type="text",
        )

    @staticmethod
    def _out_of_domain_response(start_time: float) -> NaturalQueryResponse:
        text = (
            "Men ulangan korporativ ma’lumotlarni tahlil qilaman. Qaysi biznes ko‘rsatkichi, "
            "davr yoki bo‘lim sizni qiziqtiradi?"
        )
        return NaturalQueryResponse(
            sql_query="",
            chart=None,
            charts=[],
            explanation=text,
            summary_text=text,
            table_used=None,
            columns=[],
            rows=[],
            data_count=0,
            execution_time_ms=(time.time() - start_time) * 1000,
            success=True,
            response_type="text",
        )

    @staticmethod
    def _needs_multi_table_context(prompt: str) -> bool:
        lower = prompt.lower()
        return (
            any(token in lower for token in ["farq", "taqqos", "solishtir", "nisbatan", "versus", " vs "])
            or ("reja" in lower and any(token in lower for token in ["amal", "haqiqat", "fakt"]))
            or ("debitor" in lower and "kreditor" in lower)
        )

    @classmethod
    def _build_multi_focused_schema_prompt(
        cls,
        candidates: List[TableMeta],
        schema_ctx: SchemaContext,
    ) -> str:
        selected = candidates[:4]
        sections = [
            "### KO'P MANBALI TAHLIL",
            "Savolga mos jadvalni tanlang. Bir nechta manba kerak bo'lsa bir xil kesimda oldindan hisoblang: "
            + ", ".join(f"`{table.name}`" for table in selected),
        ]
        sections.extend(cls._build_focused_schema_prompt(table, schema_ctx) for table in selected)
        selected_names = {table.name for table in selected}
        relationships = [
            relationship for relationship in schema_ctx.relationships
            if relationship.from_table in selected_names and relationship.to_table in selected_names
        ]
        if relationships:
            sections.append("### TANLANGAN MANBALAR ORASIDAGI TASDIQLANGAN BOG'LANISHLAR")
            sections.extend(
                f"- {relationship.join_hint}"
                for relationship in relationships
                if relationship.verified and relationship.source in {"foreign_key", "semantic_catalog"}
            )
        return "\n\n".join(section for section in sections if section)

    @classmethod
    def _validate_sql_semantics(
        cls,
        prompt: str,
        sql: str,
        candidates: List[TableMeta],
        schema_ctx: Optional[SchemaContext] = None,
    ) -> Tuple[bool, Optional[str]]:
        """Reject structurally valid SQL that plainly misses the user's requested metric."""
        try:
            statement = sqlglot.parse_one(sql)
        except Exception as exc:
            return False, f"Semantic validation could not parse SQL: {exc}"

        intent_ok, intent_error = validate_explicit_intent(prompt, sql, schema_ctx)
        if not intent_ok:
            if schema_ctx is not None:
                metric_ok, metric_error = SemanticCatalogManager.validate_metric_sql(prompt, sql, schema_ctx)
                if not metric_ok:
                    intent_error = f"{intent_error} Also: {metric_error}"
            return False, intent_error

        cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
        referenced = {
            table.name.lower()
            for table in statement.find_all(exp.Table)
            if table.name.lower() not in cte_names
        }
        allowed = {table.name.split(".")[-1].lower() for table in candidates}
        allowed.update(table.name.lower() for table in candidates)
        if not referenced.intersection(allowed):
            return False, (
                "The SQL does not use any focused source table selected for the question: "
                + ", ".join(sorted(allowed))
            )

        lower = prompt.lower()
        asks_for_aggregate = any(
            token in lower
            for token in ["nechta", "soni", "qancha", "jami", "o'rtacha", "o‘rtacha", "summa", "miqdor"]
        )
        has_aggregate = any(
            statement.find(kind) is not None
            for kind in (exp.Count, exp.Sum, exp.Avg, exp.Min, exp.Max)
        )
        if asks_for_aggregate and not has_aggregate:
            return False, "The user asked for an aggregate metric, but SQL contains no COUNT/SUM/AVG/MIN/MAX."

        top_match = (
            re.search(r"\btop\s+(\d+)\b", lower)
            or re.search(r"\beng\s+ko['‘’`]?p[^\d]{0,40}(\d+)\s*(?:ta)?\b", lower)
            or re.search(r"\b(\d+)\s*ta\s+eng\s+ko['‘’`]?p\b", lower)
        )
        if top_match:
            requested_limit = int(top_match.group(1))
            limit_match = re.search(r"\bLIMIT\s+(\d+)\b", sql, re.IGNORECASE)
            if statement.args.get("order") is None or not limit_match:
                return False, "A top-N question requires deterministic ORDER BY and LIMIT."
            if int(limit_match.group(1)) != requested_limit:
                return False, f"The user requested top {requested_limit}, so LIMIT must be {requested_limit}."
            sql_lower = sql.lower()
            has_not_null_filter = bool(re.search(
                r"\bIS\s+NOT\s+NULL\b|\bNOT\s+[\w\".]+\s+IS\s+NULL\b",
                sql,
                re.IGNORECASE,
            ))
            if not has_not_null_filter:
                return False, (
                    "A top-N query must exclude NULL category labels or metric inputs with an explicit IS NOT NULL filter."
                )
            has_nullable_aggregate = any(
                statement.find(kind) is not None for kind in (exp.Sum, exp.Avg, exp.Min, exp.Max)
            )
            if has_nullable_aggregate and not any(
                marker in sql_lower for marker in ["nulls last", "coalesce("]
            ):
                return False, (
                    "A top-N query with SUM/AVG/MIN/MAX must use COALESCE or ORDER BY ... NULLS LAST "
                    "so NULL aggregates cannot rank first."
                )

        distinct_measures = ("reja" in lower and any(term in lower for term in ("amal", "haqiqat", "fakt"))
                             or "debitor" in lower and "kreditor" in lower)
        if distinct_measures and len(candidates) > 1:
            focused_refs = referenced.intersection(allowed)
            if len(focused_refs) < 2:
                return False, (
                    "This is a comparison question and must use at least two focused source tables, "
                    "pre-aggregated at the same grain."
                )

        explicit_period = re.search(
            r"\b(kecha|kechagi|bugun|bugungi|o['‘’`]tgan\s+oy|bu\s+oy|o['‘’`]tgan\s+hafta|shu\s+hafta|o['‘’`]tgan\s+yil|bu\s+yil)\b",
            lower,
        )
        if explicit_period:
            sql_lower = sql.lower()
            has_time_constraint = (
                statement.find(exp.Where) is not None
                and any(token in sql_lower for token in ["date", "time", "created", "updated", "interval", "current_"])
            )
            if not has_time_constraint:
                return False, "The user specified a time period, but SQL has no explicit date/time filter."

        sql_lower = sql.lower()
        if False and "vakolatli" in lower:  # retired database-specific rule
            if statement.find(exp.Where) is None or not any(k in sql_lower for k in ["vakolat", "district_id"]):
                return False, "The 'vakolatli' qualifier must be represented by an explicit SQL filter: w.district_id IS NOT NULL AND (w.organization_id NOT IN (1, 2) OR w.organization_id IS NULL) AND w.name NOT ILIKE '%цех%' AND w.name NOT ILIKE '%склад%' (yoki name ILIKE '%vakolat%')."
        if any(token in lower for token in ["ijara", "ijaradagi", "ijaraga"]):
            if statement.find(exp.Where) is None or not any(token in sql_lower for token in ["rent", "ijara"]):
                return False, "The rental qualifier must be represented by an explicit SQL filter."
        if "faol" in lower:
            active_markers = ["active", "status", "end_date", "is_deleted", "deleted"]
            if statement.find(exp.Where) is None or not any(marker in sql_lower for marker in active_markers):
                return False, "The 'faol' qualifier must be represented by an explicit status/activity filter."

        if schema_ctx is not None:
            return SemanticCatalogManager.validate_metric_sql(prompt, sql, schema_ctx)
        return True, None

    @staticmethod
    def _normalize_top_n_sql(prompt: str, sql: str) -> str:
        """Deterministically keep NULL labels/metrics out of ranked top-N results."""
        lower = prompt.lower()
        is_top_n = bool(
            re.search(r"\btop\s+\d+\b", lower)
            or re.search(r"\beng\s+ko['‘’`]?p[^\d]{0,40}\d+\s*(?:ta)?\b", lower)
            or re.search(r"\b\d+\s*ta\s+eng\s+ko['‘’`]?p\b", lower)
        )
        if not is_top_n:
            return sql
        try:
            statement = sqlglot.parse_one(sql)
            outer_select = statement if isinstance(statement, exp.Select) else statement.find(exp.Select)
            if outer_select is None:
                return sql

            group = outer_select.args.get("group")
            if group and group.expressions:
                dimension = group.expressions[0]
                if isinstance(dimension, exp.Column):
                    where = outer_select.args.get("where")
                    already_filtered = False
                    if where is not None:
                        for is_expression in where.find_all(exp.Is):
                            if (
                                isinstance(is_expression.this, exp.Column)
                                and is_expression.this.name.lower() == dimension.name.lower()
                                and isinstance(is_expression.expression, exp.Null)
                            ):
                                already_filtered = True
                                break
                    if not already_filtered:
                        not_null = exp.Not(
                            this=exp.Is(this=dimension.copy(), expression=exp.Null())
                        )
                        existing = where.this if where else None
                        outer_select.set(
                            "where",
                            exp.Where(this=exp.and_(existing, not_null) if existing else not_null),
                        )

            order = outer_select.args.get("order")
            if order:
                for ordered in order.expressions:
                    if isinstance(ordered, exp.Ordered) and ordered.args.get("desc"):
                        ordered.set("nulls_first", False)
            # PostgreSQL sorts NULL before non-NULL values for DESC unless the
            # clause is explicit. Rendering with the PostgreSQL dialect keeps
            # ``NULLS LAST`` in the generated SQL instead of treating it as a
            # generic-dialect default and omitting it.
            return statement.sql(dialect="postgres")
        except Exception:
            return sql

    @staticmethod
    def _normalize_geographic_sql(prompt: str, sql: str, schema_ctx: SchemaContext) -> str:
        """Use the Latin Uzbek geography label column when the prompt/literal is Latin."""
        prompt_lower = prompt.lower()
        try:
            statement = sqlglot.parse_one(sql)
            aliases = {}
            for table in statement.find_all(exp.Table):
                base_name = table.name.split(".")[-1].lower()
                aliases[table.alias_or_name.lower()] = base_name
                aliases[base_name] = base_name

            localizable_tables = set()
            for table in schema_ctx.tables:
                base_name = table.name.split(".")[-1].lower()
                column_names = {column.name.lower() for column in table.columns}
                if {"name", "name_uz"}.issubset(column_names):
                    localizable_tables.add(base_name)

            if not localizable_tables:
                return sql

            changed = False
            for comparison in statement.walk():
                if not isinstance(comparison, (exp.ILike, exp.Like, exp.EQ)):
                    continue
                pairs = (
                    (comparison.this, comparison.expression),
                    (comparison.expression, comparison.this),
                )
                for column, literal in pairs:
                    if not isinstance(column, exp.Column) or not isinstance(literal, exp.Literal):
                        continue
                    if column.name.lower() not in {"name", "name_uz"} or not literal.is_string:
                        continue
                    base_name = aliases.get((column.table or "").lower())
                    if base_name not in localizable_tables:
                        continue
                    literal_lower = str(literal.this).lower()
                    latin_tokens = re.findall(r"[a-z]{4,}", literal_lower)
                    if not latin_tokens or not any(token in prompt_lower for token in latin_tokens):
                        continue
                    if column.name.lower() == "name":
                        column.set("this", exp.Identifier(this="name_uz", quoted=False))
                    # Keep equality exact. Turning '=' into a substring match
                    # silently widens the selected population.
                    changed = True
                    break

            return statement.sql(dialect="postgres") if changed else sql
        except Exception:
            return sql

    @staticmethod
    def _format_value(value: Any) -> str:
        if value is None:
            return "qiymat mavjud emas"
        if isinstance(value, bool):
            return "Ha" if value else "Yo'q"
        if isinstance(value, int):
            return f"{value:,}"
        if isinstance(value, float):
            return f"{int(value):,}" if value.is_integer() else format(Decimal(str(value)), ",f")
        return str(value)

    @staticmethod
    def _asks_why_metric_changed(prompt: str) -> bool:
        text = normalize_evidence(prompt)
        text = re.sub(r"\b(?:kamayish|o'sish)\s+tartibida\b", '', text)
        asks_why = re.search(r"\b(?:nega|nimaga|nima uchun|sabab\w*|omil\w*|why|caus(?:e|ed|es)|drivers?)\b", text)
        change = re.search(
            r"\b(?:kamay\w*|pasay\w*|tush(?:di|gan|ishi)\w*|osh(?:di|gan|ish)\w*|ko'taril\w*|"
            r"o's(?:di|gan|ish)\w*|o'zgar\w*|ort(?:di|gan|ish)\w*|fell|fall\w*|drop\w*|"
            r"declin\w*|decreas\w*|increas\w*|down|rose|grew|chang\w*)\b", text)
        return bool(asks_why and change)

    @classmethod
    def _change_summary(cls, rows: List[Dict[str, Any]], truncated: bool, observation_column: str | None = None) -> str:
        """Compare like-for-like observations; missing groups never imply zero."""
        limit = "Bu hisobdan o‘zgarish sababini aniqlab bo‘lmaydi. Buning uchun shu davrlardagi hodisalar haqida qo‘shimcha dalil kerak."
        insufficient = ("O‘zgarishni baholash uchun bir xil ko‘rsatkichning ikkita davrdagi qiymati kerak. " + limit)
        count_field = {'observation_count'} if observation_column else set()
        if not rows or truncated or (set(rows[0]) - count_field) not in (
                {'period', 'metric_value'}, {'period', 'metric_value', 'segment'}):
            return insufficient
        pairs = {}
        counts = {}
        periods = set()
        segmented = 'segment' in rows[0]
        for row in rows:
            period = str(row.get('period', ''))
            # ISO labels at one resolution sort chronologically without the
            # model having to identify which observation is the baseline.
            if not re.fullmatch(r'\d{4}(?:-\d{2}(?:-\d{2})?)?', period):
                return insufficient
            try:
                date.fromisoformat((period + '-01-01')[:10])
            except ValueError:
                return insufficient
            periods.add(period)
            segment = row.get('segment')
            if segmented and segment is None:
                continue
            if segmented and not isinstance(segment, (str, int, float, Decimal)):
                return insufficient
            values = pairs.setdefault(segment, {})
            if period in values:
                return insufficient
            value = row.get('metric_value')
            number = Decimal(str(value)) if isinstance(value, (int, float, Decimal)) and not isinstance(value, bool) else None
            values[period] = number if number is not None and number.is_finite() else None
            count = row.get('observation_count')
            if observation_column and type(count) is int and count > 0:
                counts[segment, period] = count
        if len(periods) == 1 and not segmented:
            period = next(iter(periods))
            observed = pairs[None][period]
            if observed is not None:
                return f'{period} uchun hisoblangan qiymat {observed:,f}. ' + insufficient
        if len(periods) != 2 or len({len(period) for period in periods}) != 1:
            return insufficient
        before, after = sorted(periods)
        lines = []
        for segment, values in pairs.items():
            start, end = values.get(before), values.get(after)
            if start is None or end is None:
                continue
            delta = end - start
            direction = 'oshgan' if delta > 0 else 'kamaygan'
            change = f'{abs(delta):,f} ga {direction}' if delta else 'o‘zgarmagan'
            heading = f'{segment} — ' if segmented else ''
            lines.append(f'{heading}{before} da {start:,f}, {after} da {end:,f}. Ko‘rsatkich {change}.')
            if (segment, before) in counts and (segment, after) in counts:
                n0, n1 = counts[segment, before], counts[segment, after]
                decimals = max(2, -start.as_tuple().exponent, -end.as_tuple().exponent)
                with localcontext() as precision:
                    precision.prec = max(34, max(start.adjusted(), end.adjusted()) + decimals + 24)
                    m0, m1 = start / n0, end / n1
                    # Split count * mean symmetrically. Round once for display
                    # and assign the rounding residual to the second component.
                    delta = end - start
                    count_effect = round((n1 - n0) * (m0 + m1) / 2, decimals)
                    value_effect = delta - count_effect
                lines[-1] += (
                    f' {observation_column} qiymati bor yozuvlar soni {n0:,} dan {n1:,} ga, '
                    f'bir yozuvning o‘rtacha qiymati {m0:,.2f} dan {m1:,.2f} ga o‘zgargan. '
                    f'Farq tarkibida yozuvlar soni {count_effect:+,.{decimals}f}, '
                    f'o‘rtacha qiymat {value_effect:+,.{decimals}f}, jami {delta:+,.{decimals}f}.')
        if not lines:
            return insufficient
        omitted = len(pairs) - len(lines)
        selected = lines[:10]
        if len(lines) > len(selected):
            selected.append(f'Yana {len(lines) - len(selected)} ta guruhning qiymatlari jadvalda berilgan.')
        if omitted:
            selected.append(f'{omitted} ta guruhda ikki davr qiymati to‘liq emas.')
        return '\n'.join(selected + [limit])

    @staticmethod
    def _change_columns_grounded(keys, sql, dialect, observation_column=None):
        extras = {'observation_count'} if observation_column else set()
        if set(keys) - extras not in ({'period', 'metric_value'}, {'period', 'metric_value', 'segment'}):
            return False
        try:
            tree = sqlglot.parse_one(sql, read=dialect)
            for key in keys:
                # Follow projections through CTEs so a literal invented by the
                # planner cannot masquerade as an observed metric or segment.
                nodes = list(lineage(key, tree, schema=None, dialect=dialect).walk())
                if key == 'segment' and any(not isinstance(node.expression.unalias(), (exp.Table, exp.Column)) for node in nodes):
                    return False
                if any(isinstance(node.expression, exp.Table) for node in nodes):
                    continue
                if key == 'metric_value' and any(
                    node.source.find(exp.Table) and any(isinstance(count.this, exp.Star)
                    for count in node.expression.find_all(exp.Count)) for node in nodes
                ):
                    continue
                return False
            return True
        except (TypeError, ValueError, sqlglot.errors.SqlglotError):
            return False

    @staticmethod
    def _change_observation_count(sql, schema, *, add):
        """Count the non-NULL observations behind one direct, single-table SUM."""
        dialect = 'postgres' if schema.database_type == 'postgresql' else schema.database_type
        try:
            tree = sqlglot.parse_one(sql, read=dialect)
            if not isinstance(tree, exp.Select) or not tree.args.get('group'):
                return sql, None
            if any(tree.args.get(k) for k in ('with_', 'joins', 'distinct', 'having', 'offset', 'qualify')):
                return sql, None
            tables = list(tree.find_all(exp.Table))
            if len(tables) != 1 or len(list(tree.find_all(exp.Select))) != 1 or tree.find(exp.Window):
                return sql, None
            fields = {p.alias_or_name: p.unalias() for p in tree.expressions}
            keys = set(fields) - {'observation_count'}
            if keys not in ({'period', 'metric_value'}, {'period', 'metric_value', 'segment'}):
                return sql, None
            dimensions = keys - {'metric_value'}
            grouped = set()
            for term in tree.args['group'].expressions:
                if isinstance(term, exp.Column) and not term.table and term.name in dimensions:
                    grouped.add(term.name)
                else:
                    equivalent = {key for key in dimensions if fields[key] == term}
                    if not equivalent:
                        return sql, None
                    grouped.update(equivalent)
            if grouped != dimensions or any(tree.args['group'].args.get(k) for k in ('grouping_sets', 'rollup', 'cube')):
                return sql, None
            metric = fields['metric_value']
            if not isinstance(metric, exp.Sum) or not isinstance(metric.this, exp.Column):
                return sql, None
            column = metric.this
            source = tables[0]
            full_name = '.'.join(part.name for part in source.parts).casefold()
            matches = [t for t in schema.tables if t.name.casefold() == full_name
                       or (not source.db and t.name.split('.')[-1].casefold() == full_name)]
            if len(matches) != 1 or matches[0].business_role not in (None, 'fact'):
                return sql, None
            meta = next((c for c in matches[0].columns if c.name.casefold() == column.name.casefold()), None)
            if not meta or not re.match(r'^(?:tinyint|smallint|int(?:eger|[248])?|bigint|numeric|decimal|number|real|double|float\d*|money)\b',
                                        meta.data_type.casefold()):
                return sql, None
            wanted = exp.Count(this=column.copy())
            existing = fields.get('observation_count')
            if existing is not None:
                same_count = isinstance(existing, exp.Count) and existing.this == column and not existing.expressions
                return sql, column.name if same_count else None
            if add:
                tree.select(wanted.as_('observation_count'), append=True, copy=False)
                return tree.sql(dialect=dialect), column.name
        except (TypeError, ValueError, sqlglot.errors.SqlglotError):
            pass
        return sql, None

    @staticmethod
    def _humanize_column(name: str) -> str:
        normalized = str(name).replace("_", " ").strip().lower()
        labels = {
            "count": "Jami",
            "total": "Jami",
            "total count": "Jami",
            "row count": "Jami",
            "total quantity": "Jami miqdor",
            "total amount": "Jami summa",
            "product name": "Mahsulot",
            "organization name": "Tashkilot",
            "warehouse count": "Omborlar soni",
            "authorized warehouse count": "Vakolatli omborlar soni",
        }
        return labels.get(normalized, normalized.capitalize())

    @classmethod
    def _build_evidence_summary(
        cls,
        rows: List[Dict[str, Any]],
        table_used: str,
        genuine_zero: bool = False,
        is_empty: bool = False,
        is_truncated: bool = False,
    ) -> Tuple[str, bool]:
        if genuine_zero:
            return "Tekshirildi: ko'rsatilgan shartlar bo'yicha natija **0**.", False
        if is_empty or not rows:
            return "Tekshirildi: kiritilgan shartlar bo'yicha mos yozuv topilmadi.", False

        keys = list(rows[0].keys())
        if len(rows) == 1:
            facts = [
                f"- **{cls._humanize_column(key)}:** {cls._format_value(rows[0].get(key))}"
                for key in keys
            ]
            return "Aniq natija:\n" + "\n".join(facts), False

        heading = (
            f"Birinchi **{len(rows)} ta** qaytgan natija ichidan:"
            if is_truncated
            else f"So'rov **{len(rows)} ta** qator qaytardi:"
        )
        detail_lines = []
        for index, row in enumerate(rows[:10], 1):
            first_key = keys[0]
            lead = cls._format_value(row.get(first_key))
            remaining = [
                f"{cls._humanize_column(key)}: {cls._format_value(row.get(key))}"
                for key in keys[1:]
            ]
            suffix = " — " + ", ".join(remaining) if remaining else ""
            detail_lines.append(f"{index}. **{lead}**{suffix}")
        if len(rows) > 10:
            detail_lines.append(f"Ko'rsatilgan dastlabki 10 qatordan tashqari yana {len(rows) - 10} ta qator qaytdi.")
        detail_lines.append(f"\nManba: `{table_used}`.")
        return heading + "\n" + "\n".join(detail_lines), is_truncated

    @classmethod
    def _build_focused_schema_prompt(cls, candidate: TableMeta, schema_ctx: SchemaContext) -> str:
        """Builds a rich, focused schema prompt centering on the candidate table and its related joins."""
        trusted_relationships = [
            r for r in schema_ctx.relationships
            if r.verified and r.source in {"foreign_key", "semantic_catalog"}
        ]
        lines = [
            f"### ASOSIY TEKSHIRILAYOTGAN JADVAL: `{candidate.name}` (Qatorlar soni: {candidate.row_count:,})",
            f"### TAXMINIY GRAIN: {candidate.grain_hint or 'aniqlanmagan; agregatsiyada ehtiyot bo\'ling'}",
            "### USTUNLARI VA HAQIQIY NAMUNALARI:"
        ]
        if candidate.default_label_column:
            lines.insert(2, f"Reviewed display label: {candidate.name}.{candidate.default_label_column}. Use this for entity names unless the user explicitly names another column.")
        if candidate.business_description or candidate.semantic_aliases:
            lines.insert(
                1,
                "### TASDIQLANGAN SEMANTIK KATALOG: "
                + "; ".join(filter(None, [
                    candidate.business_description,
                    f"role={candidate.business_role}" if candidate.business_role else None,
                    "aliases=" + ", ".join(candidate.semantic_aliases[:8])
                    if candidate.semantic_aliases else None,
                    "CERTIFIED SOURCE" if candidate.certified_source else None,
                ])),
            )
        table_metrics = [
            metric for metric in schema_ctx.semantic_metrics
            if metric.table.lower() == candidate.name.lower()
        ]
        if table_metrics:
            lines.insert(2, "### TASDIQLANGAN METRIKALAR:")
            for metric in reversed(table_metrics):
                filter_text = ", ".join(
                    f"{item.column} {item.operator} {item.value!r}"
                    for item in metric.filters
                ) or "none"
                lines.insert(
                    3,
                    f"- {metric.name}: {metric.aggregation.upper()}({metric.column or '*'}) "
                    f"filters=[{filter_text}] unit={metric.unit or '-'}",
                )
        
        for c in candidate.columns:
            pk_str = " [PRIMARY KEY]" if c.is_primary_key else ""
            declared_fk = next(
                (
                    r for r in trusted_relationships
                    if r.from_table == candidate.name and r.from_column == c.name
                ),
                None,
            )
            fk_str = (
                f" [DECLARED FOREIGN KEY -> {declared_fk.to_table}.{declared_fk.to_column}]"
                if declared_fk else ""
            )
            unique_str = " [UNIQUE]" if c.is_unique else ""
            
            samples = (
                f" [Namunalar: {', '.join(repr(v) for v in c.sample_values[:8])}]"
                if c.sample_values and SchemaProfiler.is_safe_enum_column(c.name) else ""
            )

            lines.append(f"  - `{c.name}` ({c.data_type}){pk_str}{unique_str}{fk_str}{samples}")

        # Add relationships involving this table
        relevant_rels = [
            r for r in schema_ctx.relationships
            if (r.from_table == candidate.name or r.to_table == candidate.name)
            and r.verified
            and r.source in {"foreign_key", "semantic_catalog"}
        ]
        
        # Unverified naming-convention joins are intentionally not suggested.
        # They must first pass the read-only relationship verifier.
        dynamic_joins = []
        aux_names = set()

        priority_aux_names = []
        ordered_rels = sorted(
            relevant_rels,
            key=lambda relationship: 0
            if (
                relationship.from_table in priority_aux_names
                or relationship.to_table in priority_aux_names
            )
            else 1,
        )

        if ordered_rels or dynamic_joins:
            lines.append("\n### BOG'LANISHLAR:")
            for r in ordered_rels[:9]:
                label = "DECLARED FOREIGN KEY" if r.source == "foreign_key" else "SEMANTIC CATALOG"
                lines.append(f"  - [{label}] {r.join_hint}")
            for dj in dynamic_joins:
                lines.append(f"  - {dj}")

        # Also list related auxiliary tables (like organization, region, district) if referenced
        for r in relevant_rels:
            if r.from_table == candidate.name and r.to_table != candidate.name:
                aux_names.add(r.to_table)
            elif r.to_table == candidate.name and r.from_table != candidate.name:
                aux_names.add(r.from_table)
        table_lookup = {table.name: table for table in schema_ctx.tables}
        ordered_aux_names = priority_aux_names + sorted(aux_names.difference(priority_aux_names))
        aux_tables = [table_lookup[name] for name in ordered_aux_names if name in table_lookup][:7]
        if aux_tables:
            lines.append("\n### BOG'LIQ YORDAMCHI JADVALLAR (JOIN UCHUN USTUNLARI):")
            for at in aux_tables:
                column_limit = 16 if at.name in priority_aux_names else 8
                col_names = ", ".join(c.name for c in at.columns[:column_limit])
                lines.append(f"  - `{at.name}`: {col_names}")
                if at.business_description and at.certified_source:
                    lines.append(f"    Reviewed definition: {at.business_description[:800]}")
                if at.default_label_column:
                    lines.append(f"    Reviewed display label: {at.name}.{at.default_label_column}; use for entity names unless another column is explicitly requested.")

        return "\n".join(lines)

    @classmethod
    def classify_user_intent(cls, prompt: str) -> str:
        """
        Intelligently classifies user prompt into:
        - 'GREETING': Friendly greetings in Uzbek.
        - 'OUT_OF_DOMAIN': Questions not related to business data analysis.
        - 'CONVERSATIONAL': In-domain concepts, definitions, system questions.
        - 'DATA_QUERY': Specific database analytical questions.
        """
        p = normalize_evidence(prompt)
        words = re.findall(r"[\w']+", p)
        greetings = {"salom", "assalom", "assalomu", "alaykum", "qalaysiz", "qalesiz",
                     "qalesan", "qandaysiz", "yaxshimisiz", "yaxshimisan", "tuzukmisiz",
                     "charchamang", "hello", "hi", "hey", "privet"}
        if (words and all(word in greetings or word in {"tabloai", "jarvis"} for word in words)
                or re.fullmatch(r"(?:xayrli|hayrli) (?:tong|kun|kech)|nima gap|ishlar qalay", " ".join(words))):
            return "GREETING"

        # A greeting, definition or advice request can accompany a business
        # calculation. The calculation must still be grounded in fresh evidence.
        data_request = re.search(
            r"\b(?:nechta\w*|qancha|\d{4}|top\s*\d+|solishtir\w*|taqqos\w*|hisobla\w*|"
            r"kesim\w*|bizning|bazada\w*|bazamiz\w*|ko'rsat\w*|kamay\w*|oshdi|o'sdi|"
            r"our|last month|last year|how many)\b", p)
        if data_request:
            return "DATA_QUERY"
        if re.search(r"\b(?:nima degani|nima o'zi|o'zi nima|nima uchun kerak|farqi nimada|"
                     r"qanday farq|ma'nosi nima|deganda nimani|tushunchasini|haqida tushuncha|"
                     r"nima qila olas\w*|imkoniyatlaring\w*|sen kimsan|tizim qanday ishlay\w*|"
                     r"loyiha qanday ishlay\w*|what is|what can you do)\b", p):
            return "CONVERSATIONAL"
        if re.search(r"\b(?:ob[- ]havo|ovqat|retsept|palov|she'r|ertak|futbol|messi|ronaldo|kino|"
                     r"saylov|latifa|anekdot|topishmoq|osh damlash|sayt yasa\w*)\b", p):
            if not re.search(r"\b(?:sot\w*|tushum\w*|buyurtma\w*|baza\w*|revenue|sales|orders)\b", p):
                return "OUT_OF_DOMAIN"

        # Unknown table/measure names and brief analytical follow-ups belong to
        # the planner, which can inspect the schema or request clarification.
        return "DATA_QUERY"

    @classmethod
    async def execute_conversational_turn(
        cls, 
        prompt: str, 
        schema_ctx: SchemaContext, 
        history: Optional[List[Dict[str, Any]]] = None
    ) -> NaturalQueryResponse:
        """
        Handles general conceptual questions, business advice, greetings, and system explanations
        conversationally with strict anti-hallucination, domain restrictions, and zero raw code.
        """
        start_time = time.time()

        prompt = cls.resolve_analytical_prompt(prompt, history)
        logger.info(f"[Agent] Generating conversational turn for prompt: '{prompt}'")

        known_tables = [t.name for t in schema_ctx.tables[:15]]
        total_tables = len(schema_ctx.tables)

        system_prompt = (
            "Siz TabloAI biznes tahlilchisisiz. So'ralgan tushuncha yoki tizim imkoniyatini "
            "o'zbek tilida sodda va aniq tushuntiring. Bu suhbatda SQL bajarilmadi. "
            "Kompaniyaning raqamlari, yozuvlari, natijalari yoki ularning mavjudligi haqida "
            "da'vo qilmang. Misol bersangiz, uni shartli misol deb belgilang. "
            "Oldingi suhbat faqat savol ma'nosini tushunishga yordam beradi. "
            f"Ulangan manbada profillangan jadvallar soni {total_tables}. "
            f"Jadval nomlaridan namuna: {', '.join(known_tables[:8])}."
        )

        history_text = ""
        if history:
            recent = history[-6:]
            history_lines = []
            for h in recent:
                role = "Foydalanuvchi" if h.get("role") == "user" or h.get("sender") == "user" else "AI Tahlilchi"
                content = h.get("content") or h.get("text") or ""
                if content:
                    history_lines.append(f"{role}: {content[:300]}")
            if history_lines:
                history_text = "\n\nOldingi suhbat konteksti:\n" + "\n".join(history_lines)

        user_prompt = f"Foydalanuvchi Savoli: {prompt}{history_text}\n\nIltimos, samimiy, insoniy, aniq va professional tarzda to'liq tushuntirib bering."

        try:
            completion = await llm_service.generate_completion(system_prompt, user_prompt, temperature=0.6, max_tokens=1500)
            text = completion.strip()
            # Strip reasoning / think tags if present
            text = re.sub(r"<think>[\s\S]*?(?:</think>|$)", "", text, flags=re.IGNORECASE).strip()
            # Clean outer markdown fence if present
            if text.startswith("```markdown"):
                text = text[len("```markdown"):].strip()
            elif text.startswith("```"):
                text = text[3:].strip()
            if text.endswith("```"):
                text = text[:-3].strip()
            if not text:
                raise LLMServiceError("Empty conversational response")
        except LLMRateLimitError as exc:
            return cls._failure_response(start_time, str(exc), "llm_rate_limited")
        except Exception as exc:
            logger.warning("Conversational answer failed: %s", type(exc).__name__)
            return cls._failure_response(start_time,
                "Tushuntirish tayyorlashda AI xizmatidan javob olinmadi. Qaytadan urinib ko'ring.",
                "llm_unavailable")

        exec_time = (time.time() - start_time) * 1000

        return NaturalQueryResponse(
            sql_query="",
            chart=None,
            charts=[],
            explanation=text,
            summary_text=text,
            table_used=None,
            columns=[],
            rows=[],
            data_count=0,
            execution_time_ms=exec_time,
            success=True,
            response_type="text"
        )

    @classmethod
    async def execute_analyst_agent(
        cls,
        prompt: str,
        schema_ctx: SchemaContext,
        history: Optional[List[Dict[str, Any]]] = None,
    ) -> NaturalQueryResponse:
        """Generate, semantically validate, execute, and summarize an analytical query."""
        start_time = time.time()
        resolved_prompt = cls.resolve_analytical_prompt(prompt, history)
        intent = cls.classify_user_intent(resolved_prompt)
        logger.info("[Agent] User prompt: %r | Resolved: %r | Intent: %s", prompt, resolved_prompt, intent)

        if intent == "GREETING":
            return cls._greeting_response(start_time)

        if intent == "OUT_OF_DOMAIN":
            return cls._out_of_domain_response(start_time)

        if intent == "CONVERSATIONAL":
            return await cls.execute_conversational_turn(prompt, schema_ctx, history)

        investigate_change = cls._asks_why_metric_changed(resolved_prompt)
        # Reviewed business metrics are compiled from a structured catalog and
        # executed deterministically.  This removes source-table, filter and
        # join guessing from the LLM while keeping the implementation portable:
        # each newly connected schema gets its own fingerprinted catalog.
        try:
            compiled_metric = SemanticCatalogManager.compile_matching_metric_sql(
                resolved_prompt,
                schema_ctx,
            )
        except SemanticCatalogError as exc:
            logger.warning("Certified metric compilation rejected: %s", exc)
            return cls._clarification_response(
                resolved_prompt,
                start_time,
                "Tasdiqlangan biznes ko'rsatkichi bir ma'noli aniqlanmadi. "
                "Semantic katalogni tekshiring yoki savolni aniqlashtiring.",
            )

        compiled_template = compiled_metric
        monthly_ranking = False
        if compiled_metric is not None:
            # Substring metric matches identify a formula, not a complete question.
            # Only exact metric names and fully recognized monthly scopes bypass planning.
            metric, _ = compiled_metric
            normalize = SemanticCatalogManager._normalize_text
            if investigate_change:
                compiled_metric = None
            elif normalize(resolved_prompt) not in {normalize(s) for s in [metric.name, *metric.aliases]}:
                compiled_metric = compile_monthly_metric(resolved_prompt, schema_ctx, metric)
                monthly_ranking = bool(compiled_metric and compiled_metric[0].group_by)

        if compiled_metric is not None:
            metric, metric_sql = compiled_metric
            is_valid, sanitized_or_error = validate_and_sanitize_sql(
                metric_sql,
                soft_delete_rules=db_manager.soft_delete_rules,
            )
            if not is_valid:
                logger.error("Compiled certified metric failed SQL policy: %s", sanitized_or_error)
                return cls._failure_response(
                    start_time,
                    "Tasdiqlangan ko'rsatkich xavfsizlik tekshiruvidan o'tmadi.",
                    "certified_metric_invalid",
                )

            metric_sql = sanitized_or_error
            semantics_ok, semantics_error = cls._validate_sql_semantics(
                resolved_prompt, metric_sql, schema_ctx.tables, schema_ctx,
            )
            if not semantics_ok:
                logger.warning("Certified SQL misses question requirements: %s", semantics_error)
                return cls._clarification_response(resolved_prompt, start_time,
                    "Savoldagi barcha talablarni tasdiqlangan hisoblash bilan qamrab bo'lmadi. Savolni aniqlashtiring.")
            joins_valid, join_error = await asyncio.to_thread(
                RelationshipGuard.validate_query,
                metric_sql,
                schema_ctx,
            )
            if not joins_valid:
                logger.error("Compiled certified metric failed join policy: %s", join_error)
                return cls._failure_response(
                    start_time,
                    "Tasdiqlangan ko'rsatkich bog'lanishi xavfsizlik tekshiruvidan o'tmadi.",
                    "certified_metric_join_invalid",
                )

            try:
                rows = await asyncio.to_thread(db_manager.execute_query, metric_sql, max_rows=501)
            except Exception as exc:
                logger.error("Certified metric execution failed: %s", exc)
                return cls._failure_response(
                    start_time,
                    "Tasdiqlangan ko'rsatkichni o'qishda xatolik yuz berdi.",
                    "certified_metric_execution_failed",
                )

            table_names = []
            compiled_has_group_by = bool(metric.group_by)
            try:
                compiled_statement = sqlglot.parse_one(metric_sql)
                table_names = list(dict.fromkeys(
                    table.name for table in compiled_statement.find_all(exp.Table)
                ))
                compiled_has_group_by = compiled_statement.find(exp.Group) is not None
            except Exception:
                table_names = [metric.table]
            value_alias = re.sub(r"[^A-Za-z0-9_]", "_", metric.id)
            result_keys = list(rows[0].keys()) if rows else []
            dimension_alias = (
                result_keys[0]
                if compiled_has_group_by and result_keys
                else value_alias
            )
            parsed = {
                "title": metric.name,
                "badge": "Tasdiqlangan KPI",
                "chart_type": "horizontal_bar" if compiled_has_group_by else "kpi",
                "x_axis": dimension_alias,
                "y_axis": (
                    result_keys[-1]
                    if compiled_has_group_by and len(result_keys) >= 2
                    else None
                ),
            }
            response = cls._build_response(
                resolved_prompt,
                rows,
                parsed,
                metric_sql,
                ", ".join(table_names) or metric.table,
                start_time,
                genuine_zero=bool(
                    rows
                    and len(rows) == 1
                    and all(value in (0, 0.0, "0") for value in rows[0].values())
                ),
            )
            if monthly_ranking:
                response.explanation += "\n\nReytingga shu oyda savol shartlariga mos yozuvi bor guruhlar kiritildi."
                response.summary_text = response.explanation
            return response

        try:
            schema_hints = [
                {"table": match.table.name, "columns": [column.name for column in match.table.columns[:24]],
                 "reviewed_description": (match.table.business_description or "")[:800],
                 "aliases": match.table.semantic_aliases[:8]}
                for match in SchemaRetriever.rank(resolved_prompt, schema_ctx, top_k=6)
            ]
            question_plan = await plan_question(resolved_prompt, schema_hints)
        except LLMRateLimitError as exc:
            return cls._failure_response(start_time, str(exc), "llm_rate_limited")
        except LLMUnavailableError:
            return cls._failure_response(start_time, "Savolni tahlil qiluvchi modelga ulanib bo'lmadi.", "llm_unavailable")
        except Exception as exc:
            logger.warning("Question planning failed: %s", type(exc).__name__)
            return cls._failure_response(start_time, "Savol talablari ishonchli rejaga aylantirilmadi. SQL bajarilmadi.", "question_plan_invalid")
        if investigate_change and not question_plan.time:
            return cls._clarification_response(resolved_prompt, start_time,
                "O‘zgarishni tekshirish uchun qaysi ikki davrni solishtiray? Masalan, sentabr va oktabr oylarini yil bilan ko‘rsating.")
        if question_plan.ambiguities:
            return cls._clarification_response(resolved_prompt, start_time, " ".join(question_plan.ambiguities))
        if investigate_change and not question_plan.dimensions:
            question_plan.dimensions = [question_plan.time[0]]

        # Only grounded excerpts enrich retrieval; no model-invented table names.
        retrieval_prompt = resolved_prompt + " " + " ".join(
            item.evidence for item in [question_plan.entity, question_plan.measure, *question_plan.dimensions]
        )
        candidates = cls.discover_candidate_tables(retrieval_prompt, schema_ctx, top_k=4)
        ranked_matches = SchemaRetriever.rank(retrieval_prompt, schema_ctx, top_k=4)
        if not candidates:
            return cls._clarification_response(
                resolved_prompt,
                start_time,
                "Savol uchun ishonchli ma'lumot manbasi aniqlanmadi. Qaysi biznes obyekt, ko'rsatkich va davr kerakligini aniqlashtiring.",
            )

        grouped_sources = SchemaRetriever.grouped_sources(
            [dimension.evidence for dimension in question_plan.dimensions], ranked_matches, schema_ctx,
            ranked_entity=question_plan.entity.evidence if question_plan.ordering else "",
        )
        if grouped_sources:
            candidates = grouped_sources
        requested_multi_source = bool(grouped_sources) or cls._needs_multi_table_context(resolved_prompt)
        if SchemaRetriever.ambiguous(ranked_matches) and not (
            requested_multi_source and len(ranked_matches) > 1
        ):
            options = ", ".join(match.table.name for match in ranked_matches[:3])
            return cls._clarification_response(
                resolved_prompt,
                start_time,
                "Savol bir nechta ma'lumot manbasiga mos keldi"
                + (f" ({options})" if options else "")
                + ". Kerakli biznes obyekt yoki jadvalni aniqlashtiring.",
            )

        is_multi_source = requested_multi_source and len(candidates) > 1
        planning_candidates = candidates[:4] if is_multi_source else [candidates[0]]
        # A successful query returning zero/empty is evidence, not a signal to
        # silently switch to a semantically weaker table.
        probe_candidates = [candidates[0]]
        logger.info(
            "[Agent] Candidate tables: %s | multi_source=%s",
            [table.name for table in candidates],
            is_multi_source,
        )

        last_clarification = ""
        last_error = ""
        for attempt_idx, candidate in enumerate(probe_candidates, 1):
            context_candidates = planning_candidates if is_multi_source else [candidate]
            for context_table in context_candidates:
                try:
                    await asyncio.to_thread(SchemaProfiler.enrich_safe_enum_samples, context_table)
                except Exception as exc:
                    logger.debug("Enum enrichment failed for %s: %s", context_table.name, exc)

            focused_schema = (
                cls._build_multi_focused_schema_prompt(context_candidates, schema_ctx)
                if is_multi_source
                else cls._build_focused_schema_prompt(candidate, schema_ctx)
            )
            system_prompt = cls._build_universal_sql_system_prompt(
                candidate,
                schema_ctx,
                context_candidates,
            )
            focused_schema += (
                "\nQUESTION CONTRACT (all requirements mandatory; do not weaken during repair):\n"
                + question_plan.model_dump_json() + "\n" + question_plan.month_filter_hint()
                + "\nResolve business terms only through reviewed metadata. If a required definition "
                "or source is missing, return clarification instead of guessing SQL.\n"
            )
            if investigate_change:
                focused_schema += (
                    "\nFor this change investigation, compare the SAME measure over the two requested periods. "
                    "If the comparison period or measure cannot be determined, ask a specific clarification in Uzbek. "
                    "Return only a period field (alias period, ISO YYYY, YYYY-MM or YYYY-MM-DD) computed from the "
                    "actual source date, and the observed numeric measure (alias metric_value). If the question "
                    "also requests a breakdown, add exactly one source-backed dimension as segment. Return one row "
                    "per period and segment. Never fabricate a missing period, metric, reason or segment. Do not "
                    "put a presumed cause in the SQL, column labels, title, clarification, or output rows.\n"
                )
            if compiled_template is not None:
                focused_schema += (
                    "\nCERTIFIED BASE FORMULA (not a complete answer):\n" + compiled_template[1]
                    + "\nPreserve its business filters, source, aggregate and fallback columns. "
                    "Adapt grouping, dates, requested filters, ordering and LIMIT to ALL requirements in the question. "
                    "Do not return the base formula unchanged if the question adds requirements.\n"
                )
            directions = re.findall(r"\b(kamayish|o'sish)\b", resolved_prompt.lower())
            requested_dimensions = requested_group_columns(resolved_prompt, schema_ctx)
            if requested_dimensions:
                focused_schema += (
                    "\nRequested final GROUP BY and output dimensions: " + ', '.join(sorted(requested_dimensions))
                    + ". For a single fact source, join the required dimensions and aggregate directly by these columns. "
                    "Do not group by a different foreign key, an aggregate result, or an unrequested dimension.\n"
                )
            if directions:
                focused_schema += "\nRequired ORDER BY directions: " + ', '.join(
                    'DESC' if d == 'kamayish' else 'ASC' for d in directions) + '.\n'
            for label_table in schema_ctx.tables:
                if label_table.default_label_column and any(
                    re.search(r'har\s+(?:bir|bitta)\s+' + re.escape(alias.lower()) + r'\b', resolved_prompt.lower())
                    for alias in label_table.semantic_aliases
                ):
                    focused_schema += f"\nRequested entity display label: {label_table.name}.{label_table.default_label_column}. Use it for grouping and output.\n"
            user_prompt = f"{focused_schema}\n\nFoydalanuvchi savoli: {resolved_prompt}"

            try:
                raw_completion = await llm_service.generate_completion(system_prompt, user_prompt)
                parsed = llm_service.extract_json(raw_completion)
                if not isinstance(parsed, dict):
                    raise ValueError("Model response must be a JSON object.")
                sql = str(parsed.get("sql_query") or "").strip()
                if not sql:
                    last_clarification = parsed.get("clarification") or (
                        "Savolni xavfsiz hisoblash uchun kerakli ustun yoki tasdiqlangan bog'lanish topilmadi. "
                        "Ko'rsatkich, davr va biznes obyektini aniqlashtiring."
                    )
                    continue

                # The LLM may forget PostgreSQL's NULL ordering behavior even
                # after being instructed. Normalize this deterministic rule
                # before validation so a correct top-N query does not spend
                # multiple slow model-repair attempts.
                sql = cls._normalize_top_n_sql(resolved_prompt, sql)
                sql = cls._normalize_geographic_sql(resolved_prompt, sql, schema_ctx)
                sql = normalize_explicit_null_order(resolved_prompt, sql, schema_ctx.database_type)
                include_observations = investigate_change and (
                    question_plan.operation == 'sum' or (question_plan.operation == 'complex' and
                    re.search(r"\b(?:sum|summa\w*|jami|yig'indi\w*|total)\b", normalize_evidence(question_plan.measure.evidence))))
                if include_observations:
                    sql, _ = cls._change_observation_count(sql, schema_ctx, add=True)

                def semantic_validator(candidate_sql):
                    plan_ok, plan_error = question_plan.validate_sql(candidate_sql, schema_ctx.database_type)
                    if not plan_ok:
                        return plan_ok, plan_error
                    return cls._validate_sql_semantics(
                        resolved_prompt, candidate_sql, context_candidates, schema_ctx,
                    )
                success, rows, final_sql, error = await SelfHealingQueryEngine.execute_with_self_healing(
                    sql,
                    focused_schema,
                    user_intent=resolved_prompt,
                    schema_ctx=schema_ctx,
                    semantic_validator=semantic_validator,
                    max_rows=501,
                )
                if not success:
                    last_error = error or "SQL generation or execution failed."
                    logger.warning("[Agent] Candidate #%s failed: %s", attempt_idx, last_error)
                    continue

                observation_column = None
                if include_observations:
                    _, observation_column = cls._change_observation_count(final_sql, schema_ctx, add=False)
                final_tree = sqlglot.parse_one(final_sql)
                cte_names = {cte.alias_or_name.lower() for cte in final_tree.find_all(exp.CTE)}
                source_label = ", ".join(dict.fromkeys(
                    table.name for table in final_tree.find_all(exp.Table)
                    if table.name.lower() not in cte_names
                ))

                # Generated secondary SQL must not bypass the question contract.
                # Render this answer from the validated primary result only.
                return cls._build_response(
                    resolved_prompt,
                    rows,
                    parsed,
                    final_sql,
                    source_label,
                    start_time,
                    database_type=schema_ctx.database_type,
                    observation_column=observation_column,
                )
            except LLMRateLimitError as exc:
                return cls._failure_response(start_time, str(exc), "llm_rate_limited")
            except LLMUnavailableError as exc:
                logger.error("[Agent] Model endpoint unavailable: %s", exc)
                return cls._failure_response(
                    start_time,
                    "AI model xizmatiga ulanib bo'lmadi. Bu holat ma'lumot yo'qligini anglatmaydi; model serveri yoki tunnelni tekshiring.",
                    "llm_unavailable",
                )
            except LLMServiceError as exc:
                last_error = str(exc)
                logger.warning("[Agent] Model request failed for %s: %s", candidate.name, exc)
            except Exception as exc:
                last_error = str(exc) or type(exc).__name__
                logger.warning("[Agent] Candidate #%s error for %s: %s", attempt_idx, candidate.name, exc)

        if last_clarification and not last_error:
            return cls._clarification_response(resolved_prompt, start_time, last_clarification)
        return cls._failure_response(
            start_time,
            "So'rov uchun ishonchli va tekshirilgan SQL natijasi olinmadi. Savolni aniqlashtiring yoki model va baza ulanishini tekshiring.",
            "query_generation_failed",
        )

    @classmethod
    def _build_response(
        cls, 
        prompt: str, 
        rows: List[Dict[str, Any]], 
        parsed: Dict[str, Any], 
        sql: str, 
        table_used: str, 
        start_time: float,
        genuine_zero: bool = False,
        is_empty_fallback: bool = False,
        database_type: str = 'sqlite',
        observation_column: str | None = None,
    ) -> NaturalQueryResponse:
        """Formats the validated Agent result into visual charts, structured rows, and rich Uzbek explanations."""
        exec_time = (time.time() - start_time) * 1000
        keys = list(rows[0].keys()) if rows else []
        is_truncated = len(rows) > 500
        rows = rows[:500]
        # Factual prose is built only from executed rows; model prose is never evidence.
        investigate_change = cls._asks_why_metric_changed(prompt)
        if investigate_change:
            dialect = 'postgres' if database_type == 'postgresql' else database_type
            if rows and not cls._change_columns_grounded(keys, sql, dialect, observation_column):
                return cls._clarification_response(prompt, start_time,
                    "So‘rovdan ikki davrning taqqoslanadigan qiymatlari tasdiqlanmadi. Qaysi ko‘rsatkich va qaysi ikki davrni solishtiray?")
            explanation = cls._change_summary(rows, is_truncated, observation_column)
        else:
            explanation, is_truncated = cls._build_evidence_summary(
                rows,
                table_used,
                genuine_zero=genuine_zero,
                is_empty=is_empty_fallback,
                is_truncated=is_truncated,
            )

        if not rows:
            return NaturalQueryResponse(
                sql_query=sql,
                chart=None,
                charts=[],
                explanation=explanation,
                summary_text=explanation,
                table_used=table_used,
                columns=[],
                rows=[],
                data_count=0,
                returned_count=0,
                is_truncated=False,
                execution_time_ms=exec_time,
                success=True,
                response_type="text",
            )

        # With periods repeated per segment, a single-series chart would
        # mix unlike groups. The paired observations remain in the table.
        primary_chart = None if investigate_change and 'segment' in keys else chart_for_rows(
            prompt, rows, title="Davrlar bo‘yicha taqqoslash" if investigate_change else (parsed.get("title") or prompt), sql=sql,
            x_axis=parsed.get("x_axis"), y_axis=parsed.get("y_axis"), truncated=is_truncated,
        )

        return NaturalQueryResponse(
            sql_query=sql,
            chart=primary_chart,
            charts=[primary_chart] if primary_chart else [],
            explanation=explanation,
            summary_text=explanation,
            table_used=table_used,
            columns=keys,
            rows=rows,
            data_count=len(rows),
            returned_count=len(rows),
            is_truncated=is_truncated,
            validation_warnings=(
                ["Natija xavfsizlik uchun 500 qator bilan cheklandi; bu jami yozuvlar soni emas."]
                if is_truncated else []
            ),
            execution_time_ms=exec_time,
            success=True,
            response_type="dashboard"
        )
