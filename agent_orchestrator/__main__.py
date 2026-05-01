"""Entry point: `python -m agent_orchestrator` or the `agent-orchestrator` CLI script."""

from __future__ import annotations

import argparse
import asyncio
import os

from .config import Config
from .orchestrator import Orchestrator, setup_logging


def main() -> None:
    ap = argparse.ArgumentParser(prog="agent-orchestrator")
    ap.add_argument(
        "-c",
        "--config",
        default=os.environ.get("ORCHESTRATOR_CONFIG", "config.toml"),
        help="Path to config.toml (default: ./config.toml or $ORCHESTRATOR_CONFIG).",
    )
    ap.add_argument(
        "--once",
        action="store_true",
        help="Run a single cycle and exit (useful for smoke tests / cron).",
    )
    args = ap.parse_args()

    cfg = Config.load(args.config)
    setup_logging(cfg.paths.logs_dir)
    orch = Orchestrator(cfg)

    if args.once:
        asyncio.run(orch.run_once())
    else:
        asyncio.run(orch.run())


if __name__ == "__main__":
    main()
