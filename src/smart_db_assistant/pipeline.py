"""End-to-end pipeline: question -> select schema -> SQL -> validate -> execute -> answer.

`pipeline="baseline"`: full schema in the prompt.
`pipeline="rag"`     : selected tables only (scorer + top-k + join-path expansion,
                       configured by the SDA_RAG_* settings).

The database is behind a `Backend` (Postgres for the e-commerce demo, SQLite for
Spider/BIRD). Benchmarks pass a pooled schema, a PooledBackend and their own
selector (including the oracle) per question.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .config import Settings, get_settings
from .db import Backend, PostgresBackend, QueryResult
from .llm import LLMClient, make_client
from .prompts import (
    REPAIR_PROMPT,
    SQL_RESPONSE_SCHEMA,
    SUMMARY_PROMPT,
    SUMMARY_SCHEMA,
    SYSTEM_PROMPT,
)
from .retrieval import FullSchemaSelector, SchemaSelector, Selection, make_scorer
from .schema import SchemaMetadata
from .tracing import Attempt, Trace, write_trace
from .validator import validate_sql


@dataclass
class Answer:
    action: str                       # sql | refuse | clarify | error
    message: str
    sql: str | None = None
    result: QueryResult | None = None
    summary: str | None = None
    chart: dict = field(default_factory=dict)
    trace: Trace | None = None


@dataclass
class Turn:
    """One prior exchange, used for simple follow-ups ("now only for 2025")."""
    question: str
    sql: str | None


def default_selector(settings: Settings, schema: SchemaMetadata):
    if settings.pipeline != "rag":
        return FullSchemaSelector(schema)
    scorer = make_scorer(settings.rag_retriever, schema, settings.rag_representation,
                         settings.rag_granularity)
    return SchemaSelector(schema, scorer, settings.rag_top_k, settings.rag_expansion)


class Assistant:
    def __init__(self, settings: Settings | None = None, *, llm: LLMClient | None = None,
                 backend: Backend | None = None, schema: SchemaMetadata | None = None,
                 selector=None, system_prompt: str = SYSTEM_PROMPT,
                 trace_file: str | None = None):
        self.settings = settings or get_settings()
        self.backend = backend or PostgresBackend(self.settings)
        self.schema = schema or self.backend.schema()
        self.llm = llm or make_client(self.settings.model, self.settings.effort)
        self.selector = selector or default_selector(self.settings, self.schema)
        self.system_prompt = system_prompt
        self.trace_path = self.settings.trace_dir / (trace_file or "runs.jsonl")

    @staticmethod
    def _history_messages(history: list[Turn] | None) -> list[dict]:
        msgs: list[dict] = []
        for turn in history or []:
            msgs.append({"role": "user", "content": turn.question})
            msgs.append({"role": "assistant", "content": json.dumps(
                {"action": "sql", "sql": turn.sql or "", "message": ""})})
        return msgs

    def ask(self, question: str, *, history: list[Turn] | None = None, summarize: bool = True,
            question_id: str | None = None) -> Answer:
        s = self.settings
        schema, selector, backend = self.schema, self.selector, self.backend
        trace = Trace(question=question, pipeline=s.pipeline, model=s.model, question_id=question_id)
        t_start = time.perf_counter()

        t0 = time.perf_counter()
        sel: Selection = selector.select(question)
        trace.retrieval_latency_ms = (time.perf_counter() - t0) * 1000
        trace.retrieved_tables = sel.tables
        trace.extra.update(ranked_tables=sel.ranked, connector_tables=sel.connectors,
                           schema_size=len(schema))

        sub = schema.subset(sel.tables)
        system = self.system_prompt.format(schema=sub.to_prompt())
        known = schema.column_names()   # validate against the whole (possibly pooled) schema
        messages = self._history_messages(history) + [{"role": "user", "content": question}]

        answer = Answer(action="error", message="No answer produced.", trace=trace)
        error = ""
        for attempt_no in range(s.max_retries + 1):
            resp = self.llm.generate_json(system, messages, SQL_RESPONSE_SCHEMA)
            trace.cost_usd += resp.cost_usd
            data = resp.data
            action, sql, msg = data.get("action"), data.get("sql", ""), data.get("message", "")
            attempt = Attempt(sql=sql, valid=False, llm_latency_ms=resp.latency_ms,
                              input_tokens=resp.input_tokens, output_tokens=resp.output_tokens)
            trace.attempts.append(attempt)

            if action in ("refuse", "clarify"):
                attempt.valid = True
                answer = Answer(action=action, message=msg, trace=trace)
                break

            check = validate_sql(sql, known, s.row_limit, dialect=backend.dialect)
            attempt.validation_errors = check.errors
            if not check.ok:
                if any("only SELECT" in e or "forbidden" in e for e in check.errors):
                    # guardrail fired: never retry a write
                    answer = Answer(action="refuse", message="This assistant is read-only and "
                                    "cannot run that kind of query.", sql=sql, trace=trace)
                    trace.extra["guardrail_fired"] = True
                    break
                error = "; ".join(check.errors)
            else:
                attempt.valid = True
                try:
                    result = backend.execute(check.sql, timeout_ms=s.statement_timeout_ms,
                                             max_rows=s.row_limit or 100_000)
                    attempt.db_latency_ms = result.elapsed_ms
                    attempt.row_count = len(result.rows)
                    answer = Answer(action="sql", message=msg, sql=check.sql, result=result,
                                    trace=trace)
                    break
                except Exception as e:  # noqa: BLE001 - surface any DB error to the repair loop
                    error = f"{type(e).__name__}: {e}".strip()
                    attempt.db_error = error

            if attempt_no == s.max_retries:
                answer = Answer(action="error", message=f"Could not produce a working query: {error}",
                                sql=sql, trace=trace)
                break
            messages = messages + [
                {"role": "assistant", "content": json.dumps(data)},
                {"role": "user", "content": REPAIR_PROMPT.format(sql=sql, error=error)},
            ]

        if summarize and answer.action == "sql" and answer.result is not None:
            self._summarize(question, answer)

        trace.action = answer.action
        trace.final_sql = answer.sql
        trace.message = answer.message
        trace.total_latency_ms = (time.perf_counter() - t_start) * 1000
        write_trace(trace, self.trace_path)
        return answer

    def _summarize(self, question: str, answer: Answer) -> None:
        r = answer.result
        shown = r.rows[:20]
        prompt = SUMMARY_PROMPT.format(
            question=question, columns=", ".join(r.columns), shown=len(shown), total=len(r.rows),
            truncated=", truncated" if r.truncated else "",
            rows="\n".join(json.dumps(dict(zip(r.columns, row)), default=str) for row in shown),
        )
        resp = self.llm.generate_json("You explain query results to business users.",
                                      [{"role": "user", "content": prompt}], SUMMARY_SCHEMA)
        answer.trace.cost_usd += resp.cost_usd
        answer.summary = resp.data.get("answer")
        if resp.data.get("chart") in ("bar", "line"):
            answer.chart = {"type": resp.data["chart"], "x": resp.data.get("x"),
                            "y": resp.data.get("y")}
