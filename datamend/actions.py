"""Registered fixes. Anything not in FIXES cannot be auto-applied."""

from datetime import datetime

from datamend import simulate

FIXES = {"stale": "rerun", "gap": "backfill"}


def rerun(con, name: str, affected: list[str], now: datetime) -> str:
    """Re-run the dataset's job, then its downstream jobs in lineage order."""
    for n in [name, *affected]:
        simulate.load_rows(con, n, now)
    return "fresh"


def backfill(con, name: str, affected: list[str], now: datetime) -> str:
    """Load only the missing rows, then rebuild downstream from the repaired table."""
    filled = simulate.fill_missing(con, name, now)
    for n in affected:
        simulate.load_rows(con, n, now)
    return f"backfilled {filled} rows"


ACTIONS = {"rerun": rerun, "backfill": backfill}
