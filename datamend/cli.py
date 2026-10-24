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
LESSONS = typer.Option(Path(".datamend/lessons.duckdb"), envvar="DATAMEND_LESSONS",
                       help="Lessons store; lessons.md is exported next to it")
lessons_app = typer.Typer(no_args_is_help=True, help="Lessons learned from reviewer feedback.")
ai_app.add_typer(lessons_app, name="lessons")


def _lessons(path: Path) -> list[dict]:
    from datamend.ai import lessons

    if not path.exists():
        return []
    with duckdb.connect(str(path)) as con:
        return lessons.all_lessons(con)


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
    db: Path = DB, catalog: Path = CATALOG, runs: Path = RUNS, lessons: Path = LESSONS,
):
    """Ask the agent to find and fix why INCIDENT (e.g. raw_payments:stale) was escalated."""
    from datamend.ai import client, fixer

    c = cat.load(catalog)
    agent = client.ReplayAgent(replay) if replay else client.HttpAgent(c.ai.endpoint)
    with duckdb.connect(str(db)) as con:
        _print_run(fixer.investigate(con, c, incident, agent, _now(), runs,
                                     lessons=_lessons(lessons)))


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
def show(run_id: str, db: Path = DB, steps: bool = typer.Option(False, help="Print the trajectory")):
    """Everything about one AI run: explanation, guardrails, validation, diff, result."""
    from datamend.ai import fixer

    with duckdb.connect(str(db)) as con:
        r = fixer.get(con, run_id)
    if r is None:
        raise typer.BadParameter(f"no run {run_id}")
    _print_run(r)
    if steps:
        _print_steps(json.loads(r.get("trajectory") or "[]"))


def _print_steps(steps: list[dict]) -> None:
    from datamend.ai import trajectory

    for s in steps:
        mark = {True: "ok ", False: "ERR"}.get(s.get("ok"), "   ")
        attempt = f"a{s['attempt']} " if s.get("attempt") else ""
        typer.echo(f"  {s['n']:>3} {mark} {attempt}{trajectory.describe(s)}  [{s['source']}]")


def _find_run(run_id: str, db: Path, state: Path) -> tuple[str, list[dict]]:
    """(scope for a lesson from this run, its trajectory), from the AI or the watch DB."""
    from datamend.ai import fixer, lessons
    from datamend.remote import watch as remote_watch

    if db.exists():
        with duckdb.connect(str(db)) as con:
            r = fixer.get(con, run_id)
        if r is not None:
            return lessons.scope_for("dataset", r["dataset"]), json.loads(r.get("trajectory") or "[]")
    if state.exists():
        with duckdb.connect(str(state)) as con:
            remote_watch.init(con)
            found = con.execute("SELECT deployment, trajectory FROM remote_runs WHERE run_id = ?",
                                [run_id]).fetchone()
        if found:
            return lessons.scope_for("deployment", found[0]), json.loads(found[1] or "[]")
    raise typer.BadParameter(f"no run {run_id} in {db} or {state}")


class Scope(StrEnum):
    run = "run"
    global_ = "global"


SCOPE = typer.Option(Scope.run, help="run: the run's dataset or deployment; or global")
WATCH_STATE = typer.Option(Path(".datamend/remote.duckdb"), envvar="DATAMEND_STATE")
EXPORT_OUT = typer.Option(None, help="Output file")


@ai_app.command()
def feedback(
    run_id: str,
    text: str,
    step: int | None = typer.Option(None, help="Comment on this step of the run's trajectory"),
    scope: Scope = SCOPE,
    by: str = typer.Option("reviewer", help="Who gives the feedback"),
    db: Path = DB, state: Path = WATCH_STATE,
    lessons: Path = LESSONS,
):
    """Give feedback on a run, or one step of it. It becomes a lesson for later prompts."""
    from datamend.ai import lessons as lessons_mod

    run_scope, steps = _find_run(run_id, db, state)
    chosen = None
    if step is not None:
        chosen = next((s for s in steps if s["n"] == step), None)
        if chosen is None:
            raise typer.BadParameter(f"{run_id} has {len(steps)} steps; no step {step}")
    lessons.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(lessons)) as con:
        row = lessons_mod.add(con, run_id=run_id, feedback=text, step=chosen, author=by, now=_now(),
                              scope="global" if scope is Scope.global_ else run_scope)
        md = lessons_mod.export(con, lessons.with_name("lessons.md"))
    typer.echo(f"{row['lesson_id']}  {row['scope']}  {row['text']}")
    typer.echo(f"later prompts for {row['scope']} include this lesson; exported to {md}")


@lessons_app.command("list")
def lessons_list(lessons: Path = LESSONS, all_: bool = typer.Option(False, "--all",
                 help="Include forgotten lessons")):
    """Lessons that later prompts include, oldest first."""
    from datamend.ai import lessons as lessons_mod

    if not lessons.exists():
        typer.echo("no lessons yet")
        return
    with duckdb.connect(str(lessons)) as con:
        for ls in lessons_mod.all_lessons(con, include_inactive=all_):
            off = "" if ls["active"] else " (forgotten)"
            typer.echo(f"{ls['lesson_id']}  {ls['scope']:<28} {ls['text']}{off}")


