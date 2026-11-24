import re
import logging
from typing import Any, Dict, Mapping, Optional, Tuple
import sqlglot
from sqlglot import exp

logger = logging.getLogger(__name__)

FORBIDDEN_KEYWORDS = {
    "DROP", "DELETE", "TRUNCATE", "UPDATE", "INSERT", 
    "ALTER", "CREATE", "GRANT", "REVOKE", "EXECUTE", 
    "EXEC", "MERGE", "CALL", "UPSERT", "COPY",
    "LOCK", "VACUUM", "ANALYZE", "REINDEX", "CLUSTER",
    "REFRESH", "DISCARD", "RESET", "DEALLOCATE", "PREPARE",
    "NOTIFY", "LISTEN", "UNLISTEN", "COMMENT", "SECURITY"
}

FORBIDDEN_PATTERNS = [
    r"\bREPLACE\s+INTO\b",
    r"\bSELECT\b[\s\S]*\bINTO\b",
    r"\bFOR\s+(?:NO\s+KEY\s+)?UPDATE\b",
    r"\bFOR\s+(?:KEY\s+)?SHARE\b",
    r"\bLOCK\s+TABLE\b",
    r"\bDO\s+\$\$",
    r"\bSET\s+ROLE\b",
    r"\bSET\s+SESSION\s+AUTHORIZATION\b",
]

# PostgreSQL exposes administrative, filesystem and blocking operations through
# SELECT-able functions. A SELECT-only AST check is therefore not sufficient.
FORBIDDEN_FUNCTIONS = {
    "pg_sleep", "pg_sleep_for", "pg_sleep_until",
    "pg_read_file", "pg_read_binary_file", "pg_ls_dir", "pg_stat_file",
    "pg_terminate_backend", "pg_cancel_backend", "pg_reload_conf",
    "pg_advisory_lock", "pg_advisory_xact_lock", "pg_try_advisory_lock",
    "pg_advisory_lock_shared", "pg_advisory_xact_lock_shared",
    "pg_try_advisory_lock_shared", "pg_notify",
    "lo_import", "lo_export", "lo_unlink", "dblink", "dblink_exec", "dblink_connect",
    "set_config", "setval", "nextval",
    "sleep", "benchmark", "load_file", "get_lock", "release_lock",
    "load_extension", "writefile", "edit", "xp_cmdshell",
}

