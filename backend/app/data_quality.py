import datetime
import logging
from typing import Dict, List, Optional

from app.database import db_manager
from app.models import (
    DataQualityAuditRequest,
    DataQualityAuditResponse,
    RelationshipQualityResult,
    SchemaContext,
    TableMeta,
    TableQualityResult,
)
from app.relationship_guard import RelationshipGuard


logger = logging.getLogger(__name__)


class DataQualityAuditor:
    """Bounded, aggregate-only data-quality checks for arbitrary SQL schemas."""

    TECHNICAL_TOKENS = (
        "_log", "_audit", "_progress", "history", "notification", "temp_", "sync_"
    )
    DATE_TOKENS = ("date", "time", "created", "updated", "period", "sana")

    @classmethod
    def _select_tables(
        cls,
        schema_ctx: SchemaContext,
        request: DataQualityAuditRequest,
    ) -> List[TableMeta]:
        table_map = {table.name.lower(): table for table in schema_ctx.tables}
        if request.tables:
            selected = []
            for name in request.tables:
                table = table_map.get(name.lower())
                if table is None:
                    raise ValueError(f"Unknown table in audit request: {name}")
                selected.append(table)
            return selected[: request.max_tables]

        business_tables = [
            table for table in schema_ctx.tables
            if not any(token in table.name.lower() for token in cls.TECHNICAL_TOKENS)
        ]
        keyed = [table for table in business_tables if table.primary_key or table.candidate_keys]
        pool = keyed or business_tables
        return sorted(pool, key=lambda table: table.row_count or 0, reverse=True)[: request.max_tables]

    @classmethod
    def _date_columns(cls, table: TableMeta) -> List[str]:
        typed = [
            column.name for column in table.columns
            if any(token in column.data_type.lower() for token in ("date", "time"))
        ]
        priority = {
            "updated_at": 0,
            "datetime_updated": 1,
            "modified_at": 2,
            "loaded_at": 3,
            "created_at": 4,
            "datetime_created": 5,
            "date": 6,
        }
        return sorted(
            typed,
            key=lambda name: (priority.get(name.lower(), 20), typed.index(name)),
        )

    @classmethod
    def _profile_table(cls, table: TableMeta, sample_rows: int) -> TableQualityResult:
        result = TableQualityResult(
            table=table.name,
            row_count_estimate=table.row_count or 0,
            declared_primary_key=list(table.primary_key),
            status="pass",
        )
        quoted_table = db_manager.quote_identifier(table.name)
        key = table.primary_key or (table.candidate_keys[0] if table.candidate_keys else [])
        if len(key) == 1:
            key_column = key[0]
            quoted_key = db_manager.quote_identifier(key_column)
            duplicate_sql = f"""
            SELECT COUNT(*) AS duplicate_key_groups
            FROM (
                SELECT {quoted_key}
                FROM {quoted_table}
                WHERE {quoted_key} IS NOT NULL
                GROUP BY {quoted_key}
                HAVING COUNT(*) > 1
                LIMIT 1001
            ) duplicate_keys
            """
            try:
                rows = db_manager.execute_query(duplicate_sql, max_rows=1)
                duplicates = int(rows[0].get("duplicate_key_groups", 0)) if rows else 0
                result.duplicate_key_groups = duplicates
                if duplicates == 0:
                    result.verified_candidate_key = [key_column]
                else:
                    result.status = "critical"
                    suffix = "+" if duplicates >= 1001 else ""
                    result.issues.append(f"{duplicates}{suffix} duplicate key groups detected")
            except Exception as exc:
                result.status = "warning"
                result.issues.append(f"key uniqueness check unavailable: {exc}")
        elif key:
            result.issues.append("composite key recorded; automatic uniqueness probe skipped")
            result.status = "warning"
        else:
            result.issues.append("no declared or candidate key")
            result.status = "warning"

        important = []
        for column in table.columns:
            if (
                column.name in key
                or column.is_foreign_key
                or not column.nullable
                or any(token in column.name.lower() for token in cls.DATE_TOKENS)
            ):
                important.append(column.name)
        if not important:
            important = [column.name for column in table.columns[:6]]
        important = list(dict.fromkeys(important))[:10]
        if important:
            projection = ", ".join(db_manager.quote_identifier(name) for name in important)
            null_exprs = ", ".join(
                f"SUM(CASE WHEN {db_manager.quote_identifier(name)} IS NULL THEN 1 ELSE 0 END) AS null_{idx}"
                for idx, name in enumerate(important)
            )
            null_sql = f"""
            SELECT COUNT(*) AS sampled_rows, {null_exprs}
            FROM (SELECT {projection} FROM {quoted_table} LIMIT {int(sample_rows)}) sampled
            """
            try:
                rows = db_manager.execute_query(null_sql, max_rows=1)
                if rows:
                    sampled = int(rows[0].get("sampled_rows", 0))
                    result.sampled_rows = sampled
                    if sampled:
                        for idx, name in enumerate(important):
                            null_count = int(rows[0].get(f"null_{idx}", 0) or 0)
                            result.null_rates[name] = round(null_count / sampled, 4)
                        for name in key:
                            if result.null_rates.get(name, 0) > 0:
                                result.status = "critical"
                                result.issues.append(f"key column {name} contains NULL values")
            except Exception as exc:
                result.status = "warning" if result.status == "pass" else result.status
                result.issues.append(f"NULL profile unavailable: {exc}")

        date_columns = cls._date_columns(table)
        if date_columns:
            result.freshness_column = date_columns[0]
        for date_column in date_columns[:5]:
            quoted_date = db_manager.quote_identifier(date_column)
            freshness_sql = (
                f"SELECT {quoted_date} AS latest_value FROM {quoted_table} "
                f"WHERE {quoted_date} IS NOT NULL ORDER BY {quoted_date} DESC LIMIT 1"
            )
            try:
                rows = db_manager.execute_query(freshness_sql, max_rows=1)
                if rows:
                    value = rows[0].get("latest_value")
                    result.latest_timestamp = str(value) if value is not None else None
                if result.latest_timestamp is not None:
                    result.freshness_column = date_column
                    break
            except Exception as exc:
                logger.debug("Freshness check failed for %s.%s: %s", table.name, date_column, exc)
        if date_columns and result.latest_timestamp is None:
            result.status = "warning" if result.status == "pass" else result.status
            result.issues.append("all candidate freshness timestamps are empty or unavailable")
        return result

    @classmethod
    def _profile_relationship(
        cls,
        relationship,
        table_map: Dict[str, TableMeta],
        sample_rows: int,
    ) -> RelationshipQualityResult:
        item = RelationshipQualityResult(
            from_table=relationship.from_table,
            from_column=relationship.from_column,
            to_table=relationship.to_table,
            to_column=relationship.to_column,
            source=relationship.source,
            verified=relationship.verified,
            cardinality=relationship.cardinality,
        )
        parent = table_map.get(relationship.to_table.lower())
        child = table_map.get(relationship.from_table.lower())
        if parent is None or child is None:
            item.issue = "relationship table is outside the profiled schema"
            return item

        unique, reason = RelationshipGuard._verify_unique_live(parent, relationship.to_column)
        if not unique:
            item.issue = reason
            return item

        child_table = db_manager.quote_identifier(child.name)
        parent_table = db_manager.quote_identifier(parent.name)
        child_key = db_manager.quote_identifier(relationship.from_column)
        parent_key = db_manager.quote_identifier(relationship.to_column)
        inactive_column = next(
            (
                column.name for column in parent.columns
                if column.name.lower() in {"is_deleted", "deleted"}
                and "bool" in column.data_type.lower()
            ),
            None,
        )
        inactive_expr = "0 AS inactive_parent_rows"
        if inactive_column:
            quoted_inactive = db_manager.quote_identifier(inactive_column)
            inactive_expr = (
                f"SUM(CASE WHEN p.{parent_key} IS NOT NULL "
                f"AND p.{quoted_inactive} = TRUE THEN 1 ELSE 0 END) AS inactive_parent_rows"
            )
        orphan_sql = f"""
        SELECT COUNT(*) AS checked_rows,
               SUM(CASE WHEN p.{parent_key} IS NULL THEN 1 ELSE 0 END) AS orphan_rows,
               {inactive_expr}
        FROM (
            SELECT {child_key}
            FROM {child_table}
            WHERE {child_key} IS NOT NULL
            LIMIT {int(sample_rows)}
        ) c
        LEFT JOIN {parent_table} p ON c.{child_key} = p.{parent_key}
        """
        try:
            # Referential integrity must inspect all physical parent rows.
            # Chat analytics continues to apply the normal soft-delete policy.
            rows = db_manager.execute_query(
                orphan_sql,
                max_rows=1,
                apply_soft_delete=False,
            )
            if rows:
                item.checked_rows = int(rows[0].get("checked_rows", 0) or 0)
                item.orphan_count_sample = int(rows[0].get("orphan_rows", 0) or 0)
                item.inactive_parent_count_sample = int(
                    rows[0].get("inactive_parent_rows", 0) or 0
                )
                if item.orphan_count_sample:
                    item.issue = "sample contains orphan foreign-key values"
                else:
                    item.verified = True
                    item.cardinality = "many-to-one"
                    relationship.verified = True
                    relationship.cardinality = "many-to-one"
                    if relationship.source != "foreign_key":
                        relationship.source = "data_probe"
                        relationship.confidence = 0.9
                    if item.inactive_parent_count_sample:
                        item.issue = "sample references soft-deleted parent rows"
        except Exception as exc:
            item.issue = f"orphan check unavailable: {exc}"
        return item

    @classmethod
    def audit(
        cls,
        schema_ctx: SchemaContext,
        request: DataQualityAuditRequest,
    ) -> DataQualityAuditResponse:
        if not db_manager.is_connected:
            raise RuntimeError("Database is not connected.")
        selected = cls._select_tables(schema_ctx, request)
        table_results = [cls._profile_table(table, request.sample_rows) for table in selected]

        relationship_results: List[RelationshipQualityResult] = []
        if request.include_relationships:
            selected_names = {table.name.lower() for table in selected}
            relevant = [
                relationship for relationship in schema_ctx.relationships
                if relationship.from_table.lower() in selected_names
                or relationship.to_table.lower() in selected_names
            ]
            relevant.sort(key=lambda rel: (not rel.verified, -float(rel.confidence or 0)))
            table_map = {table.name.lower(): table for table in schema_ctx.tables}
            for relationship in relevant[:30]:
                relationship_results.append(
                    cls._profile_relationship(relationship, table_map, request.sample_rows)
                )

        warnings = [
            "Row counts are metadata estimates unless the individual check performs an aggregate.",
            "NULL and orphan rates are bounded samples; uniqueness and freshness checks are exact when they complete within the statement timeout.",
        ]
        if schema_ctx.schema_changes:
            warnings.append(
                "Schema drift detected: " + "; ".join(schema_ctx.schema_changes[:20])
            )
        return DataQualityAuditResponse(
            database_name=schema_ctx.database_name,
            read_only=True,
            checked_at=datetime.datetime.now(datetime.timezone.utc).isoformat(),
            tables_checked=len(table_results),
            relationships_checked=len(relationship_results),
            tables=table_results,
            relationships=relationship_results,
            warnings=warnings,
        )
