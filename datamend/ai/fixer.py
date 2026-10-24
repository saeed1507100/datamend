"""Investigate an escalated incident with an AI agent, then validate before anything ships.

investigate():  gate -> sandbox -> agent -> guardrails -> shadow validation -> propose
approve():      re-check -> apply patch -> git branch -> rerun -> post-checks -> audit

Nothing the agent writes reaches the pipeline until every guardrail passes, the
validation score meets `ai.min_score`, and (by default) a person approves it.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from datetime import datetime
from pathlib import Path

import networkx as nx

from datamend import adapters, audit, checks, lineage
from datamend.adapters.local import load_job
from datamend.adapters.sql import DbApiWarehouse
from datamend.ai import guardrails, trajectory
from datamend.ai import lessons as lessons_mod
from datamend.catalog import Catalog

SCHEMA = """
CREATE TABLE IF NOT EXISTS ai_runs (
    run_id VARCHAR PRIMARY KEY, incident VARCHAR, dataset VARCHAR, job_file VARCHAR,
    status VARCHAR, status_reason VARCHAR, backend VARCHAR, model VARCHAR,
    prompt VARCHAR, response VARCHAR, explanation JSON, diff VARCHAR, original_sha VARCHAR,
    guardrails JSON, validation JSON, score DOUBLE, usage JSON,
    approved_by VARCHAR, commit_ref VARCHAR, post_checks JSON, result VARCHAR,
    created_at TIMESTAMP, decided_at TIMESTAMP
)
"""

SYSTEM = """You are a data reliability engineer working inside a sandbox copy of a data
pipeline repository. You may read files and edit exactly one file: the job named in the
task. You cannot run code. A separate validator will run your change on a shadow copy
of the warehouse, and a human reviews it before it ships. Prefer the smallest correct
change. Do not change the table's schema or what data it represents."""


def init(con) -> None:
    con.execute(SCHEMA)
    con.execute("ALTER TABLE ai_runs ADD COLUMN IF NOT EXISTS trajectory JSON")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _failed_rules(reason: str) -> list[str]:
    return [part.split(":", 1)[0].strip() for part in reason.split(";") if ":" in part]


def _escalation(con, incident_id: str) -> dict | None:
    row = con.execute(
        "SELECT dataset, reason FROM audit WHERE incident = ? AND decision = 'needs_approval' "
        'ORDER BY "at" DESC LIMIT 1', [incident_id]).fetchone()
    return None if row is None else {"dataset": row[0], "reason": row[1]}


def build_prompt(catalog: Catalog, g: nx.DiGraph, dataset: str, reason: str, job_rel: str,
                 lessons: list[str] = ()) -> str:
    ds = catalog.get(dataset)
    downstream = lineage.downstream(g, dataset)
    return f"""## Incident
Dataset `{dataset}` is stale. datamend did not auto-rerun it because a guardrail failed:
{reason}

## Context
- Job: `{ds.job}`, file `{job_rel}`. Each job is a Python file with `run(con, now, mode)`.
  `con` is a DuckDB connection, `now` is the load timestamp, `mode` is "rerun" or "backfill".
- Upstreams: {", ".join(ds.upstreams) or "none (source table)"}
- Downstream, rebuilt after this job: {", ".join(downstream) or "none"}
- Helpers available in `datamend/pipeline.py` are NOT in this sandbox; use plain SQL via `con`.
{lessons_mod.prompt_section(list(lessons))}
## Task
1. Read `{job_rel}` and the downstream jobs in `pipelines/` to understand the impact.
2. Find why rerunning `{ds.job}` is unsafe.
3. Edit only `{job_rel}` so that running it any number of times, in either mode, leaves
   `{dataset}` with the same rows as running it once. Keep the schema and columns.
4. Reply with ONLY this JSON object, no prose around it:
{{"root_cause": "...", "fix": "...", "rationale": "why this fix and not another",
  "risk": "low|medium|high, and what could still go wrong", "confidence": 0.0}}
"""


