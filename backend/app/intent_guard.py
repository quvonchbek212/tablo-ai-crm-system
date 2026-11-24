"""Conservative, schema-independent checks for explicit analytical requirements.

This is a rejection gate, not a proof of arbitrary natural-language correctness.
Checks inspect predicates and grouping scope, rather than SQL string occurrence.
"""
import re
from typing import Optional, Tuple

import sqlglot
from sqlglot import exp


def normalize_explicit_null_order(prompt, sql, database_type='postgresql'):
    if not re.search(r'null\s+oxiri(?:da|ga)', prompt.lower()):
        return sql
    dialect = 'postgres' if database_type == 'postgresql' else 'sqlite' if database_type == 'sqlite' else None
    tree = sqlglot.parse_one(sql, read=dialect)
    order = tree.args.get('order')
    if order:
        for term in order.expressions:
            term.set('nulls_first', False)
    return tree.sql(dialect=dialect)


def requested_group_columns(prompt, schema):
    lower = prompt.lower().replace('‘', "'").replace('’', "'")
    columns = {c.name.lower() for t in schema.tables for c in t.columns} if schema else set()
    requested = set()
    for match in re.finditer(r"\b([a-z_][a-z_0-9]*)\s+(?:bo'yicha|kesimida)", lower):
        suffix = lower[match.end():match.end() + 60]
        grouping_context = 'kesimida' in match.group(0) or re.search(
            r"^\s*(?:alohida|yozuvlar\s+son|guruh)", suffix)
        if match.group(1) in columns and grouping_context:
            requested.add(match.group(1))
    for match in re.finditer(r'har bir\s+([a-z_][a-z_0-9]*(?:\s+va\s+[a-z_][a-z_0-9]*)?)\s+uchun', lower):
        requested.update(token for token in match.group(1).split(' va ') if token in columns)
    return requested


def dependent_group_columns(scope, group, requested, schema):
    """Allow extra labels only when declared keys/FKs prove one value per group."""
    if not schema:
        return set()
    tables = {t.name.split('.')[-1].lower(): t for t in schema.tables}
    aliases = {t.alias_or_name.lower(): t.name.lower() for t in scope.find_all(exp.Table)}

    def resolve(column):
        if column.table:
            return aliases.get(column.table.lower()), column.name.lower()
        owners = {name for name in aliases.values() if name in tables
                  and any(c.name.lower() == column.name.lower() for c in tables[name].columns)}
        return (next(iter(owners)), column.name.lower()) if len(owners) == 1 else (None, column.name.lower())

    known = {resolve(c) for c in group.find_all(exp.Column) if c.name.lower() in requested}
    # Only direct AND-conjoined equality constants determine a grouping key.
    def terms(node):
        if isinstance(node, (exp.Where, exp.Paren)):
            return terms(node.this)
        if isinstance(node, exp.And):
            return terms(node.this) + terms(node.expression)
        return [node]
    for term in terms(scope.args.get('where')):
        if isinstance(term, exp.EQ) and isinstance(term.this, exp.Column) and isinstance(term.expression, exp.Literal):
            known.add(resolve(term.this))
    join_pairs = set()
    for join in scope.args.get('joins') or []:
        for term in terms(join.args.get('on')):
            if isinstance(term, exp.EQ) and isinstance(term.this, exp.Column) and isinstance(term.expression, exp.Column):
                join_pairs.add(frozenset((resolve(term.this), resolve(term.expression))))
    for _ in range(len(aliases) + 1):
        previous = set(known)
        for name, table in tables.items():
            keys = [table.primary_key, *table.candidate_keys]
            if any(key and all((name, col.lower()) in known for col in key) for key in keys):
                known.update((name, col.name.lower()) for col in table.columns)
        for rel in schema.relationships:
            if not rel.verified or rel.source not in {'foreign_key', 'semantic_catalog'}:
                continue
            source, target = rel.from_table.split('.')[-1].lower(), rel.to_table.split('.')[-1].lower()
            left, right = rel.from_columns or [rel.from_column], rel.to_columns or [rel.to_column]
            proven_join = all(frozenset(((source, a.lower()), (target, b.lower()))) in join_pairs
                              for a, b in zip(left, right))
            if proven_join and all((source, col.lower()) in known for col in left):
                known.update((target, col.lower()) for col in right)
        if known == previous:
            break
    return {c.name.lower() for c in group.find_all(exp.Column) if resolve(c) in known and resolve(c)[0]}


