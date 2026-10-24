"""Registered fixes. Anything not in FIXES cannot be auto-applied."""

from datetime import datetime

from datamend import simulate

FIXES = {"stale": "rerun"}


def rerun(con, name: str, affected: list[str], now: datetime) -> str:
    """Re-run the dataset's job, then its downstream jobs in lineage order."""
    for n in [name, *affected]:
        simulate.load_rows(con, n, now)
    return "fresh"


ACTIONS = {"rerun": rerun}
