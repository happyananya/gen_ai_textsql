"""Read-only SQL guardrail.

Layer 1 of 3 (the others: READ ONLY transaction in db.py, read-only DB role).
Rejects anything that is not a single SELECT / WITH ... SELECT, flags
references to unknown tables/columns (hallucinations), and enforces a LIMIT.

Owner: Harindham.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

_FORBIDDEN = (
    exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter,
    exp.Command, exp.Merge, exp.TruncateTable, exp.Grant,
)
_FORBIDDEN_FUNCS = {"pg_sleep", "pg_read_file", "pg_ls_dir", "dblink", "lo_import", "lo_export",
                    "pg_terminate_backend", "pg_cancel_backend", "set_config"}


@dataclass
class ValidationResult:
    ok: bool
    sql: str
    errors: list[str] = field(default_factory=list)
    tables: set[str] = field(default_factory=set)


def validate_sql(
    sql: str,
    known_columns: dict[str, set[str]] | None = None,
    row_limit: int | None = 500,
    dialect: str = "postgres",
) -> ValidationResult:
    """Identifiers are compared case-insensitively. `tables` in the result is lower-cased."""
    sql = sql.strip().rstrip(";").strip()
    if known_columns is not None:
        known_columns = {t.lower(): {c.lower() for c in cols} for t, cols in known_columns.items()}
    if not sql:
        return ValidationResult(False, sql, ["empty query"])
    try:
        statements = sqlglot.parse(sql, read=dialect)
    except sqlglot.errors.ParseError as e:
        return ValidationResult(False, sql, [f"parse error: {e}"])
    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        return ValidationResult(False, sql, ["exactly one statement is allowed"])
    tree = statements[0]

    errors: list[str] = []
    if not isinstance(tree, (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.With)) and not (
        isinstance(tree, exp.Subquery)
    ):
        errors.append(f"only SELECT queries are allowed (got {tree.key.upper()})")
    for node in tree.walk():
        if isinstance(node, _FORBIDDEN):
            errors.append(f"forbidden statement: {node.key.upper()}")
        if isinstance(node, exp.Anonymous) and node.name.lower() in _FORBIDDEN_FUNCS:
            errors.append(f"forbidden function: {node.name}")
        if isinstance(node, exp.Into):
            errors.append("SELECT INTO is not allowed")
        if isinstance(node, exp.Lock):
            errors.append("row locking (FOR UPDATE/SHARE) is not allowed")

    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}
    tables = {t.name.lower() for t in tree.find_all(exp.Table)
              if t.name and t.name.lower() not in cte_names}

    if known_columns is not None:
        unknown = sorted(t for t in tables if t not in known_columns)
        if unknown:
            errors.append(f"unknown table(s): {', '.join(unknown)}")
        else:
            all_cols = set().union(*(known_columns[t] for t in tables)) if tables else set()
            # also allow columns produced by CTEs / subqueries / SELECT aliases
            derived = {a.alias.lower() for a in tree.find_all(exp.Alias)}
            for cte in tree.find_all(exp.CTE):
                derived.update(c.name.lower() for c in cte.find_all(exp.Column))
            # map table aliases (FROM customers c) to real table names
            qualifier = {t.alias_or_name.lower(): t.name.lower() for t in tree.find_all(exp.Table)
                         if t.name.lower() not in cte_names}
            for col in tree.find_all(exp.Column):
                name = col.name.lower()
                if not name or name in derived or name == "*":
                    continue
                tbl = qualifier.get(col.table.lower()) if col.table else None
                if tbl and name not in known_columns[tbl]:
                    errors.append(f"unknown column: {tbl}.{col.name}")
                elif (not col.table and all_cols and name not in all_cols
                      # SQLite reads an unknown "double-quoted" name as a string literal
                      and not (dialect == "sqlite" and col.this.args.get("quoted"))):
                    errors.append(f"unknown column: {col.name}")

    if errors:
        return ValidationResult(False, sql, sorted(set(errors)), tables)

    if row_limit and isinstance(tree, exp.Select) and not tree.args.get("limit"):
        tree = tree.limit(row_limit)
        sql = tree.sql(dialect=dialect)
    return ValidationResult(True, sql, [], tables)
