"""CLI: seed, inject, run, audit, lineage."""

import json
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path

import duckdb
import typer

from datamend import audit as audit_log
from datamend import catalog as cat
from datamend import engine, lineage, simulate

app = typer.Typer(no_args_is_help=True, add_completion=False)

DB = typer.Option(Path("datamend.duckdb"), envvar="DATAMEND_DB", help="DuckDB file")
CATALOG = typer.Option(Path("examples/datasets.yaml"), envvar="DATAMEND_CATALOG")


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
def inject(kind: Failure, dataset: str, db: Path = DB, catalog: Path = CATALOG):
    """Break a dataset on purpose."""
    c = cat.load(catalog)
    with duckdb.connect(str(db)) as con:
        if kind is Failure.stale:
            affected = lineage.downstream(lineage.build(c), dataset)
            simulate.inject_stale(con, c, dataset, _now(), affected)
        else:
            simulate.inject_gap(con, dataset)
    typer.echo(f"injected {kind} into {dataset}")


@app.command()
def run(db: Path = DB, catalog: Path = CATALOG):
    """Run one check -> decide -> fix/escalate cycle."""
    c = cat.load(catalog)
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
def lineage_cmd(dataset: str, catalog: Path = CATALOG):
    """Show the blast radius of a dataset."""
    affected = lineage.downstream(lineage.build(cat.load(catalog)), dataset)
    typer.echo(f"{dataset} -> {len(affected)} downstream: {', '.join(affected) or 'none'}")
