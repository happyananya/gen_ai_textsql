"""Check every gold item: SQL validates, runs, and returns rows.

    python -m smart_db_assistant.eval.verify_gold eval/gold
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter

from ..config import get_settings
from ..db import connect, run_readonly
from ..schema import introspect
from ..validator import validate_sql
from .gold import load_gold


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("gold", nargs="?", default="eval/gold")
    args = p.parse_args()

    items = load_gold(args.gold)
    print("Items per tier:", dict(Counter(i.tier for i in items)))
    settings = get_settings()
    conn = connect(settings)
    known = introspect(conn).column_names()

    problems = 0
    for item in items:
        if item.expected != "answer":
            continue
        check = validate_sql(item.gold_sql, known, row_limit=None)
        if not check.ok:
            print(f"[INVALID] {item.id}: {check.errors}")
            problems += 1
            continue
        missing = set(item.gold_tables) - check.tables
        if missing or (item.gold_tables and set(item.gold_tables) != check.tables):
            print(f"[TABLES]  {item.id}: gold_tables={sorted(item.gold_tables)} "
                  f"sql uses={sorted(check.tables)}")
        try:
            res = run_readonly(conn, check.sql, timeout_ms=settings.statement_timeout_ms,
                               max_rows=100_000)
        except Exception as e:  # noqa: BLE001
            print(f"[ERROR]   {item.id}: {e}")
            problems += 1
            continue
        if not res.rows:
            print(f"[EMPTY]   {item.id}: returns no rows (ok only if intended)")
    print("OK" if not problems else f"{problems} problem(s)")
    sys.exit(1 if problems else 0)


if __name__ == "__main__":
    main()
