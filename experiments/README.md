# Experiments

Research question: **how should the schema be selected and represented so that an LLM writes
correct SQL over large, many-table databases?** Full-schema prompting overloads the context
and distracts the model; top-k retrieval drops the bridge tables a multi-join query needs.

Benchmark: Spider dev (and BIRD dev), run on the **original SQLite databases**, with the
schema made large by **pooling**. Each question sees its own database plus whole distractor
databases, up to N tables. Gold SQL stays valid. See `src/smart_db_assistant/benchmark/pool.py`.

## Setup

```bash
# Spider: https://yale-lily.github.io/spider  ->  data/spider/{dev.json,tables.json,database/}
# BIRD:   https://bird-bench.github.io         ->  data/bird/{dev.json,dev_tables.json,dev_databases/}
pip install -e '.[dev,rag]'
```

To get pools larger than the ~80 tables in Spider dev, also add the training databases as
distractors: `--tables data/spider/tables.json --tables data/spider/train_tables.json`
(the dev questions still only run on their own DB).

## Experiment 1: schema selection vs. schema size

| Arm | Flags |
|---|---|
| Full schema (baseline) | `--method full` |
| Top-k only | `--method rag --expansion none` |
| Top-k + 1-hop FK neighbours (naive fix) | `--method rag --expansion fk1` |
| Top-k + join-path completion (ours) | `--method rag --expansion joinpath` |
| Oracle (gold tables; upper bound) | `--method oracle` |

Sweep pool sizes `--pool-tables 0 25 50 100 200` with a fixed `--sample 200 --seed 0`.

```bash
for n in 0 25 50 100 200; do
  for exp in none fk1 joinpath; do
    sda-bench --pool-tables $n --method rag --k 4 --expansion $exp --sample 200 --tag e1-p$n-$exp
  done
  sda-bench --pool-tables $n --method full   --sample 200 --tag e1-p$n-full
  sda-bench --pool-tables $n --method oracle --sample 200 --tag e1-p$n-oracle
done
```

Report execution accuracy, schema recall / precision / completeness, input tokens, and cost.
Break each one down by the number of gold tables (`by_gold_tables`) and by whether the query
needs a bridge table (`by_has_bridge`). That breakdown is where join-path completion should
separate from top-k.

**Cost:** full-schema prompts grow with the pool. Roughly 150 tokens per table means about
30k input tokens per question at 200 tables. Run `--limit 5` first and check `total_cost_usd`
in `summary.json`.

## Experiment 2: schema representation for retrieval

Retrieval-only (no LLM calls, free), so it can sweep everything:

```bash
for r in name columns described samples; do
  for g in table column; do
    for ret in lexical embedding; do
      sda-bench --pool-tables 100 --method rag --retriever $ret --repr $r --granularity $g \
                --k 4 --expansion none --retrieval-only --tag e2-$ret-$r-$g
    done
  done
done
```

Metric: schema recall and completeness at k (also sweep `--k 1..8`). Then run the best two
or three configurations end-to-end to confirm that retrieval gains show up in execution accuracy.

## Error analysis

Every end-to-end run writes `errors.csv` with an automatic category per failure (see
`src/smart_db_assistant/eval/errors.py`):

`connector_miss` · `retrieval_miss` · `distractor_table` · `exec_error` · `wrong_tables` ·
`wrong_join` · `wrong_columns` · `wrong_aggregation` · `wrong_filter` · `wrong_order_limit` ·
`other`

The first three are **selection** failures; the rest are **generation** failures. The oracle
arm shows the generation-only error floor. Auto-categories are heuristics: fill in the
`manual_category` column for a sample (e.g. 50 failures per arm) and report agreement before
using the breakdown.

## Comparing runs

```bash
python -m smart_db_assistant.eval.compare experiments/results/e1-p100-*
```

Copy summaries you report into `experiments/reported/` so they are versioned.

## E-commerce set (demo / sanity check)

`sda-eval` runs the hand-written gold set on the Supabase database. It's too small (9 tables)
to show schema-selection effects. Use it as a sanity check, not as the headline benchmark.
