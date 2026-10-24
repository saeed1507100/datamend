"""Plug-and-play platform adapters.

datamend needs two things from a data platform:

- a Warehouse that reports when a table was last loaded and how many rows it has
- an Orchestrator that runs a job, in `rerun` or `backfill` mode

Pick them in datasets.yaml under `platform:`. Everything else (lineage, checks,
guardrails, audit) is the same on every platform.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal, NamedTuple, Protocol

from datamend.catalog import Catalog, Dataset

Mode = Literal["rerun", "backfill"]


class TableStats(NamedTuple):
    rows: int
    last_loaded: datetime | None


class Warehouse(Protocol):
    def stats(self, dataset: Dataset) -> TableStats: ...


class Orchestrator(Protocol):
    def trigger(self, dataset: Dataset, mode: Mode, now: datetime) -> str:
        """Start the dataset's job. Returns a result: `fresh`, or `triggered:<run id>`."""
        ...


@dataclass
class Platform:
    warehouse: Warehouse
    orchestrator: Orchestrator


def build(catalog: Catalog, con) -> Platform:
    """Platform from the catalog's `platform:` block. `con` is the local DuckDB connection."""
    from datamend.adapters import aws, http_orchestrators, local, sql

    wh_cfg = dict(catalog.platform.warehouse)
    or_cfg = dict(catalog.platform.orchestrator)
    wh_type, or_type = wh_cfg.pop("type"), or_cfg.pop("type")

    warehouses = {
        "duckdb": lambda: sql.DbApiWarehouse(con, **wh_cfg),
        "postgres": lambda: sql.postgres(**wh_cfg),
    }
    orchestrators = {
        "local": lambda: local.LocalOrchestrator(con, catalog.base_dir / or_cfg.pop("pipelines")),
        "airflow": lambda: http_orchestrators.Airflow(**or_cfg),
        "prefect": lambda: http_orchestrators.Prefect(**or_cfg),
        "aws_glue": lambda: aws.Glue(**or_cfg),
        "aws_stepfunctions": lambda: aws.StepFunctions(**or_cfg),
    }
    if wh_type not in warehouses:
        raise ValueError(f"unknown warehouse type {wh_type!r}; known: {sorted(warehouses)}")
    if or_type not in orchestrators:
        raise ValueError(f"unknown orchestrator type {or_type!r}; known: {sorted(orchestrators)}")
    return Platform(warehouses[wh_type](), orchestrators[or_type]())
