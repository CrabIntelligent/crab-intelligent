"""Run a burrow worker.

  .venv/bin/python -m worker --name my-burrow --providers claude
  .venv/bin/python -m worker --name reviewer --providers openai --kinds review
  .venv/bin/python -m worker --name demo-a --providers mock --mock-family mock-a
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path

from .client import Client, Keypair
from .runner import run


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m worker", description="Run a Crab Intelligent burrow worker.")
    parser.add_argument("--server", default=os.environ.get("CRAB_SERVER", "http://localhost:8000"))
    parser.add_argument("--name", default="burrow", help="A label shown in the network stats.")
    parser.add_argument("--key", help="Path to this burrow's keypair (default ~/.crab-worker/<name>.json).")
    parser.add_argument("--providers", default="claude", help="Comma list: claude, openai, mock.")
    parser.add_argument("--kinds", default="work,review", help="Which tasks to take: work, review or both.")
    parser.add_argument("--claude-model", default=os.environ.get("CLAUDE_MODEL", "claude-opus-5-5"))
    parser.add_argument("--effort", default=os.environ.get("CLAUDE_EFFORT", "high"),
                        choices=["low", "medium", "high", "xhigh", "max"])
    parser.add_argument("--openai-model", default=os.environ.get("OPENAI_MODEL", ""))
    parser.add_argument("--mock-family", default="mock-a")
    parser.add_argument("--mock-rework", type=float, default=0.0, help="Share of first attempts the mock sends back.")
    parser.add_argument("--mock-delay", type=float, default=1.5, help="Seconds the mock spends on each task.")
    parser.add_argument("--once", action="store_true", help="Stop when there is no task left.")
    args = parser.parse_args()

    providers = []
    for name in [p.strip() for p in args.providers.split(",") if p.strip()]:
        if name == "claude":
            from .providers.claude import ClaudeProvider
            providers.append(ClaudeProvider(model=args.claude_model, effort=args.effort))
        elif name == "openai":
            from .providers.openai_provider import OpenAIProvider
            providers.append(OpenAIProvider(model=args.openai_model))
        elif name == "mock":
            from .providers.mock import MockProvider
            providers.append(MockProvider(family=args.mock_family, rework_rate=args.mock_rework, delay=args.mock_delay))
        else:
            parser.error(f"Unknown provider '{name}'")
    if len({p.family for p in providers}) != len(providers):
        parser.error("Each provider must be a different model family.")

    key_path = Path(args.key).expanduser() if args.key else Path.home() / ".crab-worker" / f"{args.name}.json"
    client = Client(args.server, Keypair(key_path))
    kinds = [k.strip() for k in args.kinds.split(",") if k.strip()]
    run(client, args.name, providers, kinds, once=args.once)


if __name__ == "__main__":
    main()