@lessons_app.command("forget")
def lessons_forget(lesson_id: str, lessons: Path = LESSONS):
    """Stop including a lesson in prompts. It stays in the store, marked inactive."""
    from datamend.ai import lessons as lessons_mod

    with duckdb.connect(str(lessons)) as con:
        if not lessons_mod.forget(con, lesson_id):
            raise typer.BadParameter(f"no active lesson {lesson_id}")
        lessons_mod.export(con, lessons.with_name("lessons.md"))
    typer.echo(f"forgot {lesson_id}")


@lessons_app.command("export")
def lessons_export(lessons: Path = LESSONS, out: Path | None = EXPORT_OUT):
    """Write active lessons as Markdown (default: lessons.md next to the store)."""
    from datamend.ai import lessons as lessons_mod

    with duckdb.connect(str(lessons)) as con:
        typer.echo(f"wrote {lessons_mod.export(con, out or lessons.with_name('lessons.md'))}")


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


watch_app = typer.Typer(no_args_is_help=True, help="Watch a remote orchestrator and fix failures by PR.")
app.add_typer(watch_app, name="watch")
REPO = typer.Option(..., envvar="DATAMEND_REPO", help="GitHub repo, owner/name")
CLONE_URL = typer.Option(None, envvar="DATAMEND_CLONE_URL",
                         help="Clone/push URL (default https://github.com/<repo>.git)")
BASE = typer.Option("main", envvar="DATAMEND_BASE", help="Base branch for pull requests")
STATE = typer.Option(Path(".datamend/remote.duckdb"), envvar="DATAMEND_STATE")
REMOTE_RUNS = typer.Option(Path(".datamend/remote"), envvar="DATAMEND_REMOTE_RUNS")
LOOKBACK = typer.Option(24, help="Hours of failed runs to consider")
PUBLISH = typer.Option(True, help="Push the branch and open the PR")
AGENT_ENDPOINT = typer.Option("http://127.0.0.1:8787", envvar="DATAMEND_AGENT_ENDPOINT")


def _watch(source, repo, clone_url, base, state, runs_dir, lookback_hours, publish, replay,
           agent_endpoint, lessons) -> None:
    import os

    from datamend.ai import client
    from datamend.remote import github
    from datamend.remote import watch as remote_watch

    token = os.environ.get("GITHUB_TOKEN")
    agent = client.ReplayAgent(replay) if replay else client.HttpAgent(agent_endpoint)
    state.parent.mkdir(parents=True, exist_ok=True)
    results = remote_watch.watch(
        source=source, github=github.GitHub(repo, token) if publish else None,
        clone_url=clone_url or f"https://github.com/{repo}.git", base=base, agent=agent,
        state=state, runs_dir=runs_dir.resolve(), now=_now(), lookback_hours=lookback_hours,
        publish=publish, token=token if not clone_url else None, lessons=_lessons(lessons))
    if not results:
        typer.echo("no new failures")
    for r in results:
        typer.echo(f"{r['run_id']}  {r['status']:<14} {r['deployment']:<24} {r['reason']}")


@watch_app.command("prefect")
def watch_prefect(repo: str = REPO, clone_url: str | None = CLONE_URL, base: str = BASE,
                  state: Path = STATE, runs_dir: Path = REMOTE_RUNS, lookback_hours: int = LOOKBACK,
                  publish: bool = PUBLISH, replay: Path | None = REPLAY,
                  agent_endpoint: str = AGENT_ENDPOINT, lessons: Path = LESSONS):
    """One pass: find failed Prefect runs, fix them in shadow mode, open pull requests.

    Needs PREFECT_API_URL (and PREFECT_API_KEY for Prefect Cloud), GITHUB_TOKEN,
    and an agent endpoint (`datamend agent serve`) or --replay.
    """
    from datamend.remote import prefect

    _watch(prefect.PrefectSource(), repo, clone_url, base, state, runs_dir, lookback_hours,
           publish, replay, agent_endpoint, lessons)


AIRFLOW_URL = typer.Option(..., envvar="AIRFLOW_API_URL", help="Airflow server root, e.g. http://127.0.0.1:8080")
DAGS_DIR = typer.Option("dags", help="Folder in the repo that holds the DAG files")
AIRFLOW_API = typer.Option(None, help="Force the REST API version (v2 or v1); default: detect")


@watch_app.command("airflow")
def watch_airflow(repo: str = REPO, airflow_url: str = AIRFLOW_URL, dags_dir: str = DAGS_DIR,
                  api: str | None = AIRFLOW_API, clone_url: str | None = CLONE_URL,
                  base: str = BASE, state: Path = STATE, runs_dir: Path = REMOTE_RUNS,
                  lookback_hours: int = LOOKBACK, publish: bool = PUBLISH,
                  replay: Path | None = REPLAY, agent_endpoint: str = AGENT_ENDPOINT,
                  lessons: Path = LESSONS):
    """One pass: find failed Airflow DAG runs, fix them in shadow mode, open pull requests.

    Auth: AIRFLOW_TOKEN, or AIRFLOW_USERNAME and AIRFLOW_PASSWORD. Shadow runs use
    `airflow dags test` in the repo's own venv, with a throwaway metadata DB.
    """
    from datamend.remote import airflow

    _watch(airflow.AirflowSource(airflow_url, api=api, repo_dags_dir=dags_dir), repo, clone_url,
           base, state, runs_dir, lookback_hours, publish, replay, agent_endpoint, lessons)
