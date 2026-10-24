"""Merge lineage edges from OpenLineage run events into a catalog."""

import json
from pathlib import Path

from datamend.catalog import Catalog


def _short(name: str) -> str:
    """`warehouse.public.orders` -> `orders`."""
    return name.rsplit(".", 1)[-1]


def edges(path: str | Path) -> set[tuple[str, str]]:
    """(upstream, downstream) pairs from COMPLETE events in a JSON-lines file."""
    out = set()
    for line in Path(path).read_text().splitlines():
        if not line.strip():
            continue
        event = json.loads(line)
        if event.get("eventType") != "COMPLETE":
            continue
        for o in event.get("outputs", []):
            for i in event.get("inputs", []):
                out.add((_short(i["name"]), _short(o["name"])))
    return out


def merge(catalog: Catalog, path: str | Path) -> tuple[Catalog, list[tuple[str, str]]]:
    """Add event edges as upstreams. Returns the new catalog and edges skipped as unknown."""
    names = {d.name for d in catalog.datasets}
    found = edges(path)
    skipped = sorted(e for e in found if not set(e) <= names)
    extra: dict[str, set[str]] = {}
    for up, down in found - set(skipped):
        extra.setdefault(down, set()).add(up)
    datasets = [
        d.model_copy(update={"upstreams": sorted(set(d.upstreams) | extra.get(d.name, set()))})
        for d in catalog.datasets
    ]
    return Catalog.model_validate({**catalog.model_dump(), "datasets": [d.model_dump() for d in datasets]}), skipped
