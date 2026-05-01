"""Tiny CLI for inspecting the orchestrator's cycle table.

Usage:
  python -m agent_orchestrator.status            # pretty-print recent cycles
  python -m agent_orchestrator.status --watch    # refresh every 5s
  agent-orchestrator-status -c path/to/config.toml
"""

from __future__ import annotations

import argparse
import os
import time

from .config import Config
from .state import State


def _print(state: State) -> None:
    print(state.dump())
    print(f"-- today's spend: ${state.today_spend():.4f}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "-c",
        "--config",
        default=os.environ.get("ORCHESTRATOR_CONFIG", "config.toml"),
    )
    ap.add_argument("--watch", action="store_true", help="refresh every 5s")
    args = ap.parse_args()

    cfg = Config.load(args.config)
    state = State(cfg.paths.db_path)
    if not args.watch:
        _print(state)
        return
    try:
        while True:
            os.system("clear" if os.name != "nt" else "cls")
            _print(state)
            time.sleep(5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
