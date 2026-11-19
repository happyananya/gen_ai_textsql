"""JSONL trace logging for trajectory evaluation.

Each run records: retrieved tables, every SQL attempt, validation/DB errors,
token usage and timings. The eval harness scores trajectories from these.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Attempt:
    sql: str
    valid: bool
    validation_errors: list[str] = field(default_factory=list)
    db_error: str | None = None
    row_count: int | None = None
    llm_latency_ms: float = 0.0
    db_latency_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0


@dataclass
class Trace:
    question: str
    pipeline: str
    model: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    started_at: float = field(default_factory=time.time)
    question_id: str | None = None
    retrieved_tables: list[str] = field(default_factory=list)
    retrieval_latency_ms: float = 0.0
    attempts: list[Attempt] = field(default_factory=list)
    action: str | None = None          # sql | refuse | clarify | error
    final_sql: str | None = None
    message: str | None = None
    total_latency_ms: float = 0.0
    cost_usd: float = 0.0
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def input_tokens(self) -> int:
        return sum(a.input_tokens for a in self.attempts)

    @property
    def output_tokens(self) -> int:
        return sum(a.output_tokens for a in self.attempts)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["input_tokens"] = self.input_tokens
        d["output_tokens"] = self.output_tokens
        return d


def write_trace(trace: Trace, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(trace.to_dict(), default=str) + "\n")
