import os
import json
import datetime
import logging
import time
import re
import math
import hashlib
import tempfile
from typing import List, Dict, Any, Optional
from sqlalchemy import text
from app.database import db_manager
from app.demo_seed import LEGACY_DEALERSHIP_VIEWS, is_enterprise_demo
from app.config import settings
from app.models import TableMeta, TableColumnMeta, SchemaContext, SuggestedPreset, TableRelationship
from app.schema_retriever import SchemaRetriever
from app.semantic_catalog import SemanticCatalogManager

logger = logging.getLogger(__name__)

SCHEMA_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "schema_metadata.json")
SCHEMA_CACHE_DIRECTORY = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "schema_cache",
)

class SchemaProfiler:
    """
    Introspects connected database to extract comprehensive schema metadata:
    - Tables & Row counts
    - Columns & Data types
    - Primary & Foreign Keys (Relationships)
    - Saves all profiled metadata to schema_metadata.json for instant persistent re-use
    """

    _cached_context: Optional[SchemaContext] = None
    _cached_time: float = 0
    _CACHE_TTL: float = 86400.0  # 24 hours in-memory cache
    _MAX_PROFILE_OBJECTS = 50_000
    _MAX_PROFILE_COLUMNS = 1_000_000
    _MAX_PROFILE_KEYS = 250_000
    _SAFE_SAMPLE_TOKENS = {
        "status", "state", "type", "category", "mode", "priority",
        "condition", "ownership_type", "document_status", "transfer_status",
    }
    _SENSITIVE_TOKENS = {
        "password", "secret", "token", "api_key", "phone", "email",
        "address", "passport", "pinfl", "inn", "tin", "account",
        "signature", "file", "url", "name", "description", "message",
    }

    @classmethod
    def get_schema_file_path(cls) -> str:
        """Legacy/latest schema path kept for backward compatibility."""
        return SCHEMA_FILE_PATH

    @classmethod
    def _scoped_schema_file_path(cls, connection_fingerprint: Optional[str] = None) -> Optional[str]:
        fingerprint = connection_fingerprint or db_manager.connection_fingerprint
        if not fingerprint:
            return None
        return os.path.join(SCHEMA_CACHE_DIRECTORY, f"{fingerprint}.json")

    @classmethod
    def _schema_file_path_for_context(cls, ctx: SchemaContext) -> str:
        return cls._scoped_schema_file_path(ctx.connection_fingerprint) or cls.get_schema_file_path()

    @classmethod
    def _metadata_query(cls, sql: str, limit: int, label: str) -> List[Dict[str, Any]]:
        """Read metadata without ever accepting a silently truncated catalog."""
        rows = db_manager.execute_query(sql, max_rows=limit + 1)
        if len(rows) > limit:
            raise RuntimeError(
                f"{label} metadata exceeds the supported safety limit ({limit:,}); "
                "profiling was stopped instead of saving a partial schema."
            )
        return rows

    @staticmethod
    def _qualified_object_name(
        schema: Optional[str],
        table: str,
        included_schemas: List[str],
    ) -> str:
        if schema and (schema != "public" or len(included_schemas) > 1):
            return f"{schema}.{table}"
        return table

    @classmethod
    def clear_cache(cls, delete_file: bool = True):
        cls._cached_context = None
        cls._cached_time = 0
        paths = [cls._scoped_schema_file_path(), cls.get_schema_file_path()]
        if delete_file:
            for path in dict.fromkeys(item for item in paths if item):
                if not os.path.exists(path):
                    continue
                try:
                    os.remove(path)
                    logger.info(f"Cleared persistent schema file: {path}")
                except Exception as e:
                    logger.warning(f"Could not remove schema file: {e}")

    @classmethod
    def is_safe_enum_column(cls, column_name: str) -> bool:
        name = column_name.lower()
        return (any(token == name or name.endswith(f"_{token}") for token in cls._SAFE_SAMPLE_TOKENS)
                and not any(token in name for token in cls._SENSITIVE_TOKENS))

    @classmethod
    def _sanitize_context(cls, ctx: SchemaContext) -> SchemaContext:
        """Remove row-level and sensitive samples before caching or prompting."""
        cls._normalize_context_metadata(ctx)
        for table in ctx.tables:
            table.sample_rows = []
            for column in table.columns:
                if not cls.is_safe_enum_column(column.name):
                    column.sample_values = []
                else:
                    column.sample_values = [str(v)[:50] for v in column.sample_values[:8]]
        return ctx

    @classmethod
    def _fingerprint_tables(
        cls,
        tables: List[TableMeta],
        relationships: Optional[List[TableRelationship]] = None,
    ) -> str:
        """Fingerprint structural metadata, including declared join contracts."""
        parts = ["schema-fingerprint-v2"]
        for table in sorted(tables, key=lambda item: item.name):
            parts.append(
                f"T:{table.name}:{table.schema_name or ''}:{table.object_type}:"
                f"PK={','.join(table.primary_key or [])}:"
                f"UK={';'.join(','.join(key) for key in sorted(table.candidate_keys or []))}"
            )
            for column in sorted(table.columns, key=lambda item: item.name):
                parts.append(
                    "C:%s:%s:%s:%s:%s:%s:%s:%s"
                    % (
                        column.name,
                        column.data_type,
                        column.nullable,
                        column.is_primary_key,
                        column.is_unique,
                        column.is_foreign_key,
                        column.foreign_table or "",
                        column.foreign_column or "",
                    )
                )
        declared = [
            relationship for relationship in (relationships or [])
            if relationship.source == "foreign_key"
        ]
        for relationship in sorted(
            declared,
            key=lambda item: (
                item.from_table,
                tuple(item.from_columns or [item.from_column]),
                item.to_table,
                tuple(item.to_columns or [item.to_column]),
            ),
        ):
            parts.append(
                "R:%s:%s:%s:%s:%s"
                % (
                    relationship.from_table,
                    ",".join(relationship.from_columns or [relationship.from_column]),
                    relationship.to_table,
                    ",".join(relationship.to_columns or [relationship.to_column]),
                    relationship.constraint_validated,
                )
            )
        return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()

    @classmethod
    def _describe_schema_changes(
        cls,
        previous: Optional[SchemaContext],
        current_tables: List[TableMeta],
    ) -> List[str]:
        if previous is None:
            return []
        old = {table.name: {column.name: column.data_type for column in table.columns} for table in previous.tables}
        new = {table.name: {column.name: column.data_type for column in table.columns} for table in current_tables}
        changes = []
        for name in sorted(new.keys() - old.keys()):
            changes.append(f"table added: {name}")
        for name in sorted(old.keys() - new.keys()):
            changes.append(f"table removed: {name}")
        for table_name in sorted(old.keys() & new.keys()):
            for column in sorted(new[table_name].keys() - old[table_name].keys()):
                changes.append(f"column added: {table_name}.{column}")
            for column in sorted(old[table_name].keys() - new[table_name].keys()):
                changes.append(f"column removed: {table_name}.{column}")
            for column in sorted(old[table_name].keys() & new[table_name].keys()):
                if old[table_name][column] != new[table_name][column]:
                    changes.append(
                        f"column type changed: {table_name}.{column} "
                        f"({old[table_name][column]} -> {new[table_name][column]})"
                    )
        return changes[:200]

    @classmethod
    def _normalize_context_metadata(cls, ctx: SchemaContext) -> None:
        """Populate backward-compatible key metadata without claiming inferred keys as declared."""
        for table in ctx.tables:
            if not table.schema_name and "." in table.name:
                table.schema_name = table.name.rsplit(".", 1)[0]
            declared_pk = [c.name for c in table.columns if c.is_primary_key]
            table.primary_key = list(table.primary_key or declared_pk)
            candidates = [list(k) for k in (table.candidate_keys or []) if k]
            for column in table.columns:
                if column.is_unique and [column.name] not in candidates:
                    candidates.append([column.name])
            # A conventional non-null `id`/`uuid` column is not automatically
            # a key. Only database-declared PK/UNIQUE metadata belongs in
            # candidate_keys; otherwise grain and join cardinality are unknown.
            table.candidate_keys = candidates
            if table.primary_key:
                table.grain_hint = table.grain_hint or f"one row per ({', '.join(table.primary_key)})"
            elif table.candidate_keys:
                table.grain_hint = table.grain_hint or f"candidate grain ({', '.join(table.candidate_keys[0])})"

            # Soft-delete behavior is derived from this database's own schema,
            # never from a hardcoded customer table list.
            columns_by_name = {column.name.lower(): column for column in table.columns}
            if not table.soft_delete_column:
                if "deleted_at" in columns_by_name:
                    table.soft_delete_column = columns_by_name["deleted_at"].name
                    table.soft_delete_mode = "is_null"
                else:
                    for candidate in ("is_deleted", "deleted"):
                        column = columns_by_name.get(candidate)
                        if not column:
                            continue
                        type_name = column.data_type.lower()
                        if any(token in type_name for token in ("bool", "bit")):
                            table.soft_delete_column = column.name
                            table.soft_delete_mode = "not_true"
                            break
                        if any(token in type_name for token in (
                            "smallint", "integer", "bigint", "numeric", "decimal", "number",
                        )):
                            table.soft_delete_column = column.name
                            table.soft_delete_mode = "zero_or_null"
                            break
                        # Text/status columns can use arbitrary values such as
                        # ACTIVE/DELETED. Without reviewed semantics, guessing
                        # their active value would silently corrupt results.

        for relationship in ctx.relationships:
            if relationship.source == "foreign_key":
                relationship.confidence = 1.0
                relationship.verified = bool(
                    relationship.verified and relationship.constraint_validated
                )
            elif relationship.source == "semantic_catalog":
                relationship.confidence = 1.0
                relationship.verified = True
            else:
                relationship.source = relationship.source or "inferred"
                relationship.confidence = min(float(relationship.confidence or 0.5), 0.7)
        if not ctx.schema_fingerprint:
            ctx.schema_fingerprint = cls._fingerprint_tables(ctx.tables, ctx.relationships)
            ctx.fingerprint_version = 2
        ctx.source_object_count = ctx.source_object_count or len(ctx.tables)
        ctx.profiled_column_count = ctx.profiled_column_count or sum(
            len(table.columns) for table in ctx.tables
        )
        ctx.declared_relationship_count = ctx.declared_relationship_count or sum(
            1 for relationship in ctx.relationships if relationship.source == "foreign_key"
        )
        ctx.inferred_relationship_count = ctx.inferred_relationship_count or sum(
            1 for relationship in ctx.relationships
            if relationship.source not in {"foreign_key", "semantic_catalog"}
        )

    @classmethod
    def _configure_soft_delete_rules(cls, ctx: SchemaContext) -> None:
        """Activate rules only after metadata is matched to the live connection."""
        rules: Dict[str, Dict[str, str]] = {}
        base_name_counts: Dict[str, int] = {}
        for table in ctx.tables:
            base = table.name.split(".")[-1].lower()
            base_name_counts[base] = base_name_counts.get(base, 0) + 1
        for table in ctx.tables:
            if not table.soft_delete_column:
                continue
            rule = {
                "column": table.soft_delete_column,
                "mode": table.soft_delete_mode or "not_true",
            }
            rules[table.name.lower()] = rule
            base = table.name.split(".")[-1].lower()
            if base_name_counts.get(base) == 1:
                rules[base] = rule
        db_manager.configure_soft_delete_rules(rules)

    @classmethod
    def apply_runtime_metadata(cls, ctx: SchemaContext) -> SchemaContext:
        """Apply database-scoped semantic metadata and runtime safety rules."""
        cls._normalize_context_metadata(ctx)
        SemanticCatalogManager.apply_to_context(ctx)
        ctx.schema_prompt = cls._build_prompt_representation(ctx.database_type, ctx.tables)
        cls._configure_soft_delete_rules(ctx)
        return ctx

    @classmethod
    def _matches_current_database(cls, ctx: SchemaContext) -> bool:
        if not db_manager.is_connected:
            return False
        if is_enterprise_demo(db_manager._engine) and any(
            table.name in LEGACY_DEALERSHIP_VIEWS and table.object_type == "view" for table in ctx.tables
        ):
            return False
        return (
            ctx.database_name == db_manager.current_db_name
            and (ctx.included_schemas or "public") == (db_manager.include_schemas or "public")
            and bool(ctx.connection_fingerprint)
            and ctx.connection_fingerprint == db_manager.connection_fingerprint
        )

    @classmethod
    def _is_persisted_cache_fresh(cls, ctx: SchemaContext) -> bool:
        if ctx.fingerprint_version < 2:
            return False
        if not ctx.saved_at:
            return False
        try:
            saved_at = datetime.datetime.fromisoformat(ctx.saved_at)
            age = (datetime.datetime.now(saved_at.tzinfo) - saved_at).total_seconds()
            return 0 <= age < settings.SCHEMA_CACHE_TTL_SECONDS
        except (TypeError, ValueError):
            return False

    @classmethod
    def enrich_safe_enum_samples(
        cls,
        table: TableMeta,
        max_sample_values: int = 8,
    ) -> TableMeta:
        """Load bounded DISTINCT values only for low-risk enum-like columns."""
        if not db_manager.is_connected:
            return table
        safe_cols = [
            column for column in table.columns
            if cls.is_safe_enum_column(column.name)
        ][:8]
        if not safe_cols:
            return table

        quoted_table = db_manager.quote_identifier(table.name)
        branches = []
        for index, column in enumerate(safe_cols):
            quoted_column = db_manager.quote_identifier(column.name)
            literal_name = column.name.replace("'", "''")
            branches.append(
                "SELECT '%s' AS __column, __value FROM ("
                "SELECT DISTINCT CAST(sample_value AS TEXT) AS __value FROM ("
                "SELECT %s AS sample_value FROM %s WHERE %s IS NOT NULL LIMIT 2000"
                ") bounded_values ORDER BY 1 LIMIT %s"
                ") enum_%s"
                % (
                    literal_name,
                    quoted_column,
                    quoted_table,
                    quoted_column,
                    int(max_sample_values),
                    index,
                )
            )
        rows = db_manager.execute_query(" UNION ALL ".join(branches), max_rows=64)
        values_by_column: Dict[str, List[str]] = {column.name: [] for column in safe_cols}
        for row in rows:
            column_name = row.get("__column")
            value = row.get("__value")
            if column_name in values_by_column and value not in (None, ""):
                rendered = str(value)[:50]
                if rendered not in values_by_column[column_name]:
                    values_by_column[column_name].append(rendered)
        for column in safe_cols:
            column.sample_values = values_by_column[column.name][:max_sample_values]
        return table

    @classmethod
    def save_to_file(cls, ctx: SchemaContext) -> bool:
        """Persists analyzed schema, columns, and foreign key relationships to schema_metadata.json."""
        temp_path = None
        try:
            path = cls._schema_file_path_for_context(ctx)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            ctx = cls._sanitize_context(ctx)
            data = ctx.model_dump()
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=os.path.dirname(path),
                prefix=".schema-metadata-",
                suffix=".tmp",
                delete=False,
            ) as file:
                temp_path = file.name
                json.dump(data, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temp_path, path)
            temp_path = None
            logger.info(f"Schema metadata successfully saved to: {path}")
            return True
        except Exception as e:
            logger.error(f"Failed to save schema metadata to file: {e}")
            return False
        finally:
            if temp_path and os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    @classmethod
    def load_from_file(cls) -> Optional[SchemaContext]:
        """Loads analyzed schema directly from schema_metadata.json."""
        scoped_path = cls._scoped_schema_file_path()
        cached_paths: List[str] = []
        if scoped_path:
            cached_paths.append(scoped_path)
        elif os.path.isdir(SCHEMA_CACHE_DIRECTORY):
            cached_paths.extend(sorted(
                (
                    os.path.join(SCHEMA_CACHE_DIRECTORY, name)
                    for name in os.listdir(SCHEMA_CACHE_DIRECTORY)
                    if name.endswith(".json")
                ),
                key=os.path.getmtime,
                reverse=True,
            ))
        paths = [*cached_paths, cls.get_schema_file_path()]
        for path in dict.fromkeys(item for item in paths if item):
            if not os.path.exists(path):
                continue
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                ctx = cls._sanitize_context(SchemaContext(**data))
                logger.info(f"Loaded schema metadata directly from file: {path} ({len(ctx.tables)} tables, {len(ctx.relationships)} relationships)")
                return ctx
            except Exception as e:
                logger.warning(f"Could not load schema metadata from file: {e}")
        return None

    @classmethod
    def profile(cls, max_sample_values: int = 4, force_refresh: bool = False) -> SchemaContext:
        now = time.time()
        previous_context = None
        if force_refresh and db_manager.is_connected:
            saved_before_refresh = cls.load_from_file()
            if saved_before_refresh and cls._matches_current_database(saved_before_refresh):
                previous_context = saved_before_refresh
        if (
            not force_refresh
            and cls._cached_context is not None
            and (now - cls._cached_time) < cls._CACHE_TTL
            and cls._matches_current_database(cls._cached_context)
        ):
            return cls.apply_runtime_metadata(cls._cached_context)

        # Check if saved schema file exists and can be loaded instantly
        if not force_refresh and db_manager.is_connected:
            saved_ctx = cls.load_from_file()
            if (
                saved_ctx
                and saved_ctx.tables
                and cls._matches_current_database(saved_ctx)
                and cls._is_persisted_cache_fresh(saved_ctx)
            ):
                cls._cached_context = saved_ctx
                cls._cached_time = now
                return cls.apply_runtime_metadata(saved_ctx)

        if not db_manager.is_connected:
            return SchemaContext(
                database_type="none",
                database_name=None,
                included_schemas=db_manager.include_schemas,
                total_tables=0,
                tables=[],
                schema_prompt="Hech qanday ma'lumotlar bazasi ulanmagan. Iltimos, ma'lumotlar bazangizga ulaning."
            )

        inspector = db_manager.get_inspector()
        if inspector is None:
            return SchemaContext(
                database_type="none",
                total_tables=0,
                tables=[],
                schema_prompt="Hech qanday ma'lumotlar bazasi ulanmagan. Iltimos, ma'lumotlar bazangizga ulaning."
            )

        dialect = db_manager.dialect_name
        target_schemas = db_manager.get_schemas_list() if dialect == "postgresql" else [None]

        tables_meta: List[TableMeta] = []
        declared_relationships: List[TableRelationship] = []
        profile_warnings: List[str] = []
        source_object_count = 0

        # -------------------------------------------------------------
        # 1. High-Performance Batch Profiling for PostgreSQL
        # -------------------------------------------------------------
        if dialect == "postgresql":
            try:
                schemas_clause = ", ".join(f"'{s}'" for s in target_schemas)
                
                # A. Batch get tables, row counts, and permissions in ONE query
                priv_sql = f"""
                SELECT c.relname AS tbl, n.nspname AS sch, c.relkind,
                       obj_description(c.oid, 'pg_class') AS table_comment,
                       COALESCE(s.n_live_tup, GREATEST(0, c.reltuples::bigint)) AS est_rows,
                       has_table_privilege(c.oid, 'SELECT') AS can_select
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                LEFT JOIN pg_stat_user_tables s ON s.relid = c.oid
                WHERE c.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND n.nspname IN ({schemas_clause})
                ORDER BY est_rows DESC;
                """
                priv_rows = cls._metadata_query(
                    priv_sql,
                    cls._MAX_PROFILE_OBJECTS,
                    "database objects",
                )
                
                valid_tables = {}
                table_object_types = {}
                table_comments = {}
                for pr in priv_rows:
                    tbl = pr.get("tbl")
                    sch = pr.get("sch")
                    if tbl.startswith("pg_") or tbl.startswith("sql_"):
                        continue
                    if pr.get("can_select"):
                        valid_tables[(sch, tbl)] = int(pr.get("est_rows", 0))
                        table_object_types[(sch, tbl)] = {
                            "r": "table", "p": "partitioned_table", "v": "view",
                            "m": "materialized_view", "f": "foreign_table",
                        }.get(pr.get("relkind"), "table")
                        table_comments[(sch, tbl)] = pr.get("table_comment")

                # For tables where statistics say 0 (e.g. unanalyzed tables), check if they actually have rows
                for (sch, tbl), est in list(valid_tables.items()):
                    if est <= 0 and len(valid_tables) <= 50:
                        try:
                            chk = db_manager.execute_query(f'SELECT 1 FROM "{sch}"."{tbl}" LIMIT 1;', max_rows=1)
                            if chk and len(chk) > 0:
                                cnt_res = db_manager.execute_query(f'SELECT COUNT(*) AS cnt FROM "{sch}"."{tbl}";', max_rows=1)
                                valid_tables[(sch, tbl)] = int(cnt_res[0].get("cnt", 1)) if cnt_res else 1
                        except Exception:
                            pass

                logger.info(f"PostgreSQL batch identified {len(valid_tables)} readable tables and views.")
                source_object_count = len(valid_tables)

                # B. Batch get ALL columns for ALL tables in ONE single query!
                cols_sql = f"""
                SELECT n.nspname AS table_schema, c.relname AS table_name,
                       a.attname AS column_name,
                       format_type(a.atttypid, a.atttypmod) AS data_type,
                       CASE WHEN a.attnotnull THEN 'NO' ELSE 'YES' END AS is_nullable,
                       col_description(c.oid, a.attnum) AS column_comment
                FROM pg_attribute a
                JOIN pg_class c ON c.oid = a.attrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname IN ({schemas_clause})
                  AND c.relkind IN ('r', 'p', 'v', 'm', 'f')
                  AND a.attnum > 0 AND NOT a.attisdropped
                ORDER BY n.nspname, c.relname, a.attnum;
                """
                cols_rows = cls._metadata_query(
                    cols_sql,
                    cls._MAX_PROFILE_COLUMNS,
                    "columns",
                )
                table_columns = {}
                for cr in cols_rows:
                    key = (cr.get("table_schema"), cr.get("table_name"))
                    if key not in table_columns:
                        table_columns[key] = []
                    table_columns[key].append(cr)

                # C. Read primary and unique keys directly from pg_index. This is
                # more reliable for restricted read-only roles than relying on
                # information_schema constraint visibility.
                pks_sql = f"""
                SELECT n.nspname AS table_schema, c.relname AS table_name,
                       array_agg(a.attname ORDER BY k.ordinality) AS columns
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
                WHERE i.indisprimary AND i.indisvalid
                  AND n.nspname IN ({schemas_clause})
                GROUP BY n.nspname, c.relname;
                """
                pks_rows = cls._metadata_query(
                    pks_sql,
                    cls._MAX_PROFILE_KEYS,
                    "primary keys",
                )
                table_pks = {}
                for pkr in pks_rows:
                    key = (pkr.get("table_schema"), pkr.get("table_name"))
                    table_pks[key] = set(pkr.get("columns") or [])

                unique_sql = f"""
                SELECT n.nspname AS table_schema, c.relname AS table_name,
                       ci.relname AS index_name,
                       array_agg(a.attname ORDER BY k.ordinality) AS columns
                FROM pg_index i
                JOIN pg_class c ON c.oid = i.indrelid
                JOIN pg_namespace n ON n.oid = c.relnamespace
                JOIN pg_class ci ON ci.oid = i.indexrelid
                CROSS JOIN LATERAL unnest(i.indkey) WITH ORDINALITY AS k(attnum, ordinality)
                JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = k.attnum
                WHERE i.indisunique AND i.indisvalid AND NOT i.indisprimary
                  AND i.indpred IS NULL AND i.indexprs IS NULL
                  AND n.nspname IN ({schemas_clause})
                GROUP BY n.nspname, c.relname, ci.relname;
                """
                unique_rows = cls._metadata_query(
                    unique_sql,
                    cls._MAX_PROFILE_KEYS,
                    "unique keys",
                )
                table_uniques = {}
                for ur in unique_rows:
                    key = (ur.get("table_schema"), ur.get("table_name"))
                    columns = list(ur.get("columns") or [])
                    if columns:
                        table_uniques.setdefault(key, []).append(columns)

                # D. Read declared foreign keys with column ordinality preserved.
                fks_sql = f"""
                SELECT con.conname AS constraint_name, con.convalidated AS is_validated,
                       ns.nspname AS table_schema, src.relname AS table_name,
                       sa.attname AS column_name, nt.nspname AS foreign_schema,
                       tgt.relname AS foreign_table, ta.attname AS foreign_column
                FROM pg_constraint con
                JOIN pg_class src ON src.oid = con.conrelid
                JOIN pg_namespace ns ON ns.oid = src.relnamespace
                JOIN pg_class tgt ON tgt.oid = con.confrelid
                JOIN pg_namespace nt ON nt.oid = tgt.relnamespace
                CROSS JOIN LATERAL unnest(con.conkey, con.confkey)
                  WITH ORDINALITY AS k(src_attnum, tgt_attnum, ordinality)
                JOIN pg_attribute sa ON sa.attrelid = src.oid AND sa.attnum = k.src_attnum
                JOIN pg_attribute ta ON ta.attrelid = tgt.oid AND ta.attnum = k.tgt_attnum
                WHERE con.contype = 'f' AND ns.nspname IN ({schemas_clause})
                ORDER BY ns.nspname, src.relname, con.conname, k.ordinality;
                """
                fks_rows = cls._metadata_query(
                    fks_sql,
                    cls._MAX_PROFILE_KEYS,
                    "foreign keys",
                )
                table_fks = {}
                grouped_fks = {}
                for fkr in fks_rows:
                    key = (fkr.get("table_schema"), fkr.get("table_name"))
                    if key not in table_fks:
                        table_fks[key] = {}
                    table_fks[key][fkr.get("column_name")] = {
                        "foreign_schema": fkr.get("foreign_schema"),
                        "foreign_table": fkr.get("foreign_table"),
                        "foreign_column": fkr.get("foreign_column"),
                        "is_validated": bool(fkr.get("is_validated", True)),
                    }
                    group_key = (
                        fkr.get("table_schema"), fkr.get("table_name"),
                        fkr.get("constraint_name"), fkr.get("foreign_schema"),
                        fkr.get("foreign_table"),
                    )
                    grouped = grouped_fks.setdefault(
                        group_key,
                        {"from": [], "to": [], "is_validated": True},
                    )
                    grouped["from"].append(fkr.get("column_name"))
                    grouped["to"].append(fkr.get("foreign_column"))
                    grouped["is_validated"] = (
                        grouped["is_validated"] and bool(fkr.get("is_validated", True))
                    )

                for (src_schema, src_table, constraint, dst_schema, dst_table), columns in grouped_fks.items():
                    source_name = cls._qualified_object_name(src_schema, src_table, target_schemas)
                    target_name = cls._qualified_object_name(dst_schema, dst_table, target_schemas)
                    pairs = " AND ".join(
                        f"{source_name}.{left} = {target_name}.{right}"
                        for left, right in zip(columns["from"], columns["to"])
                    )
                    declared_relationships.append(TableRelationship(
                        from_table=source_name,
                        from_column=columns["from"][0],
                        to_table=target_name,
                        to_column=columns["to"][0],
                        from_columns=columns["from"],
                        to_columns=columns["to"],
                        constraint_name=constraint,
                        join_hint=f"LEFT JOIN {target_name} ON {pairs}",
                        source="foreign_key",
                        confidence=1.0,
                        verified=bool(columns["is_validated"]),
                        cardinality="many-to-one",
                        constraint_validated=bool(columns["is_validated"]),
                    ))

                # Construct TableMeta objects in-memory in milliseconds
                for (schema, tbl), row_count in valid_tables.items():
                    display_name = cls._qualified_object_name(schema, tbl, target_schemas)
                    col_defs = table_columns.get((schema, tbl), [])
                    pk_set = table_pks.get((schema, tbl), set())
                    unique_sets = table_uniques.get((schema, tbl), [])
                    fk_map = table_fks.get((schema, tbl), {})

                    columns_meta: List[TableColumnMeta] = []
                    for c in col_defs:
                        col_name = c.get("column_name")
                        col_type = str(c.get("data_type", "varchar"))
                        is_pk = col_name in pk_set
                        is_unique = any(keys == [col_name] for keys in unique_sets)
                        fk_info = fk_map.get(col_name)
                        foreign_name = (
                            cls._qualified_object_name(
                                fk_info.get("foreign_schema"),
                                fk_info["foreign_table"],
                                target_schemas,
                            )
                            if fk_info else None
                        )

                        columns_meta.append(
                            TableColumnMeta(
                                name=col_name,
                                data_type=col_type,
                                comment=c.get("column_comment"),
                                is_primary_key=is_pk,
                                is_unique=is_unique,
                                is_foreign_key=bool(fk_info),
                                foreign_table=foreign_name,
                                foreign_column=fk_info["foreign_column"] if fk_info else None,
                                nullable=(c.get("is_nullable") == "YES"),
                                sample_values=[]
                            )
                        )

                    tables_meta.append(
                        TableMeta(
                            name=display_name,
                            row_count=row_count,
                            schema_name=schema,
                            object_type=table_object_types.get((schema, tbl), "table"),
                            comment=table_comments.get((schema, tbl)),
                            columns=columns_meta,
                            sample_rows=[],
                            primary_key=sorted(pk_set),
                            candidate_keys=unique_sets,
                            grain_hint=(
                                f"one row per ({', '.join(sorted(pk_set))})"
                                if pk_set else None
                            ),
                        )
                    )

                # E. Fetch bounded DISTINCT values for low-risk enum columns.
                # Business tables are prioritized over large logs/progress tables.
                active_tables = list(tables_meta)
                active_tables.sort(key=lambda t: (
                    cls._is_log_table(t.name),
                    not bool(t.comment),
                    -(t.row_count or 0),
                    t.name,
                ))
                sampled_tables = 0
                for t in active_tables:
                    if sampled_tables >= 40:
                        break
                    try:
                        before = sum(bool(column.sample_values) for column in t.columns)
                        cls.enrich_safe_enum_samples(t, max_sample_values=max_sample_values)
                        after = sum(bool(column.sample_values) for column in t.columns)
                        if after > before:
                            sampled_tables += 1
                    except Exception as e:
                        logger.debug(f"Could not fetch sample rows for {t.name}: {e}")

            except Exception as batch_err:
                logger.warning(f"Batch PostgreSQL profiling failed: {batch_err}. Falling back to standard inspector...")
                profile_warnings.append(f"PostgreSQL batch profiling failed: {batch_err}")
                tables_meta = []
                declared_relationships = []

        # -------------------------------------------------------------
        # 2. Fallback Standard Inspector (for SQLite or if batch failed)
        # -------------------------------------------------------------
        if not tables_meta:
            schema_tables = []
            fallback_object_types = {}
            for s in target_schemas:
                try:
                    tbls = inspector.get_table_names(schema=s) if s else inspector.get_table_names()
                    for tbl in tbls:
                        if tbl.startswith("pg_") or tbl.startswith("sqlite_"):
                            continue
                        schema_tables.append((s, tbl))
                        fallback_object_types[(s, tbl)] = "table"
                    views = inspector.get_view_names(schema=s) if s else inspector.get_view_names()
                    if is_enterprise_demo(db_manager._engine):
                        views = [view for view in views if view not in LEGACY_DEALERSHIP_VIEWS]
                    for view in views:
                        if view.startswith("pg_") or view.startswith("sqlite_"):
                            continue
                        if (s, view) not in schema_tables:
                            schema_tables.append((s, view))
                        fallback_object_types[(s, view)] = "view"
                except Exception as e:
                    logger.warning(f"Could not inspect schema '{s}': {e}")
                    profile_warnings.append(f"Could not enumerate schema {s or '<default>'}: {e}")

            source_object_count = len(schema_tables)

            # Inspect every object so 500-1000 table databases are not silently
            # truncated. Exact row counts are sampled only for small schemas.
            for schema, tbl in schema_tables:
                display_name = cls._qualified_object_name(schema, tbl, target_schemas)
                query_name = f'"{schema}"."{tbl}"' if schema else f'"{tbl}"'
                try:
                    row_count = 0
                    try:
                        if len(schema_tables) <= 50:
                            count_res = db_manager.execute_query(f"SELECT COUNT(*) AS cnt FROM {query_name};")
                            if count_res:
                                row_count = count_res[0].get("cnt", 0)
                    except Exception:
                        pass

                    try:
                        pk_constraint = inspector.get_pk_constraint(tbl, schema=schema) if schema else inspector.get_pk_constraint(tbl)
                    except (NotImplementedError, TypeError):
                        pk_constraint = {}
                    pk_cols = set(pk_constraint.get("constrained_columns", [])) if pk_constraint else set()
                    unique_sets: List[List[str]] = []
                    try:
                        unique_constraints = (
                            inspector.get_unique_constraints(tbl, schema=schema)
                            if schema else inspector.get_unique_constraints(tbl)
                        )
                    except (NotImplementedError, TypeError, AttributeError):
                        unique_constraints = []
                    for constraint in unique_constraints or []:
                        columns = list(constraint.get("column_names") or [])
                        if columns and columns not in unique_sets:
                            unique_sets.append(columns)
                    try:
                        indexes = (
                            inspector.get_indexes(tbl, schema=schema)
                            if schema else inspector.get_indexes(tbl)
                        )
                    except (NotImplementedError, TypeError, AttributeError):
                        indexes = []
                    for index in indexes or []:
                        columns = list(index.get("column_names") or [])
                        if index.get("unique") and columns and columns not in unique_sets:
                            unique_sets.append(columns)
                    try:
                        fks = inspector.get_foreign_keys(tbl, schema=schema) if schema else inspector.get_foreign_keys(tbl)
                    except (NotImplementedError, TypeError):
                        fks = []
                    fk_map = {}
                    for fk in fks:
                        constrained_columns = list(fk.get("constrained_columns", []))
                        referred_columns = list(fk.get("referred_columns", []))
                        referred_schema = fk.get("referred_schema")
                        referred_table = fk.get("referred_table")
                        target_name = (
                            f"{referred_schema}.{referred_table}"
                            if referred_schema and (referred_schema != "public" or len(target_schemas) > 1)
                            else referred_table
                        )
                        for constrained, referred in zip(constrained_columns, referred_columns):
                            fk_map[constrained] = {
                                "foreign_table": target_name,
                                "foreign_column": referred
                            }
                        if constrained_columns and referred_columns and target_name:
                            pairs = " AND ".join(
                                f"{display_name}.{left} = {target_name}.{right}"
                                for left, right in zip(constrained_columns, referred_columns)
                            )
                            declared_relationships.append(TableRelationship(
                                from_table=display_name,
                                from_column=constrained_columns[0],
                                to_table=target_name,
                                to_column=referred_columns[0],
                                from_columns=constrained_columns,
                                to_columns=referred_columns,
                                constraint_name=fk.get("name"),
                                join_hint=f"LEFT JOIN {target_name} ON {pairs}",
                                source="foreign_key",
                                confidence=1.0,
                                verified=True,
                                cardinality="many-to-one",
                            ))

                    columns = inspector.get_columns(tbl, schema=schema) if schema else inspector.get_columns(tbl)
                    columns_meta = []
                    for col in columns:
                        col_name = col["name"]
                        col_type = str(col["type"])
                        fk_info = fk_map.get(col_name)
                        columns_meta.append(
                            TableColumnMeta(
                                name=col_name,
                                data_type=col_type,
                                comment=col.get("comment"),
                                is_primary_key=(col_name in pk_cols),
                                is_unique=any(keys == [col_name] for keys in unique_sets),
                                is_foreign_key=bool(fk_info),
                                foreign_table=fk_info["foreign_table"] if fk_info else None,
                                foreign_column=fk_info["foreign_column"] if fk_info else None,
                                nullable=col.get("nullable", True),
                                sample_values=[]
                            )
                        )

                    sample_rows = []
                    table_comment = None
                    if fallback_object_types.get((schema, tbl), "table") == "table":
                        try:
                            comment_info = (
                                inspector.get_table_comment(tbl, schema=schema)
                                if schema else inspector.get_table_comment(tbl)
                            )
                            table_comment = (comment_info or {}).get("text")
                        except (NotImplementedError, AttributeError):
                            pass

                    tables_meta.append(
                        TableMeta(
                            name=display_name,
                            row_count=row_count,
                            schema_name=schema,
                            object_type=fallback_object_types.get((schema, tbl), "table"),
                            comment=table_comment,
                            columns=columns_meta,
                            sample_rows=sample_rows,
                            primary_key=sorted(pk_cols),
                            candidate_keys=unique_sets,
                            grain_hint=(
                                f"one row per ({', '.join(sorted(pk_cols))})"
                                if pk_cols else None
                            ),
                        )
                    )
                except Exception as e:
                    logger.error(f"Error profiling table {display_name}: {e}")
                    profile_warnings.append(f"Could not profile object {display_name}: {e}")

        # Build clean concise prompt representation for Gemma
        prompt_repr = cls._build_prompt_representation(dialect, tables_meta)
        dynamic_presets = cls.generate_dynamic_presets(tables_meta)

        # 1. Direct DDL Foreign Keys (if present)
        relationships: List[TableRelationship] = list(declared_relationships)
        seen_rels = {
            (relationship.from_table, left, relationship.to_table, right)
            for relationship in declared_relationships
            for left, right in zip(
                relationship.from_columns or [relationship.from_column],
                relationship.to_columns or [relationship.to_column],
            )
        }
        for t in tables_meta:
            for c in t.columns:
                if c.is_foreign_key and c.foreign_table and c.foreign_column:
                    hint = f"LEFT JOIN {c.foreign_table} ON {t.name}.{c.name} = {c.foreign_table}.{c.foreign_column}"
                    key = (t.name, c.name, c.foreign_table, c.foreign_column)
                    if key not in seen_rels:
                        seen_rels.add(key)
                        relationships.append(
                            TableRelationship(
                                from_table=t.name,
                                from_column=c.name,
                                to_table=c.foreign_table,
                                to_column=c.foreign_column,
                                join_hint=hint,
                                source="foreign_key",
                                confidence=1.0,
                                verified=True,
                                cardinality="many-to-one",
                            )
                        )

        # 2. Inferred implicit relationships based on column naming conventions (<entity>_id -> <entity>.id)
        inferred = cls.infer_relationships(tables_meta)
        for r in inferred:
            key = (r.from_table, r.from_column, r.to_table, r.to_column)
            if key not in seen_rels:
                seen_rels.add(key)
                relationships.append(r)

        logger.info(f"Discovered total {len(relationships)} relationships ({len(inferred)} inferred from naming conventions).")

        if source_object_count != len(tables_meta):
            profile_warnings.append(
                f"Profiled {len(tables_meta)} of {source_object_count} discovered readable objects."
            )
        columnless = [table.name for table in tables_meta if not table.columns]
        if columnless:
            preview = ", ".join(columnless[:10])
            profile_warnings.append(
                f"{len(columnless)} readable objects have no visible columns: {preview}"
            )
        profiled_column_count = sum(len(table.columns) for table in tables_meta)
        declared_relationship_count = sum(
            1 for relationship in relationships if relationship.source == "foreign_key"
        )
        inferred_relationship_count = sum(
            1 for relationship in relationships
            if relationship.source not in {"foreign_key", "semantic_catalog"}
        )

        ctx = SchemaContext(
            database_type=dialect,
            database_name=db_manager.current_db_name,
            included_schemas=db_manager.include_schemas,
            total_tables=len(tables_meta),
            tables=tables_meta,
            schema_prompt=prompt_repr,
            suggested_presets=dynamic_presets,
            relationships=relationships,
            saved_at=datetime.datetime.now().isoformat(),
            schema_fingerprint=cls._fingerprint_tables(tables_meta, relationships),
            connection_fingerprint=db_manager.connection_fingerprint,
            schema_changes=cls._describe_schema_changes(previous_context, tables_meta),
            profile_complete=not profile_warnings,
            profile_warnings=profile_warnings[:100],
            source_object_count=source_object_count,
            profiled_column_count=profiled_column_count,
            declared_relationship_count=declared_relationship_count,
            inferred_relationship_count=inferred_relationship_count,
            fingerprint_version=2,
        )
        cls.apply_runtime_metadata(ctx)
        cls._cached_context = ctx
        cls._cached_time = time.time()
        cls.save_to_file(ctx)
        return ctx

    @classmethod
    def _get_table_business_concept(cls, name: str, columns: list = None) -> str:
        """Returns clear Uzbek and Russian business meaning for English/technical table names."""
        n = name.lower()
        col_text = " ".join(c.name.lower() for c in columns) if columns else ""
        concepts = []
        if any(k in n or k in col_text for k in ["faktura", "invoice", "bill", "document", "hujjat"]):
            concepts.append("Hisob-fakturalar / Счета-фактуры / To'lov hujjatlari")
        if any(k in n or k in col_text for k in ["trip", "reys", "qatnov", "route"]):
            concepts.append("Reyslar / Qatnovlar / Поездки")
        if any(k in n or k in col_text for k in ["waybill", "varaq"]):
            concepts.append("Yo'l varaqlari / Yoqilg'i sarfi / Путевые листы")
        if any(k in n or k in col_text for k in ["vehicle", "transport", "mashina", "avto", "car"]):
            concepts.append("Avtomashinalar / Texnika parki / Транспорт")
        if any(k in n or k in col_text for k in ["driver", "haydovchi", "shofyor"]):
            concepts.append("Haydovchilar tarkibi / Водители")
        if any(k in n or k in col_text for k in ["fuel", "benzin", "yoqilgi", "gaz", "metan"]):
            concepts.append("Yoqilg'i ta'minoti / Sarf / Топливо")
        if any(k in n or k in col_text for k in ["org", "company", "zavod", "tashkilot", "factory"]):
            concepts.append("Zavodlar / Tashkilotlar / Korxonalar / Организации")
        if any(k in n or k in col_text for k in ["order", "buyurtma", "contract", "shartnoma"]):
            concepts.append("Buyurtmalar / Shartnomalar / Заказы")
        if any(k in n or k in col_text for k in ["product", "tovar", "mahsulot", "item"]):
            concepts.append("Mahsulotlar / Tovarlar / Продукция")
        if any(k in n or k in col_text for k in ["warehouse", "ombor", "sklad", "stock"]):
            concepts.append("Omborlar / Omborxona / Склад")
        if any(k in n or k in col_text for k in ["tax", "soliq", "debt", "nedoimka", "penya", "pereplata", "qarzdorlik"]):
            concepts.append("Soliqlar / Soliq qarzdorligi / Nedoimka / Penya / Налоги / Налоговая задолженность")
        if any(k in n or k in col_text for k in ["payment", "tolov", "pay", "kassa", "moliya"]):
            concepts.append("To'lovlar / Moliya / Платежи")
        if any(k in n or k in col_text for k in ["user", "xodim", "ishchi", "employee"]):
            concepts.append("Xodimlar / Foydalanuvchilar / Сотрудники")
        if any(k in n or k in col_text for k in ["region", "district", "viloyat", "tuman", "hudud"]):
            concepts.append("Viloyatlar / Tumanlar / Hududlar / Qashqadaryo / Samarqand / Toshkent / Farg'ona / Andijon / Namangan / Buxoro / Navoiy / Surxondaryo / Jizzax / Sirdaryo / Xorazm / Qoraqalpog'iston / Регионы / Области / Районы")
        if any(k in n for k in ["daily_amount", "warehouse_amount", "stock_income"]):
            concepts.append("Ombor qoldiqlari / Qoldiq / Zaxira / Kunlik qoldiq / Tovarlar qoldig'i / Mahsulot zaxirasi / Остатки")
        if n.startswith("finmodel_"):
            concepts.append("Moliya modeli / Moliyaviy reja / Finmodel")
        return f" [{ ' | '.join(concepts) }]" if concepts else ""

    @classmethod
    def _build_prompt_representation(cls, dialect: str, tables: List[TableMeta]) -> str:
        # Row estimates are often zero for views and stale PostgreSQL stats, so
        # never hide an object based on row_count. Prefer documented business
        # objects and demote technical logs.
        sorted_tables = sorted(
            tables,
            key=lambda table: (
                table.certified_source,
                not cls._is_log_table(table.name),
                bool(table.business_description or table.comment),
                table.object_type in {"view", "materialized_view"},
                bool(table.primary_key or table.candidate_keys),
                table.row_count or 0,
                table.name,
            ),
            reverse=True,
        )

        lines = [
            f"### DATABASE DIALECT: {dialect.upper()}",
            "### SEMANTIK KO'RSATMA: Jadvallar inglizcha bo'lsa ham, ular quyidagi o'zbekcha/ruscha biznes sohalariga mos keladi:",
            f"### PROFILED DATABASE OBJECTS (Total {len(sorted_tables)} tables/views):"
        ]
        
        # Bounded detail keeps dashboard prompts usable while query-time
        # retrieval still searches the complete in-memory catalog.
        top_tables = sorted_tables[:30]
        for t in top_tables:
            concept = cls._get_table_business_concept(t.name)
            semantic_description = t.business_description or t.comment
            semantic_bits = []
            if t.certified_source:
                semantic_bits.append("CERTIFIED SOURCE")
            if t.business_role:
                semantic_bits.append(f"role={t.business_role}")
            if t.semantic_aliases:
                semantic_bits.append("aliases=" + ", ".join(t.semantic_aliases[:6]))
            col_strs = []
            for c in t.columns[:30]:
                extras = []
                if c.is_primary_key:
                    extras.append("PK")
                if c.is_foreign_key and c.foreign_table:
                    extras.append(f"FK->{c.foreign_table}.{c.foreign_column}")
                if c.sample_values and len(c.sample_values) <= 3:
                    samples = ", ".join(f"'{v}'" for v in c.sample_values[:3])
                    extras.append(f"samples: [{samples}]")
                if c.name in t.measure_columns:
                    extras.append("MEASURE")
                if c.name in t.dimension_columns:
                    extras.append("DIMENSION")
                ext = f" [{', '.join(extras)}]" if extras else ""
                col_strs.append(f"{c.name} ({c.data_type}){ext}")
            
            semantic = f" [{'; '.join(semantic_bits)}]" if semantic_bits else ""
            description = f" — {semantic_description}" if semantic_description else ""
            lines.append(
                f"- `{t.name}` ({t.object_type}, {t.row_count} rows{concept})"
                f"{semantic}{description}: {', '.join(col_strs)}"
            )

        if len(sorted_tables) > 30:
            remaining = [f"{t.name}{cls._get_table_business_concept(t.name)}" for t in sorted_tables[30:]]
            lines.append(f"\n### OTHER RELEVANT BUSINESS TABLES ({len(remaining)} tables):")
            lines.append(", ".join(remaining))
            
        return "\n".join(lines)

    @classmethod
    def generate_dynamic_presets(cls, tables: List[TableMeta]) -> List[SuggestedPreset]:
        """Generates dynamic analytical presets tailored specifically to whatever database is connected."""
        active = [t for t in tables if t.row_count > 0]
        eval_tables = active if active else tables
        if not eval_tables:
            return []

        colors = [
            "from-indigo-500/20 to-cyan-500/20 text-indigo-400 border-indigo-500/30",
            "from-cyan-500/20 to-blue-500/20 text-cyan-400 border-cyan-500/30",
            "from-emerald-500/20 to-teal-500/20 text-emerald-400 border-emerald-500/30",
            "from-amber-500/20 to-orange-500/20 text-amber-400 border-amber-500/30",
            "from-rose-500/20 to-pink-500/20 text-rose-400 border-rose-500/30",
            "from-violet-500/20 to-fuchsia-500/20 text-violet-400 border-violet-500/30"
        ]

        presets: List[SuggestedPreset] = []
        used_tables = set()

        for t in eval_tables:
            if len(presets) >= 6:
                break
            if t.name in used_tables:
                continue

            date_col = next((c.name for c in t.columns if any(k in c.name.lower() or k in c.data_type.lower() for k in ["date", "time", "sana", "created_at", "datetime"])), None)
            num_col = next((c.name for c in t.columns if not c.is_primary_key and not c.is_foreign_key and not c.name.endswith("_id") and any(k in c.data_type.lower() for k in ["int", "numeric", "float", "dec", "double", "money"])), None)
            cat_col = next((c.name for c in t.columns if not c.is_primary_key and not c.is_foreign_key and not c.name.endswith("_id") and any(k in c.data_type.lower() for k in ["varchar", "text", "char", "string"])), None)
            status_col = next((c.name for c in t.columns if any(k in c.name.lower() for k in ["status", "state", "holat", "type", "turi"])), None)

            clean_name = t.name.replace("_", " ").title()
            color = colors[len(presets) % len(colors)]

            if date_col and num_col:
                presets.append(SuggestedPreset(
                    id=f"preset_{len(presets)}",
                    tag="Dinamika",
                    chartTag="Area / Dinamika",
                    title=f"{clean_name} oylik dinamikasi va umumiy ko'rsatkichlari",
                    desc=f"{clean_name} jadvalida vaqt bo'yicha hisoblangan asosiy ko'rsatkichlar o'sish tendentsiyasi",
                    table_name=t.name,
                    color=color
                ))
                used_tables.add(t.name)
            elif status_col:
                presets.append(SuggestedPreset(
                    id=f"preset_{len(presets)}",
                    tag="Taqsimot",
                    chartTag="Donut / Ulush",
                    title=f"{clean_name} holatlari va toifalari bo'yicha taqsimot",
                    desc=f"{clean_name} ma'lumotlarining segmentlar va statuslar kesimidagi mutanosibligi",
                    table_name=t.name,
                    color=color
                ))
                used_tables.add(t.name)
            elif cat_col and (num_col or t.row_count > 0):
                presets.append(SuggestedPreset(
                    id=f"preset_{len(presets)}",
                    tag="Reyting",
                    chartTag="Bar / Reyting",
                    title=f"{clean_name} bo'yicha eng yuqori ko'rsatkichlar reytingi",
                    desc=f"{clean_name} obyektlari orasida eng yuqori natijaga ega yetakchilar tahlili",
                    table_name=t.name,
                    color=color
                ))
                used_tables.add(t.name)
            else:
                presets.append(SuggestedPreset(
                    id=f"preset_{len(presets)}",
                    tag="Tahlil",
                    chartTag="Jadval / Ro'yxat",
                    title=f"{clean_name} bo'yicha asosiy ma'lumotlar va yozuvlar tahlili",
                    desc=f"{clean_name} jadvalidagi yozuvlar tarkibi va muhim parametrlari ko'rinishi",
                    table_name=t.name,
                    color=color
                ))
                used_tables.add(t.name)

        return presets

    @classmethod
    def _is_log_table(cls, name: str) -> bool:
        """Identifies technical audit, sync, background worker, or progress log tables."""
        n = name.lower()
        return any(k in n for k in [
            '_log', '_progress', '_audit', '_history', 'audit_log', 'action_log', 
            'import_page_log', 'import_run_log', 'message_log', 'bot_message',
            '_request', '_detail_request', 'staging', 'temp_', 'sync_',
            'stock_income_output', 'warehouse_stock_income_output',
            'warehouse_stock_adjustment', 'warehouse_auditing', '_files', '_camera'
        ])

    @classmethod
    def infer_relationships(cls, tables: List[TableMeta]) -> List[TableRelationship]:
        """
        Infers implicit relationships across tables based on column naming conventions:
        e.g., <entity>_id -> <entity>.id or <entities>.id
        """
        table_dict = {t.name: {c.name: c for c in t.columns} for t in tables}
        table_names = set(table_dict.keys())
        relationships = []
        seen = set()

        for t in tables:
            for c in t.columns:
                cname = c.name.lower()
                if cname.endswith('_id') and cname != 'id':
                    target = cname[:-3]
                    candidates = [target, target + 's', target + 'es']
                    if target == 'user':
                        candidates.insert(0, 'users')
                    for cand in candidates:
                        if cand in table_names and 'id' in table_dict[cand] and cand != t.name:
                            rel_key = (t.name, c.name, cand, 'id')
                            if rel_key not in seen:
                                seen.add(rel_key)
                                hint = f"LEFT JOIN {cand} ON {t.name}.{c.name} = {cand}.id"
                                relationships.append(
                                    TableRelationship(
                                        from_table=t.name,
                                        from_column=c.name,
                                        to_table=cand,
                                        to_column='id',
                                        join_hint=hint,
                                        source="naming_convention",
                                        confidence=0.55,
                                        verified=False,
                                        cardinality=None,
                                    )
                                )
                                break
        return relationships

    @classmethod
    def get_prompt_for_query(cls, query_prompt: str) -> str:
        """Dynamically retrieves and prioritizes tables and columns relevant to the user's specific query."""
        ctx = cls.profile()
        if not ctx.tables:
            return ctx.schema_prompt

        matches = SchemaRetriever.rank(query_prompt, ctx, top_k=8)
        if not matches:
            return ctx.schema_prompt
        selected = [match.table for match in matches]
        selected_names = {table.name for table in selected}
        lines = [
            f"### DATABASE DIALECT: {ctx.database_type.upper()}",
            "### QUERY-RELEVANT OBJECTS FROM THE COMPLETE CATALOG:",
        ]
        for match in matches:
            table = match.table
            columns = []
            for column in table.columns:
                flags = []
                if column.is_primary_key:
                    flags.append("PK")
                if column.is_foreign_key and column.foreign_table:
                    flags.append(f"FK->{column.foreign_table}.{column.foreign_column}")
                if column.comment:
                    flags.append(column.comment[:80])
                suffix = f" [{', '.join(flags)}]" if flags else ""
                columns.append(f"{column.name} ({column.data_type}){suffix}")
            table_comment = f" — {table.comment}" if table.comment else ""
            lines.append(
                f"- `{table.name}` ({table.object_type}, score={match.score}){table_comment}: "
                + ", ".join(columns)
            )
        relationships = [
            relationship for relationship in ctx.relationships
            if relationship.from_table in selected_names
            and relationship.to_table in selected_names
            and (relationship.verified or relationship.source == "foreign_key")
        ]
        if relationships:
            lines.append("### DECLARED RELATIONSHIPS:")
            lines.extend(f"- {relationship.join_hint}" for relationship in relationships)
        return "\n".join(lines)
