"""How a table is turned into text for retrieval (experiment 2).

Representations, from least to most information:

    name        "singer"
    columns     "singer: singer id, name, country, age"
    described   + column types, table/column descriptions and FK relationships
    samples     + a few example values per column ("country e.g. France, USA")

Granularity:

    table       one document per table
    column      one document per column (+ one for the table name); a table's
                score is the max over its documents. Helps when the question
                mentions a single column buried in a wide table.

Identifiers are humanised (snake_case / camelCase -> words) in every variant,
because neither embeddings nor token overlap match "signup_date" to
"signed up".
"""

from __future__ import annotations

import re

from .schema import Column, Table

REPRESENTATIONS = ("name", "columns", "described", "samples")
GRANULARITIES = ("table", "column")

_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")


def humanize(identifier: str) -> str:
    """'signup_date' -> 'signup date', 'CustomerID' -> 'customer id'."""
    if "__" in identifier:                       # pooled names: db__table -> table
        identifier = identifier.split("__", 1)[1]
    s = _CAMEL.sub(" ", identifier).replace("_", " ")
    return re.sub(r"\s+", " ", s).strip().lower()


def _column_text(c: Column, repr_: str) -> str:
    text = humanize(c.name)
    if repr_ in ("described", "samples"):
        text += f" ({c.data_type})"
        if c.comment and c.comment.lower() != humanize(c.name):
            text += f": {c.comment}"
    if repr_ == "samples" and c.samples:
        text += " e.g. " + ", ".join(c.samples[:3])
    return text


def table_document(t: Table, repr_: str = "described") -> str:
    if repr_ not in REPRESENTATIONS:
        raise ValueError(f"unknown representation {repr_!r}; choose from {REPRESENTATIONS}")
    name = humanize(t.name)
    if repr_ == "name":
        return name
    cols = "; ".join(_column_text(c, repr_) for c in t.columns)
    if repr_ == "columns":
        return f"{name}: {cols}"
    parts = [f"Table {name}."]
    if t.comment:
        parts.append(t.comment)
    parts.append(f"Columns: {cols}.")
    if t.foreign_keys:
        rels = "; ".join(f"{humanize(fk.column)} references {humanize(fk.ref_table)}"
                         for fk in t.foreign_keys)
        parts.append(f"Links: {rels}.")
    return " ".join(parts)


def column_documents(t: Table, repr_: str = "described") -> list[str]:
    """Documents for column granularity: the table itself plus one per column."""
    name = humanize(t.name)
    head = table_document(t, "name") if repr_ in ("name", "columns") else (
        f"Table {name}. {t.comment or ''}".strip())
    if repr_ == "name":
        return [head]
    return [head] + [f"{name} {_column_text(c, repr_)}" for c in t.columns]


def documents(t: Table, repr_: str, granularity: str) -> list[str]:
    if granularity == "table":
        return [table_document(t, repr_)]
    if granularity == "column":
        return column_documents(t, repr_)
    raise ValueError(f"unknown granularity {granularity!r}; choose from {GRANULARITIES}")
