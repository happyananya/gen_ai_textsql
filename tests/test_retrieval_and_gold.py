from pathlib import Path

import pytest

from smart_db_assistant.eval.gold import load_gold
from smart_db_assistant.representation import REPRESENTATIONS, documents, humanize
from smart_db_assistant.retrieval import LexicalScorer, SchemaSelector
from smart_db_assistant.schema import Column, ForeignKey, SchemaMetadata, Table
from smart_db_assistant.validator import validate_sql

ROOT = Path(__file__).resolve().parents[1]


def toy_schema() -> SchemaMetadata:
    return SchemaMetadata({
        "customers": Table("customers", [Column("customer_id", "integer"),
                                         Column("state", "text", samples=["California", "Texas"])],
                           comment="People who have an account"),
        "orders": Table("orders", [Column("order_id", "integer"), Column("customer_id", "integer"),
                                   Column("order_date", "timestamp")],
                        foreign_keys=[ForeignKey("customer_id", "customers", "customer_id")]),
        "reviews": Table("reviews", [Column("review_id", "integer"), Column("rating", "integer")],
                         comment="Customer product reviews, 1-5 stars"),
    })


def test_humanize():
    assert humanize("signup_date") == "signup date"
    assert humanize("CustomerID") == "customer id"
    assert humanize("concert_singer__singer_in_concert") == "singer in concert"


@pytest.mark.parametrize("repr_", REPRESENTATIONS)
def test_representations_grow(repr_):
    docs = documents(toy_schema().tables["customers"], repr_, "table")
    assert len(docs) == 1 and "customers" in docs[0]
    assert ("California" in docs[0]) == (repr_ == "samples")


def test_column_granularity_one_doc_per_column():
    docs = documents(toy_schema().tables["orders"], "described", "column")
    assert len(docs) == 1 + 3


def test_sample_values_help_retrieval():
    schema = toy_schema()
    q = "How many people live in California?"
    with_samples = LexicalScorer(schema, "samples").scores(q)
    without = LexicalScorer(schema, "columns").scores(q)
    assert with_samples["customers"] > without["customers"]


def test_selector_ranks_and_expands():
    sel = SchemaSelector(toy_schema(), LexicalScorer(toy_schema()), k=1, expansion="fk1")
    s = sel.select("how many orders last month")
    assert s.ranked == ["orders"] and s.connectors == ["customers"]
    s = SchemaSelector(toy_schema(), LexicalScorer(toy_schema()), k=1, expansion="none") \
        .select("average star rating")
    assert s.tables == ["reviews"]


def test_gold_set_loads_and_parses():
    items = load_gold(ROOT / "eval" / "gold")
    assert {i.tier for i in items} == {"easy", "medium", "hard", "adversarial"}
    for i in items:
        if i.expected == "answer":
            assert validate_sql(i.gold_sql, row_limit=None).ok, i.id
