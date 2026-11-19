"""Command-line entry: `sda-ask "How many customers are in California?"`."""

from __future__ import annotations

import argparse

from .config import get_settings
from .pipeline import Assistant


def main() -> None:
    p = argparse.ArgumentParser(description="Ask the database a question in plain English.")
    p.add_argument("question")
    p.add_argument("--pipeline", choices=["baseline", "rag"])
    p.add_argument("--model")
    args = p.parse_args()

    overrides = {k: v for k, v in {"pipeline": args.pipeline, "model": args.model}.items() if v}
    assistant = Assistant(get_settings(**overrides))
    ans = assistant.ask(args.question)

    print(f"[{ans.action}] {ans.message}")
    if ans.sql:
        print(f"\nSQL:\n{ans.sql}")
    if ans.result:
        print("\n" + " | ".join(ans.result.columns))
        for row in ans.result.rows[:20]:
            print(" | ".join(str(v) for v in row))
        if len(ans.result.rows) > 20:
            print(f"... ({len(ans.result.rows)} rows)")
    if ans.summary:
        print(f"\n{ans.summary}")
    t = ans.trace
    print(f"\n({t.total_latency_ms:.0f} ms, {t.input_tokens}+{t.output_tokens} tokens, "
          f"${t.cost_usd:.4f}, attempts={len(t.attempts)})")


if __name__ == "__main__":
    main()
