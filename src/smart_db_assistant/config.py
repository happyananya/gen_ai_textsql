"""Runtime settings, read from environment / .env."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass(frozen=True)
class Settings:
    database_url: str = field(default_factory=lambda: _env("DATABASE_URL", ""))
    model: str = field(default_factory=lambda: _env("SDA_MODEL", "claude-opus-5-5"))
    effort: str = field(default_factory=lambda: _env("SDA_EFFORT", "medium"))
    pipeline: str = field(default_factory=lambda: _env("SDA_PIPELINE", "baseline"))
    rag_top_k: int = field(default_factory=lambda: int(_env("SDA_RAG_TOP_K", "4")))
    rag_retriever: str = field(default_factory=lambda: _env("SDA_RAG_RETRIEVER", "lexical"))
    rag_representation: str = field(default_factory=lambda: _env("SDA_RAG_REPR", "described"))
    rag_granularity: str = field(default_factory=lambda: _env("SDA_RAG_GRANULARITY", "table"))
    rag_expansion: str = field(default_factory=lambda: _env("SDA_RAG_EXPANSION", "joinpath"))
    max_retries: int = field(default_factory=lambda: int(_env("SDA_MAX_RETRIES", "2")))
    row_limit: int = field(default_factory=lambda: int(_env("SDA_ROW_LIMIT", "500")))
    statement_timeout_ms: int = field(
        default_factory=lambda: int(_env("SDA_STATEMENT_TIMEOUT_MS", "10000"))
    )
    trace_dir: Path = field(default_factory=lambda: Path(_env("SDA_TRACE_DIR", "traces")))


def get_settings(**overrides) -> Settings:
    s = Settings()
    if overrides:
        from dataclasses import replace

        s = replace(s, **overrides)
    return s
