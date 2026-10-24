"""Freshness and row-count checks that raise Incidents."""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from datamend.adapters import Warehouse
from datamend.catalog import Catalog

Kind = Literal["stale", "gap", "dq"]


class Incident(BaseModel):
    dataset: str
    kind: Kind
    detail: str
    detected_at: datetime

    @property
    def id(self) -> str:
        return f"{self.dataset}:{self.kind}"


def run_all(warehouse: Warehouse, catalog: Catalog, now: datetime) -> list[Incident]:
    incidents = []
    for d in catalog.datasets:
        rows, last = warehouse.stats(d)
        if last is None or now - last > timedelta(minutes=d.cadence_minutes):
            age = "never loaded" if last is None else f"{int((now - last).total_seconds() // 60)} min old"
            incidents.append(Incident(dataset=d.name, kind="stale",
                                      detail=f"{age}, cadence {d.cadence_minutes} min",
                                      detected_at=now))
        if d.checks.min_rows is not None and rows < d.checks.min_rows:
            incidents.append(Incident(dataset=d.name, kind="gap",
                                      detail=f"{rows} rows, expected >= {d.checks.min_rows}",
                                      detected_at=now))
    return incidents
