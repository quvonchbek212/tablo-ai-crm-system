"""Compile complete, simple monthly questions against a reviewed additive KPI."""
import re
from datetime import date

import sqlglot
from sqlglot import exp

from app.models import SemanticFilterDefinition
from app.question_plan import _COUNT_WORDS, _MONTHS, normalize_evidence
from app.semantic_catalog import SemanticCatalogError, SemanticCatalogManager


_MONTH = re.compile(
    rf"\b(?P<year>(?:19|20)\d{{2}})(?:[- ]+yil(?:ning)?)?\s+"
    rf"(?P<month>{'|'.join(_MONTHS)})(?:dagi|da|\s+oyi(?:dagi|da| uchun)?)?\b"
)
_COUNT = r"(?:[1-9]\d{0,2}(?:\s*ta)?|" + "|".join(map(re.escape, _COUNT_WORDS)) + ")"
_RANK = r"(?:eng (?:katta|yuqori|ko'p|kam|kichik|past)(?: bo'lgan)?|top|bottom)"


def _phrase_pattern(phrases):
    normalized = {normalize_evidence(s) for s in phrases if s.strip()}
    return "(?:" + "|".join(re.escape(s) for s in sorted(normalized, key=len, reverse=True)) + ")"


def _rank_request(text, measure, dimension):
    dim = _phrase_pattern([dimension.name, *dimension.semantic_aliases]) + r"(?:lar)?(?:ni)?"
    rank_first = rf"(?P<rank>{_RANK}) (?P<count>{_COUNT})"
    count_first = rf"(?P<count_first>{_COUNT}) (?P<rank_after>{_RANK})"
    # Every word must belong to the supported question or its display clause.
    # For instance, an extra region, status or currency cannot disappear.
    pattern = (
        rf"{measure}(?: bo'yicha)? (?:"
        rf"(?:{rank_first}|{count_first}) {dim}(?: qaysi(?:lar)?| (?:ko'rsat|chiqar))?"
        rf"|qaysi (?P<leaders>{_COUNT}) {dim} yetakchi)"
        rf"(?: (?:faqat )?(?:{dim} nomi va summasini )?"
        rf"(?:(?P<display>kamayish|o'sish) tartibida )?(?:ko'rsat|chiqar))?"
    )
    match = re.fullmatch(pattern, text)
    if not match:
        return None
    count = match["count"] or match["count_first"] or match["leaders"]
    limit = int(re.match(r"\d+", count)[0]) if count[0].isdigit() else _COUNT_WORDS[count]
    if limit > 500:
        return None
    rank = match["rank"] or match["rank_after"] or "top"
    direction = "asc" if rank == "bottom" or rank.startswith(("eng kam", "eng kichik", "eng past")) else "desc"
    display = {"kamayish": "desc", "o'sish": "asc"}.get(match["display"], direction)
    return limit, direction, display


def compile_monthly_metric(prompt, ctx, metric):
    """Return None for every unsupported or ambiguous modifier or relationship."""
    if (not ctx.semantic_catalog_reviewed or not metric.certified or metric.aggregation != "sum"
            or metric.latest_by or metric.limit or metric.group_by or metric.join_paths
            or metric.prompt_filters or metric.fallback_columns):
        return None
    fact = next((t for t in ctx.tables if t.name == metric.table and t.certified_source), None)
    if not fact or not fact.default_time_column:
        return None
    time_col = next((c for c in fact.columns if c.name == fact.default_time_column), None)
    if not time_col:
        return None
    storage = time_col.data_type.lower()
    if not ("date" in storage or "timestamp" in storage
            or (fact.default_time_format == "iso8601" and any(t in storage for t in ("char", "text")))):
        return None
    text = normalize_evidence(prompt).replace("`", "'")
    text = re.sub(r"\b(top|bottom)-(?=\d)", r"\1 ", text)
    periods = list(_MONTH.finditer(text))
    if len(periods) != 1:
        return None
    period = periods[0]
    start = date(int(period["year"]), _MONTHS[period["month"]], 1)
    end = date(start.year + (start.month == 12), start.month % 12 + 1, 1)
    text = " ".join(re.sub(r"[.,?!]", " ", text[:period.start()] + " " + text[period.end():]).split())
    measure = _phrase_pattern([metric.name, *metric.aliases])
    additions = [
        SemanticFilterDefinition(column=fact.default_time_column, operator=op, value=value.isoformat())
        for op, value in (("gte", start), ("lt", end))
    ]
    scoped = metric.model_copy(update={"filters": [*metric.filters, *additions]})
    if re.fullmatch(rf"{measure}(?: qancha(?: bo'lgan)?| nechaga teng| ko'rsat)?", text):
        return SemanticCatalogManager.compile_matching_metric_sql(metric.name, ctx.model_copy(update={"semantic_metrics": [scoped]}))

    matches = [(dim, rank) for dim in ctx.tables if dim.certified_source and dim.business_role == "dimension"
               and dim.default_label_column and dim.primary_key and (rank := _rank_request(text, measure, dim))]
    if len(matches) != 1:
        return None
    dimension, (limit, direction, display) = matches[0]
    edges = [r for r in ctx.relationships if r.from_table == fact.name and r.to_table == dimension.name
             and r.verified and r.source in {"foreign_key", "semantic_catalog"}]
    if len(edges) != 1 or (edges[0].cardinality or "many-to-one") not in {"many-to-one", "one-to-one"}:
        return None
    label = dimension.default_label_column
    value_alias = re.sub(r"[^A-Za-z0-9_]", "_", metric.id)
    if label.casefold() == value_alias.casefold():
        return None
    scoped = scoped.model_copy(update={"group_by": [f"{dimension.name}.{label}"], "group_by_alias": label,
                                       "exclude_null_group": True, "order": direction, "limit": limit})
    try:
        _, sql = SemanticCatalogManager.compile_matching_metric_sql(
            metric.name, ctx.model_copy(update={"semantic_metrics": [scoped]}))
        dialect = "postgres" if ctx.database_type == "postgresql" else ctx.database_type
        tree = sqlglot.parse_one(sql, read=dialect)
        pk = [sqlglot.parse_one(SemanticCatalogManager._column_sql(ctx, key, dimension.name)[0], read=dialect)
              for key in dimension.primary_key]
        for col in pk:
            if col.name != label:
                tree.group_by(col.copy(), copy=False)
        value = exp.column(value_alias, quoted=True)
        tree.set("order", None)
        tree.order_by(exp.Ordered(this=value.copy(), desc=direction == "desc", nulls_first=False),
                      *(col.copy() for col in pk), copy=False)
        if display != direction:
            keys = [f"_metric_sort_{idx}" for idx in range(len(pk))]
            if label.casefold() in keys or value_alias.casefold() in keys:
                return None
            tree.select(*(col.copy().as_(alias) for col, alias in zip(pk, keys)), copy=False)
            tree = exp.select(exp.column(label, quoted=True), value.copy()).from_(tree.subquery("ranked")).order_by(
                exp.Ordered(this=value, desc=display == "desc", nulls_first=False),
                *(exp.column(key) for key in keys))
        return scoped, tree.sql(dialect=dialect)
    except SemanticCatalogError:
        return None
