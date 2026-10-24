"""CLI: seed, inject, run, audit, lineage."""

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import duckdb
import typer

from datamend import audit as audit_log
from datamend import catalog as cat
from datamend import engine, lineage, openlineage, simulate, web

app = typer.Typer(no_args_is_help=True, add_completion=False)

DB = typer.Option(Path("datamend.duckdb"), envvar="DATAMEND_DB", help="DuckDB file")
CATALOG = typer.Option(Path("examples/datasets.yaml"), envvar="DATAMEND_CATALOG")
EVENTS = typer.Option(None, envvar="DATAMEND_OPENLINEAGE",
                      help="OpenLineage events (JSON lines) to merge into the catalog's lineage")


def _load(catalog: Path, events: Path | None) -> cat.Catalog:
    c = cat.load(catalog)
    if events is None:
        return c
    c, skipped = openlineage.merge(c, events)
    for up, down in skipped:
        typer.echo(f"openlineage: skipped {up} -> {down} (not in catalog)", err=True)
    return c


class Failure(StrEnum):
    stale = "stale"
    gap = "gap"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@app.command()
def seed(db: Path = DB, catalog: Path = CATALOG):
    """Create a fresh synthetic warehouse."""
    c = cat.load(catalog)
    with duckdb.connect(str(db)) as con:
        simulate.seed(con, c, _now())
    typer.echo(f"seeded {len(c.datasets)} datasets into {db}")


@app.command()
def inject(kind: Failure, dataset: str, db: Path = DB, catalog: Path = CATALOG,
           events: Path | None = EVENTS):
    """Break a dataset on purpose."""
    c = _load(catalog, events)
    with duckdb.connect(str(db)) as con:
        if kind is Failure.stale:
            affected = lineage.downstream(lineage.build(c), dataset)
            simulate.inject_stale(con, c, dataset, _now(), affected)
        else:
            simulate.inject_gap(con, dataset)
    typer.echo(f"injected {kind} into {dataset}")


@app.command()
def run(db: Path = DB, catalog: Path = CATALOG, events: Path | None = EVENTS):
    """Run one check -> decide -> fix/escalate cycle."""
    c = _load(catalog, events)
    with duckdb.connect(str(db)) as con:
        decisions = engine.run_cycle(con, c, lineage.build(c), _now())
    if not decisions:
        typer.echo("all datasets healthy")
    for d in decisions:
        line = f"{d.incident.id:<22} {d.verdict:<15} blast_radius={d.blast_radius}"
        if d.failed:
            line += "  failed: " + ", ".join(r.rule for r in d.failed)
        else:
            line += f"  -> {d.action}"
        typer.echo(line)


@app.command()
def audit(db: Path = DB):
    """Print the audit log as JSON lines."""
    with duckdb.connect(str(db)) as con:
        audit_log.init(con)
        for e in audit_log.entries(con):
            e["at"] = e["at"].isoformat(timespec="seconds") + "Z"
            typer.echo(json.dumps({k: v for k, v in e.items() if v is not None}))


@app.command("lineage")
def lineage_cmd(
    dataset: str | None = typer.Argument(None),
    mermaid: bool = typer.Option(False, help="Print the whole graph as Mermaid"),
    db: Path = DB,
    catalog: Path = CATALOG,
    events: Path | None = EVENTS,
):
    """Show a dataset's blast radius, or the whole graph with --mermaid."""
    g = lineage.build(_load(catalog, events))
    if mermaid:
        states = {}
        if db.exists():
            with duckdb.connect(str(db)) as con:
                audit_log.init(con)
                states = audit_log.latest_states(con)
        typer.echo(lineage.mermaid(g, states))
        return
    if dataset is None:
        raise typer.BadParameter("pass a dataset, or --mermaid")
    affected = lineage.downstream(g, dataset)
    typer.echo(f"{dataset} -> {len(affected)} downstream: {', '.join(affected) or 'none'}")


@app.command()
def serve(port: int = 8000, db: Path = DB, catalog: Path = CATALOG, events: Path | None = EVENTS):
    """Serve a read-only page: lineage graph and audit log."""
    typer.echo(f"serving http://127.0.0.1:{port}")
    web.serve(_load(catalog, events), db, port)
