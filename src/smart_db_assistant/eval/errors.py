"""Schema coverage metrics and automatic error categorisation.

Owner: Ananya.

Schema coverage (separates *selection* failures from *generation* failures):

    schema_recall     |gold tables ∩ selected| / |gold tables|
    schema_precision  |gold tables ∩ selected| / |selected|
    schema_complete   every gold table was selected (the SQL is writable)

Gold tables come from parsing the gold SQL. A gold table is a *bridge* when
it is only referenced in JOIN conditions (nothing selected, filtered, grouped
or ordered from it) - the connector tables top-k retrieval tends to miss.

Error categories (first matching rule wins, in this order):

    correct            result matches gold
    no_sql             model refused / produced nothing runnable after retries
    connector_miss     a *bridge* gold table was not in the selected schema
    retrieval_miss     a non-bridge gold table was not in the selected schema
    distractor_table   SQL used a table from another (pooled) database
    exec_error         SQL failed at execution for another reason
    wrong_tables       all gold tables available, but SQL used a different set
    wrong_join         same tables, different join conditions
    wrong_columns      same tables/joins, different selected columns
    wrong_aggregation  different aggregate functions or GROUP BY
    wrong_filter       different WHERE/HAVING columns or literal values
    wrong_order_limit  different ORDER BY / LIMIT
    other              structure matches but results differ (values, DISTINCT,
                       formatting, NULL handling, ...)

These are heuristics for triage. Export them with `errors.csv` and confirm
by hand (`manual_category` column) before reporting the breakdown.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

CATEGORIES = (
    "correct", "no_sql", "connector_miss", "retrieval_miss", "distractor_table", "exec_error",
    "wrong_tables", "wrong_join", "wrong_columns", "wrong_aggregation", "wrong_filter",
    "wrong_order_limit", "other",
)


@dataclass
class SqlParts:
    tables: set[str] = field(default_factory=set)
    bridge_tables: set[str] = field(default_factory=set)
    joins: set[frozenset] = field(default_factory=set)
    select_cols: set[tuple] = field(default_factory=set)
    aggregates: set[str] = field(default_factory=set)
    group_by: set[tuple] = field(default_factory=set)
    filter_cols: set[tuple] = field(default_factory=set)
    literals: set[str] = field(default_factory=set)
    order_by: set[tuple] = field(default_factory=set)
    limit: str | None = None
    parsed: bool = True


def _resolve(col: exp.Column, alias_map: dict[str, str], tables: set[str],
             columns: dict[str, set[str]] | None) -> tuple:
    name = col.name.lower()
    if col.table:
        return (alias_map.get(col.table.lower(), col.table.lower()), name)
    if columns:
        owners = [t for t in tables if name in columns.get(t, set())]
        if len(owners) == 1:
            return (owners[0], name)
    return ("?", name)


def parse_parts(sql: str, columns: dict[str, set[str]] | None = None,
                dialect: str = "sqlite") -> SqlParts:
    """Pull out the comparable pieces of a query. `columns` (lower-cased) helps resolve
    unqualified column names to their table."""
    try:
        tree = sqlglot.parse_one(sql, read=dialect)
    except Exception:  # noqa: BLE001
        return SqlParts(parsed=False)
    if tree is None:
        return SqlParts(parsed=False)
    if columns:
        columns = {t.lower(): {c.lower() for c in cs} for t, cs in columns.items()}

    cte = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    tbls = [t for t in tree.find_all(exp.Table) if t.name.lower() not in cte]
    tables = {t.name.lower() for t in tbls}
    alias_map = {t.alias_or_name.lower(): t.name.lower() for t in tbls}
    res = lambda c: _resolve(c, alias_map, tables, columns)

    p = SqlParts(tables=tables)
    join_cols: set[tuple] = set()
    for j in tree.find_all(exp.Join):
        on = j.args.get("on")
        if on is None:
            continue
        for eq in on.find_all(exp.EQ):
            if isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column):
                a, b = res(eq.left), res(eq.right)
                p.joins.add(frozenset((a, b)))
                join_cols.update((a, b))
    # implicit joins: WHERE a.x = b.y
    for where in tree.find_all(exp.Where):
        for eq in where.find_all(exp.EQ):
            if isinstance(eq.left, exp.Column) and isinstance(eq.right, exp.Column):
                a, b = res(eq.left), res(eq.right)
                if a[0] != b[0]:
                    p.joins.add(frozenset((a, b)))
                    join_cols.update((a, b))

    select_star = False
    for sel in tree.find_all(exp.Select):
        for e in sel.expressions:
            p.select_cols.update(res(c) for c in e.find_all(exp.Column))
            if isinstance(e.unalias(), exp.Star):          # SELECT *   (not COUNT(*))
                p.select_cols.add(("*", "*"))
                select_star = True
        group = sel.args.get("group")
        if group:
            p.group_by.update(res(c) for c in group.find_all(exp.Column))
        order = sel.args.get("order")
        if order:
            p.order_by.update(res(c) for c in order.find_all(exp.Column))
            p.order_by.update(("desc" if o.args.get("desc") else "asc", "")
                              for o in order.expressions)
        limit = sel.args.get("limit")
        if limit is not None:
            p.limit = limit.sql()
    p.aggregates = {type(a).__name__.lower() for a in tree.find_all(exp.AggFunc)}
    for clause in (*tree.find_all(exp.Where), *tree.find_all(exp.Having)):
        for c in clause.find_all(exp.Column):
            r = res(c)
            if r not in join_cols:
                p.filter_cols.add(r)
        p.literals.update(str(lit.this).lower() for lit in clause.find_all(exp.Literal))

    used_outside_joins = {t for t, _ in p.select_cols | p.group_by | p.filter_cols | p.order_by}
    in_joins = {col[0] for pair in p.joins for col in pair}
    if not select_star and "?" not in used_outside_joins:
        p.bridge_tables = {t for t in tables if t in in_joins and t not in used_outside_joins}
    return p


def gold_tables(sql: str, dialect: str = "sqlite") -> list[str]:
    return sorted(parse_parts(sql, dialect=dialect).tables)


def schema_coverage(selected: list[str], gold: list[str]) -> dict:
    sel = {t.lower() for t in selected}
    g = {t.lower() for t in gold}
    hit = sel & g
    return {
        "schema_recall": round(len(hit) / len(g), 4) if g else None,
        "schema_precision": round(len(hit) / len(sel), 4) if sel else None,
        "schema_complete": g <= sel if g else None,
        "missing_tables": sorted(g - sel),
        "n_selected": len(sel),
    }


def categorize(*, exec_match: bool | None, action: str, pred_sql: str | None, gold_sql: str,
               selected: list[str], db_error: str | None = None,
               distractor_tables: list[str] | None = None,
               columns: dict[str, set[str]] | None = None, dialect: str = "sqlite") -> str:
    if exec_match:
        return "correct"
    g = parse_parts(gold_sql, columns, dialect)
    missing = g.tables - {t.lower() for t in selected}
    if missing:
        return "connector_miss" if missing & g.bridge_tables else "retrieval_miss"
    if action != "sql" or not pred_sql:
        if distractor_tables:
            return "distractor_table"
        return "no_sql" if not db_error else "exec_error"
    if distractor_tables:
        return "distractor_table"
    if db_error:
        return "exec_error"
    p = parse_parts(pred_sql, columns, dialect)
    if not (p.parsed and g.parsed):
        return "other"
    if p.tables != g.tables:
        return "wrong_tables"
    if p.joins != g.joins:
        return "wrong_join"
    if {c for c in p.select_cols} != {c for c in g.select_cols}:
        return "wrong_columns"
    if p.aggregates != g.aggregates or p.group_by != g.group_by:
        return "wrong_aggregation"
    if p.filter_cols != g.filter_cols or p.literals != g.literals:
        return "wrong_filter"
    if p.order_by != g.order_by or p.limit != g.limit:
        return "wrong_order_limit"
    return "other"
