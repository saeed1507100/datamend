"""Export the demo story as JSON for the showcase site.

Runs the real system end to end in a temp folder (seed, break, detect, decide,
fix, AI investigate with the recorded run, approve) and snapshots every
dataset's state after each step. The site replays this file; nothing is made up.
"""

import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import duckdb
import networkx as nx

from datamend import adapters, audit, catalog, checks, engine, lineage, simulate
from datamend.ai import client, fixer, lessons


def _graph(cat: catalog.Catalog, g: nx.DiGraph) -> dict:
    layer = {}
    for n in nx.topological_sort(g):
        layer[n] = max((layer[p] + 1 for p in g.predecessors(n)), default=0)
    return {
        "nodes": [{"id": d.name, "layer": layer[d.name], "job": d.job,
                   "cadence_minutes": d.cadence_minutes, "idempotent": d.idempotent,
                   "critical": d.critical} for d in cat.datasets],
        "edges": [{"from": u, "to": v} for u, v in sorted(g.edges)],
    }


def _stats(platform, cat, now) -> dict:
    out = {}
    for d in cat.datasets:
        rows, last = platform.warehouse.stats(d)
        out[d.name] = {"rows": rows,
                       "age_minutes": None if last is None else int((now - last).total_seconds() // 60)}
    return out


EXAMPLE_FEEDBACK = ("Good fix. Next time, also say in the risk which downstream tables "
                    "a rerun rebuilds, so the reviewer can check them.")


def _feedback_example(cat: catalog.Catalog, g: nx.DiGraph, run: dict, now: datetime) -> dict:
    """A reviewer comment on the run's edit step, the lesson it became, and the next prompt."""
    steps = json.loads(run["trajectory"])
    edit = next(s for s in steps if s.get("tool") == "Edit")
    with duckdb.connect() as con:
        lesson = lessons.add(con, run_id=run["run_id"], feedback=EXAMPLE_FEEDBACK, step=edit,
                             scope=lessons.scope_for("dataset", run["dataset"]), now=now,
                             author="data-eng on-call")
        rows = lessons.all_lessons(con)
    prompt = fixer.build_prompt(cat, g, run["dataset"], "idempotent: job is not idempotent",
                                run["job_file"], lessons.select(rows, f"dataset:{run['dataset']}"))
    start = prompt.index("## Lessons from past reviews")
    return {
        "feedback": {"run_id": run["run_id"], "step": edit["n"], "author": lesson["author"],
                     "text": EXAMPLE_FEEDBACK},
        "lesson": {k: lesson[k] for k in ("lesson_id", "scope", "text", "step", "step_summary")},
        "next_prompt_excerpt": prompt[start:prompt.index("## Task", start)].strip(),
        "cli": [f'datamend ai feedback {run["run_id"]} --step {edit["n"]} "{EXAMPLE_FEEDBACK}"',
                "datamend ai lessons list"],
    }


def export(recording: Path, examples: Path, now: datetime | None = None) -> dict:
    now = now or datetime(2026, 10, 24, 9, 0)
    tmp = Path(tempfile.mkdtemp(prefix="datamend-showcase-"))
    try:
        root = tmp / "demo"
        shutil.copytree(examples, root)
        git = ["git", "-C", str(root), "-c", "user.name=datamend", "-c", "user.email=demo@localhost"]
        subprocess.run(["git", "init", "-q", str(root)], check=True)
        subprocess.run([*git, "add", "-A"], check=True)
        subprocess.run([*git, "commit", "-qm", "pipelines"], check=True)

        cat = catalog.load(root / "datasets.yaml")
        g = lineage.build(cat)
        con = duckdb.connect(str(tmp / "w.duckdb"))
        platform = adapters.build(cat, con)
        steps = []

        def step(key, title, summary, states, **extra):
            steps.append({"key": key, "title": title, "summary": summary, "states": states,
                          "stats": _stats(platform, cat, now), **extra})

        healthy = {d.name: "healthy" for d in cat.datasets}
        simulate.seed(con, cat, now - timedelta(minutes=5))
        step("load", "Pipelines load",
             "Ten datasets in three layers load on schedule. Each job is a Python file "
             "run by the orchestrator.", healthy,
             order=list(nx.topological_sort(g)))

        simulate.inject_stale(con, cat, "raw_orders", now, lineage.downstream(g, "raw_orders"))
        simulate.inject_stale(con, cat, "raw_payments", now, lineage.downstream(g, "raw_payments"))
        simulate.inject_gap(con, "raw_customers")
        incidents = checks.run_all(platform.warehouse, cat, now)
        broken = {**healthy, **{i.dataset: i.kind for i in incidents}}
        step("detect", "Failures detected",
             f"Two loads stop and one loses rows. Freshness and row-count checks raise "
             f"{len(incidents)} incidents.", broken,
             incidents=[{"id": i.id, "dataset": i.dataset, "kind": i.kind, "detail": i.detail}
                        for i in incidents])

        stale = {i.dataset for i in incidents if i.kind == "stale"}
        roots = lineage.root_causes(g, stale)
        step("lineage", "Lineage finds the roots",
             f"{len(stale)} stale datasets trace back to {len(roots)} root causes. "
             "Everything downstream is a symptom, not a separate incident.", broken,
             roots=[{"dataset": r, "blast_radius": lineage.downstream(g, r)} for r in roots])

        decisions = engine.run_cycle(con, cat, g, now, platform)
        after = dict(healthy)
        for e in audit.entries(con):
            after[e["dataset"]] = e["decision"]
        step("decide", "Guardrails decide, safe fixes run",
             "Each root faces five rules. Two pass and are fixed automatically. "
             "raw_payments fails `idempotent`, so it goes to a person, and its "
             "downstream datasets wait instead of being rebuilt on bad data.", after,
             decisions=[{"incident": d.incident.id, "verdict": d.verdict, "action": d.action,
                         "blast_radius": d.blast_radius,
                         "rules": [r.model_dump() for r in d.rules]} for d in decisions])

        run = fixer.investigate(con, cat, "raw_payments:stale", client.ReplayAgent(recording),
                                now, tmp / "runs")
        rec = json.loads(recording.read_text())
        step("investigate", "AI investigates the escalation",
             "The agent reads the pipeline in a sandbox and edits one file. Static "
             "guardrails and a shadow-warehouse validation check the patch before a "
             "person sees it.", {**after, "raw_payments": "ai_proposed"},
             ai={"run_id": run["run_id"], "status": run["status"],
                 "status_reason": run["status_reason"], "prompt": run["prompt"],
                 "explanation": json.loads(run["explanation"]),
                 "guardrails": json.loads(run["guardrails"]),
                 "validation": json.loads(run["validation"]), "score": run["score"],
                 "diff": run["diff"], "model": rec["model"],
                 "recorded_at": rec["recorded_at"],
                 "usage": {k: v for k, v in rec["response"]["datamend"].items() if k != "trajectory"},
                 "trajectory": json.loads(run["trajectory"]),
                 "trajectory_source": rec.get("trajectory_source", "live")})
        feedback = _feedback_example(cat, g, run, now)

        done = fixer.approve(con, cat, run["run_id"], "data-eng on-call", now, tmp / "runs")
        final = {**healthy, **{d: "ai_fix" for d in ["raw_payments"]},
                 **{d: "auto_fix" for d in ("raw_orders", "raw_customers")}}
        step("approve", "Approved, shipped, rerun, verified",
             "A person approves. datamend commits the fix to a branch, reruns the chain, "
             "and re-checks it. Every dataset is healthy, and the catalog now marks the "
             "job idempotent.", final,
             approval={"approved_by": done["approved_by"], "commit": json.loads(done["commit_ref"]),
                       "result": done["result"]})

        entries = [{**e, "at": e["at"].isoformat() + "Z"} for e in audit.entries(con)]
        return {"generated_by": "datamend export-scenario", "now": now.isoformat() + "Z",
                "graph": _graph(cat, g), "steps": steps, "audit": entries,
                "policy": cat.policy.model_dump(), "ai_config": cat.ai.model_dump(),
                "feedback_example": feedback}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
