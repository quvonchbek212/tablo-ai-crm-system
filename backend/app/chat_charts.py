"""Select one chart from the evidence returned for a chat question."""

import math
import re
from datetime import date, datetime, timezone
from decimal import Decimal

from app.models import ChartSpec


_TIME_DIMENSIONS = {"date", "time", "day", "week", "month", "quarter", "year", "period",
                    "sana", "kun", "hafta", "oy", "chorak", "yil"}
_IDENTIFIERS = {"id", "uuid", "code", "kod", "rank"}
_MONTHS = (
    ("jan", "january", "yanvar"), ("feb", "february", "fevral"), ("mar", "march", "mart"),
    ("apr", "april", "aprel"), ("may",), ("jun", "june", "iyun"), ("jul", "july", "iyul"),
    ("aug", "august", "avgust"), ("sep", "sept", "september", "sentabr", "sentyabr"),
    ("oct", "october", "oktabr", "oktyabr"), ("nov", "november", "noyabr"),
    ("dec", "december", "dekabr"),
)
_MONTH_NUMBER = {name: month for month, names in enumerate(_MONTHS, 1) for name in names}


def _words(name):
    return set(re.findall(r"[^\W_]+", name.casefold()))


def _period_key(value, dimension):
    """Return a comparable key for common SQL time buckets and ISO timestamps."""
    text = str(value).strip().casefold()
    if re.fullmatch(r"\d{4}-\d{2}(-\d{2}([ t].+)?)?", text):
        try:
            instant = datetime.fromisoformat(text.replace("z", "+00:00") + ("-01" if len(text) == 7 else ""))
        except ValueError:
            return None
        if instant.tzinfo:
            instant = instant.astimezone(timezone.utc).replace(tzinfo=None)
        return instant
    quarter = re.fullmatch(r"(\d{4})[- ]?q([1-4])", text)
    if quarter and int(quarter[1]) > 0:
        return datetime(int(quarter[1]), 3 * int(quarter[2]) - 2, 1)
    if dimension & _TIME_DIMENSIONS:
        if text in _MONTH_NUMBER:
            return datetime(1, _MONTH_NUMBER[text], 1)
        if re.fullmatch(r"\d{1,4}", text):
            value = int(text)
            if value > 0:
                return datetime(value, 1, 1)
    return None


def _is_number(value, allow_text=False):
    if isinstance(value, bool) or not isinstance(value, (int, float, Decimal, str)):
        return False
    if isinstance(value, str) and (not allow_text or not value.strip()):
        return False
    try:
        return math.isfinite(float(value))
    except (ValueError, OverflowError):
        return False


def _rank_chart_types(*, temporal, cumulative, ranking, share, complete_composition, wide_categories):
    """Rank by eligibility, requested value order, time pattern, share intent, then label fit."""
    suitability = {
        "bar":            (True,                 False,   False,          False, not wide_categories),
        "horizontal_bar": (True,                 ranking, False,          False, wide_categories),
        "line":           (temporal,             False,   not cumulative, False, False),
        "area":           (temporal,             False,   cumulative,     False, False),
        "donut":          (complete_composition, False,   False,          share, False),
    }
    # Ties retain declaration order. Only the first candidate is rendered.
    return sorted(suitability, key=suitability.__getitem__, reverse=True)


