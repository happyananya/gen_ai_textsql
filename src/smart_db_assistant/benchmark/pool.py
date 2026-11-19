"""Schema pooling: simulate a large enterprise schema from many benchmark databases.

For a question about database D, the pooled schema contains every table of D
plus whole distractor databases (picked deterministically per D) until the
pool reaches `n_tables`. Questions and gold SQL stay valid on D's own SQLite
file; the other tables are noise the selector has to ignore.

Table names are prefixed `<db_id>__<table>` because table names collide
across databases (many Spider DBs have `student`, `people`, ...). Before
execution the target prefix is stripped; using a table from another database
fails like a real "no such table" error and is recorded as `distractor_table`.
"""

from __future__ import annotations

import random
import re
import sqlite3
from copy import deepcopy

import sqlglot
from sqlglot import exp

from ..db import QueryResult, SQLiteBackend
from ..schema import ForeignKey, SchemaMetadata

SEP = "__"


def prefixed(schema: SchemaMetadata, db_id: str) -> dict:
    out = {}
    for t in schema.tables.values():
        t2 = deepcopy(t)
        t2.name = f"{db_id}{SEP}{t.name}"
        t2.db_id = db_id
        t2.foreign_keys = [ForeignKey(fk.column, f"{db_id}{SEP}{fk.ref_table}", fk.ref_column)
                           for fk in t.foreign_keys]
        out[t2.name] = t2
    return out


def build_pool(target: str, schemas: dict[str, SchemaMetadata], n_tables: int = 0,
               seed: int = 0) -> tuple[SchemaMetadata, list[str]]:
    """Return (pooled schema, db_ids in the pool). n_tables=0 -> target database only."""
    tables = prefixed(schemas[target], target)
    dbs = [target]
    others = sorted(d for d in schemas if d != target)
    random.Random(f"{seed}:{target}").shuffle(others)
    for d in others:
        if len(tables) >= n_tables:
            break
        tables.update(prefixed(schemas[d], d))
        dbs.append(d)
    return SchemaMetadata(tables), dbs


def to_prefixed(db_id: str, tables: list[str], pool: SchemaMetadata) -> list[str]:
    lookup = {k.lower(): k for k in pool.tables}
    return [lookup[f"{db_id}{SEP}{t}".lower()] for t in tables if f"{db_id}{SEP}{t}".lower() in lookup]


def unprefix(db_id: str, tables: list[str]) -> list[str]:
    p = f"{db_id}{SEP}".lower()
    return [t[len(p):] if t.lower().startswith(p) else t for t in tables]


class PooledBackend:
    """Executes pooled-schema SQL against the target database."""

    dialect = "sqlite"

    def __init__(self, target: str, backend: SQLiteBackend):
        self.target = target
        self.inner = backend
        self._prefix = re.compile(rf"\b{re.escape(target)}{SEP}", re.IGNORECASE)
        self.distractors_used: list[str] = []

    def schema(self) -> SchemaMetadata:
        raise NotImplementedError("pass the pooled schema explicitly")

    def foreign_tables(self, sql: str) -> list[str]:
        try:
            tree = sqlglot.parse_one(sql, read="sqlite")
        except Exception:  # noqa: BLE001
            return []
        p = f"{self.target}{SEP}".lower()
        return sorted({t.name for t in tree.find_all(exp.Table)
                       if SEP in t.name and not t.name.lower().startswith(p)})

    def to_target_sql(self, sql: str) -> str:
        return self._prefix.sub("", sql)

    def execute(self, sql: str, *, timeout_ms: int = 10000, max_rows: int = 500) -> QueryResult:
        foreign = self.foreign_tables(sql)
        if foreign:
            self.distractors_used.extend(foreign)
            raise sqlite3.OperationalError(f"no such table: {foreign[0]}")
        return self.inner.execute(self.to_target_sql(sql), timeout_ms=timeout_ms, max_rows=max_rows)
