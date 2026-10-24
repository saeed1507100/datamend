"""Registered fixes. Anything not in FIXES cannot be auto-applied."""

from datetime import datetime

from datamend.adapters import Orchestrator
from datamend.catalog import Dataset

FIXES = {"stale": "rerun", "gap": "backfill"}


def _run(orch: Orchestrator, root: Dataset, mode: str, affected: list[Dataset], now) -> str:
    """Run the root's job in `mode`, then rebuild downstream in lineage order."""
    results = [orch.trigger(root, mode, now)]
    results += [orch.trigger(d, "rerun", now) for d in affected]
    pending = [r for r in results if r != "fresh"]
    return "fresh" if not pending else ", ".join(pending)


def rerun(orch: Orchestrator, root: Dataset, affected: list[Dataset], now: datetime) -> str:
    return _run(orch, root, "rerun", affected, now)


def backfill(orch: Orchestrator, root: Dataset, affected: list[Dataset], now: datetime) -> str:
    return _run(orch, root, "backfill", affected, now)


ACTIONS = {"rerun": rerun, "backfill": backfill}
