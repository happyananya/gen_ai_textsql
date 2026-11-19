"""Benchmark runner: schema selection x schema size on Spider / BIRD.

Examples
--------
Retrieval only (no LLM calls, free) - experiment 2 and the selection half of 1:

    sda-bench --data spider --pool-tables 100 --method rag --expansion joinpath --retrieval-only

Full runs (billed LLM calls):

    sda-bench --data spider --pool-tables 100 --method full     --sample 200 --tag full-100
    sda-bench --data spider --pool-tables 100 --method rag --k 4 --expansion none     --sample 200
    sda-bench --data spider --pool-tables 100 --method rag --k 4 --expansion joinpath --sample 200
    sda-bench --data spider --pool-tables 100 --method oracle   --sample 200   # upper bound

`--data spider` expects data/spider/{dev.json,tables.json,database/}; `--data bird` expects
data/bird/{dev.json,dev_tables.json,dev_databases/}. Override with --questions/--tables/--db-root.

Output: experiments/results/<tag>/{items.jsonl, summary.json, errors.csv, traces.jsonl}
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

from ..config import get_settings
from ..db import SQLiteBackend
from ..eval.errors import categorize, parse_parts, schema_coverage
from ..eval.metrics import rows_match
from ..llm import make_client
from ..pipeline import Assistant
from ..retrieval import FullSchemaSelector, OracleSelector, SchemaSelector, make_scorer
from .datasets import BenchItem, attach_samples, db_path, load_questions, load_schemas
from .pool import PooledBackend, build_pool, to_prefixed, unprefix

BENCH_SYSTEM_PROMPT = """You write SQLite queries that answer questions about a database.

Schema (table names are prefixed with their database name; use them exactly as written):
{schema}

