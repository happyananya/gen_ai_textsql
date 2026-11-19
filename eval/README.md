# Gold set

`gold/{easy,medium,hard,adversarial}.jsonl`, one question per line. Format is documented in
`src/smart_db_assistant/eval/gold.py`.

Current: a 20-question starter set. Target per the plan: **120** (30 easy, 40 medium, 30 hard,
20 adversarial).

Writing guidelines:
- Business language only: no table or column names in the question.
- `expected`: `answer`, `refuse` (destructive, injection, data we don't have) or `clarify`
  (ambiguous; a refusal also counts as safe).
- Set `order_matters: true` only when the question asks for a ranking or sequence.
- List every table the gold SQL needs in `gold_tables` (used for retrieval recall).
- Revenue convention: `quantity * unit_price * (1 - discount)`, excluding cancelled orders.
- After editing, run `python -m smart_db_assistant.eval.verify_gold eval/gold`.

## Spider (external sanity check)
Download the Spider dev set from https://yale-lily.github.io/spider into `eval/spider/data/`
(git-ignored). Spider ships SQLite databases, so these need to be loaded into Postgres (or the
executor extended for SQLite) before running. Not wired up yet.
