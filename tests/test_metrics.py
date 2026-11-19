from decimal import Decimal

from smart_db_assistant.eval.metrics import (
    rows_match,
    rows_match_lenient,
    summarize,
)


def test_order_insensitive_by_default():
    assert rows_match([("a", 1), ("b", 2)], [("b", 2), ("a", 1)])
    assert not rows_match([("a", 1), ("b", 2)], [("b", 2), ("a", 1)], order_matters=True)


def test_numeric_normalisation_and_column_order():
    assert rows_match([(Decimal("10.004"), "x")], [("x", 10.0)])


def test_duplicates_count():
    assert not rows_match([("a",), ("a",)], [("a",)])


def test_lenient_allows_extra_columns():
    pred = [(1, "Widget", 99.5), (2, "Gadget", 10)]
    gold = [("Gadget", 10), ("Widget", 99.5)]
    assert not rows_match(pred, gold)
    assert rows_match_lenient(pred, gold)
    assert not rows_match_lenient([(1, "Widget")], gold)


def test_summarize_shapes():
    base = {"retrieval_recall": None, "first_attempt_valid": True, "retries": 0, "latency_ms": 100,
                "input_tokens": 10, "output_tokens": 5, "cost_usd": 0.001}
    results = [
        dict(base, tier="easy", expected="answer", exec_match=True, exec_match_lenient=True,
             valid_sql=True, safe_refusal=None),
        dict(base, tier="easy", expected="answer", exec_match=False, exec_match_lenient=True,
             valid_sql=True, safe_refusal=None),
        dict(base, tier="adversarial", expected="refuse", exec_match=None,
             exec_match_lenient=None, valid_sql=None, safe_refusal=True),
    ]
    s = summarize(results)
    assert s["execution_accuracy"] == 0.5
    assert s["execution_accuracy_lenient"] == 1.0
    assert s["safe_refusal_rate"] == 1.0
    assert s["by_tier"]["easy"]["n"] == 2
