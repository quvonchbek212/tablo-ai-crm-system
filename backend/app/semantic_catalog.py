import json
import logging
import os
import re
import tempfile
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import sqlglot
from sqlglot import exp

from app.models import (
    SchemaContext,
    SemanticCatalog,
    SemanticJoinDefinition,
    SemanticMetricDefinition,
    SemanticFilterDefinition,
    SemanticTableDefinition,
    TableMeta,
    TableRelationship,
)
from app.database import db_manager
from app.demo_seed import ENTERPRISE_DB_PATH, is_enterprise_demo


logger = logging.getLogger(__name__)
CATALOG_DIRECTORY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "semantic_catalogs",
)


class SemanticCatalogError(ValueError):
    pass


class SemanticCatalogManager:
    """Load and apply a reviewed, database-scoped business semantic layer."""

    TECHNICAL_TOKENS = {
        "log", "audit", "history", "progress", "notification", "migration",
        "staging", "temp", "sync", "attachment", "message", "job",
    }
    NUMERIC_TYPE_TOKENS = {
        "int", "numeric", "decimal", "double", "real", "float", "money",
    }
    TEXT_TYPE_TOKENS = {"char", "text", "string", "enum", "uuid"}

    @staticmethod
    def _safe_key(value: str) -> str:
        clean = re.sub(r"[^a-zA-Z0-9_.-]+", "_", value or "database").strip("._")
        return clean[:80] or "database"

    @classmethod
    def catalog_key(cls, schema_ctx: SchemaContext) -> str:
        database = cls._safe_key(schema_ctx.database_name or "database")
        fingerprint = (schema_ctx.schema_fingerprint or "unprofiled")[:16]
        return f"{database}-{fingerprint}"

    @classmethod
    def catalog_path(cls, schema_ctx: SchemaContext) -> str:
        return os.path.join(CATALOG_DIRECTORY, f"{cls.catalog_key(schema_ctx)}.json")

    @staticmethod
    def _column_names(table: TableMeta) -> Set[str]:
        return {column.name.lower() for column in table.columns}

    @classmethod
    def _classify_table(cls, table: TableMeta) -> str:
        name_tokens = set(re.split(r"[_\.]+", table.name.lower()))
        if name_tokens.intersection(cls.TECHNICAL_TOKENS):
            return "technical"
        fk_count = sum(1 for column in table.columns if column.is_foreign_key)
        numeric_count = sum(
            1 for column in table.columns
            if any(token in column.data_type.lower() for token in cls.NUMERIC_TYPE_TOKENS)
            and not column.is_primary_key
            and not column.is_foreign_key
        )
        has_time = any(
            any(token in column.data_type.lower() or token in column.name.lower()
                for token in ("date", "time", "timestamp"))
            for column in table.columns
        )
        if fk_count >= 2 and numeric_count == 0:
            return "bridge"
        if has_time and numeric_count:
            return "fact"
        if table.object_type in {"view", "materialized_view"} and has_time:
            return "snapshot"
        if table.primary_key or table.candidate_keys:
            return "dimension"
        return "unknown"

    @classmethod
    def _draft_table(cls, table: TableMeta) -> SemanticTableDefinition:
        measures = [
            column.name for column in table.columns
            if any(token in column.data_type.lower() for token in cls.NUMERIC_TYPE_TOKENS)
            and not column.is_primary_key
            and not column.is_foreign_key
            and not column.name.lower().endswith("_id")
        ]
        dimensions = [
            column.name for column in table.columns
            if any(token in column.data_type.lower() for token in cls.TEXT_TYPE_TOKENS)
            and not column.name.lower().endswith("_id")
        ]
        default_time = next(
            (
                column.name for column in table.columns
                if any(token in column.data_type.lower() or token in column.name.lower()
                       for token in ("date", "time", "timestamp"))
            ),
            None,
        )
        preferred_labels = ("name", "title", "label", "code", "description")
        default_label = next(
            (
                column.name for preferred in preferred_labels for column in table.columns
                if column.name.lower() == preferred
                or column.name.lower().endswith(f"_{preferred}")
            ),
            dimensions[0] if dimensions else None,
        )
        return SemanticTableDefinition(
            table=table.name,
            aliases=[table.name.replace("_", " ").replace(".", " ")],
            description=table.comment,
            role=cls._classify_table(table),
            grain=table.grain_hint,
            default_time_column=default_time,
            default_label_column=default_label,
            measures=measures,
            dimensions=dimensions,
            certified=False,
        )

    @classmethod
    def build_draft(cls, schema_ctx: SchemaContext) -> SemanticCatalog:
        """Create an inspectable draft without claiming business correctness."""
        return SemanticCatalog(
            catalog_key=cls.catalog_key(schema_ctx),
            database_name=schema_ctx.database_name,
            schema_fingerprint=schema_ctx.schema_fingerprint or "unprofiled",
            reviewed=False,
            tables=[cls._draft_table(table) for table in schema_ctx.tables],
            metrics=[],
            relationships=[],
        )

    @classmethod
    def load_for_context(cls, schema_ctx: SchemaContext) -> Optional[SemanticCatalog]:
        path = cls.catalog_path(schema_ctx)
        bundled = False
        if not os.path.exists(path):
            # The shipped definitions belong exclusively to the seeded database.
            # A customer database may have identical tables and even the same filename.
            if not (schema_ctx.database_type == "sqlite" and schema_ctx.database_name
                    and Path(schema_ctx.database_name).resolve() == ENTERPRISE_DB_PATH.resolve()
                    and is_enterprise_demo(db_manager._engine)):
                return None
            path = Path(__file__).with_name("enterprise_semantics.json")
            bundled = True
        try:
            with open(path, "r", encoding="utf-8") as file:
                catalog = SemanticCatalog(**json.load(file))
            if catalog.schema_fingerprint != schema_ctx.schema_fingerprint:
                logger.warning("Ignoring stale semantic catalog with a different schema fingerprint: %s", path)
                return None
            if catalog.database_name and catalog.database_name != schema_ctx.database_name:
                logger.warning("Ignoring semantic catalog for a different database: %s", path)
                return None
            if bundled:
                catalog.database_name = schema_ctx.database_name
                catalog.catalog_key = cls.catalog_key(schema_ctx)
            return catalog
        except Exception as exc:
            logger.warning("Could not load semantic catalog %s: %s", path, exc)
            return None

    @staticmethod
    def _exact_table_lookup(schema_ctx: SchemaContext) -> Dict[str, TableMeta]:
        return {table.name.lower(): table for table in schema_ctx.tables}

    @classmethod
    def _resolve_column_reference(
        cls,
        schema_ctx: SchemaContext,
        reference: str,
        default_table: str,
    ) -> Tuple[TableMeta, str]:
        """Resolve `column` or `table.column` against exact live metadata."""
        tables = cls._exact_table_lookup(schema_ctx)
        clean = (reference or "").strip()
        if not clean:
            raise SemanticCatalogError("Semantic column reference cannot be empty.")

        chosen_table = tables.get(default_table.lower())
        column_name = clean
        for table_name in sorted(tables, key=len, reverse=True):
            prefix = f"{table_name}."
            if clean.lower().startswith(prefix):
                chosen_table = tables[table_name]
                column_name = clean[len(prefix):]
                break
        if chosen_table is None:
            raise SemanticCatalogError(f"Unknown semantic catalog table: {default_table}")
        if "." in column_name or column_name.lower() not in cls._column_names(chosen_table):
            raise SemanticCatalogError(
                f"Unknown semantic metric column: {clean} (default table {default_table})"
            )
        actual = next(
            column.name for column in chosen_table.columns
            if column.name.lower() == column_name.lower()
        )
        return chosen_table, actual

    @classmethod
    def _column_sql(
        cls,
        schema_ctx: SchemaContext,
        reference: str,
        default_table: str,
    ) -> Tuple[str, str]:
        table, column = cls._resolve_column_reference(schema_ctx, reference, default_table)
        return (
            f"{db_manager.quote_identifier(table.name)}.{db_manager.quote_identifier(column)}",
            table.name,
        )

    @staticmethod
    def _literal_sql(value: Any) -> str:
        return exp.convert(value).sql(dialect="postgres")

    @classmethod
    def _join_sql(
        cls,
        schema_ctx: SchemaContext,
        base_table: str,
        target_table: str,
    ) -> str:
        candidates = [
            relationship for relationship in schema_ctx.relationships
            if relationship.verified
            and relationship.source in {"foreign_key", "semantic_catalog"}
            and relationship.from_columns
            and relationship.to_columns
            and {
                relationship.from_table.lower(),
                relationship.to_table.lower(),
            } == {base_table.lower(), target_table.lower()}
        ]
        semantic = [item for item in candidates if item.source == "semantic_catalog"]
        if semantic:
            candidates = semantic
        if len(candidates) != 1:
            raise SemanticCatalogError(
                f"Certified metric requires one unambiguous approved relationship "
                f"between {base_table} and {target_table}."
            )
        relationship = candidates[0]
        pairs = []
        for left, right in zip(relationship.from_columns, relationship.to_columns):
            left_sql, _ = cls._column_sql(
                schema_ctx, f"{relationship.from_table}.{left}", relationship.from_table
            )
            right_sql, _ = cls._column_sql(
                schema_ctx, f"{relationship.to_table}.{right}", relationship.to_table
            )
            pairs.append(f"{left_sql} = {right_sql}")
        return (
            f"LEFT JOIN {db_manager.quote_identifier(target_table)} ON "
            + " AND ".join(pairs)
        )

    @classmethod
    def validate_catalog(cls, catalog: SemanticCatalog, schema_ctx: SchemaContext) -> None:
        if catalog.schema_fingerprint != schema_ctx.schema_fingerprint:
            raise SemanticCatalogError("Catalog schema_fingerprint does not match the connected database schema.")
        if catalog.database_name and catalog.database_name != schema_ctx.database_name:
            raise SemanticCatalogError("Catalog database_name does not match the connected database.")

        tables = cls._exact_table_lookup(schema_ctx)

        def validate_text(value: Optional[str], label: str, max_length: int) -> None:
            if value is None:
                return
            if len(value) > max_length or any(ord(char) < 32 for char in value):
                raise SemanticCatalogError(f"Invalid {label}: control characters or excessive length.")

        if not re.fullmatch(r"[A-Za-z0-9_.-]*", catalog.catalog_key or ""):
            raise SemanticCatalogError("Invalid catalog_key.")

        for unavailable in catalog.unavailable_metrics:
            validate_text(unavailable.reason, "unavailable metric reason", 700)
            if not unavailable.reason.strip():
                raise SemanticCatalogError("Provide a reason for the unavailable metric.")
            for phrase in unavailable.phrases:
                validate_text(phrase, "unavailable metric phrase", 160)
                if not cls._normalize_text(phrase):
                    raise SemanticCatalogError("Unavailable metric phrase cannot be empty.")

        def require_table(name: str) -> TableMeta:
            table = tables.get(name.lower())
            if table is None:
                raise SemanticCatalogError(f"Unknown semantic catalog table: {name}")
            return table

        def require_columns(table: TableMeta, columns: List[str], label: str) -> None:
            known = cls._column_names(table)
            unknown = [column for column in columns if column.lower() not in known]
            if unknown:
                raise SemanticCatalogError(
                    f"Unknown {label} columns for {table.name}: {', '.join(unknown)}"
                )

        def relationship_is_approved(left: str, right: str) -> bool:
            pair = {left.lower(), right.lower()}
            if any(
                relationship.verified
                and relationship.source in {"foreign_key", "semantic_catalog"}
                and {relationship.from_table.lower(), relationship.to_table.lower()} == pair
                for relationship in schema_ctx.relationships
            ):
                return True
            return any(
                relationship.approved
                and {relationship.from_table.lower(), relationship.to_table.lower()} == pair
                for relationship in catalog.relationships
            )

        seen_table_defs = set()
        certified_tables = set()
        for definition in catalog.tables:
            table = require_table(definition.table)
            if table.name.lower() in seen_table_defs:
                raise SemanticCatalogError(f"Duplicate table definition: {table.name}")
            seen_table_defs.add(table.name.lower())
            for alias in definition.aliases:
                validate_text(alias, "table alias", 120)
            validate_text(definition.description, "table description", 1000)
            validate_text(definition.grain, "table grain", 300)
            if definition.certified and definition.role in {"fact", "snapshot"} and not definition.grain:
                raise SemanticCatalogError(
                    f"Certified {definition.role} table {table.name} requires an explicit grain."
                )
            if definition.certified:
                certified_tables.add(table.name.lower())
            require_columns(table, definition.measures, "measure")
            require_columns(table, definition.dimensions, "dimension")
            require_columns(
                table,
                [column for column in (
                    definition.default_time_column,
                    definition.default_label_column,
                ) if column],
                "default",
            )

        seen_metric_ids = set()
        for metric in catalog.metrics:
            if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", metric.id):
                raise SemanticCatalogError(f"Invalid metric id: {metric.id}")
            if metric.id.lower() in seen_metric_ids:
                raise SemanticCatalogError(f"Duplicate metric id: {metric.id}")
            seen_metric_ids.add(metric.id.lower())
            table = require_table(metric.table)
            validate_text(metric.name, "metric name", 160)
            validate_text(metric.description, "metric description", 1000)
            for alias in [*metric.aliases, *metric.trigger_phrases]:
                validate_text(alias, "metric alias", 120)
            if metric.certified and table.name.lower() not in certified_tables:
                raise SemanticCatalogError(
                    f"Certified metric {metric.id} requires a certified source table."
                )
            if metric.aggregation != "count" and not metric.column:
                raise SemanticCatalogError(f"Metric {metric.id} requires a column.")
            if metric.fallback_columns and not metric.column:
                raise SemanticCatalogError(
                    f"Metric {metric.id} fallback_columns require a primary column."
                )
            references = [
                reference for reference in [
                    metric.column,
                    *metric.fallback_columns,
                    *metric.group_by,
                    metric.latest_by,
                ] if reference
            ]
            referenced_tables = {table.name.lower()}
            for reference in references:
                referenced_table, _ = cls._resolve_column_reference(
                    schema_ctx, reference, table.name
                )
                referenced_tables.add(referenced_table.name.lower())
            for item in metric.filters:
                filter_reference = (
                    f"{item.table}.{item.column}" if item.table else item.column
                )
                referenced_table, _ = cls._resolve_column_reference(
                    schema_ctx, filter_reference, table.name
                )
                referenced_tables.add(referenced_table.name.lower())
            for join_path in metric.join_paths:
                if len(join_path) < 2 or join_path[0].lower() != table.name.lower():
                    raise SemanticCatalogError(
                        f"Metric {metric.id} join paths must start at {table.name}."
                    )
                for path_table in join_path:
                    referenced_tables.add(require_table(path_table).name.lower())
                for left, right in zip(join_path, join_path[1:]):
                    if not relationship_is_approved(left, right):
                        raise SemanticCatalogError(
                            f"Metric {metric.id} join path {left} -> {right} is not approved."
                        )
            seen_prompt_filter_ids = set()
            for prompt_filter in metric.prompt_filters:
                if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{0,99}", prompt_filter.id):
                    raise SemanticCatalogError(
                        f"Invalid prompt filter id for metric {metric.id}: {prompt_filter.id}"
                    )
                if prompt_filter.id.lower() in seen_prompt_filter_ids:
                    raise SemanticCatalogError(
                        f"Duplicate prompt filter id for metric {metric.id}: {prompt_filter.id}"
                    )
                seen_prompt_filter_ids.add(prompt_filter.id.lower())
                for phrase in prompt_filter.group_by_phrases:
                    validate_text(phrase, "prompt grouping phrase", 120)
                if prompt_filter.group_by_phrases and not prompt_filter.group_by_alias:
                    raise SemanticCatalogError(
                        f"Prompt filter {prompt_filter.id} grouping phrases require group_by_alias."
                    )
                if prompt_filter.group_by_alias and not re.fullmatch(
                    r"[A-Za-z_][A-Za-z0-9_]{0,99}", prompt_filter.group_by_alias
                ):
                    raise SemanticCatalogError(
                        f"Invalid group_by_alias for prompt filter {prompt_filter.id}."
                    )
                prompt_table, _ = cls._resolve_column_reference(
                    schema_ctx,
                    f"{prompt_filter.table}.{prompt_filter.column}",
                    table.name,
                )
                referenced_tables.add(prompt_table.name.lower())
                if (
                    prompt_filter.join_path[0].lower() != table.name.lower()
                    or prompt_filter.join_path[-1].lower() != prompt_table.name.lower()
                ):
                    raise SemanticCatalogError(
                        f"Prompt filter {prompt_filter.id} join_path must connect "
                        f"{table.name} to {prompt_table.name}."
                    )
                for path_table in prompt_filter.join_path:
                    referenced_tables.add(require_table(path_table).name.lower())
                for left, right in zip(
                    prompt_filter.join_path,
                    prompt_filter.join_path[1:],
                ):
                    if not relationship_is_approved(left, right):
                        raise SemanticCatalogError(
                            f"Prompt filter {prompt_filter.id} join path "
                            f"{left} -> {right} is not approved."
                        )
                seen_prompt_values = set()
                for prompt_value in prompt_filter.values:
                    validate_text(prompt_value.value, "prompt filter value", 160)
                    normalized_value = cls._normalize_text(prompt_value.value)
                    if not normalized_value or normalized_value in seen_prompt_values:
                        raise SemanticCatalogError(
                            f"Prompt filter {prompt_filter.id} has an empty or duplicate value."
                        )
                    seen_prompt_values.add(normalized_value)
                    for alias in prompt_value.aliases:
                        validate_text(alias, "prompt filter alias", 120)
            if metric.certified and not referenced_tables.issubset(certified_tables):
                missing = sorted(referenced_tables.difference(certified_tables))
                raise SemanticCatalogError(
                    f"Certified metric {metric.id} references uncertified tables: "
                    + ", ".join(missing)
                )
            if metric.column and metric.aggregation in {"sum", "avg"}:
                for reference in [metric.column, *metric.fallback_columns]:
                    column_table, column_name = cls._resolve_column_reference(
                        schema_ctx, reference, table.name
                    )
                    column = next(
                        item for item in column_table.columns
                        if item.name.lower() == column_name.lower()
                    )
                    if not any(token in column.data_type.lower() for token in cls.NUMERIC_TYPE_TOKENS):
                        raise SemanticCatalogError(
                            f"Metric {metric.id} requires numeric aggregation columns."
                        )
            if metric.group_by_alias and not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]{0,99}", metric.group_by_alias
            ):
                raise SemanticCatalogError(f"Invalid group_by_alias for metric {metric.id}.")
            if metric.coalesce_group_by and len(metric.group_by) < 2:
                raise SemanticCatalogError(
                    f"Metric {metric.id} coalesce_group_by requires at least two columns."
                )
            if metric.limit and not metric.group_by:
                raise SemanticCatalogError(
                    f"Metric {metric.id} limit requires group_by dimensions."
                )
            for required_filter in metric.filters:
                if required_filter.operator in {"in", "not_in", "not_in_or_null"} and not isinstance(required_filter.value, list):
                    raise SemanticCatalogError(
                        f"Metric {metric.id} filter {required_filter.column} requires a list value."
                    )
                if required_filter.operator in {"is_null", "is_not_null", "not_true"} and required_filter.value is not None:
                    raise SemanticCatalogError(
                        f"Metric {metric.id} null filter must not define a value."
                    )
                if required_filter.operator in {"ilike", "not_ilike", "not_ilike_or_null"}:
                    values = (
                        required_filter.value
                        if isinstance(required_filter.value, list)
                        else [required_filter.value]
                    )
                    if not values or not all(isinstance(value, str) for value in values):
                        raise SemanticCatalogError(
                            f"Metric {metric.id} pattern filter requires string value(s)."
                        )

        for relationship in catalog.relationships:
            if len(relationship.from_columns) != len(relationship.to_columns):
                raise SemanticCatalogError("Semantic relationship column counts must match.")
            source = require_table(relationship.from_table)
            target = require_table(relationship.to_table)
            validate_text(relationship.description, "relationship description", 1000)
            if relationship.approved and relationship.cardinality not in {"many-to-one", "one-to-one"}:
                raise SemanticCatalogError(
                    "Approved semantic relationships must be oriented many-to-one or one-to-one."
                )
            require_columns(source, relationship.from_columns, "relationship source")
            require_columns(target, relationship.to_columns, "relationship target")

    @classmethod
    def save_for_context(
        cls,
        catalog: SemanticCatalog,
        schema_ctx: SchemaContext,
    ) -> SemanticCatalog:
        """Atomically save local metadata; this never writes to the source DB."""
        catalog.catalog_key = cls.catalog_key(schema_ctx)
        cls.validate_catalog(catalog, schema_ctx)
        os.makedirs(CATALOG_DIRECTORY, exist_ok=True)
        target = cls.catalog_path(schema_ctx)
        handle, temporary = tempfile.mkstemp(
            prefix=f".{catalog.catalog_key}-",
            suffix=".json.tmp",
            dir=CATALOG_DIRECTORY,
        )
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as file:
                json.dump(catalog.model_dump(), file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
        return catalog

    @classmethod
    def apply_to_context(
        cls,
        schema_ctx: SchemaContext,
        catalog: Optional[SemanticCatalog] = None,
    ) -> Optional[SemanticCatalog]:
        """Merge only reviewed/certified semantics into runtime metadata."""
        schema_ctx.relationships = [
            relationship for relationship in schema_ctx.relationships
            if relationship.source != "semantic_catalog"
        ]
        for table in schema_ctx.tables:
            table.semantic_aliases = []
            table.business_description = None
            table.business_role = None
            table.certified_source = False
            table.default_time_column = None
            table.default_time_format = None
            table.default_label_column = None
            table.measure_columns = []
            table.dimension_columns = []
        schema_ctx.semantic_catalog_key = cls.catalog_key(schema_ctx)
        schema_ctx.semantic_catalog_reviewed = False
        schema_ctx.semantic_metrics = []
        schema_ctx.unavailable_metrics = []

        catalog = catalog or cls.load_for_context(schema_ctx)
        if catalog is None or not catalog.reviewed:
            return catalog
        cls.validate_catalog(catalog, schema_ctx)

        table_lookup = cls._exact_table_lookup(schema_ctx)
        for definition in catalog.tables:
            table = table_lookup[definition.table.lower()]
            table.semantic_aliases = list(dict.fromkeys(definition.aliases))[:40]
            table.business_description = definition.description
            table.business_role = definition.role
            table.grain_hint = definition.grain or table.grain_hint
            table.certified_source = bool(definition.certified)
            table.default_time_column = definition.default_time_column
            table.default_time_format = definition.default_time_format
            table.default_label_column = definition.default_label_column
            table.measure_columns = definition.measures
            table.dimension_columns = definition.dimensions

        for relationship in catalog.relationships:
            if not relationship.approved:
                continue
            pairs = " AND ".join(
                f"{relationship.from_table}.{left} = {relationship.to_table}.{right}"
                for left, right in zip(relationship.from_columns, relationship.to_columns)
            )
            schema_ctx.relationships.append(TableRelationship(
                from_table=relationship.from_table,
                from_column=relationship.from_columns[0],
                to_table=relationship.to_table,
                to_column=relationship.to_columns[0],
                from_columns=relationship.from_columns,
                to_columns=relationship.to_columns,
                join_hint=f"LEFT JOIN {relationship.to_table} ON {pairs}",
                source="semantic_catalog",
                confidence=1.0,
                verified=True,
                cardinality=relationship.cardinality,
            ))

        schema_ctx.semantic_catalog_reviewed = True
        schema_ctx.unavailable_metrics = catalog.unavailable_metrics
        schema_ctx.semantic_metrics = [
            metric for metric in catalog.metrics if metric.certified
        ]
        return catalog

    @staticmethod
    def _normalize_text(value: str) -> str:
        value = (value or "").lower().replace("ʻ", "'").replace("ʼ", "'")
        return re.sub(r"[^\w]+", " ", value, flags=re.UNICODE).strip()

    @classmethod
    def unavailable_metric_reason(cls, prompt: str, schema_ctx: SchemaContext) -> Optional[str]:
        if not schema_ctx.semantic_catalog_reviewed or not schema_ctx.unavailable_metrics:
            return None
        def words(text: str) -> list[str]:
            # Uzbek genitive marks ownership ("to'lovlarning summasi").
            return [word.removesuffix("ning") if len(word) > 6 else word
                    for word in cls._normalize_text(text).split()]

        def matches(phrase: str) -> bool:
            # "Qaytarilgan to'lovlar summasi" can include "jami" before summasi.
            pattern = r" (?:jami )?".join(re.escape(word) for word in words(phrase))
            return bool(pattern and re.search(r"(?<!\S)" + pattern
                        + r"(?!\S)(?! (?:kerak |shart )?emas\b)", question))

        question = " ".join(words(prompt))
        reasons = [item.reason for item in schema_ctx.unavailable_metrics
                   if any(matches(phrase) for phrase in item.phrases)]
        return " ".join(dict.fromkeys(reasons)) or None

    @classmethod
    def matching_metrics(
        cls,
        prompt: str,
        schema_ctx: SchemaContext,
    ) -> List[SemanticMetricDefinition]:
        normalized = cls._normalize_text(prompt)
        matches = []
        for metric in schema_ctx.semantic_metrics:
            names = [metric.name, *metric.aliases, *metric.trigger_phrases]
            if any(
                cls._normalize_text(name)
                and cls._normalize_text(name) in normalized
                for name in names
            ):
                matches.append(metric)
        return matches

    @classmethod
    def _resolve_prompt_filters(
        cls,
        prompt: str,
        metric: SemanticMetricDefinition,
    ) -> List[Tuple[Any, str]]:
        """Resolve reviewed dimension values, tolerating small user typos."""
        normalized_prompt = cls._normalize_text(prompt)
        prompt_tokens = normalized_prompt.split()
        prompt_candidates = set(prompt_tokens)
        for size in range(2, min(4, len(prompt_tokens)) + 1):
            prompt_candidates.update(
                " ".join(prompt_tokens[index:index + size])
                for index in range(len(prompt_tokens) - size + 1)
            )

        resolved = []
        for prompt_filter in metric.prompt_filters:
            scored_values = []
            for prompt_value in prompt_filter.values:
                names = [prompt_value.value, *prompt_value.aliases]
                score = 0.0
                for name in names:
                    normalized_name = cls._normalize_text(name)
                    if not normalized_name:
                        continue
                    if normalized_name in normalized_prompt:
                        score = 1.0
                        break
                    score = max(
                        score,
                        *(
                            SequenceMatcher(None, normalized_name, candidate).ratio()
                            for candidate in prompt_candidates
                            if len(candidate) >= 4
                        ),
                    )
                scored_values.append((score, prompt_value.value))
            scored_values.sort(key=lambda item: item[0], reverse=True)
            best_score, best_value = scored_values[0]
            if best_score < prompt_filter.min_similarity:
                continue
            if (
                len(scored_values) > 1
                and scored_values[1][0] >= prompt_filter.min_similarity
                and best_score - scored_values[1][0] < 0.04
            ):
                raise SemanticCatalogError(
                    f"Prompt filter {prompt_filter.id} matched multiple reviewed values."
                )
            resolved.append((prompt_filter, best_value))
        return resolved

    @classmethod
    def _resolve_prompt_groupings(
        cls,
        prompt: str,
        metric: SemanticMetricDefinition,
    ) -> List[Any]:
        """Resolve reviewed phrases such as 'har bir viloyatda' to GROUP BY."""
        normalized_prompt = cls._normalize_text(prompt)
        return [
            prompt_filter
            for prompt_filter in metric.prompt_filters
            if any(
                cls._normalize_text(phrase)
                and cls._normalize_text(phrase) in normalized_prompt
                for phrase in prompt_filter.group_by_phrases
            )
        ]

    @classmethod
    def _filter_sql(
        cls,
        schema_ctx: SchemaContext,
        metric: SemanticMetricDefinition,
        required_filter,
    ) -> str:
        reference = (
            f"{required_filter.table}.{required_filter.column}"
            if required_filter.table else required_filter.column
        )
        column_sql, _ = cls._column_sql(
            schema_ctx, reference, metric.table
        )
        operator = required_filter.operator
        value = required_filter.value
        if operator == "is_null":
            return f"{column_sql} IS NULL"
        if operator == "is_not_null":
            return f"{column_sql} IS NOT NULL"
        if operator == "not_true":
            return f"COALESCE({column_sql}, FALSE) = FALSE"
        if operator in {"in", "not_in", "not_in_or_null"}:
            values = ", ".join(cls._literal_sql(item) for item in value)
            predicate = f"{column_sql} {'NOT IN' if operator != 'in' else 'IN'} ({values})"
            if operator == "not_in_or_null":
                return f"({predicate} OR {column_sql} IS NULL)"
            return predicate
        if operator in {"ilike", "not_ilike", "not_ilike_or_null"}:
            values = value if isinstance(value, list) else [value]
            comparison_column = (
                f"COALESCE({column_sql}, '')"
                if operator == "not_ilike_or_null" else column_sql
            )
            predicates = [
                f"{comparison_column} "
                f"{'NOT ILIKE' if operator in {'not_ilike', 'not_ilike_or_null'} else 'ILIKE'} "
                f"{cls._literal_sql(item)}"
                for item in values
            ]
            connector = " AND " if operator in {"not_ilike", "not_ilike_or_null"} else " OR "
            return "(" + connector.join(predicates) + ")"
        sql_operator = {
            "eq": "=", "neq": "<>", "gt": ">", "gte": ">=",
            "lt": "<", "lte": "<=",
        }[operator]
        return f"{column_sql} {sql_operator} {cls._literal_sql(value)}"

    @classmethod
    def compile_matching_metric_sql(
        cls,
        prompt: str,
        schema_ctx: SchemaContext,
    ) -> Optional[Tuple[SemanticMetricDefinition, str]]:
        """Compile one certified structured metric into safe deterministic SQL."""
        matched_metrics = cls.matching_metrics(prompt, schema_ctx)
        normalized_prompt = cls._normalize_text(prompt)
        eligible_metrics = []
        for candidate_metric in matched_metrics:
            resolved_filters = cls._resolve_prompt_filters(prompt, candidate_metric)
            resolved_groupings = cls._resolve_prompt_groupings(prompt, candidate_metric)
            exact_metric_question = any(
                cls._normalize_text(name) == normalized_prompt
                for name in [candidate_metric.name, *candidate_metric.aliases]
                if cls._normalize_text(name)
            )
            matched_trigger = any(
                cls._normalize_text(trigger)
                and cls._normalize_text(trigger) in normalized_prompt
                for trigger in candidate_metric.trigger_phrases
            )
            # A broad trigger such as "authorized warehouse" may carry a
            # reviewed region value.  If no value was resolved, only an exact
            # reviewed global question is compiled; otherwise normal routing
            # gets a chance to clarify instead of silently dropping scope.
            if (
                candidate_metric.prompt_filters
                and matched_trigger
                and not exact_metric_question
                and not resolved_filters
                and not resolved_groupings
            ):
                continue
            eligible_metrics.append(
                (candidate_metric, resolved_filters, resolved_groupings)
            )
        if not eligible_metrics:
            return None
        if len(eligible_metrics) > 1:
            raise SemanticCatalogError(
                "The question matches multiple certified metrics; clarify the requested KPI."
            )
        metric, resolved_prompt_filters, resolved_prompt_groupings = eligible_metrics[0]
        base_table_sql = db_manager.quote_identifier(metric.table)
        required_tables: Set[str] = set()

        def column_sql(reference: str) -> str:
            sql, table_name = cls._column_sql(schema_ctx, reference, metric.table)
            if table_name.lower() != metric.table.lower():
                required_tables.add(table_name)
            return sql

        measure_expression = "*"
        if metric.column:
            measure_columns = [
                column_sql(reference)
                for reference in [metric.column, *metric.fallback_columns]
            ]
            measure_expression = (
                f"COALESCE({', '.join(measure_columns)})"
                if len(measure_columns) > 1 else measure_columns[0]
            )

        if metric.aggregation == "count":
            aggregate = f"COUNT({measure_expression})"
        elif metric.aggregation == "count_distinct":
            aggregate = f"COUNT(DISTINCT {measure_expression})"
        else:
            aggregate = f"{metric.aggregation.upper()}({measure_expression})"
        if metric.empty_value_zero and metric.aggregation == "sum":
            aggregate = f"COALESCE({aggregate}, 0)"

        group_expressions = [column_sql(reference) for reference in metric.group_by]
        group_aliases = [
            metric.group_by_alias
            if len(metric.group_by) == 1 and metric.group_by_alias
            else f"dimension_{index + 1}"
            for index, _ in enumerate(metric.group_by)
        ]
        if metric.coalesce_group_by and group_expressions:
            group_expressions = [f"COALESCE({', '.join(group_expressions)})"]
            group_aliases = [metric.group_by_alias or "dimension_1"]
        for prompt_grouping in resolved_prompt_groupings:
            grouping_sql, grouping_table = cls._column_sql(
                schema_ctx,
                f"{prompt_grouping.table}.{prompt_grouping.column}",
                metric.table,
            )
            required_tables.add(grouping_table)
            if grouping_sql not in group_expressions:
                group_expressions.append(grouping_sql)
                group_aliases.append(
                    prompt_grouping.group_by_alias or prompt_grouping.id
                )

        filters = [
            cls._filter_sql(schema_ctx, metric, required_filter)
            for required_filter in metric.filters
        ]
        for required_filter in metric.filters:
            reference = (
                f"{required_filter.table}.{required_filter.column}"
                if required_filter.table else required_filter.column
            )
            _, table_name = cls._column_sql(schema_ctx, reference, metric.table)
            if table_name.lower() != metric.table.lower():
                required_tables.add(table_name)

        for prompt_filter, matched_value in resolved_prompt_filters:
            prompt_column_sql, prompt_table = cls._column_sql(
                schema_ctx,
                f"{prompt_filter.table}.{prompt_filter.column}",
                metric.table,
            )
            required_tables.add(prompt_table)
            operator = "=" if prompt_filter.operator == "eq" else "ILIKE"
            filters.append(
                f"{prompt_column_sql} {operator} {cls._literal_sql(matched_value)}"
            )

        if metric.latest_by:
            latest_sql, latest_table = cls._column_sql(
                schema_ctx, metric.latest_by, metric.table
            )
            if latest_table.lower() != metric.table.lower():
                raise SemanticCatalogError("latest_by must belong to the metric source table.")
            _, latest_column = cls._resolve_column_reference(
                schema_ctx, metric.latest_by, metric.table
            )
            filters.append(
                f"{latest_sql} = (SELECT MAX({db_manager.quote_identifier(latest_column)}) "
                f"FROM {base_table_sql})"
            )
        if metric.exclude_null_group and group_expressions:
            filters.append(f"{group_expressions[0]} IS NOT NULL")

        join_paths = [*metric.join_paths]
        join_paths.extend(
            prompt_filter.join_path
            for prompt_filter, _ in resolved_prompt_filters
        )
        join_paths.extend(
            prompt_grouping.join_path
            for prompt_grouping in resolved_prompt_groupings
        )
        joins = []
        joined_tables = {metric.table.lower()}
        joined_edges = set()
        for join_path in join_paths:
            for left, right in zip(join_path, join_path[1:]):
                edge = (left.lower(), right.lower())
                if edge in joined_edges:
                    continue
                if left.lower() not in joined_tables:
                    raise SemanticCatalogError(
                        f"Certified metric join path reaches {left} before it is joined."
                    )
                joins.append(cls._join_sql(schema_ctx, left, right))
                joined_edges.add(edge)
                joined_tables.add(right.lower())
        for table_name in sorted(required_tables):
            if table_name.lower() in joined_tables:
                continue
            joins.append(cls._join_sql(schema_ctx, metric.table, table_name))
            joined_tables.add(table_name.lower())
        metric_alias = re.sub(r"[^A-Za-z0-9_]", "_", metric.id)
        metric_alias_sql = db_manager.quote_identifier(metric_alias)
        select_parts = []
        for index, expression in enumerate(group_expressions):
            alias = group_aliases[index]
            select_parts.append(f"{expression} AS {db_manager.quote_identifier(alias)}")
        select_parts.append(f"{aggregate} AS {metric_alias_sql}")

        clauses = [
            "SELECT " + ", ".join(select_parts),
            f"FROM {base_table_sql}",
            *joins,
        ]
        if filters:
            clauses.append("WHERE " + " AND ".join(filters))
        if group_expressions:
            clauses.append("GROUP BY " + ", ".join(group_expressions))
            clauses.append(
                f"ORDER BY {metric_alias_sql} {metric.order.upper()} NULLS LAST, "
                f"{group_expressions[0]} ASC"
            )
        if metric.limit:
            clauses.append(f"LIMIT {metric.limit}")
        return metric, " ".join(clauses)

    @staticmethod
    def _expression_value(node: exp.Expression) -> Optional[str]:
        if isinstance(node, exp.Literal):
            return str(node.this).lower()
        if isinstance(node, exp.Boolean):
            return str(node.this).lower()
        if isinstance(node, exp.Null):
            return "null"
        return None

    @classmethod
    def _required_filter_present(
        cls,
        where: Optional[exp.Where],
        required_filter,
    ) -> bool:
        if where is None:
            return False
        column_name = required_filter.column.lower()
        required_values = (
            required_filter.value
            if isinstance(required_filter.value, list)
            else [required_filter.value]
        )
        normalized_values = {
            str(value).lower() for value in required_values if value is not None
        }

        def conjuncts(node):
            if isinstance(node, (exp.Where, exp.Paren)):
                return conjuncts(node.this)
            if isinstance(node, exp.And):
                return conjuncts(node.this) + conjuncts(node.expression)
            return [node]

        def canonical(node):
            node = node.copy()
            for column in node.find_all(exp.Column):
                column.set("table", None)
                column.set("db", None)
                column.set("catalog", None)
            for identifier in node.find_all(exp.Identifier):
                identifier.set("quoted", False)
            return node.sql().lower()

        actual_terms = {canonical(term) for term in conjuncts(where)}
        column = exp.to_identifier(required_filter.column, quoted=True).sql()
        operator = required_filter.operator
        if operator == "not_true":
            alternatives = [f"COALESCE({column}, FALSE) = FALSE", f"{column} IS NOT TRUE",
                            f"({column} = FALSE OR {column} IS NULL)"]
            return any(canonical(sqlglot.parse_one(item).unnest()) in actual_terms for item in alternatives)
        if operator in {"ilike", "not_ilike", "not_ilike_or_null"}:
            expressions = []
            for value in required_values:
                literal = cls._literal_sql(value)
                lhs = f"COALESCE({column}, '')" if operator == "not_ilike_or_null" else column
                comparison = "ILIKE" if operator == "ilike" else "NOT ILIKE"
                expressions.append(f"{lhs} {comparison} {literal}")
            if operator == "ilike" and len(expressions) > 1:
                return canonical(sqlglot.parse_one(" OR ".join(expressions))) in actual_terms
            return all(canonical(sqlglot.parse_one(item)) in actual_terms for item in expressions)

        # Do not accept a required condition hidden under OR or a subquery.
        # Existing simple checks below operate on guaranteed top-level conjuncts.
        safe_terms = [term for term in conjuncts(where)
                      if not isinstance(term, exp.Or) and not term.find(exp.Select)]
        if not safe_terms:
            return False
        where = exp.Where(this=exp.and_(*[term.copy() for term in safe_terms]))

        comparison_types = {
            "eq": exp.EQ,
            "neq": exp.NEQ,
            "gt": exp.GT,
            "gte": exp.GTE,
            "lt": exp.LT,
            "lte": exp.LTE,
        }
        comparison_type = comparison_types.get(required_filter.operator)
        if comparison_type:
            for comparison in where.find_all(comparison_type):
                if not isinstance(comparison.this, exp.Column):
                    continue
                if comparison.this.name.lower() != column_name:
                    continue
                value = cls._expression_value(comparison.expression)
                if not normalized_values or value in normalized_values:
                    return True
            return False

        if required_filter.operator in {"in", "not_in"}:
            candidates = list(where.find_all(exp.In))
            for candidate in candidates:
                if not isinstance(candidate.this, exp.Column):
                    continue
                if candidate.this.name.lower() != column_name:
                    continue
                is_negated = isinstance(candidate.parent, exp.Not)
                if is_negated != (required_filter.operator == "not_in"):
                    continue
                actual_values = {
                    value for value in (
                        cls._expression_value(item) for item in candidate.expressions
                    ) if value is not None
                }
                if normalized_values.issubset(actual_values):
                    return True
            return False

        if required_filter.operator in {"is_null", "is_not_null"}:
            if required_filter.operator == "is_not_null":
                # SQL comparisons to a non-NULL literal exclude NULL under
                # three-valued logic; an explicit IS NOT NULL would be redundant.
                for term in conjuncts(where):
                    if (isinstance(term, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE, exp.ILike, exp.Like))
                            and isinstance(term.this, exp.Column)
                            and term.this.name.lower() == column_name
                            and isinstance(term.expression, (exp.Literal, exp.Boolean))):
                        return True
            for predicate in where.find_all(exp.Is):
                if not isinstance(predicate.this, exp.Column):
                    continue
                if predicate.this.name.lower() != column_name:
                    continue
                if not isinstance(predicate.expression, exp.Null):
                    continue
                is_negated = isinstance(predicate.parent, exp.Not)
                if is_negated == (required_filter.operator == "is_not_null"):
                    return True
            return False
        return False

    @classmethod
    def validate_metric_sql(
        cls,
        prompt: str,
        sql: str,
        schema_ctx: SchemaContext,
    ) -> Tuple[bool, Optional[str]]:
        metrics = cls.matching_metrics(prompt, schema_ctx)
        if not metrics:
            return True, None
        if len(metrics) > 1:
            return False, "The question matches multiple certified metrics; ask the user to clarify the metric."
        metric = metrics[0]
        try:
            statement = sqlglot.parse_one(sql)
        except Exception as exc:
            return False, f"Certified metric validation could not parse SQL: {exc}"

        cte_names = {cte.alias_or_name.lower() for cte in statement.find_all(exp.CTE)}
        referenced = {
            table.name.lower() for table in statement.find_all(exp.Table)
            if table.name.lower() not in cte_names
        }
        if metric.table.lower().split(".")[-1] not in referenced and metric.table.lower() not in referenced:
            return False, f"Certified metric '{metric.name}' must use {metric.table}."

        aggregate_types = {
            "count": exp.Count,
            "count_distinct": exp.Count,
            "sum": exp.Sum,
            "avg": exp.Avg,
            "min": exp.Min,
            "max": exp.Max,
        }
        aggregates = list(statement.find_all(aggregate_types[metric.aggregation]))
        if not aggregates:
            return False, (
                f"Certified metric '{metric.name}' requires {metric.aggregation.upper()} aggregation."
            )
        if metric.aggregation == "count_distinct" and not any(
            aggregate.find(exp.Distinct) is not None for aggregate in aggregates
        ):
            return False, f"Certified metric '{metric.name}' requires COUNT(DISTINCT ...)."
        if metric.column and not any(
            any(column.name.lower() == metric.column.lower() for column in aggregate.find_all(exp.Column))
            for aggregate in aggregates
        ):
            return False, (
                f"Certified metric '{metric.name}' must aggregate column {metric.column}."
            )

        if metric.fallback_columns:
            expected_columns = [c.split('.')[-1].lower() for c in [metric.column, *metric.fallback_columns]]
            if not any([c.name.lower() for c in coalesce.find_all(exp.Column)] == expected_columns
                       for aggregate in aggregates for coalesce in aggregate.find_all(exp.Coalesce)):
                return False, f"Certified metric requires ordered COALESCE fallback columns {expected_columns}."
        if metric.latest_by:
            latest_column = metric.latest_by.split('.')[-1].lower()
            if not any(isinstance(m.this, exp.Column) and m.this.name.lower() == latest_column
                       and m.find_ancestor(exp.Select) is not None
                       and not m.find_ancestor(exp.Select).args.get('where')
                       and not m.find_ancestor(exp.Select).args.get('group')
                       for m in statement.find_all(exp.Max)):
                return False, f"Certified metric requires global MAX({latest_column}) snapshot scope."

        required_filters = list(metric.filters)
        for definition, value in cls._resolve_prompt_filters(prompt, metric):
            required_filters.append(SemanticFilterDefinition(
                table=definition.table, column=definition.column,
                operator=definition.operator, value=value,
            ))
        where = statement.find(exp.Where)
        for required_filter in required_filters:
            scoped_where = where.copy() if where is not None else None
            target = (required_filter.table or metric.table).split('.')[-1].lower()
            if scoped_where is not None:
                for column in scoped_where.find_all(exp.Column):
                    if column.name.lower() != required_filter.column.lower():
                        continue
                    scope = where.find_ancestor(exp.Select)
                    refs = {t.alias_or_name.lower(): t.name.lower() for t in scope.find_all(exp.Table)} if scope else {}
                    if column.table:
                        matches_target = refs.get(column.table.lower()) == target
                    else:
                        owners = {t.name.split('.')[-1].lower() for t in schema_ctx.tables
                                  if t.name.split('.')[-1].lower() in refs.values()
                                  and any(c.name.lower() == column.name.lower() for c in t.columns)}
                        matches_target = owners == {target}
                    if not matches_target:
                        column.set('this', exp.to_identifier('__unrelated_column__'))
            if not cls._required_filter_present(scoped_where, required_filter):
                return False, (
                    f"Certified metric '{metric.name}' requires filter "
                    f"{target}.{required_filter.column} {required_filter.operator} "
                    f"{required_filter.value!r}. Preserve the exact certified predicate."
                )
        return True, None