def chart_for_rows(prompt, rows, *, title, sql="", query_id=None, x_axis=None, y_axis=None,
                   dimension=None, truncated=False, additive=True):
    """Plot one finite metric against distinct labels. Keep original rows and SQL order intact."""
    if len(rows) < 2:
        return None
    columns = list(rows[0])
    metrics = []
    for column in columns:
        tokens = re.findall(r"[^\W_]+", column.casefold())
        if tokens and tokens[-1] in (_TIME_DIMENSIONS | _IDENTIFIERS):
            continue
        values = [row[column] for row in rows if row.get(column) is not None]
        if len(values) >= 2 and all(_is_number(v, allow_text=column == y_axis) for v in values):
            metrics.append(column)
    if not metrics:
        return None
    metric = y_axis if y_axis in metrics else max(metrics, key=lambda key: len(_words(key) & _words(prompt)))
    # In particular, a numeric year must be interpreted as a dimension, not revenue.
    dimension_columns = sorted(
        (col for col in columns if col != metric and (col == x_axis or col not in metrics)),
        key=lambda col: (col != x_axis, bool(_words(col) & _IDENTIFIERS)),
    )
    for label in dimension_columns:
        labels = [row.get(label) for row in rows]
        if not all(isinstance(v, (str, int, float, date)) and not isinstance(v, bool)
                   and str(v).strip() and (not isinstance(v, float) or math.isfinite(v)) for v in labels):
            continue
        if len({str(v) for v in labels}) != len(labels):
            continue
        break
    else:
        return None

    question = prompt.casefold().translate(str.maketrans("‘’ʻʼ`", "'''''"))
    ascending = re.search(r"\b(?:ascending|o'sish tartibida|kichikdan kattaga|pastdan yuqoriga)\b", question)
    descending = re.search(r"\b(?:descending|kamayish tartibida|kattadan kichikka|yuqoridan pastga)\b", question)
    bottom = re.search(r"\b(?:bottom\d*|lowest|least|cheapest|eng (?:kichik|kam|past|arzon))\b", question)
    top = re.search(r"\b(?:top\d*|rank\w*|highest|most|reyting\w*|eng (?:katta|ko'p|kop|yuqori|qimmat))\b", question)
    # A question may ask for the bottom three, then explicitly display them descending.
    low_first = bool(ascending or (bottom and not descending))
    ranking = bool(ascending or descending or top or bottom)
    parts = _words(dimension or label)
    dates = [_period_key(v, parts) for v in labels]
    values = [row.get(metric) for row in rows]
    share = re.search(r"\b(?:ulush\w*|tarkib\w*|hissa\w*|share\w*|composition|proportion\w*|donut|doughnut|doiraviy)\b", question)
    partial_groups = truncated or re.search(r"\b(?:top|bottom)\s*\d+", question)
    limit = re.search(r"\bLIMIT\s+(\d+)\s*(?:OFFSET\s+\d+)?\s*;?\s*$", sql, re.I)
    if limit and len(rows) >= int(limit[1]):
        partial_groups = True
    nonadditive = (not additive or _words(metric) & {"avg", "average", "mean", "rate", "median", "ratio", "growth", "margin", "marja"}
                   or re.search(r"\b(?:avg\s*\(|count\s*\(\s*distinct\s)", sql, re.I))
    cumulative = re.search(r"\b(?:kumul\w*|cumul\w*|running total|yig'indi\w*|jamlanma|to'plan\w*|maydonli|area)\b",
                           question + " " + metric.replace("_", " ").casefold())
    complete_composition = (not partial_groups and not nonadditive and len(rows) <= 8
                            and all(v is not None and float(v) >= 0 for v in values)
                            and any(float(v) > 0 for v in values))
    if complete_composition and _words(metric) & {"pct", "percent", "percentage", "share", "foiz", "ulush"}:
        total = sum(float(v) for v in values)
        complete_composition = any(math.isclose(total, whole, rel_tol=0.001) for whole in (1, 100))
    candidates = _rank_chart_types(
        temporal=all(key is not None for key in dates), cumulative=bool(cumulative),
        ranking=ranking, share=bool(share), complete_composition=complete_composition,
        wide_categories=len(rows) > 7 or max(len(str(v)) for v in labels) > 18,
    )
    chart_type = candidates[0]
    if chart_type in {"line", "area"}:
        ordered = [row for _, row in sorted(zip(dates, rows), key=lambda pair: pair[0])]
    else:
        # Exact decimal keys keep adjacent large integer amounts distinguishable.
        def value_order(row):
            missing = row.get(metric) is None
            amount = Decimal(0) if missing else Decimal(str(row[metric]))
            return missing, amount if low_first else amount.copy_negate(), str(row[label]).casefold()

        ordered = sorted(rows, key=value_order)
    return ChartSpec(id=f"chart-{query_id}" if query_id else "chat_chart", query_id=query_id,
                     title=title, sql_query=sql, chart_type=chart_type, x_axis=label, y_axis=metric,
                     data_keys=[metric], data=ordered)
