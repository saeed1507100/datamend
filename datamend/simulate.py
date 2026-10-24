"""Synthetic warehouse in DuckDB: one table per dataset, each row stamped with _loaded_at."""

from datetime import datetime, timedelta

import duckdb

from datamend.catalog import Catalog

SEED_ROWS = 200


def load_rows(con: duckdb.DuckDBPyConnection, name: str, at: datetime, rows: int = SEED_ROWS):
    """Replace a table's contents: what an idempotent job run does."""
    con.execute(f'CREATE OR REPLACE TABLE "{name}" (id INTEGER, _loaded_at TIMESTAMP)')
    con.execute(f'INSERT INTO "{name}" SELECT range, ? FROM range(?)', [at, rows])


def seed(con: duckdb.DuckDBPyConnection, catalog: Catalog, now: datetime) -> None:
    for d in catalog.datasets:
        load_rows(con, d.name, now)


def inject_stale(con, catalog: Catalog, name: str, now: datetime, affected: list[str]) -> None:
    """Age a dataset and everything downstream of it, as if its job stopped running."""
    for n in [name, *affected]:
        age = timedelta(minutes=catalog.get(n).cadence_minutes * 3)
        con.execute(f'UPDATE "{n}" SET _loaded_at = ?', [now - age])


def inject_gap(con, name: str, keep_rows: int = 10) -> None:
    con.execute(f'DELETE FROM "{name}" WHERE id >= ?', [keep_rows])
