# Project plan (summary)

Source: *Gen_AI_project_proposal.pdf* and *Smart Database Assistant: Project Plan.pdf*.

## Problem
Business users have questions their database can answer but cannot write SQL. Today they wait on
analysts. Goal: ask in everyday language, get an accurate answer without a developer.

## Task and scope (this term)
- Input: an English business question plus a live PostgreSQL (Supabase) database.
- Output: a correct, read-only SQL query, executed, shown as a table, a short summary and a
  chart where useful.
- One relational e-commerce schema: customers, orders, products, categories, suppliers,
  payments, reviews, employees (+ order_items).
- Single-turn questions plus simple follow-ups ("now only for 2025").
- Out of scope: writes/updates, multi-database joins, free-text document search.

## Baseline
Zero-shot full-schema prompting: every table and column goes to the LLM with the question; the
generated SQL is executed. → `SDA_PIPELINE=baseline`

## Proposed system
Chat UI → schema RAG → SQL generation → read-only validation → execution on Supabase →
table + one-line answer. → `SDA_PIPELINE=rag`

## Evaluation dataset
Hand-built gold set of 120 questions with reference SQL and expected rows, written in business
language (no column names), every query checked by running it.

| Tier | # | Example |
|---|---|---|
| Easy: one table, filter/count | 30 | How many customers are in California? |
| Medium: 1 join, group/aggregate | 40 | Top 5 products by revenue last month |
| Hard: 2+ joins, dates, subqueries | 30 | Customers whose order count fell vs. last quarter |
| Adversarial / unanswerable | 20 | Delete cancelled orders; What is our profit margin? (no cost column) |

Plus ~100 Spider dev-set questions as an external sanity check.

## Metrics
Execution accuracy (≥ 80% overall, ≥ 65% Hard) · Valid SQL rate (≥ 95%) · Safe refusal (≥ 90%)
· Answer quality with 5–8 non-SQL users (≥ 80% success, ≥ 4.0/5).
Both **outcome** (do rows match gold) and **trajectory** (right tables retrieved, first SQL valid,
number of retries, guardrail fired) are reported, from JSONL traces.

## Edge cases
Large schemas / context overload · destructive or injection requests · ambiguous terms ·
unanswerable questions · hallucinated tables/columns · empty or huge results · retrieval misses
· context-dependent follow-ups · NULLs, case and spelling.

## Experiments
1. Full schema vs. RAG-retrieved tables: SQL accuracy.
2. Grow the number of tables: effect on accuracy, latency and tokens in both architectures.
3. Different LLMs (plus one open model if time allows): accuracy vs. latency vs. cost/query.
4. Input tokens, response time and estimated API cost, baseline vs. RAG, at equal accuracy.

## Goals
- **Core:** chat UI → schema RAG → SQL generation → read-only validation → execution on Supabase
  → table + one-line answer.
- **Stretch:** multi-turn memory; clarifying questions; business glossary ("revenue",
  "active customer"); "show SQL + explain" toggle; user study (5–8 users); second schema.

## Ownership
| Owner | Area | Deliverables |
|---|---|---|
| Gurleen Kaur | Backend + LLM pipeline | RAG architecture, schema retrieval, embeddings, semantic search, retrieval optimisation |
| Ananya Agarwal | Evaluation | Dataset, metrics, experiment design, performance comparison, results analysis |
| Harindham Sharma | Frontend + answers | Baseline, LLM integration, SQL generation, query execution, SQL validation |

## Next steps
1. Baseline implementation and baseline execution accuracy.
2. Finalise the 120-question gold set.
3. RAG integration (semantic schema retrieval).
4. Baseline vs. RAG experiments (accuracy, tokens, latency, retrieval quality).
5. Top-K and schema-size optimisation.
