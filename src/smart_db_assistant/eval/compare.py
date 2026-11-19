"""Compare several eval runs side by side (baseline vs RAG, models, top-k).

    python -m smart_db_assistant.eval.compare experiments/results/*
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

COLUMNS = [
    ("execution_accuracy", "ExecAcc"),
    ("execution_accuracy_lenient", "ExecAcc~"),
    ("valid_sql_rate", "ValidSQL"),
    ("safe_refusal_rate", "SafeRef"),
    ("retrieval_recall", "RetRecall"),
    ("schema_recall", "SchRecall"),
    ("schema_precision", "SchPrec"),
    ("schema_complete_rate", "SchCompl"),
    ("mean_selected_tables", "SelTabs"),
    ("first_attempt_valid", "1stValid"),
    ("mean_retries", "Retries"),
    ("mean_latency_ms", "Latency"),
    ("mean_input_tokens", "InTok"),
    ("total_cost_usd", "Cost$"),
]


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("runs", nargs="+")
    args = p.parse_args()

    rows = []
    for run in args.runs:
        f = Path(run) / "summary.json"
        if f.exists():
            rows.append((Path(run).name, json.loads(f.read_text())))

    cols = [(k, label) for k, label in COLUMNS if any(s.get(k) is not None for _, s in rows)]
    header = f"{'run':<32}" + "".join(f"{label:>11}" for _, label in cols)
    print(header)
    print("-" * len(header))
    for name, s in rows:
        cells = "".join(f"{'' if s.get(k) is None else s.get(k):>11}" for k, _ in cols)
        print(f"{name:<32}{cells}")


if __name__ == "__main__":
    main()
