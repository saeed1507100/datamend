"""Synthetic warehouse: seed it by running every job, then break it on purpose."""

from datetime import datetime, timedelta

import duckdb
import networkx as nx

from datamend import adapters, lineage
from datamend.catalog import Catalog


def seed(con: duckdb.DuckDBPyConnection, catalog: Catalog, now: datetime) -> None:
    """Run every job once, upstream first."""
    orch = adapters.build(catalog, con).orchestrator
    for name in nx.topological_sort(lineage.build(catalog)):
        orch.trigger(catalog.get(name), "rerun", now)


def inject_stale(con, catalog: Catalog, name: str, now: datetime, affected: list[str]) -> None:
    """Age a dataset and everything downstream of it, as if its job stopped running."""
    for n in [name, *affected]:
        age = timedelta(minutes=catalog.get(n).cadence_minutes * 3)
        con.execute(f'UPDATE "{n}" SET _loaded_at = ?', [now - age])


def inject_gap(con, name: str, keep_rows: int = 10) -> None:
    con.execute(f'DELETE FROM "{name}" WHERE id >= ?', [keep_rows])