def _parse_explanation(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    needed = {"root_cause", "fix", "rationale", "risk"}
    return data if needed <= data.keys() else None


def _snapshot(con, tables: list[str]) -> dict:
    out = {}
    for t in tables:
        rows, ids, digest = con.execute(
            "SELECT count(*), count(DISTINCT id), coalesce(sum(hash(r)), 0) "
            f'FROM (SELECT * EXCLUDE (_loaded_at) FROM "{t}") r').fetchone()
        out[t] = {"rows": rows, "distinct_ids": ids, "digest": str(digest)}
    return out


def validate(con, catalog: Catalog, g: nx.DiGraph, dataset: str, patched: Path,
             now: datetime) -> list[dict]:
    """Run the patched job on a shadow copy of the warehouse. Never touches live tables."""
    ds = catalog.get(dataset)
    downstream = [catalog.get(n) for n in lineage.downstream(g, dataset)]
    pipelines = Path(catalog.base_dir / catalog.platform.orchestrator["pipelines"])
    live_db = con.execute("SELECT current_database()").fetchone()[0]
    columns_before = con.execute(f'DESCRIBE "{dataset}"').fetchall()
    tables = [t for (t,) in con.execute("SHOW TABLES").fetchall() if t not in ("audit", "ai_runs")]

    con.execute("ATTACH ':memory:' AS shadow")
    results = []

    def check(name: str, passed: bool, detail: str):
        results.append({"check": name, "passed": bool(passed), "detail": detail})

    try:
        for t in tables:
            con.execute(f'CREATE TABLE shadow."{t}" AS SELECT * FROM "{live_db}"."{t}"')
        con.execute("USE shadow")
        chain = [dataset] + [d.name for d in downstream]

        def run_chain():
            load_job(patched).run(con, now, "rerun")
            for d in downstream:
                load_job(pipelines / f"{d.job}.py").run(con, now, "rerun")

        try:
            run_chain()
            first = _snapshot(con, chain)
            run_chain()
            second = _snapshot(con, chain)
            load_job(patched).run(con, now, "backfill")
            third = _snapshot(con, [dataset])
            check("runs_clean", True, "rerun x2 and backfill x1 completed without errors")
        except Exception as exc:  # noqa: BLE001 - any failure of AI-written code is a result
            check("runs_clean", False, f"{type(exc).__name__}: {exc}"[:300])
            return results

        check("idempotent_rerun", first == second,
              "second rerun changed nothing in " + ", ".join(chain) if first == second
              else f"rerun changed data: {first} -> {second}")
        check("idempotent_backfill", third[dataset] == first[dataset],
              "backfill after rerun changed nothing" if third[dataset] == first[dataset]
              else f"backfill changed {dataset}: {first[dataset]} -> {third[dataset]}")
        s = second[dataset]
        check("unique_ids", s["rows"] == s["distinct_ids"],
              f"{s['rows']} rows, {s['distinct_ids']} distinct ids")
        columns_after = con.execute(f'DESCRIBE "{dataset}"').fetchall()
        check("schema_stable", columns_before == columns_after,
              f"{len(columns_after)} columns, names and types unchanged"
              if columns_before == columns_after else f"{columns_before} -> {columns_after}")
        live_ids = con.execute(
            f'SELECT count(DISTINCT id) FROM "{live_db}"."{dataset}"').fetchone()[0]
        check("volume", s["distinct_ids"] == live_ids,
              f"{s['distinct_ids']} ids, live table has {live_ids} distinct ids")
        shadow = DbApiWarehouse(con)
        # Cursors don't inherit USE, so name the shadow tables explicitly.
        in_shadow = [d.model_copy(update={"table": f"shadow.{d.name}"}) for d in [ds, *downstream]]
        stale = [i.id for i in checks.run_all(shadow, catalog.model_copy(
            update={"datasets": in_shadow}), now)]
        check("post_checks", not stale,
              f"{len(chain)} datasets fresh and complete" if not stale else ", ".join(stale))
        return results
    finally:
        con.execute(f'USE "{live_db}"')
        con.execute("DETACH shadow")


def investigate(con, catalog: Catalog, incident_id: str, agent, now: datetime,
                runs_dir: Path = Path(".datamend/ai"), lessons: list[dict] | None = None) -> dict:
    """`lessons`: rows from `lessons.all_lessons()`; global and this dataset's go in the prompt."""
    init(con)
    g = lineage.build(catalog)
    run_id = f"ai_{now:%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}"
    cfg = catalog.ai
    row = {"run_id": run_id, "incident": incident_id, "backend": agent.name,
           "model": cfg.model, "created_at": now}
    steps: list[dict] = []

    def finish(status: str, reason: str) -> dict:
        trajectory.add(steps, "decision", summary=f"{status}: {reason}")
        row.update(status=status, status_reason=reason, trajectory=json.dumps(steps))
        _save(con, row)
        return row

    esc = _escalation(con, incident_id)
    if esc is None:
        row["dataset"] = incident_id.split(":")[0]
        return finish("refused", "no needs_approval decision for this incident in the audit log")
    dataset = row["dataset"] = esc["dataset"]
    failed = _failed_rules(esc["reason"])
    gate = [r for r in failed if r not in cfg.eligible_rules]
    if gate:
        return finish("refused", f"rules {', '.join(gate)} need a person, not an AI "
                                 f"(eligible: {', '.join(cfg.eligible_rules)})")

    pipelines = Path(catalog.base_dir / catalog.platform.orchestrator["pipelines"])
    job_rel = f"pipelines/{catalog.get(dataset).job}.py"
    original = (pipelines / f"{catalog.get(dataset).job}.py").read_text()
    row.update(job_file=job_rel, original_sha=_sha(original))

    workspace = (runs_dir / run_id / "workspace").resolve()
    shutil.copytree(pipelines, workspace / "pipelines")
    before = {p.relative_to(workspace): p.read_text() for p in workspace.rglob("*.py")}

    prompt = build_prompt(catalog, g, dataset, esc["reason"], job_rel,
                          lessons_mod.select(lessons, f"dataset:{dataset}"))
    row["prompt"] = prompt
    try:
        response = agent.messages({
            "model": cfg.model, "system": SYSTEM, "max_turns": cfg.max_turns,
            "max_budget_usd": cfg.max_budget_usd,
            "messages": [{"role": "user", "content": prompt}],
            "metadata": {"workspace": str(workspace)},
        })
    except Exception as exc:  # noqa: BLE001 - the run is recorded whatever the agent does
        return finish("failed", f"agent call failed: {type(exc).__name__}: {exc}"[:500])
    text = "".join(b.get("text", "") for b in response.get("content", []))
    meta = dict(response.get("datamend", {}))
    trajectory.extend(steps, meta.pop("trajectory", None) or [])
    row.update(response=text, usage=json.dumps({**response.get("usage", {}), **meta}))
    after = {p.relative_to(workspace): p.read_text() for p in workspace.rglob("*.py")}
    patched_text = after.get(Path(job_rel), "")
    diff = guardrails.unified_diff(original, patched_text, job_rel)
    explanation = _parse_explanation(text)
    row.update(diff=diff, explanation=json.dumps(explanation))

    changed = sorted(str(p) for p in set(before) | set(after) if before.get(p) != after.get(p))
    n_lines = guardrails.diff_lines(diff)
    safe, safe_detail = guardrails.static_safety(patched_text)
    rails = [
        {"rule": "eligible_incident", "passed": True,
         "detail": f"escalated for {', '.join(failed)}; AI may handle {', '.join(cfg.eligible_rules)}"},
        {"rule": "single_file", "passed": changed == [job_rel],
         "detail": f"changed: {', '.join(changed) or 'nothing'}"},
        {"rule": "diff_size", "passed": 0 < n_lines <= cfg.max_diff_lines,
         "detail": f"{n_lines} changed lines, limit {cfg.max_diff_lines}"},
        {"rule": "static_safety", "passed": safe, "detail": safe_detail},
        {"rule": "explained", "passed": explanation is not None,
         "detail": "root cause, fix, rationale, and risk given" if explanation
         else "reply did not include the required JSON"},
    ]
    row["guardrails"] = json.dumps(rails)
    trajectory.add(steps, "guardrails", ok=all(r["passed"] for r in rails),
                   summary=f"{sum(r['passed'] for r in rails)}/{len(rails)} passed",
                   items=[{"name": r["rule"], "passed": r["passed"]} for r in rails])
    if not all(r["passed"] for r in rails):
        return finish("rejected", "guardrail failed: "
                      + ", ".join(r["rule"] for r in rails if not r["passed"]))

    try:
        results = validate(con, catalog, g, dataset, workspace / job_rel, now)
    except Exception as exc:  # noqa: BLE001 - a validator bug must fail closed, and be recorded
        return finish("failed", f"validator error: {type(exc).__name__}: {exc}"[:500])
    score = sum(r["passed"] for r in results) / len(results)
    row.update(validation=json.dumps(results), score=score)
    trajectory.add(steps, "validation", ok=score >= cfg.min_score,
                   summary=f"shadow validation {sum(r['passed'] for r in results)}/{len(results)}",
                   items=[{"name": r["check"], "passed": r["passed"]} for r in results])
    if score < cfg.min_score:
        return finish("rejected", f"validation score {score:.2f} < {cfg.min_score:.2f}")

    finish("proposed", f"all {len(rails)} guardrails passed, validation "
                       f"{sum(r['passed'] for r in results)}/{len(results)}")
    audit.record_ai(con, incident_id, dataset, "ai_proposed", f"patch:{job_rel}",
                    row["status_reason"], f"run {run_id}", now)
    if cfg.apply == "auto":
        return approve(con, catalog, run_id, "datamend (ai.apply=auto)", now, runs_dir)
    return row


def approve(con, catalog: Catalog, run_id: str, approver: str, now: datetime,
            runs_dir: Path = Path(".datamend/ai")) -> dict:
    row = get(con, run_id)
    if row is None or row["status"] != "proposed":
        raise ValueError(f"{run_id} is not a proposed fix (status: {row and row['status']})")
    g = lineage.build(catalog)
    root = Path(catalog.base_dir)
    pipelines = root / catalog.platform.orchestrator["pipelines"]
    target = pipelines / Path(row["job_file"]).name
    if _sha(target.read_text()) != row["original_sha"]:
        raise ValueError(f"{row['job_file']} changed since the AI read it; investigate again")

    # Prepare every change in memory, commit it, and only then write files,
    # so a failure part-way leaves the pipeline untouched.
    changes = {target: (runs_dir / run_id / "workspace" / row["job_file"]).read_text()}
    catalog_file = root / "datasets.yaml"
    marked = _mark_idempotent(catalog_file, row["dataset"])
    if marked is not None:
        changes[catalog_file] = marked
    commit = _git_branch(root, changes, run_id, row, approver) if catalog.ai.git_branch else None
    if commit and catalog.ai.git_push:
        subprocess.run(["git", "-C", str(root), "push", "origin", commit["branch"]],
                       check=True, capture_output=True)
    for path, text in changes.items():
        path.write_text(text)

    platform = adapters.build(catalog, con)
    ds = catalog.get(row["dataset"])
    chain = [ds, *[catalog.get(n) for n in lineage.downstream(g, ds.name)]]
    for d in chain:
        platform.orchestrator.trigger(d, "rerun", now)
    sub = catalog.model_copy(update={"datasets": chain})
    open_incidents = [i.id for i in checks.run_all(platform.warehouse, sub, now)]
    result = "healthy" if not open_incidents else "still failing: " + ", ".join(open_incidents)

    con.execute(
        "UPDATE ai_runs SET status = 'applied', approved_by = ?, commit_ref = ?, "
        "post_checks = ?, result = ?, decided_at = ? WHERE run_id = ?",
        [approver, json.dumps(commit), json.dumps(open_incidents), result, now, run_id])
    audit.record_ai(con, row["incident"], row["dataset"], "ai_fix", f"patch:{row['job_file']}",
                    f"approved by {approver}; validation score {row['score']:.2f}",
                    result, now)
    return get(con, run_id)


def reject(con, run_id: str, by: str, reason: str, now: datetime) -> dict:
    con.execute("UPDATE ai_runs SET status = 'rejected', status_reason = ?, approved_by = ?, "
                "decided_at = ? WHERE run_id = ? AND status = 'proposed'",
                [f"rejected by {by}: {reason}", by, now, run_id])
    return get(con, run_id)


def _mark_idempotent(catalog_file: Path, dataset: str) -> str | None:
    """Catalog text with `idempotent: false` flipped on the dataset's line, or None."""
    if not catalog_file.exists():
        return None
    lines = catalog_file.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        if re.search(rf"\bname:\s*{re.escape(dataset)}\b", line) and "idempotent: false" in line:
            lines[i] = line.replace("idempotent: false", "idempotent: true")
            return "".join(lines)
    return None


def _git_branch(root: Path, changes: dict[Path, str], run_id: str, row: dict,
                approver: str) -> dict | None:
    """Commit the fix on a new branch without touching the checked-out branch or index."""
    def git(*args, env=None, input=None):
        return subprocess.run(["git", "-C", str(root), *args], check=True, text=True,
                              capture_output=True, env=env, input=input).stdout.strip()

    try:
        top = Path(git("rev-parse", "--show-toplevel"))
        head = git("rev-parse", "HEAD")
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    index = root.resolve() / f".git-index-{run_id}"
    env = {**os.environ, "GIT_INDEX_FILE": str(index)}
    try:
        git("read-tree", head, env=env)
        for path, text in changes.items():
            blob = git("hash-object", "-w", "--stdin", input=text)
            rel = path.resolve().relative_to(top)
            git("update-index", "--add", "--cacheinfo", f"100644,{blob},{rel}", env=env)
        tree = git("write-tree", env=env)
    finally:
        index.unlink(missing_ok=True)
    expl = json.loads(row["explanation"] or "{}")
    message = (f"datamend: fix {row['incident']} ({run_id})\n\n"
               f"Root cause: {expl.get('root_cause', '')}\nFix: {expl.get('fix', '')}\n"
               f"Validation score: {row['score']:.2f}\nApproved-by: {approver}\n")
    bot = {"GIT_AUTHOR_NAME": "datamend[bot]", "GIT_AUTHOR_EMAIL": "datamend@users.noreply.github.com",
           "GIT_COMMITTER_NAME": "datamend[bot]",
           "GIT_COMMITTER_EMAIL": "datamend@users.noreply.github.com"}
    commit = git("commit-tree", tree, "-p", head, input=message, env={**os.environ, **bot})
    branch = f"datamend/ai-fix-{run_id}"
    git("update-ref", f"refs/heads/{branch}", commit)
    return {"branch": branch, "commit": commit[:12]}


def _save(con, row: dict) -> None:
    cols = list(row)
    con.execute(f"INSERT OR REPLACE INTO ai_runs ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})", [row[c] for c in cols])


def get(con, run_id: str) -> dict | None:
    init(con)
    cur = con.execute("SELECT * FROM ai_runs WHERE run_id = ?", [run_id])
    found = cur.fetchone()
    return None if found is None else dict(zip([c[0] for c in cur.description], found))


def runs(con) -> list[dict]:
    init(con)
    cur = con.execute("SELECT * FROM ai_runs ORDER BY created_at")
    return [dict(zip([c[0] for c in cur.description], r)) for r in cur.fetchall()]
