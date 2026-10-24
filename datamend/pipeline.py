"""Helpers for pipeline jobs. A job is a file with `run(con, now, mode)`."""

from datetime import datetime

SOURCE_ROWS = 200


def load_synthetic(con, table: str, now: datetime, mode: str, rows: int = SOURCE_ROWS) -> None:
    """Load a synthetic source extract.

    `rerun` replaces the table. `backfill` inserts only the missing ids. Both are idempotent.
    """
    if mode == "backfill":
        con.execute(f'INSERT INTO "{table}" SELECT range, ? FROM range(?) '
                    f'WHERE range NOT IN (SELECT id FROM "{table}")', [now, rows])
        return
    con.execute(f'CREATE OR REPLACE TABLE "{table}" (id INTEGER, _loaded_at TIMESTAMP)')
    con.execute(f'INSERT INTO "{table}" SELECT range, ? FROM range(?)', [now, rows])


def build(con, table: str, select_sql: str, now: datetime) -> None:
    """Rebuild a derived table from a SELECT. `?` in the SELECT is bound to `now`."""
    con.execute(f'CREATE OR REPLACE TABLE "{table}" AS {select_sql}',
                [now] * select_sql.count("?"))
