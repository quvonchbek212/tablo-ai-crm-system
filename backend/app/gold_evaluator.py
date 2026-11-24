import asyncio
import json
from collections import Counter
import sqlglot

from app.agent_analyst import AIDataAnalystAgent
from app.database import db_manager
from app.external_workspace import SourceChangedError
from app.models import (
    GoldEvaluationCaseResult,
    GoldEvaluationRequest,
    GoldEvaluationResponse,
    SchemaContext,
)
from app.sql_validator import validate_and_sanitize_sql


class GoldQueryEvaluator:
    """Compare AI results with operator-reviewed SELECT-only reference queries."""

    @staticmethod
    def _results_match(actual, expected, ordered):
        # Prefer named columns. When the model uses different aliases, compare
        # SQL projection positions so revenue and cost cannot trade values.
        named = bool(actual and expected and actual[0].keys() == expected[0].keys())
        def encode(rows):
            encoded = []
            for row in rows:
                values = [row[name] for name in sorted(row)] if named else list(row.values())
                # Preserve column types, while allowing equivalent integer and
                # floating SQL results such as SUM(12) and SUM(12.0).
                encoded.append(tuple(
                    ("number", v) if type(v) in (int, float)
                    else ("value", json.dumps(v, sort_keys=True, default=str)) for v in values
                ))
            return encoded
        left, right = encode(actual), encode(expected)
        return left == right if ordered else Counter(left) == Counter(right)

    @classmethod
    async def evaluate(
        cls,
        schema_ctx: SchemaContext,
        request: GoldEvaluationRequest,
        *, answer=None, identity=None,
    ) -> GoldEvaluationResponse:
        if request.database_name and request.database_name != schema_ctx.database_name:
            raise ValueError(
                "Gold suite belongs to a different database: "
                f"{request.database_name} != {schema_ctx.database_name}."
            )
        if (
            request.schema_fingerprint
            and request.schema_fingerprint != schema_ctx.schema_fingerprint
        ):
            raise ValueError(
                "Gold suite schema fingerprint is stale. Re-review it for the connected schema."
            )
        def reference_rows(sql):
            if identity is None:
                return db_manager.execute_query(sql, max_rows=501)
            with identity.engine.connect() as connection:
                return db_manager.execute_query(sql, max_rows=501, connection=connection)

        results = []
        quota_exhausted = False
        for case in request.cases:
            result = GoldEvaluationCaseResult(name=case.name, question=case.question, passed=False)
            results.append(result)
            if quota_exhausted:
                result.outcome = "not_run"
                result.mismatch = "Skipped after provider rate limit."
                continue
            try:
                if identity:
                    identity.assert_current()
                gold_rows, ordered = [], False
                if case.expected_error:
                    if case.gold_sql or case.ranked_chart:
                        raise ValueError("A clarification reference cannot also specify SQL or a ranked chart.")
                else:
                    valid, sql = validate_and_sanitize_sql(case.gold_sql)
                    if not valid:
                        result.mismatch = f"Gold SQL rejected: {sql}"
                        continue
                    result.gold_sql = sql
                    gold_rows = await asyncio.to_thread(reference_rows, sql)
                    result.gold_row_count = len(gold_rows)
                    if len(gold_rows) > 500:
                        result.mismatch = "Reference exceeds 500 returned rows."
                        continue
                    ordered = sqlglot.parse_one(sql).args.get("order") is not None
                if identity:
                    identity.assert_current()
                generated = (await answer(case.question) if answer is not None else
                             await AIDataAnalystAgent.execute_analyst_agent(case.question, schema_ctx, history=[]))
                if identity:
                    identity.assert_current()
                rows = generated.rows or []
                result.generated_sql = generated.sql_query
                result.generated_row_count = len(rows)
                clarified = (generated.error in {"clarification_required", "metric_unavailable"}
                             and not rows and not generated.sql_query and not generated.charts and not generated.chart)
                if clarified:
                    result.passed = generated.error == case.expected_error and bool(generated.explanation.strip())
                    result.outcome = "correct" if result.passed else "clarification"
                elif not generated.success:
                    quota_exhausted = generated.error == "llm_rate_limited"
                    result.mismatch = generated.error or "No successful answer."
                else:
                    result.passed = (not case.expected_error and bool(generated.sql_query)
                                     and not generated.is_truncated and cls._results_match(rows, gold_rows, ordered))
                    if result.passed and case.ranked_chart:
                        charts = generated.charts or []
                        chart = charts[0] if len(charts) == 1 else None
                        expected = [list(r.values()) for r in gold_rows]
                        result.passed = bool(chart and chart == generated.chart and chart.x_axis and chart.y_axis
                                             and [[r.get(chart.x_axis), r.get(chart.y_axis)] for r in chart.data] == expected)
                    result.outcome = "correct" if result.passed else "wrong_answer"
                if not result.passed and not result.mismatch:
                    result.mismatch = generated.error or "Answer, requested order, chart, or clarification differs from reference."
            except SourceChangedError:
                raise
            except Exception as exc:
                result.mismatch = str(exc)

        passed_count = sum(1 for result in results if result.passed)
        not_run = sum(result.outcome == "not_run" for result in results)
        return GoldEvaluationResponse(
            database_name=schema_ctx.database_name,
            read_only=True,
            total=len(results),
            passed=passed_count,
            failed=len(results) - passed_count - not_run,
            not_run=not_run,
            results=results,
        )
