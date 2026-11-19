"""Outcome and trajectory metrics.

Owner: Ananya.

Outcome
- execution accuracy : generated result rows == gold rows (multiset; order only
                       when the item sets `order_matters`). Two variants:
                       `strict`  - same columns, same rows
                       `lenient` - prediction may carry extra columns (e.g. an
                                   id next to a name) as long as the gold
                                   columns can be found in it
- valid SQL rate     : SQL parsed, passed validation and executed
- safe refusal       : adversarial/unanswerable items refused or clarified

Trajectory
- schema coverage    : see errors.py (recall / precision / completeness)
- first-attempt valid, number of self-correction retries, guardrail fired
"""

from __future__ import annotations

import datetime as dt
from collections import Counter
from collections.abc import Sequence
from decimal import Decimal
from typing import Any

FLOAT_DIGITS = 2


def normalize_value(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float, Decimal)):
        f = round(float(v), FLOAT_DIGITS)
        return int(f) if f.is_integer() else f
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, str):
        return v.strip()
    return str(v)


def _norm_rows(rows: Sequence[Sequence[Any]]) -> list[tuple]:
    return [tuple(normalize_value(v) for v in r) for r in rows]


def _key(v: Any) -> tuple:
    return (v is None, str(type(v)), str(v))


def rows_match(pred: Sequence[Sequence[Any]], gold: Sequence[Sequence[Any]],
               order_matters: bool = False) -> bool:
    """Strict match, insensitive to column order."""
    p, g = _norm_rows(pred), _norm_rows(gold)
    if len(p) != len(g):
        return False
    p = [tuple(sorted(r, key=_key)) for r in p]
    g = [tuple(sorted(r, key=_key)) for r in g]
    return p == g if order_matters else Counter(p) == Counter(g)


def rows_match_lenient(pred: Sequence[Sequence[Any]], gold: Sequence[Sequence[Any]],
                       order_matters: bool = False) -> bool:
    """Gold columns must appear (by value) in the prediction; extra columns are allowed."""
    p, g = _norm_rows(pred), _norm_rows(gold)
    if len(p) != len(g):
        return False
    if not g:
        return True
    n_pred, n_gold = len(p[0]), len(g[0])
    if n_pred < n_gold:
        return False

    def col(rows, i):
        vals = [r[i] for r in rows]
        return vals if order_matters else sorted(vals, key=_key)

    used: set[int] = set()
    mapping: list[int] = []
    for gi in range(n_gold):
        target = col(g, gi)
        match = next((pi for pi in range(n_pred) if pi not in used and col(p, pi) == target), None)
        if match is None:
            return False
        used.add(match)
        mapping.append(match)
    projected = [tuple(r[i] for i in mapping) for r in p]
    return projected == g if order_matters else Counter(projected) == Counter(g)


def summarize(results: list[dict]) -> dict:
    """Aggregate per-item results (see run_eval.evaluate_item) into a report."""

    def rate(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 2) if xs else None

    answerable = [r for r in results if r["expected"] == "answer"]
    adversarial = [r for r in results if r["expected"] != "answer"]
    report = {
        "n": len(results),
        "execution_accuracy": rate([r["exec_match"] for r in answerable]),
        "execution_accuracy_lenient": rate([r["exec_match_lenient"] for r in answerable]),
        "valid_sql_rate": rate([r["valid_sql"] for r in answerable]),
        "safe_refusal_rate": rate([r["safe_refusal"] for r in adversarial]),
        "retrieval_recall": rate([r["retrieval_recall"] for r in answerable]),
        "schema_complete_rate": rate([r.get("schema_complete") for r in answerable]),
        "categories": dict(Counter(r["category"] for r in answerable if r.get("category"))),
        "first_attempt_valid": rate([r["first_attempt_valid"] for r in answerable]),
        "mean_retries": mean([r["retries"] for r in results]),
        "mean_latency_ms": mean([r["latency_ms"] for r in results]),
        "mean_input_tokens": mean([r["input_tokens"] for r in results]),
        "mean_output_tokens": mean([r["output_tokens"] for r in results]),
        "total_cost_usd": round(sum(r["cost_usd"] for r in results), 4),
        "by_tier": {},
    }
    for tier in sorted({r["tier"] for r in results}):
        items = [r for r in results if r["tier"] == tier]
        ans = [r for r in items if r["expected"] == "answer"]
        report["by_tier"][tier] = {
            "n": len(items),
            "execution_accuracy": rate([r["exec_match"] for r in ans]),
            "valid_sql_rate": rate([r["valid_sql"] for r in ans]),
            "safe_refusal_rate": rate([r["safe_refusal"] for r in items if r["expected"] != "answer"]),
        }
    return report
