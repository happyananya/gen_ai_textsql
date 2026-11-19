"""Database backends: schema introspection + read-only execution.

- `PostgresBackend` - the Supabase e-commerce database (demo / sanity set).
- `SQLiteBackend`   - Spider / BIRD benchmark databases (opened read-only).
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .config import Settings, get_settings
from .schema import Column, ForeignKey, SchemaMetadata, Table, introspect


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    elapsed_ms: float
    truncated: bool = False


class Backend(Protocol):
    dialect: str

    def schema(self) -> SchemaMetadata: ...

    def execute(self, sql: str, *, timeout_ms: int = 10000, max_rows: int = 500) -> QueryResult: ...


# ---------------------------------------------------------------- Postgres --

def connect(settings: Settings | None = None):
    import psycopg

    settings = settings or get_settings()
    if not settings.database_url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return psycopg.connect(settings.database_url, autocommit=False)


def run_readonly(conn, sql: str, *, timeout_ms: int = 10000, max_rows: int = 500) -> QueryResult:
    """Execute inside a READ ONLY transaction that is always rolled back."""
    start = time.perf_counter()
    try:
        with conn.cursor() as cur:
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            cur.execute(sql)
            columns = [d.name for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1) if cur.description else []
    finally:
        conn.rollback()
    truncated = len(rows) > max_rows
    return QueryResult(columns, rows[:max_rows], (time.perf_counter() - start) * 1000, truncated)


class PostgresBackend:
    dialect = "postgres"

    def __init__(self, settings: Settings | None = None, conn=None):
        self.conn = conn or connect(settings)
        self._schema: SchemaMetadata | None = None

    def schema(self) -> SchemaMetadata:
        if self._schema is None:
            self._schema = introspect(self.conn)
        return self._schema

    def execute(self, sql: str, *, timeout_ms: int = 10000, max_rows: int = 500) -> QueryResult:
        return run_readonly(self.conn, sql, timeout_ms=timeout_ms, max_rows=max_rows)


# ------------------------------------------------------------------ SQLite --

class QueryTimeout(Exception):
    pass


class SQLiteBackend:
    dialect = "sqlite"

    def __init__(self, path: str | Path):
        self.path = Path(path)
        if not self.path.exists():
            raise FileNotFoundError(self.path)
        self.conn = sqlite3.connect(f"file:{self.path}?mode=ro", uri=True, check_same_thread=False)
        self.conn.text_factory = lambda b: b.decode("utf-8", errors="replace")
        self._schema: SchemaMetadata | None = None

    def schema(self) -> SchemaMetadata:
        if self._schema is None:
            self._schema = introspect_sqlite(self.conn)
        return self._schema

    def execute(self, sql: str, *, timeout_ms: int = 10000, max_rows: int = 500) -> QueryResult:
        start = time.perf_counter()
        deadline = start + timeout_ms / 1000

        def _check() -> int:
            return 1 if time.perf_counter() > deadline else 0

        self.conn.set_progress_handler(_check, 10_000)
        try:
            cur = self.conn.execute(sql)
            columns = [d[0] for d in cur.description] if cur.description else []
            rows = cur.fetchmany(max_rows + 1) if cur.description else []
        except sqlite3.OperationalError as e:
            if "interrupted" in str(e):
                raise QueryTimeout(f"query exceeded {timeout_ms} ms") from e
            raise
        finally:
            self.conn.set_progress_handler(None, 0)
        truncated = len(rows) > max_rows
        return QueryResult(columns, rows[:max_rows], (time.perf_counter() - start) * 1000, truncated)

    def column_samples(self, table: str, column: str, n: int = 3) -> list[str]:
        try:
            cur = self.conn.execute(
                f'SELECT DISTINCT "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL LIMIT {n}')
            return [str(r[0])[:40] for r in cur.fetchall()]
        except sqlite3.Error:
            return []


def introspect_sqlite(conn: sqlite3.Connection) -> SchemaMetadata:
    tables: dict[str, Table] = {}
    names = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
    for name in names:
        t = Table(name)
        for _cid, cname, ctype, notnull, _default, pk in conn.execute(f'PRAGMA table_info("{name}")'):
            t.columns.append(Column(cname, ctype or "text", not notnull))
            if pk:
                t.primary_key.append(cname)
        for row in conn.execute(f'PRAGMA foreign_key_list("{name}")'):
            ref_table, from_col, to_col = row[2], row[3], row[4]
            t.foreign_keys.append(ForeignKey(from_col, ref_table, to_col or from_col))
        tables[name] = t
    return SchemaMetadata(tables)