Rules:
- Return a single SELECT statement (CTEs allowed) in "sql" and set action="sql".
- Use only the tables and columns shown. Join tables through the foreign keys shown.
- Select only the columns the question asks for, in the order it asks for them.
- If a hint is given, follow it.
"""

DEFAULT_PATHS = {
    "spider": ("data/spider/dev.json", "data/spider/tables.json", ["data/spider/database"]),
    "bird": ("data/bird/dev.json", "data/bird/dev_tables.json", ["data/bird/dev_databases"]),
}


def _bucket(n: int) -> str:
    return str(n) if n < 4 else "4+"


def summarize_bench(results: list[dict]) -> dict:
    def rate(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 4) if xs else None

    def mean(xs):
        xs = [x for x in xs if x is not None]
        return round(sum(xs) / len(xs), 2) if xs else None

    out = {
        "n": len(results),
        "execution_accuracy": rate([r.get("exec_match") for r in results]),
        "valid_sql_rate": rate([r.get("valid_sql") for r in results]),
        "schema_recall": rate([r["schema_recall"] for r in results]),
        "schema_precision": rate([r["schema_precision"] for r in results]),
        "schema_complete_rate": rate([r["schema_complete"] for r in results]),
        "mean_selected_tables": mean([r["n_selected"] for r in results]),
        "mean_connectors_added": mean([len(r["connector_tables"]) for r in results]),
        "mean_pool_tables": mean([r["pool_tables"] for r in results]),
        "mean_input_tokens": mean([r.get("input_tokens") for r in results]),
        "mean_latency_ms": mean([r.get("latency_ms") for r in results]),
        "total_cost_usd": round(sum(r.get("cost_usd") or 0 for r in results), 4),
        "first_attempt_valid": rate([r.get("first_attempt_valid") for r in results]),
        "categories": dict(Counter(r["category"] for r in results if r.get("category"))),
    }
    for key, label in (("n_gold_tables", "by_gold_tables"), ("has_bridge", "by_has_bridge")):
        groups: dict[str, list[dict]] = defaultdict(list)
        for r in results:
            k = _bucket(r[key]) if key == "n_gold_tables" else str(r[key])
            groups[k].append(r)
        out[label] = {k: {"n": len(v),
                          "execution_accuracy": rate([r.get("exec_match") for r in v]),
                          "schema_complete_rate": rate([r["schema_complete"] for r in v])}
                      for k, v in sorted(groups.items())}
    return out


def main() -> None:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    p.add_argument("--data", choices=["spider", "bird"], default="spider")
    p.add_argument("--questions")
    p.add_argument("--tables", action="append",
                   help="tables.json; repeat to add distractor databases (e.g. Spider train)")
    p.add_argument("--db-root", action="append")
    p.add_argument("--pool-tables", type=int, default=0,
                   help="pooled schema size; 0 = only the question's own database")
    p.add_argument("--method", choices=["full", "rag", "oracle"], default="rag")
    p.add_argument("--retriever", choices=["lexical", "embedding"], default="lexical")
    p.add_argument("--repr", choices=["name", "columns", "described", "samples"],
                   default="described")
    p.add_argument("--granularity", choices=["table", "column"], default="table")
    p.add_argument("--k", type=int, default=4)
    p.add_argument("--expansion", choices=["none", "fk1", "joinpath"], default="joinpath")
    p.add_argument("--retrieval-only", action="store_true", help="score selection only, no LLM")
    p.add_argument("--sample", type=int, help="random subset of questions (seeded)")
    p.add_argument("--min-gold-tables", type=int, default=0)
    p.add_argument("--limit", type=int)
    p.add_argument("--seed", type=int, default=0)
    p.add_argument("--model")
    p.add_argument("--effort")
    p.add_argument("--max-retries", type=int)
    p.add_argument("--tag")
    p.add_argument("--out", default="experiments/results")
    args = p.parse_args()

    q_path, t_path, roots = DEFAULT_PATHS[args.data]
    q_path = args.questions or q_path
    t_paths = args.tables or [t_path]
    roots = [Path(r) for r in (args.db_root or roots)]

    schemas = {}
    for tp in t_paths:
        schemas.update(load_schemas(tp, roots))
    items: list[BenchItem] = load_questions(q_path)

    def gold_n(it: BenchItem) -> int:
        return len(parse_parts(it.gold_sql, dialect="sqlite").tables)

    if args.min_gold_tables:
        items = [i for i in items if gold_n(i) >= args.min_gold_tables]
    if args.sample and args.sample < len(items):
        items = random.Random(args.seed).sample(items, args.sample)
    if args.limit:
        items = items[: args.limit]

    method_desc = args.method if args.method != "rag" else (
        f"rag-{args.retriever}-{args.repr}-{args.granularity}-k{args.k}-{args.expansion}")
    tag = args.tag or f"{time.strftime('%Y%m%d-%H%M%S')}-{args.data}-p{args.pool_tables}-{method_desc}"
    out = Path(args.out) / tag
    out.mkdir(parents=True, exist_ok=True)

    overrides = {k: v for k, v in {"model": args.model, "effort": args.effort,
                                   "max_retries": args.max_retries}.items() if v is not None}
    settings = get_settings(**overrides, pipeline=args.method, row_limit=0, trace_dir=out)
    llm = None if args.retrieval_only else make_client(settings.model, settings.effort)

    pools: dict[str, tuple] = {}
    backends: dict[str, SQLiteBackend] = {}
    scorers: dict[str, object] = {}
    results: list[dict] = []

    for n, item in enumerate(items, 1):
        rec: dict = {"id": item.id, "db_id": item.db_id, "question": item.question,
                     "gold_sql": item.gold_sql, "difficulty": item.difficulty}
        try:
            if item.db_id not in pools:
                pool, dbs = build_pool(item.db_id, schemas, args.pool_tables, args.seed)
                if args.repr == "samples":
                    attach_samples(pool, roots)
                pools[item.db_id] = (pool, dbs)
            pool, dbs = pools[item.db_id]

            gparts = parse_parts(item.gold_sql, schemas[item.db_id].column_names(), "sqlite")
            gold_tabs = sorted(gparts.tables)
            rec.update(n_gold_tables=len(gold_tabs), gold_tables=gold_tabs,
                       bridge_tables=sorted(gparts.bridge_tables),
                       has_bridge=bool(gparts.bridge_tables), pool_tables=len(pool),
                       pool_dbs=len(dbs))

            if args.method == "full":
                selector = FullSchemaSelector(pool)
            elif args.method == "oracle":
                selector = OracleSelector(to_prefixed(item.db_id, gold_tabs, pool))
            else:
                if item.db_id not in scorers:
                    scorers[item.db_id] = make_scorer(args.retriever, pool, args.repr,
                                                      args.granularity)
                selector = SchemaSelector(pool, scorers[item.db_id], args.k, args.expansion)

            if args.retrieval_only:
                sel = selector.select(item.prompt_question)
                selected, connectors, ranked = sel.tables, sel.connectors, sel.ranked
            else:
                if item.db_id not in backends:
                    backends[item.db_id] = SQLiteBackend(db_path(item.db_id, roots))
                pooled_be = PooledBackend(item.db_id, backends[item.db_id])
                assistant = Assistant(settings, llm=llm, backend=pooled_be, schema=pool,
                                      selector=selector, system_prompt=BENCH_SYSTEM_PROMPT,
                                      trace_file="traces.jsonl")
                ans = assistant.ask(item.prompt_question, summarize=False, question_id=item.id)
                t = ans.trace
                selected = t.retrieved_tables
                connectors = t.extra.get("connector_tables", [])
                ranked = t.extra.get("ranked_tables", [])
                pred_sql = pooled_be.to_target_sql(ans.sql) if ans.sql else None
                last_err = next((a.db_error or "; ".join(a.validation_errors)
                                 for a in reversed(t.attempts)
                                 if a.db_error or a.validation_errors), None)

                gold = backends[item.db_id].execute(item.gold_sql, timeout_ms=60_000,
                                                    max_rows=100_000)
                exec_match = (ans.action == "sql" and ans.result is not None
                              and rows_match(ans.result.rows, gold.rows,
                                             order_matters="order by" in item.gold_sql.lower()))
                rec.update(
                    action=ans.action, pred_sql=pred_sql, exec_match=bool(exec_match),
                    valid_sql=ans.action == "sql", last_error=last_err,
                    distractor_tables_used=sorted(set(pooled_be.distractors_used)),
                    first_attempt_valid=bool(t.attempts) and t.attempts[0].valid
                    and t.attempts[0].db_error is None,
                    retries=max(len(t.attempts) - 1, 0), input_tokens=t.input_tokens,
                    output_tokens=t.output_tokens, latency_ms=t.total_latency_ms,
                    cost_usd=t.cost_usd,
                )
                rec["category"] = categorize(
                    exec_match=exec_match, action=ans.action, pred_sql=pred_sql,
                    gold_sql=item.gold_sql, selected=unprefix(item.db_id, selected),
                    db_error=None if ans.action == "sql" else last_err,
                    distractor_tables=rec["distractor_tables_used"],
                    columns=schemas[item.db_id].column_names(),
                )

            sel_target = unprefix(item.db_id, selected)
            rec.update(selected_tables=selected, ranked_tables=ranked,
                       connector_tables=connectors, **schema_coverage(sel_target, gold_tabs))
            # precision must count distractor-db tables as wrong picks
            rec["schema_precision"] = (round(sum(t.lower() in gold_tabs for t in sel_target)
                                             / len(selected), 4) if selected else None)
        except Exception as e:  # noqa: BLE001 - one bad item must not kill the run
            rec.update(error=f"{type(e).__name__}: {e}", category="harness_error",
                       n_gold_tables=rec.get("n_gold_tables", 0),
                       has_bridge=rec.get("has_bridge", False),
                       pool_tables=rec.get("pool_tables"), schema_recall=None,
                       schema_precision=None, schema_complete=None, n_selected=None,
                       connector_tables=[])
        results.append(rec)
        status = rec.get("category") or (
            "complete" if rec.get("schema_complete") else f"missing {rec.get('missing_tables')}")
        print(f"[{n}/{len(items)}] {item.db_id:<28} gold={rec.get('n_gold_tables')} "
              f"sel={rec.get('n_selected')} {status}")

    with (out / "items.jsonl").open("w") as f:
        for r in results:
            f.write(json.dumps(r, default=str) + "\n")

    summary = summarize_bench(results)
    summary["config"] = {**vars(args), "model": settings.model, "effort": settings.effort,
                         "max_retries": settings.max_retries, "n_distractor_sources": len(schemas)}
    (out / "summary.json").write_text(json.dumps(summary, indent=2, default=str))

    if not args.retrieval_only:
        with (out / "errors.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["id", "db_id", "n_gold_tables", "category", "manual_category", "notes",
                        "question", "gold_sql", "pred_sql", "missing_tables", "last_error"])
            for r in results:
                if r.get("category") not in ("correct", None):
                    w.writerow([r["id"], r["db_id"], r.get("n_gold_tables"), r["category"], "", "",
                                r["question"], r["gold_sql"], r.get("pred_sql"),
                                r.get("missing_tables"), r.get("last_error") or r.get("error")])

    print(json.dumps({k: v for k, v in summary.items() if k != "config"}, indent=2))
    print(f"\nResults in {out}")


if __name__ == "__main__":
    main()
