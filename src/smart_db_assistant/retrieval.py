"""Schema selection: decide which tables go into the prompt.

Owner: Gurleen.

    selector = SchemaSelector(schema, LexicalScorer(schema, "described", "table"),
                              k=4, expansion="joinpath")
    sel = selector.select("Top 5 products by revenue last month")
    sel.tables       -> ranked tables + connector tables
    sel.connectors   -> tables added only to complete join paths

Scorers (how relevant is each table):
- `LexicalScorer`   - BM25-style token overlap; no dependencies.
- `EmbeddingScorer` - sentence-transformers cosine similarity
                      (`pip install -e '.[rag]'`). Embeddings are cached by
                      document text, so pooled schemas that share tables
                      don't re-encode them.

Representation and granularity of the documents: see representation.py.
Expansion strategy (none / fk1 / joinpath): see schema_graph.py.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from .representation import documents
from .schema import SchemaMetadata
from .schema_graph import expand

_TOKEN = re.compile(r"[a-z0-9]+")
_STOP = {"the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "by", "is", "are", "was",
         "were", "what", "which", "who", "how", "many", "much", "show", "list", "give", "me",
         "all", "each", "every", "that", "with", "from", "their", "do", "does", "did", "have",
         "has", "table", "column", "columns", "eg", "references", "links"}


def tokens(text: str) -> list[str]:
    out = []
    for t in _TOKEN.findall(text.lower()):
        if t in _STOP:
            continue
        if len(t) > 3 and t.endswith("ies"):
            t = t[:-3] + "y"
        elif len(t) > 3 and t.endswith("s") and not t.endswith("ss"):
            t = t[:-1]
        out.append(t)
    return out


class TableScorer(Protocol):
    def scores(self, question: str) -> dict[str, float]: ...


class LexicalScorer:
    def __init__(self, schema: SchemaMetadata, representation: str = "described",
                 granularity: str = "table", k1: float = 1.2, b: float = 0.75):
        self.k1, self.b = k1, b
        self.docs: list[tuple[str, Counter]] = [
            (name, Counter(tokens(d)))
            for name, t in schema.tables.items()
            for d in documents(t, representation, granularity)
        ]
        n = len(self.docs)
        df = Counter(tok for _, doc in self.docs for tok in doc)
        self.idf = {tok: math.log(1 + (n - c + 0.5) / (c + 0.5)) for tok, c in df.items()}
        self.avg_len = sum(sum(d.values()) for _, d in self.docs) / max(n, 1)
        self.tables = list(schema.tables)

    def scores(self, question: str) -> dict[str, float]:
        q = set(tokens(question))
        out = dict.fromkeys(self.tables, 0.0)
        for name, doc in self.docs:
            length = sum(doc.values()) or 1
            s = 0.0
            for tok in q & doc.keys():
                tf = doc[tok]
                s += self.idf[tok] * tf * (self.k1 + 1) / (
                    tf + self.k1 * (1 - self.b + self.b * length / self.avg_len))
            out[name] = max(out[name], s)
        return out


class Encoder:
    """Shared sentence-transformers model with a text -> vector cache."""

    _instances: ClassVar[dict[str, Encoder]] = {}

    def __init__(self, model_name: str):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(model_name)
        self.cache: dict[str, object] = {}

    @classmethod
    def get(cls, model_name: str = "all-MiniLM-L6-v2") -> Encoder:
        if model_name not in cls._instances:
            cls._instances[model_name] = cls(model_name)
        return cls._instances[model_name]

    def encode(self, texts: list[str]):
        import numpy as np

        missing = [t for t in dict.fromkeys(texts) if t not in self.cache]
        if missing:
            vecs = self.model.encode(missing, normalize_embeddings=True, batch_size=64)
            self.cache.update(zip(missing, vecs))
        return np.stack([self.cache[t] for t in texts])


class EmbeddingScorer:
    def __init__(self, schema: SchemaMetadata, representation: str = "described",
                 granularity: str = "table", model_name: str = "all-MiniLM-L6-v2"):
        self.encoder = Encoder.get(model_name)
        pairs = [(name, d) for name, t in schema.tables.items()
                 for d in documents(t, representation, granularity)]
        self.owners = [p[0] for p in pairs]
        self.matrix = self.encoder.encode([p[1] for p in pairs])
        self.tables = list(schema.tables)

    def scores(self, question: str) -> dict[str, float]:
        q = self.encoder.encode([question])[0]
        sims = self.matrix @ q
        out = dict.fromkeys(self.tables, -1.0)
        for owner, s in zip(self.owners, sims):
            out[owner] = max(out[owner], float(s))
        return out


def make_scorer(kind: str, schema: SchemaMetadata, representation: str = "described",
                granularity: str = "table") -> TableScorer:
    if kind == "lexical":
        return LexicalScorer(schema, representation, granularity)
    if kind == "embedding":
        return EmbeddingScorer(schema, representation, granularity)
    raise ValueError(f"unknown retriever {kind!r}; choose lexical or embedding")


@dataclass
class Selection:
    tables: list[str]                       # what goes into the prompt
    ranked: list[str] = field(default_factory=list)       # top-k before expansion
    connectors: list[str] = field(default_factory=list)   # added by join-path / fk1
    scores: dict[str, float] = field(default_factory=dict)


class SchemaSelector:
    def __init__(self, schema: SchemaMetadata, scorer: TableScorer | None, k: int = 4,
                 expansion: str = "joinpath"):
        self.schema, self.scorer, self.k, self.expansion = schema, scorer, k, expansion

    def select(self, question: str) -> Selection:
        scores = self.scorer.scores(question)
        ranked = [n for n, s in sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))][: self.k]
        exp_ = expand(self.schema, ranked, self.expansion)
        return Selection(exp_.tables, ranked, exp_.connectors, scores)


class FullSchemaSelector:
    """Baseline: every table."""

    def __init__(self, schema: SchemaMetadata):
        self.schema = schema

    def select(self, question: str) -> Selection:
        return Selection(list(self.schema.tables))


class OracleSelector:
    """Upper bound: exactly the gold tables. Isolates SQL-generation errors."""

    def __init__(self, gold_tables: list[str]):
        self.gold = gold_tables

    def select(self, question: str) -> Selection:
        return Selection(list(self.gold), list(self.gold))
