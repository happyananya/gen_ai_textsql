# Smart Database Assistant

Natural-language questions to read-only SQL, with a research focus on **schema selection for
large, many-table databases**: which tables to show the LLM, how to represent them for
retrieval, and how to recover the *bridge tables* that multi-join queries need but that
similarity search misses (join-path completion over the foreign-key graph).

- **Benchmark:** Spider / BIRD on their original SQLite databases, with schemas enlarged by
  pooling distractor databases (10 → 200+ tables). See [`experiments/`](experiments/README.md).
- **Demo / sanity set:** a Supabase (PostgreSQL) e-commerce database with a hand-written gold set.

**Team:** Gurleen Kaur · Ananya Agarwal · Harindham Sharma
Proposal and plan are summarised in [`docs/`](docs/).

```
question ──► [schema: full (baseline) | retrieved tables (RAG)] ──► LLM ──► SQL
                                                                          │
     answer + table + chart ◄── summarise ◄── execute (read-only) ◄── validate
                                                    │ error
                                                    └──► self-correction retry (≤ N)
every run ──► JSONL trace (tables retrieved, each SQL attempt, errors, tokens, timings)
```

## Repository layout

| Path | What | Owner |
|---|---|---|
| `db/schema.sql`, `db/seed.py` | E-commerce schema (9 tables) and deterministic synthetic data | all |
| `db/readonly_role.sql` | Read-only Postgres role for the assistant | all |
| `src/smart_db_assistant/schema.py` | Introspects tables/columns/FKs, renders DDL for the prompt | Harindham / Gurleen |
| `src/smart_db_assistant/retrieval.py` | Schema selection: lexical / embedding scorers, top-k, oracle | Gurleen |
| `src/smart_db_assistant/representation.py` | Table → text for retrieval (name / columns / described / samples; table or column granularity) | Gurleen |
| `src/smart_db_assistant/schema_graph.py` | FK graph, join-path completion (Steiner-tree approximation), 1-hop baseline | Gurleen |
| `src/smart_db_assistant/benchmark/` | Spider/BIRD loaders, schema pooling, `sda-bench` runner | Ananya / Gurleen |
| `src/smart_db_assistant/llm.py`, `prompts.py` | LLM client (Claude by default, pluggable), prompts, output schema | Harindham |
| `src/smart_db_assistant/validator.py` | Guardrail: single SELECT only, no unknown tables/columns, LIMIT | Harindham |
| `src/smart_db_assistant/pipeline.py` | End-to-end `Assistant.ask()` with retries, follow-ups, tracing | Harindham / Gurleen |
| `src/smart_db_assistant/eval/` | Metrics, schema coverage, error taxonomy, e-commerce eval, run comparison | Ananya |
| `eval/gold/*.jsonl` | Gold questions per tier (starter set, grow to 120) | Ananya |
| `app/streamlit_app.py` | Chat UI | Harindham |
| `experiments/` | Experiment plan and results | Ananya |
| `tests/` | Unit tests (no DB or API key needed) | all |

## Setup

```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev,ui,seed]'        # add ,rag for the embedding retriever
cp .env.example .env                    # fill in DATABASE_URL, ANTHROPIC_API_KEY
```

**Database (Supabase):**

1. Create a Supabase project and copy the connection string (Project Settings → Database).
2. Put the admin string in `DATABASE_ADMIN_URL` and load the data:
   `python db/seed.py`
3. Run `db/readonly_role.sql` in the Supabase SQL editor (change the password first), then set
   `DATABASE_URL` to that role. The assistant should never connect as `postgres`.

## Usage

```bash
sda-ask "How many customers are in California?"
sda-ask "Top 5 products by revenue last month" --pipeline rag
streamlit run app/streamlit_app.py
```

## Benchmark (main experiments)

```bash
# download Spider into data/spider/ (see experiments/README.md), then:
sda-bench --pool-tables 100 --method rag --expansion joinpath --retrieval-only   # free
sda-bench --pool-tables 100 --method rag --expansion joinpath --sample 200       # billed
```

Each run reports execution accuracy, schema recall / precision / completeness, tokens and cost,
broken down by the number of gold tables and by whether a bridge table is needed. It also
writes `errors.csv` with an automatic error category for each failure.

## E-commerce evaluation (sanity check)

```bash
pytest                                                     # unit tests, offline
python -m smart_db_assistant.eval.verify_gold eval/gold    # every gold SQL runs
sda-eval --pipeline baseline --tag baseline
sda-eval --pipeline rag --top-k 3 --tag rag-k3
python -m smart_db_assistant.eval.compare experiments/results/*
```

`sda-eval` makes billed LLM calls (about one per question, plus retries). Use `--limit` and
`--tier` while iterating.

| Metric | How measured | Target |
|---|---|---|
| Execution accuracy | Generated rows = gold rows (order-insensitive unless ORDER BY asked) | ≥ 80% overall, ≥ 65% Hard |
| Valid SQL rate | Query parses, passes the guardrail and runs | ≥ 95% |
| Safe refusal | Adversarial/unanswerable items refused or clarified | ≥ 90% |
| Answer quality | 5–8 non-SQL users: task success + 1–5 rating | ≥ 80%, ≥ 4.0 |

Trajectory metrics come from the traces: retrieval recall, first-attempt validity, retries and
whether the guardrail fired. See [`experiments/README.md`](experiments/README.md).

## Safety

The assistant is read-only in three layers: (1) the `sqlglot` validator rejects anything but a
single SELECT and blocks dangerous functions, (2) queries run in a `READ ONLY` transaction with a
statement timeout and are always rolled back, (3) the database role only has `SELECT`.

## Model

Defaults to `claude-opus-5-5` (`SDA_MODEL`), with structured JSON output. To compare other
models (experiment 3), set `SDA_MODEL` / `--model`, or add a client class to `llm.py`.
