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


ai_app = typer.Typer(no_args_is_help=True, help="Investigate escalated incidents with an AI agent.")
agent_app = typer.Typer(no_args_is_help=True, help="The local agent endpoint (Claude Code CLI).")
app.add_typer(ai_app, name="ai")
app.add_typer(agent_app, name="agent")
RUNS = typer.Option(Path(".datamend/ai"), envvar="DATAMEND_AI_RUNS", help="AI run folders")
REPLAY = typer.Option(None, help="Replay a recorded agent run instead of calling one")
BY = typer.Option(..., help="Who decides; recorded in the audit trail")
REASON = typer.Option(..., help="Why; recorded in the audit trail")
BARE = typer.Option(False, help="claude --bare (needs ANTHROPIC_API_KEY)")


def _print_run(r: dict) -> None:
    typer.echo(f"{r['run_id']}  {r['status']}  {r['incident']}")
    typer.echo(f"  reason: {r['status_reason']}")
    expl = json.loads(r.get("explanation") or "null") or {}
    for key in ("root_cause", "fix", "rationale", "risk"):
        if key in expl:
            typer.echo(f"  {key}: {expl[key]}")
    for g in json.loads(r.get("guardrails") or "[]"):
        typer.echo(f"  guardrail {'PASS' if g['passed'] else 'FAIL'} {g['rule']}: {g['detail']}")
    for v in json.loads(r.get("validation") or "[]"):
        typer.echo(f"  validate  {'PASS' if v['passed'] else 'FAIL'} {v['check']}: {v['detail']}")
    if r.get("score") is not None:
        typer.echo(f"  score: {r['score']:.2f}")
    if r.get("diff"):
        typer.echo(r["diff"])
    if r.get("result"):
        typer.echo(f"  result: {r['result']}  commit: {r.get('commit_ref')}")


@ai_app.command()
def investigate(
    incident: str,
    replay: Path | None = REPLAY,
    db: Path = DB, catalog: Path = CATALOG, runs: Path = RUNS,
):
    """Ask the agent to find and fix why INCIDENT (e.g. raw_payments:stale) was escalated."""
    from datamend.ai import client, fixer

    c = cat.load(catalog)
    agent = client.ReplayAgent(replay) if replay else client.HttpAgent(c.ai.endpoint)
    with duckdb.connect(str(db)) as con:
        _print_run(fixer.investigate(con, c, incident, agent, _now(), runs))


@ai_app.command()
def approve(run_id: str, by: str = BY,
            db: Path = DB, catalog: Path = CATALOG, runs: Path = RUNS):
    """Apply a proposed fix: patch, git branch, rerun, re-check, audit."""
    from datamend.ai import fixer

    with duckdb.connect(str(db)) as con:
        _print_run(fixer.approve(con, cat.load(catalog), run_id, by, _now(), runs))


@ai_app.command()
def reject(run_id: str, by: str = BY, reason: str = REASON,
           db: Path = DB):
    """Reject a proposed fix. It stays in the audit trail."""
    from datamend.ai import fixer

    with duckdb.connect(str(db)) as con:
        _print_run(fixer.reject(con, run_id, by, reason, _now()))


@ai_app.command()
def show(run_id: str, db: Path = DB):
    """Everything about one AI run: explanation, guardrails, validation, diff, result."""
    from datamend.ai import fixer

    with duckdb.connect(str(db)) as con:
        r = fixer.get(con, run_id)
    if r is None:
        raise typer.BadParameter(f"no run {run_id}")
    _print_run(r)


@ai_app.command("list")
def list_runs(db: Path = DB):
    """All AI runs, oldest first."""
    from datamend.ai import fixer

    with duckdb.connect(str(db)) as con:
        for r in fixer.runs(con):
            typer.echo(f"{r['run_id']}  {r['status']:<9} {r['incident']:<22} {r['status_reason']}")


@agent_app.command("serve")
def agent_serve(port: int = 8787, workspace_root: Path = RUNS,
                bare: bool = BARE):
    """Serve POST /v1/messages on 127.0.0.1, backed by `claude -p` in a locked-down sandbox."""
    from datamend.ai import server

    workspace_root.mkdir(parents=True, exist_ok=True)
    typer.echo(f"agent endpoint on http://127.0.0.1:{port}/v1/messages")
    server.serve(port, workspace_root, bare=bare)


SCENARIO_OUT = typer.Option(Path("site/src/scenario.json"))
RECORDING = typer.Option(Path("examples/ai/raw_payments_stale.json"))
EXAMPLES = typer.Option(Path("examples"))


@app.command("export-scenario")
def export_scenario(out: Path = SCENARIO_OUT, recording: Path = RECORDING,
                    examples: Path = EXAMPLES):
    """Run the full story in a temp folder and write it as JSON for the showcase site."""
    from datamend import showcase

    data = showcase.export(recording, examples)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=1, default=str) + "\n")
    typer.echo(f"wrote {len(data['steps'])} steps, {len(data['audit'])} audit entries to {out}")
