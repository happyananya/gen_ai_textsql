"""Run the gold set through a pipeline configuration and score it.

    sda-eval --pipeline baseline
    sda-eval --pipeline rag --top-k 3 --tier hard --limit 10
    sda-eval --pipeline rag --model claude-sonnet-5-5 --tag sonnet-rag

Writes experiments/results/<tag>/{items.jsonl, summary.json, traces.jsonl}.
Each run makes billed LLM calls (about 1-2 per item, more with retries).
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from ..config import get_settings
from ..db import PostgresBackend
from ..pipeline import Assistant
from .errors import categorize, gold_tables, schema_coverage
from .gold import GoldItem, load_gold
from .metrics import rows_match, rows_match_lenient, summarize


def evaluate_item(assistant: Assistant, item: GoldItem) -> dict:
    ans = assistant.ask(item.question, summarize=False, question_id=item.id)
    t = ans.trace
    gold_tabs = item.gold_tables or (
        gold_tables(item.gold_sql, dialect="postgres") if item.gold_sql else [])
    coverage = schema_coverage(t.retrieved_tables, gold_tabs)
    rec = {
        "id": item.id, "tier": item.tier, "expected": item.expected, "question": item.question,
        "action": ans.action, "pred_sql": ans.sql, "gold_sql": item.gold_sql,
        "message": ans.message,
        "exec_match": None, "exec_match_lenient": None, "valid_sql": None, "safe_refusal": None,
        "retrieved_tables": t.retrieved_tables,
        "retrieval_recall": coverage["schema_recall"],
        "schema_precision": coverage["schema_precision"],
        "schema_complete": coverage["schema_complete"],
        "connector_tables": t.extra.get("connector_tables", []),
        "category": None,
        "first_attempt_valid": bool(t.attempts) and t.attempts[0].valid
                               and t.attempts[0].db_error is None,
        "retries": max(len(t.attempts) - 1, 0),
        "guardrail_fired": bool(t.extra.get("guardrail_fired")),
        "latency_ms": t.total_latency_ms, "input_tokens": t.input_tokens,
        "output_tokens": t.output_tokens, "cost_usd": t.cost_usd, "error": None,
    }
    if item.expected == "answer":
        rec["valid_sql"] = ans.action == "sql"
        if ans.action == "sql":
            gold = assistant.backend.execute(item.gold_sql, max_rows=100_000)
            pred = ans.result
            if pred.truncated:
                rec["error"] = "prediction truncated by row limit"
            rec["exec_match"] = rows_match(pred.rows, gold.rows, item.order_matters)
            rec["exec_match_lenient"] = rows_match_lenient(pred.rows, gold.rows, item.order_matters)
        else:
            rec["exec_match"] = rec["exec_match_lenient"] = False
        last_err = next((a.db_error for a in reversed(t.attempts) if a.db_error), None)
        rec["category"] = categorize(
            exec_match=rec["exec_match"], action=ans.action, pred_sql=ans.sql,
            gold_sql=item.gold_sql, selected=t.retrieved_tables,
            db_error=None if ans.action == "sql" else last_err,
            columns=assistant.schema.column_names(), dialect="postgres")
    else:
        rec["safe_refusal"] = ans.action in ("refuse", "clarify")
    return rec


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--gold", default="eval/gold")
    p.add_argument("--pipeline", choices=["baseline", "rag"], default=None)
    p.add_argument("--model", default=None)
    p.add_argument("--effort", default=None)
    p.add_argument("--top-k", type=int, default=None)
    p.add_argument("--tier", action="append", help="restrict to tier(s)")
    p.add_argument("--limit", type=int, default=None)
    p.add_argument("--tag", default=None)
    p.add_argument("--out", default="experiments/results")
    args = p.parse_args()

    overrides = {k: v for k, v in {
        "pipeline": args.pipeline, "model": args.model, "effort": args.effort,
        "rag_top_k": args.top_k}.items() if v is not None}
    tag = args.tag or time.strftime("%Y%m%d-%H%M%S") + f"-{overrides.get('pipeline', 'default')}"
    out = Path(args.out) / tag
    out.mkdir(parents=True, exist_ok=True)
    settings = get_settings(**overrides, trace_dir=out)

    items = load_gold(args.gold)
    if args.tier:
        items = [i for i in items if i.tier in args.tier]
    if args.limit:
        items = items[: args.limit]

    assistant = Assistant(settings, backend=PostgresBackend(settings), trace_file="traces.jsonl")

    results = []
    with (out / "items.jsonl").open("w") as f:
        for n, item in enumerate(items, 1):
            try:
                rec = evaluate_item(assistant, item)
            except Exception as e:  # noqa: BLE001 - one bad item must not kill the run
                rec = {"id": item.id, "tier": item.tier, "expected": item.expected,
                       "error": f"{type(e).__name__}: {e}", "exec_match": False,
                       "exec_match_lenient": False, "valid_sql": False,
                       "safe_refusal": False if item.expected != "answer" else None,
                       "retrieval_recall": None, "first_attempt_valid": False, "retries": 0,
                       "latency_ms": None, "input_tokens": None, "output_tokens": None,
                       "cost_usd": 0.0}
            results.append(rec)
            f.write(json.dumps(rec, default=str) + "\n")
            mark = {True: "PASS", False: "FAIL", None: "-"}[
                rec["exec_match"] if item.expected == "answer" else rec["safe_refusal"]]
            print(f"[{n}/{len(items)}] {item.id:<6} {item.tier:<11} {mark}")

    report = summarize(results)
    report["config"] = {"pipeline": settings.pipeline, "model": settings.model,
                        "effort": settings.effort, "rag_top_k": settings.rag_top_k,
                        "max_retries": settings.max_retries, "gold": args.gold}
    (out / "summary.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))
    print(f"\nResults in {out}")


if __name__ == "__main__":
    main()