def validate_explicit_intent(prompt, sql, schema=None) -> Tuple[bool, Optional[str]]:
    dialect = 'postgres' if schema and schema.database_type == 'postgresql' else None
    tree = sqlglot.parse_one(sql, read=dialect)
    lower = prompt.lower().replace('‘', "'").replace('’', "'")
    predicates = list(tree.find_all(exp.Where)) + list(tree.find_all(exp.Having))
    predicate_literals = {
        str(n.this) for p in predicates for n in p.find_all(exp.Literal)
    }
    dates = set(re.findall(r'\b\d{4}-\d{2}-\d{2}\b', lower))
    for date in dates:
        if date not in predicate_literals:
            return False, f"Requested date boundary {date} must be used in a filter, not ignored."
    years = set(re.findall(r'\b(?:19|20)\d{2}\b', lower))
    if not dates:
        for year in years:
            if not any(value == year or value.startswith(year + '-') for value in predicate_literals):
                return False, f"Requested year {year} must constrain the result in WHERE/HAVING."

    top = re.search(r'\btop\s*(\d+)\b', lower)
    if top:
        limit = tree.args.get('limit')
        if not limit or str(limit.expression) != top.group(1) or not tree.args.get('order'):
            return False, f"Outer query must use ORDER BY and LIMIT {top.group(1)}."

    directions = re.findall(r"\b(kamayish|o'sish)\b", lower)
    if re.search(r'null\s+oxiri(?:da|ga)', lower):
        order = tree.args.get('order')
        if not order or any(t.args.get('nulls_first') or t.find(exp.Coalesce) for t in order.expressions):
            return False, "The question explicitly requires NULLS LAST. Preserve this ordering."
    if directions:
        ordering = tree.args.get('order')
        terms = ordering.expressions if ordering else []
        if len(terms) < len(directions) or any(
            bool(term.args.get('desc')) != (direction == 'kamayish')
            for term, direction in zip(terms, directions)
        ):
            return False, "Requested ORDER BY directions (in order) are " + ', '.join(
                'DESC' if d == 'kamayish' else 'ASC' for d in directions)

    # Collect requested grouping identifiers only when present in the connected schema.
    groups = list(tree.find_all(exp.Group))
    grouped_columns = {c.name.lower() for g in groups for c in g.find_all(exp.Column)}
    projected_columns = {c.name.lower() for s in tree.find_all(exp.Select)
                         for item in s.expressions for c in item.find_all(exp.Column)}
    requested = requested_group_columns(prompt, schema)
    for column in requested:
        if column not in grouped_columns or column not in projected_columns:
            return False, f"Requested dimension {column} must be grouped AND returned; do not collapse to a total."
    if requested:
        result_scope = tree
        ctes = {c.alias_or_name.lower(): c.this for c in tree.find_all(exp.CTE)}
        seen = set()
        while not result_scope.args.get('group'):
            source = result_scope.args.get('from_')
            name = source.this.name.lower() if source and isinstance(source.this, exp.Table) else None
            if name not in ctes or name in seen:
                break
            seen.add(name)
            result_scope = ctes[name]
        result_group = result_scope.args.get('group')
        if result_group:
            # A requested derived dimension (e.g. quarter computed from month)
            # is not an extra monthly grain. Match the actual grouped expression,
            # not merely the presence of a CASE somewhere in the SQL.
            derived = {}
            for projection in result_scope.expressions:
                if not isinstance(projection, exp.Alias) or isinstance(projection.this, exp.Column):
                    continue
                alias = projection.alias.lower()
                requested_alias = bool(re.search(r'\b' + re.escape(alias) + r'\b', lower))
                requested_alias |= alias == 'quarter' and bool(re.search(r'\bchorak|\bchorag', lower))
                if requested_alias and not projection.this.find(exp.AggFunc):
                    derived[alias] = projection.this.sql()
            ordinary_groups = [g for g in result_group.expressions
                               if not (g.sql() in derived.values()
                                       or (isinstance(g, exp.Column) and not g.table and g.name.lower() in derived))]
            extras = {c.name.lower() for g in ordinary_groups for c in g.find_all(exp.Column)} - requested
            constants = {eq.this.name.lower() for p in predicates for eq in p.find_all(exp.EQ)
                         if isinstance(eq.this, exp.Column) and isinstance(eq.expression, exp.Literal)}
            dependent = dependent_group_columns(result_scope, result_group, requested, schema)
            if extras - constants - dependent:
                return False, "Final result grain has unrequested grouping dimensions: " + ', '.join(sorted(extras - constants - dependent)) + ". Aggregate at exactly the requested dimensions: " + ', '.join(sorted(requested))

    if schema:
        facts = {t.name.split('.')[-1].lower() for t in schema.tables
                 if t.business_role in {'fact', 'snapshot'}}
        fact_ctes = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)
                     if any(t.name.lower() in facts for t in cte.find_all(exp.Table))}
        from_clause = tree.args.get('from_')
        base = from_clause.this if from_clause else None
        asks_missing = any(s in lower for s in ('barcha oy', 'barcha davr', "yo'q oy", 'nol bilan', 'zero-fill', 'including missing'))
        if isinstance(base, exp.Table) and base.name.lower() not in facts | fact_ctes and not asks_missing:
            padded_facts = any(isinstance(join.this, exp.Table)
                               and join.this.name.lower() in facts | fact_ctes
                               and join.args.get('side') == 'LEFT'
                               for join in tree.args.get('joins') or [])
            if padded_facts and tree.find(exp.Sum):
                return False, "Actual aggregates must start from fact rows. A dimension/calendar LEFT JOIN adds missing records; only pad missing periods when explicitly requested."
        for table in schema.tables:
            for alias in table.semantic_aliases:
                if re.search(r"har\s+(?:bir|bitta)\s+" + re.escape(alias.lower()) + r"\b", lower):
                    # Explicit entity grouping must use that dimension table, not another one.
                    refs = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)}
                    entity_groups = [c for g in groups for c in g.find_all(exp.Column)
                                     if refs.get(c.table.lower()) == table.name.split('.')[-1].lower()]
                    if not entity_groups:
                        return False, f"Group by the requested entity {alias} using {table.name}; a scalar total is insufficient."
                    label = table.default_label_column
                    if label and not any(c.name.lower() == label.lower() for c in entity_groups):
                        explicitly_named = any(re.search(r'\b' + re.escape(c.name.lower()) + r'\b', lower)
                                               for c in entity_groups)
                        if not explicitly_named:
                            return False, f"Use reviewed display label {table.name}.{label} for {alias}, unless the user explicitly requests another column."

    for match in re.finditer(r'\b(?:[a-z_][\w]*\.)?([a-z_][\w]*)\s+is\s+not\s+null\b', lower):
        column = match.group(1)
        if not any(isinstance(n.expression, exp.Null)
                   and isinstance(n.this, exp.Column) and n.this.name.lower() == column
                   and (n.args.get('negate') or isinstance(n.parent, exp.Not))
                   for p in predicates for n in p.find_all(exp.Is)):
            return False, f"Explicit filter {column} IS NOT NULL is missing."

    # A global latest snapshot must not quietly become latest within an organization.
    if 'umumiy' in lower and any(word in lower for word in ("eng oxirgi", "eng so'nggi", 'snapshot')):
        maxima = list(tree.find_all(exp.Max))
        if not maxima:
            return False, "Global latest snapshot requires an unfiltered MAX(date) subquery."
        for maximum in maxima:
            scope = maximum.find_ancestor(exp.Select)
            if scope is not None and (scope.args.get('where') or scope.args.get('group') or scope.args.get('joins')):
                return False, "Global latest snapshot MAX must have no organization/type filters, joins or grouping. Apply entity filters AFTER computing the global date."
    return True, None
