"""Load Spider / BIRD: schemas from tables.json, questions from dev.json.

Both datasets share the tables.json format:
    db_id, table_names_original, column_names_original [[table_idx, name]],
    column_names (human-readable), column_types, primary_keys, foreign_keys [[col, ref_col]]

Questions:
    Spider dev.json: {db_id, question, query}
    BIRD   dev.json: {question_id, db_id, question, evidence, SQL, difficulty}

Databases: <db_root>/<db_id>/<db_id>.sqlite (both datasets).
BIRD column descriptions: <db_root>/<db_id>/database_description/<table>.csv
"""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from pathlib import Path

from ..db import SQLiteBackend
from ..representation import humanize
from ..schema import Column, ForeignKey, SchemaMetadata, Table


@dataclass
class BenchItem:
    id: str
    db_id: str
    question: str
    gold_sql: str
    evidence: str = ""
    difficulty: str = ""

    @property
    def prompt_question(self) -> str:
        return f"{self.question}\nHint: {self.evidence}" if self.evidence else self.question


def load_questions(path: str | Path) -> list[BenchItem]:
    data = json.loads(Path(path).read_text())
    items = []
    for i, d in enumerate(data):
        items.append(BenchItem(
            id=str(d.get("question_id", i)),
            db_id=d["db_id"],
            question=d["question"],
            gold_sql=d.get("query") or d.get("SQL") or "",
            evidence=d.get("evidence", "") or "",
            difficulty=d.get("difficulty", "") or "",
        ))
    return items


def _bird_descriptions(db_dir: Path, table: str) -> dict[str, str]:
    f = db_dir / "database_description" / f"{table}.csv"
    if not f.exists():
        return {}
    for enc in ("utf-8-sig", "latin-1"):
        try:
            rows = list(csv.DictReader(f.read_text(encoding=enc).splitlines()))
            break
        except UnicodeDecodeError:
            continue
    else:
        return {}
    out = {}
    for r in rows:
        name = (r.get("original_column_name") or "").strip()
        desc = " ".join(x.strip() for x in (r.get("column_description"), r.get("value_description"))
                        if x and x.strip())
        if name and desc:
            out[name.lower()] = desc[:200]
    return out


def load_schemas(tables_json: str | Path, db_roots: list[Path] | None = None
                 ) -> dict[str, SchemaMetadata]:
    schemas: dict[str, SchemaMetadata] = {}
    for db in json.loads(Path(tables_json).read_text()):
        db_id = db["db_id"]
        tnames = db["table_names_original"]
        cols = db["column_names_original"]
        nat_cols = db.get("column_names") or cols
        types = db.get("column_types") or ["text"] * len(cols)
        nat_tables = db.get("table_names") or tnames
        db_dir = find_db_dir(db_id, db_roots) if db_roots else None

        tables = []
        for ti, t in enumerate(tnames):
            comment = nat_tables[ti] if humanize(t) != nat_tables[ti].lower() else None
            tables.append(Table(t, comment=comment, db_id=db_id))
        descs = {ti: (_bird_descriptions(db_dir, t) if db_dir else {}) for ti, t in enumerate(tnames)}
        for ci, (ti, cname) in enumerate(cols):
            if ti < 0:
                continue
            nat = nat_cols[ci][1]
            comment = descs[ti].get(cname.lower()) or (nat if humanize(cname) != nat.lower() else None)
            tables[ti].columns.append(Column(cname, types[ci], True, comment))
        for pk in db.get("primary_keys", []):
            for ci in (pk if isinstance(pk, list) else [pk]):
                ti, cname = cols[ci]
                tables[ti].primary_key.append(cname)
        for c1, c2 in db.get("foreign_keys", []):
            (t1, n1), (t2, n2) = cols[c1], cols[c2]
            tables[t1].foreign_keys.append(ForeignKey(n1, tnames[t2], n2))
        schemas[db_id] = SchemaMetadata({t.name: t for t in tables})
    return schemas


def find_db_dir(db_id: str, db_roots: list[Path]) -> Path | None:
    for root in db_roots:
        if (Path(root) / db_id).is_dir():
            return Path(root) / db_id
    return None


def db_path(db_id: str, db_roots: list[Path]) -> Path:
    d = find_db_dir(db_id, db_roots)
    if d is None or not (d / f"{db_id}.sqlite").exists():
        raise FileNotFoundError(f"{db_id}.sqlite not found under {db_roots}")
    return d / f"{db_id}.sqlite"


def attach_samples(schema: SchemaMetadata, db_roots: list[Path], n: int = 3,
                   _cache: dict = {}) -> None:  # noqa: B006 - deliberate process-wide cache
    """Fill Column.samples from the SQLite files (for the 'samples' representation)."""
    for t in schema.tables.values():
        key = (t.db_id, t.name)
        if key not in _cache:
            try:
                be = SQLiteBackend(db_path(t.db_id, db_roots))
                _cache[key] = {c.name: be.column_samples(t.name, c.name, n) for c in t.columns}
            except FileNotFoundError:
                _cache[key] = {}
        for c in t.columns:
            c.samples = _cache[key].get(c.name, [])
