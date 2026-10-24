"""Warehouse over any DB-API connection: DuckDB, Postgres, Redshift, SQLite, ..."""

import os
from datetime import datetime

from datamend.adapters import TableStats
from datamend.catalog import Dataset


class DbApiWarehouse:
    def __init__(self, con, freshness_column: str = "_loaded_at"):
        self.con = con
        self.col = freshness_column

    def stats(self, dataset: Dataset) -> TableStats:
        cur = self.con.cursor()
        cur.execute(f'SELECT count(*), max("{self.col}") FROM {_quote(dataset.table or dataset.name)}')
        rows, last = cur.fetchone()
        if isinstance(last, str):  # SQLite returns timestamps as text
            last = datetime.fromisoformat(last)
        return TableStats(rows, last)


def _quote(table: str) -> str:
    """`schema.table` -> `"schema"."table"`."""
    return ".".join(f'"{part}"' for part in table.split("."))


def postgres(dsn_env: str = "DATAMEND_PG_DSN", freshness_column: str = "_loaded_at"):
    import psycopg  # optional: uv sync --extra postgres

    return DbApiWarehouse(psycopg.connect(os.environ[dsn_env]), freshness_column)