def ensure_soft_delete_filtered(
    statement: exp.Expression,
    soft_delete_rules: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> exp.Expression:
    """Add a scoped soft-delete predicate without breaking LEFT JOIN semantics."""
    rules: Dict[str, Mapping[str, Any]] = {
        str(name).lower(): rule for name, rule in (soft_delete_rules or {}).items()
    }

    def table_rule(table: exp.Table) -> Optional[Mapping[str, Any]]:
        full_name = ".".join(
            part for part in ((table.db or "").lower(), table.name.lower()) if part
        )
        return rules.get(full_name) or rules.get(table.name.lower())

    def has_filter(node: exp.Expression | None, alias: str, column_name: str) -> bool:
        if node is None:
            return False
        for column in node.find_all(exp.Column):
            if column.name.lower() != column_name.lower():
                continue
            if not column.table or column.table.lower() == alias.lower():
                return True
        return False

    def predicate(alias: str, column_name: str, mode: str) -> exp.Expression:
        column = exp.column(column_name, table=alias)
        if mode == "is_null":
            return exp.Is(this=column, expression=exp.Null())
        if mode == "zero_or_null":
            return exp.EQ(
                this=exp.Coalesce(this=column, expressions=[exp.Literal.number(0)]),
                expression=exp.Literal.number(0),
            )
        # Treat NULL booleans as not deleted as well. This is safer than
        # `column = FALSE`, which silently drops unknown/null legacy values.
        return exp.EQ(
            this=exp.Coalesce(this=column, expressions=[exp.false()]),
            expression=exp.false(),
        )

    try:
        for select in statement.find_all(exp.Select):
            where_clause = select.args.get("where")
            from_clause = select.args.get("from_")
            base_table = from_clause.this if from_clause and isinstance(from_clause.this, exp.Table) else None

            base_rule = table_rule(base_table) if base_table else None
            if base_table and base_rule:
                alias = base_table.alias_or_name
                column_name = str(base_rule.get("column") or "is_deleted")
                mode = str(base_rule.get("mode") or "not_true")
                if not has_filter(where_clause, alias, column_name):
                    cond = predicate(alias, column_name, mode)
                    existing = where_clause.this if where_clause else None
                    select.set("where", exp.Where(this=exp.and_(existing, cond) if existing else cond))

            for join in select.args.get("joins") or []:
                table = join.this if isinstance(join.this, exp.Table) else None
                rule = table_rule(table) if table else None
                if not table or not rule:
                    continue
                alias = table.alias_or_name
                on_clause = join.args.get("on")
                column_name = str(rule.get("column") or "is_deleted")
                mode = str(rule.get("mode") or "not_true")
                if not has_filter(on_clause, alias, column_name):
                    cond = predicate(alias, column_name, mode)
                    join.set("on", exp.and_(on_clause, cond) if on_clause else cond)
    except Exception as exc:
        logger.warning("Soft-delete filter injection failed: %s", exc)
    return statement

def validate_and_sanitize_sql(
    sql: str,
    apply_soft_delete: bool = True,
    soft_delete_rules: Optional[Mapping[str, Mapping[str, Any]]] = None,
) -> Tuple[bool, str]:
    """
    Validates that the SQL query is strictly read-only (SELECT / WITH ... SELECT).
    Rejects any destructive, data-modifying, or schema-altering statements.
    
    Returns: (is_valid: bool, cleaned_sql_or_error_message: str)
    """
    if not sql or not sql.strip():
        return False, "SQL query cannot be empty."

    # Strip markdown code fences if LLM wrapped it in ```sql ... ```
    cleaned = sql.strip()
    cleaned = re.sub(r"^```(?:sql)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    cleaned = cleaned.strip()

    # Fast regex pre-check for hazardous statements
    upper_sql = cleaned.upper()
    for word in FORBIDDEN_KEYWORDS:
        # Match whole word
        if re.search(r"\b" + word + r"\b", upper_sql):
            return False, f"Security Violation: '{word}' statements are strictly prohibited. Only read-only SELECT queries are allowed."

    for pattern in FORBIDDEN_PATTERNS:
        if re.search(pattern, upper_sql, re.IGNORECASE):
            return False, f"Security Violation: Data-modifying '{pattern}' statements are strictly prohibited."

    # Parse with sqlglot for robust AST analysis
    try:
        parsed_statements = sqlglot.parse(cleaned)
        if not parsed_statements:
            return False, "Failed to parse SQL query."
        
        if len(parsed_statements) > 1:
            return False, "Multiple SQL statements in a single execution are not allowed for safety."
        
        statement = parsed_statements[0]
        
        # Verify the root expression is either Select or Union or Expression containing Select
        if not isinstance(statement, (exp.Select, exp.Union)):
            # Check if it's a CTE with SELECT
            if isinstance(statement, exp.Expression) and statement.find(exp.Select):
                pass
            else:
                return False, f"Only SELECT queries are allowed. Received statement type: {statement.key}"

        # Ensure no write operations in subqueries
        for node in statement.walk():
            if isinstance(node, (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Alter, exp.Create)):
                return False, f"Forbidden operation detected in query: {node.key}"

        for func in statement.find_all(exp.Func):
            sql_name = (getattr(func, "sql_name", lambda: "")() or "").lower()
            func_name = (func.name or sql_name or "").lower()
            if func_name == "anonymous":
                func_name = sql_name
            if func_name in FORBIDDEN_FUNCTIONS:
                return False, f"Security Violation: function '{func_name}' is not allowed."

        # Reject slow substring/ILIKE joins that cause catastrophic Cartesian products
        for join in statement.find_all(exp.Join):
            join_sql = join.sql().upper()
            if " ILIKE " in join_sql or " LIKE " in join_sql:
                return False, (
                    "JOIN shartida (ON ...) ILIKE/LIKE ishlatish taqiqlanadi (juda sekin ishlaydi). "
                    "Jadvallarni aniq key ustunlari orqali tenglik bilan bog'lang "
                    "(masalan: fact.dimension_id = dimension.id); matnli qidiruvni esa WHERE qismida bajaring."
                )

        # Ensure soft-deleted records are automatically filtered out for tables having is_deleted
        if apply_soft_delete:
            statement = ensure_soft_delete_filtered(statement, soft_delete_rules)

        # sqlglot's generic renderer treats DESC NULLS LAST as its implicit
        # default and removes the clause. PostgreSQL does the opposite for
        # DESC, so dropping an explicit clause changes query results. Render
        # explicit NULL ordering with PostgreSQL semantics to preserve it.
        has_explicit_null_order = bool(
            re.search(r"\bNULLS\s+(?:FIRST|LAST)\b", cleaned, re.IGNORECASE)
        )
        formatted_sql = (
            statement.sql(dialect="postgres")
            if has_explicit_null_order
            else statement.sql()
        )
        return True, formatted_sql

    except Exception as e:
        logger.warning(f"sqlglot parse exception: {e}")
        # Fail closed. Executing unparsed LLM SQL would bypass the AST policy.
        return False, f"SQL validation error: {str(e)}"
