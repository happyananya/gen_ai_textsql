"""Gold-set loading.

Format: one JSON object per line in eval/gold/*.jsonl

    {
      "id": "M007",
      "tier": "easy" | "medium" | "hard" | "adversarial",
      "question": "Top 5 products by revenue last month",   # business language, no column names
      "expected": "answer" | "refuse" | "clarify",
      "gold_sql": "SELECT ...",                              # empty for refuse/clarify
      "order_matters": true,                                 # only if the question asks for an order
      "gold_tables": ["products", "order_items", "orders"],  # for retrieval recall
      "notes": "revenue excludes cancelled orders"
    }

Expected rows are produced by running `gold_sql` at evaluation time, so
relative dates ("last month") resolve the same way for gold and prediction.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, model_validator

TIERS = ("easy", "medium", "hard", "adversarial")


class GoldItem(BaseModel):
    id: str
    tier: str
    question: str
    expected: str = "answer"
    gold_sql: str = ""
    order_matters: bool = False
    gold_tables: list[str] = []
    notes: str = ""

    @model_validator(mode="after")
    def _check(self):
        if self.tier not in TIERS:
            raise ValueError(f"{self.id}: tier must be one of {TIERS}")
        if self.expected not in ("answer", "refuse", "clarify"):
            raise ValueError(f"{self.id}: bad expected value {self.expected!r}")
        if self.expected == "answer" and not self.gold_sql.strip():
            raise ValueError(f"{self.id}: answerable items need gold_sql")
        return self


def load_gold(path: str | Path) -> list[GoldItem]:
    path = Path(path)
    files = sorted(path.glob("*.jsonl")) if path.is_dir() else [path]
    items: list[GoldItem] = []
    for f in files:
        for n, line in enumerate(f.read_text().splitlines(), 1):
            if line.strip():
                try:
                    items.append(GoldItem.model_validate_json(line))
                except Exception as e:
                    raise ValueError(f"{f}:{n}: {e}") from e
    ids = [i.id for i in items]
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        raise ValueError(f"duplicate gold ids: {sorted(dupes)}")
    return items


def dump_gold(items: list[GoldItem], path: str | Path) -> None:
    Path(path).write_text("\n".join(json.dumps(i.model_dump()) for i in items) + "\n")
