import logging
import time
from typing import Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp
from sqlglot.optimizer.scope import traverse_scope

from app.database import db_manager
from app.models import SchemaContext, TableMeta, TableRelationship


logger = logging.getLogger(__name__)


class RelationshipGuard:
    """Verify physical-table joins before analytical SQL is executed."""

    _cache: Dict[str, Tuple[float, bool, str]] = {}
    _ttl_seconds = 600.0

    @classmethod
    def clear_cache(cls) -> None:
        cls._cache.clear()

    @staticmethod
    def _table_lookup(schema_ctx: SchemaContext) -> Dict[str, TableMeta]:
        lookup: Dict[str, TableMeta] = {}
        base_counts: Dict[str, int] = {}
        for table in schema_ctx.tables:
            base = table.name.split(".")[-1].lower()
            base_counts[base] = base_counts.get(base, 0) + 1
        for table in schema_ctx.tables:
            lookup[table.name.lower()] = table
            base = table.name.split(".")[-1].lower()
            if base_counts[base] == 1:
                lookup[base] = table
        return lookup

    @staticmethod
    def _physical_table_name(table: exp.Table) -> str:
        return ".".join(
            part for part in ((table.db or "").lower(), table.name.lower()) if part
        )

    @classmethod
    def _verify_unique_live(cls, table: TableMeta, column: str) -> Tuple[bool, str]:
        cache_key = "|".join([
            db_manager.connection_fingerprint or db_manager.current_db_name,
            table.name.lower(),
            column.lower(),
        ])
        cached = cls._cache.get(cache_key)
        if cached and time.time() - cached[0] < cls._ttl_seconds:
            return cached[1], cached[2]

        quoted_table = db_manager.quote_identifier(table.name)
        quoted_column = db_manager.quote_identifier(column)
        sql = f"""
        SELECT COUNT(*) AS duplicate_key_groups
        FROM (
            SELECT {quoted_column}
            FROM {quoted_table}
            WHERE {quoted_column} IS NOT NULL
            GROUP BY {quoted_column}
            HAVING COUNT(*) > 1
            LIMIT 1
        ) duplicate_keys
        """
        try:
            rows = db_manager.execute_query(sql, max_rows=1)
            duplicate_groups = int(rows[0].get("duplicate_key_groups", 1)) if rows else 1
            valid = duplicate_groups == 0
            reason = "verified unique key" if valid else "target join key contains duplicates"
        except Exception as exc:
            valid = False
            reason = f"join uniqueness could not be verified: {exc}"
        cls._cache[cache_key] = (time.time(), valid, reason)
        return valid, reason

    @staticmethod
    def _join_equalities(predicate):
        """Only unconditional qualified equalities establish a relationship."""
        if isinstance(predicate, exp.Paren):
            yield from RelationshipGuard._join_equalities(predicate.this)
        elif isinstance(predicate, exp.And):
            yield from RelationshipGuard._join_equalities(predicate.this)
            yield from RelationshipGuard._join_equalities(predicate.expression)
        elif (isinstance(predicate, exp.EQ) and isinstance(predicate.this, exp.Column)
              and isinstance(predicate.expression, exp.Column)):
            left, right = predicate.this, predicate.expression
            if left.table and right.table and left.table.lower() != right.table.lower():
                yield ((left.table.lower(), left.name.lower()), (right.table.lower(), right.name.lower()))

    @staticmethod
    def _check_pair(left, right, pairs, tables, relationships, safe_edges):
        """Validate a complete key for this join, then record non-multiplying directions."""
        covered = set()
        partial = False
        for relation in relationships:
            if not relation.verified or relation.source not in {"foreign_key", "semantic_catalog"}:
                continue
            for child, parent in [(left, right), (right, left)]:
                if (relation.from_table.lower() != tables[child].name.lower()
                        or relation.to_table.lower() != tables[parent].name.lower()):
                    continue
                source = relation.from_columns or [relation.from_column]
                target = relation.to_columns or [relation.to_column]
                required = {frozenset(((child, a.lower()), (parent, b.lower()))) for a, b in zip(source, target)}
                if not required.issubset(pairs):
                    partial |= bool(required & pairs)
                    continue
                covered.update(required)
                cardinality = relation.cardinality or "many-to-one"
                if cardinality in {"many-to-one", "one-to-one"}:
                    safe_edges[child].add(parent)
                keys = [tables[child].primary_key, *tables[child].candidate_keys]
                unique_source = any(key and set(key).issubset(source) for key in keys)
                if cardinality in {"one-to-many", "one-to-one"} or (relation.source == "foreign_key" and unique_source):
                    safe_edges[parent].add(child)
        if covered != pairs:
            if partial:
                return "Unsafe join rejected: composite foreign key must use every key column."
            return f"Unsafe join rejected: {left} and {right} have no declared or catalog-approved relationship for the specified columns."
        return None

    @classmethod
    def _scope_error(cls, scope, lookup, relationships):
        sources = scope.selected_sources
        tables = {}
        for alias, (_, source) in sources.items():
            if isinstance(source, exp.Table):
                name = cls._physical_table_name(source)
                if name not in lookup:
                    return f"SQL references tables outside the profiled database scope: {name}"
                tables[alias.lower()] = lookup[name]
        columns = {a: {c.name.lower() for c in t.columns} for a, t in tables.items()}
        for col in scope.columns:
            owner = col.table.lower()
            if owner in columns and not col.is_star and col.name.lower() not in columns[owner]:
                return f"SQL references unknown column {tables[owner].name}.{col.name} outside the profiled schema."

        safe_edges = {a: set() for a in tables}
        physical_edges = {a: set() for a in tables}
        for join in scope.expression.args.get("joins") or ():
            if join.kind == "CROSS" or join.method == "NATURAL" or join.args.get("using"):
                return "Unsafe join rejected: write an explicit qualified ON equality."
            joined = join.this.alias_or_name.lower()
            equalities = [pair for pair in cls._join_equalities(join.args.get("on"))
                          if joined in {pair[0][0], pair[1][0]}]
            if not equalities:
                return "Unsafe join rejected: every JOIN must contain a qualified column equality for the joined source."
            by_alias = {}
            for a, b in equalities:
                if a[0] in tables and b[0] in tables:
                    other = a[0] if b[0] == joined else b[0]
                    by_alias.setdefault(other, set()).add(frozenset((a, b)))
            for other, pairs in by_alias.items():
                issue = cls._check_pair(joined, other, pairs, tables, relationships, safe_edges)
                if issue:
                    return issue
                physical_edges[joined].add(other)
                physical_edges[other].add(joined)

        def reachable(start, edges):
            seen, pending = {start}, [start]
            while pending:
                neighbors = edges[pending.pop()] - seen
                seen.update(neighbors)
                pending.extend(neighbors)
            return seen

        # Check each aggregate inside its own SELECT. Two raw child facts of
        # the same parent multiply each other even with two individually valid FKs.
        for agg in scope.find_all(exp.Sum, exp.Avg, exp.Count):
            if isinstance(agg, exp.Count) and isinstance(agg.this, exp.Distinct):
                continue
            owners = set()
            for col in agg.find_all(exp.Column):
                if col.find_ancestor(exp.Select) is not scope.expression:
                    continue
                if col.table:
                    owners.add(col.table.lower())
                else:
                    matches = [a for a, names in columns.items() if col.name.lower() in names]
                    if len(matches) == 1:
                        owners.add(matches[0])
            if owners and owners.issubset(tables) and all(
                reachable(a, safe_edges) != reachable(a, physical_edges) for a in owners
            ):
                return (f"Join blow-up risk: {agg.sql()} can repeat source rows. "
                        "Aggregate each fact at the requested grain before joining, or use EXISTS for an existence filter.")
        return None

    @classmethod
    def validate_query(cls, sql: str, schema_ctx: Optional[SchemaContext]) -> Tuple[bool, Optional[str]]:
        if schema_ctx is None:
            return True, None
        try:
            dialect = "postgres" if schema_ctx.database_type == "postgresql" else schema_ctx.database_type
            tree = sqlglot.parse_one(sql, read=dialect)
            lookup = cls._table_lookup(schema_ctx)
            for scope in traverse_scope(tree):
                issue = cls._scope_error(scope, lookup, schema_ctx.relationships)
                if issue:
                    return False, issue
        except Exception as exc:
            return False, f"Join validation could not parse SQL: {exc}"
        return True, None
