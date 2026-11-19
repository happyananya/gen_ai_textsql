"""LLM client layer.

Every pipeline talks to an `LLMClient`, so experiment 3 (compare different
LLMs on accuracy / latency / cost) only needs a new class implementing
`generate_json`. The default is Claude via the official Anthropic SDK.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

# USD per 1M tokens (input, output) - used for cost-per-query estimates.
PRICING: dict[str, tuple[float, float]] = {
    "claude-opus-5-5": (4.00, 20.00),
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
}


@dataclass
class LLMResponse:
    data: dict[str, Any]
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: float
    stop_reason: str | None = None

    @property
    def cost_usd(self) -> float:
        in_p, out_p = PRICING.get(self.model, (0.0, 0.0))
        return (self.input_tokens * in_p + self.output_tokens * out_p) / 1_000_000


class LLMClient(Protocol):
    model: str

    def generate_json(self, system: str, messages: list[dict], schema: dict) -> LLMResponse: ...


class ClaudeClient:
    """Claude with JSON-schema-constrained output.

    Server-side refusal fallback is enabled (`fallbacks="default"`): if a
    safety classifier declines a request, the API retries on a fallback model
    within the same call. Remove it if you want raw refusals for evaluation.
    """

    def __init__(self, model: str = "claude-opus-5-5", effort: str = "medium",
                 max_tokens: int = 16000):
        self.model = model
        self.effort = effort
        self.max_tokens = max_tokens
        self._client = anthropic.Anthropic()

    def generate_json(self, system: str, messages: list[dict], schema: dict) -> LLMResponse:
        start = time.perf_counter()
        response = self._client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=system,
            messages=messages,
            output_config={
                "effort": self.effort,
                "format": {"type": "json_schema", "schema": schema},
            },
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
        latency = (time.perf_counter() - start) * 1000

        if response.stop_reason == "refusal":
            data = {"action": "refuse", "sql": "", "message": "The model declined this request."}
        else:
            text = next((b.text for b in response.content if b.type == "text"), "{}")
            data = json.loads(text)
        return LLMResponse(
            data=data,
            model=response.model,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency,
            stop_reason=response.stop_reason,
        )


def make_client(model: str, effort: str = "medium") -> LLMClient:
    if model.startswith("claude-"):
        return ClaudeClient(model=model, effort=effort)
    raise ValueError(
        f"No LLMClient registered for model {model!r}. Add a class implementing "
        "LLMClient.generate_json in llm.py and register it here."
    )
