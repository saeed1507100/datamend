"""One watch pass over a Prefect workspace: failed runs in, pull requests out.

For each new failure: clone the repo, install its dependencies, reproduce the failure in
shadow mode, let the agent patch allowed files, validate the patch in shadow mode (twice,
plus the repo's tests), compare shadow output with production, and open a pull request
that explains all of it. Every step is recorded in the `remote_runs` table.
"""

import fnmatch
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timedelta
from pathlib import Path

import duckdb

from datamend.remote import compare
from datamend.remote.config import Deployment, RepoConfig
from datamend.remote.config import load as load_config
from datamend.remote.prefect import FailedRun, PrefectSource
from datamend.remote.workspace import Workspace

SCHEMA = """
CREATE TABLE IF NOT EXISTS remote_runs (
    run_id VARCHAR PRIMARY KEY, flow_run_id VARCHAR, deployment VARCHAR, signature VARCHAR,
    error VARCHAR, status VARCHAR, reason VARCHAR, attempts INTEGER, explanation JSON,
    diff VARCHAR, guardrails JSON, validation JSON, comparison JSON, score DOUBLE,
    branch VARCHAR, commit_sha VARCHAR, pr_url VARCHAR, usage JSON,
    created_at TIMESTAMP, finished_at TIMESTAMP
)
"""
# Columns added after the first release; ADD COLUMN IF NOT EXISTS upgrades old state DBs.
COLUMNS = {"confidence": "DOUBLE", "confidence_breakdown": "JSON", "review": "JSON"}

SYSTEM = """You are a data reliability engineer. A production data pipeline failed. You are
in a sandbox clone of its repository. You may read any file and edit only the allowed
paths. You cannot run code: datamend will run your change in shadow mode, compare its
output with production, and a human will review the pull request. Fix the root cause,
not the symptom. Keep the change small, keep the output schema unchanged, and never
silently drop or zero out bad records unless the data contract says to."""

SECRET_PATTERNS = re.compile(
    r"(AKIA[0-9A-Z]{16}|gh[pousr]_[A-Za-z0-9]{30,}|github_pat_\w{20,}|sk-ant-[\w-]{20,}"
    r"|pnu_[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)")
RISKY_ADDITIONS = re.compile(
    r"\b(subprocess|os\.system|os\.popen|eval\(|exec\(|socket\.|shutil\.rmtree|DROP\s+(TABLE|SCHEMA)"
    r"|TRUNCATE)\b", re.IGNORECASE)


def signature(deployment: str, message: str) -> str:
    """Stable id for 'this deployment fails this way', ignoring values that change per run."""
    norm = re.sub(r"'[^']*'|\"[^\"]*\"|\d+(\.\d+)?", "<v>", message)
    return hashlib.sha256(f"{deployment}|{norm}".encode()).hexdigest()[:12]


def init(con) -> None:
    con.execute(SCHEMA)
    for col, typ in COLUMNS.items():
        con.execute(f"ALTER TABLE remote_runs ADD COLUMN IF NOT EXISTS {col} {typ}")


def _save(con, row: dict) -> None:
    cols = list(row)
    con.execute(f"INSERT OR REPLACE INTO remote_runs ({', '.join(cols)}) "
                f"VALUES ({', '.join('?' * len(cols))})",
                [json.dumps(v) if isinstance(v, (dict, list)) else v for v in row.values()])


def build_prompt(run: FailedRun, dep: Deployment, logs: str, repro: dict,
                 feedback: list[str]) -> str:
    entry_file = run.entrypoint.split(":")[0]
    text = f"""## Failed run
Deployment `{run.deployment}`, run `{run.name}` ({run.state_type}).
Entrypoint: `{run.entrypoint}`. Parameters: {json.dumps(run.parameters) or "{}"}.

State message:
{run.message}

Error logs (tail):
```
{logs[-3000:]}
```

## Reproduction
datamend re-ran the unchanged code in shadow mode. Result: {repro.get("error", "succeeded")}

## Rules
- Edit only files matching: {", ".join(dep.allowed_paths)}. Start from `{entry_file}`.
- Keep the output tables' columns and types the same.
- Don't add dependencies, network calls, subprocesses, or SQL that drops or truncates.
- If the input data is malformed, handle it explicitly and keep every valid record.
"""
    if feedback:
        text += "\n## Your previous attempt failed validation\n" + "\n".join(
            f"- {f}" for f in feedback) + "\nFix these and keep what worked.\n"
    text += """
Reply with ONLY this JSON object:
{"root_cause": "...", "fix": "...", "rationale": "why this fix and not another",
 "risk": "what could still go wrong", "confidence": 0.0}
"""
    return text


def _explanation(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text, re.DOTALL)
    try:
        data = json.loads(m.group(0)) if m else None
    except json.JSONDecodeError:
        return None
    return data if data and {"root_cause", "fix", "rationale", "risk"} <= data.keys() else None


def guardrails(changed: list[str], diff: str, dep: Deployment, max_lines: int,
               explanation: dict | None, secret_values: list[str]) -> list[dict]:
    added = [line[1:] for line in diff.splitlines()
             if line.startswith("+") and not line.startswith("+++")]
    n = sum(1 for line in diff.splitlines()
            if line[:1] in "+-" and not line.startswith(("+++", "---")))
    outside = [f for f in changed if not any(fnmatch.fnmatch(f, g) for g in dep.allowed_paths)]
    leaked = [line for line in added if SECRET_PATTERNS.search(line)
              or any(v and v in line for v in secret_values)]
    risky = sorted({m.group(0) for line in added for m in [RISKY_ADDITIONS.search(line)] if m})
    return [
        {"rule": "changed_something", "passed": bool(changed),
         "detail": ", ".join(changed) or "no files changed"},
        {"rule": "allowed_paths", "passed": not outside,
         "detail": f"outside {dep.allowed_paths}: {outside}" if outside else "all changes in allowed paths"},
        {"rule": "diff_size", "passed": 0 < n <= max_lines, "detail": f"{n} changed lines, limit {max_lines}"},
        {"rule": "no_secrets", "passed": not leaked,
         "detail": "no credentials in added lines" if not leaked else f"{len(leaked)} suspicious lines"},
        {"rule": "no_risky_code", "passed": not risky,
         "detail": "no subprocess, eval, sockets, or destructive SQL added" if not risky
         else f"added: {', '.join(risky)}"},
        {"rule": "explained", "passed": explanation is not None,
         "detail": "root cause, fix, rationale, and risk given" if explanation
         else "reply did not include the required JSON"},
    ]


class Shadow:
    """Runs the flow in shadow mode and inspects what it wrote."""

    def __init__(self, ws: Workspace, run: FailedRun, dep: Deployment, cfg: RepoConfig,
                 warehouse: str):
        self.ws, self.run, self.dep, self.cfg, self.warehouse = ws, run, dep, cfg, warehouse

    def params(self, tag: str) -> tuple[dict, str]:
        schema = self.dep.shadow.schema_name.format(run=tag)
        params = {k: v.format(run=tag) if isinstance(v, str) else v
                  for k, v in self.dep.shadow.parameters.items()}
        return {**self.run.parameters, **params}, schema

    def execute(self, tag: str) -> tuple[dict, str]:
        params, schema = self.params(tag)
        return self.ws.run_flow(self.run.entrypoint, params, f"datamend-run:{tag}"), schema

    def digest(self, schema: str) -> dict:
        with duckdb.connect(self.warehouse, read_only=True) as con:
            out = {}
            for c in self.dep.compare:
                try:
                    out[c.table] = str(con.execute(
                        f'SELECT count(*), coalesce(sum(hash(r)), 0) FROM "{schema}"."{c.table}" r'
                    ).fetchone())
                except duckdb.Error:
                    out[c.table] = None
            return out

    def compare(self, schema: str) -> list[dict]:
        with duckdb.connect(self.warehouse, read_only=True) as con:
            return [compare.compare_table(con, self.dep.prod_schema, schema, c,
                                          self.cfg.limits.max_row_delta) for c in self.dep.compare]

    def drop(self, schema: str) -> None:
        if not schema.startswith("dm_shadow_"):
            raise ValueError(f"refusing to drop non-shadow schema {schema}")
        with duckdb.connect(self.warehouse) as con:
            con.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')


def validate(shadow: Shadow, tag: str) -> tuple[list[dict], list[dict]]:
    """Run the patched flow twice, run tests, compare with production."""
    checks = []
    first, schema = shadow.execute(f"{tag}a")
    checks.append({"check": "shadow_run", "passed": first["ok"], "hard": True,
                   "detail": "patched flow succeeded in shadow mode" if first["ok"]
                   else first["error"]})
    comparison = []
    if first["ok"]:
        d1 = shadow.digest(schema)
        second, schema2 = shadow.execute(f"{tag}a")  # same schema: a rerun must not change it
        d2 = shadow.digest(schema2)
        checks.append({"check": "idempotent_rerun", "passed": second["ok"] and d1 == d2, "hard": True,
                       "detail": "second run produced identical tables" if d1 == d2
                       else f"tables changed on rerun: {d1} -> {d2}"})
        comparison = shadow.compare(schema)
        for table in comparison:
            checks += table["checks"]
        shadow.drop(schema)
    if shadow.dep.tests:
        t = shadow.ws.run_tests(shadow.dep.tests)
        checks.append({"check": "tests", "passed": t["ok"], "hard": True,
                       "detail": t["output"].splitlines()[-1] if t["output"] else t["command"]})
    return checks, comparison


def confidence(checks: list[dict], comparison: list[dict],
               explanation: dict | None = None) -> tuple[float, list[dict]]:
    """Weighted share of evidence that passed, with one line per item.

    Hard validation checks and `datamend.yaml` expectations weigh 1; soft comparison checks
    (volume drift, new nulls) weigh 0.5. The agent's own confidence is shown, not counted.
    """
    items = [{"source": "validation" if c.get("hard", True) else "comparison",
              "check": c["check"], "passed": c["passed"],
              "weight": 1.0 if c.get("hard", True) else 0.5, "detail": c["detail"]}
             for c in checks]
    items += [{"source": "expectation", "check": e["check"], "passed": e["passed"], "weight": 1.0,
               "detail": e["detail"]} for t in comparison for e in t.get("expectations", [])]
    total = sum(i["weight"] for i in items)
    score = sum(i["weight"] for i in items if i["passed"]) / total if total else 0.0
    if explanation and isinstance(explanation.get("confidence"), (int, float)):
        items.append({"source": "agent", "check": "self_reported", "passed": True, "weight": 0.0,
                      "detail": f"agent says {explanation['confidence']:.2f} (not counted)"})
    return round(score, 4), items


def request_review(github, number: int, reviewers: list[str], labels: list[str]) -> dict:
    """Ask reviewers to look at the PR and label it. Failures are recorded, not raised."""
    out = {"reviewers": reviewers, "labels": labels, "requested": [], "errors": []}
    if reviewers:
        try:
            got = github.request_reviewers(number, reviewers)
            out["requested"] = [u["login"] for u in got.get("requested_reviewers", [])]
        except Exception as exc:  # noqa: BLE001 - a PR without a reviewer is still a PR
            out["errors"].append(f"request_reviewers: {type(exc).__name__}: {exc}"[:300])
    if labels:
        try:
            github.add_labels(number, labels)
        except Exception as exc:  # noqa: BLE001
            out["errors"].append(f"add_labels: {type(exc).__name__}: {exc}"[:300])
    return out


def pr_body(run: FailedRun, row: dict, logs: str) -> str:
    e = row["explanation"]
    lines = [
        f"## datamend: fix for failed `{run.deployment}` run",
        "",
        f"Run `{run.name}` (`{run.id}`) failed with:",
        "", "```", run.message[:800], "```", "",
        "### Root cause", e["root_cause"], "",
        "### Fix", e["fix"], "",
        "### Why this fix", e["rationale"], "",
        "### Risk", e["risk"], "",
        f"### Validation: score {row['score']:.2f}", "",
        ("Run in shadow mode against this workspace's real orchestrator variables and data, "
         "writing only to a throwaway schema."), "",
        "| Check | Result | Detail |", "| --- | --- | --- |",
    ]
    for c in row["validation"]:
        mark = "✅" if c["passed"] else ("❌" if c.get("hard", True) else "⚠️")
        lines.append(f"| `{c['check']}` | {mark} | {str(c['detail']).replace('|', '/')} |")
    for table in row["comparison"]:
        lines += ["", f"### Before and after: `{table['table']}`", "",
                  "| Metric | Production (last good load) | This fix (shadow) |", "| --- | --- | --- |"]
        lines += [f"| {m['metric']} | {m['prod']} | {m['shadow']} |" for m in table["metrics"]]
        if table.get("expectations"):
            lines += ["", "| Expectation | Expected | Actual | Result |", "| --- | --- | --- | --- |"]
            lines += [f"| `{e['check']}` | {e['expected']} | {e['actual']} | "
                      f"{'✅' if e['passed'] else '❌'} |" for e in table["expectations"]]
    if row.get("confidence_breakdown"):
        lines += ["", (f"### Confidence: {row['confidence']:.2f} "
                      f"(PRs open only at {row.get('min_confidence', 1.0):.2f} or above)"), "",
                  "| Source | Check | Weight | Result |", "| --- | --- | --- | --- |"]
        lines += [f"| {i['source']} | `{i['check']}` | {i['weight']:g} | "
                  f"{'✅' if i['passed'] else '❌'} {str(i['detail']).replace('|', '/')} |"
                  for i in row["confidence_breakdown"]]
    lines += ["", "### Guardrails", ""]
    lines += [f"- {'✅' if g['passed'] else '❌'} `{g['rule']}`: {g['detail']}" for g in row["guardrails"]]
    usage = row.get("usage") or {}
    lines += ["", "---",
              f"Generated by [datamend](https://github.com/saeed1507100/datamend) run `{row['run_id']}` "
              f"in {row['attempts']} attempt(s)"
              + (f", {usage.get('num_turns')} agent turns, ${usage.get('cost_usd', 0):.3f}" if usage else "")
              + ". Review it like any other change: datamend never merges.", "",
              f"<!-- datamend-signature: {row['signature']} -->"]
    return "\n".join(lines)


def investigate(run: FailedRun, *, source: PrefectSource, github, clone_url: str, base: str,
                agent, con, runs_dir: Path, now: datetime, publish: bool = True,
                token: str | None = None) -> dict:
    run_id = f"rm_{now:%Y%m%d%H%M%S}_{uuid.uuid4().hex[:6]}"
    row = {"run_id": run_id, "flow_run_id": run.id, "deployment": run.deployment,
           "signature": signature(run.deployment, run.message), "error": run.message[:1000],
           "attempts": 0, "created_at": now}

    def finish(status: str, reason: str) -> dict:
        row.update(status=status, reason=reason, finished_at=datetime.now())
        _save(con, row)
        return row

    if github is not None and (url := github.has_open_pr_for(row["signature"])):
        return finish("skipped", f"open PR already covers this failure: {url}")

    ws = Workspace.clone(clone_url, base, runs_dir / run_id / "repo", token)
    cfg_path = ws.path / "datamend.yaml"
    if not cfg_path.exists():
        return finish("skipped", "no datamend.yaml in the repo: no shadow contract")
    cfg = load_config(cfg_path)
    dep = cfg.deployments.get(run.deployment)
    if dep is None:
        return finish("skipped", f"{run.deployment} has no shadow contract in datamend.yaml")

    ws.install()
    baseline = {f: (ws.path / f).read_bytes() for f in ws.changed_files() if (ws.path / f).is_file()}
    warehouse = cfg.warehouse.path or source.variable(cfg.warehouse.path_variable)
    shadow = Shadow(ws, run, dep, cfg, warehouse)

    tag = uuid.uuid4().hex[:8]
    repro, schema = shadow.execute(f"{tag}r")
    shadow.drop(schema)
    if repro["ok"]:
        return finish("not_reproduced", "the unchanged code succeeded in shadow mode; "
                      "likely transient (rerun the deployment) or data has since changed")

    logs = source.logs(run.id)
    feedback: list[str] = []
    secret_values = [v for k, v in os.environ.items()
                     if any(s in k for s in ("TOKEN", "KEY", "SECRET", "PASSWORD")) and len(v) > 8]
    usage_total = {"num_turns": 0, "cost_usd": 0.0}
    for attempt in range(1, cfg.limits.max_attempts + 1):
        row["attempts"] = attempt
        try:
            response = agent.messages({
                "model": cfg.agent.model, "system": SYSTEM, "max_turns": cfg.agent.max_turns,
                "max_budget_usd": cfg.agent.max_budget_usd,
                "messages": [{"role": "user", "content": build_prompt(run, dep, logs, repro, feedback)}],
                "metadata": {"workspace": str(ws.path.resolve())},
            })
        except Exception as exc:  # noqa: BLE001 - recorded, never raised past the pass
            return finish("failed", f"agent call failed: {type(exc).__name__}: {exc}"[:500])
        meta = response.get("datamend", {})
        usage_total["num_turns"] += meta.get("num_turns") or 0
        usage_total["cost_usd"] += meta.get("cost_usd") or 0.0
        row["usage"] = usage_total
        text = "".join(b.get("text", "") for b in response.get("content", []))
        explanation = _explanation(text)
        changed = sorted(f for f in ws.changed_files()
                         if not (ws.path / f).is_file() or baseline.get(f) != (ws.path / f).read_bytes())
        diff = ws.git("diff", "--", *changed) if changed else ""
        rails = guardrails(changed, diff, dep, cfg.limits.max_diff_lines, explanation, secret_values)
        row.update(explanation=explanation, diff=diff, guardrails=rails)
        if not all(r["passed"] for r in rails):
            feedback = [f"guardrail {r['rule']} failed: {r['detail']}" for r in rails if not r["passed"]]
            continue
        checks, comparison = validate(shadow, f"{tag}{attempt}")
        hard = [c for c in checks if c.get("hard", True)]
        conf, breakdown = confidence(checks, comparison, explanation)
        row.update(validation=checks, comparison=comparison,
                   score=sum(c["passed"] for c in hard) / len(hard),
                   confidence=conf, confidence_breakdown=breakdown)
        if row["score"] == 1.0 and conf >= cfg.limits.min_confidence:
            break
        feedback = [f"{i['check']}: {i['detail']}" for i in breakdown
                    if not i["passed"] and i["weight"] > 0]
    else:
        if row.get("score") == 1.0:
            return finish("escalated", f"validation passed but confidence {row['confidence']:.2f} "
                          f"< {cfg.limits.min_confidence:.2f}; needs a person: "
                          + "; ".join(feedback)[:600])
        return finish("rejected", f"no passing fix after {cfg.limits.max_attempts} attempts: "
                      + "; ".join(feedback)[:600])
    row["min_confidence"] = cfg.limits.min_confidence

    slug = re.sub(r"[^a-z0-9]+", "-", run.deployment.lower()).strip("-")
    row["branch"] = f"datamend/fix-{slug}-{row['signature'][:6]}"
    body = pr_body(run, row, logs)
    row.pop("min_confidence")
    (runs_dir / run_id / "PR.md").write_text(body)
    if not publish:
        return finish("proposed", f"validation {row['score']:.2f}, confidence "
                      f"{row['confidence']:.2f}; PR not published (--no-publish), "
                      f"body in {runs_dir / run_id / 'PR.md'}")
    row["commit_sha"] = ws.commit_and_push(
        row["branch"], changed,
        f"Fix {run.deployment}: {explanation['fix'][:60]}\n\n{explanation['root_cause']}\n\n"
        f"datamend run {run_id}")
    kind = re.search(r"\b(\w+(?:Error|Exception))\b", run.message)
    title = f"datamend: fix {run.deployment}" + (f" ({kind.group(1)})" if kind else "")
    pr = github.create_pr(row["branch"], base, title, body)
    row["pr_url"] = pr["html_url"]
    row["review"] = request_review(github, pr["number"], cfg.reviewers, cfg.labels)
    asked = ", ".join(row["review"]["requested"]) or "no reviewer"
    return finish("pr_opened", f"validation {row['score']:.2f}, confidence "
                  f"{row['confidence']:.2f}; {row['pr_url']} (review: {asked})")


def watch(*, source: PrefectSource, github, clone_url: str, base: str, agent, state: Path,
          runs_dir: Path, now: datetime, lookback_hours: int = 24, max_investigations: int = 3,
          publish: bool = True, token: str | None = None) -> list[dict]:
    """One pass: newest failure per deployment, skipping signatures already handled."""
    with duckdb.connect(str(state)) as con:
        init(con)
        done = {s for (s,) in con.execute(
            "SELECT signature FROM remote_runs WHERE status IN ('pr_opened', 'proposed', 'escalated')").fetchall()}
        seen, results = set(), []
        for run in source.failures(now - timedelta(hours=lookback_hours)):
            sig = signature(run.deployment, run.message)
            if run.deployment in seen or sig in done:
                continue
            seen.add(run.deployment)
            results.append(investigate(run, source=source, github=github, clone_url=clone_url,
                                       base=base, agent=agent, con=con, runs_dir=runs_dir, now=now,
                                       publish=publish, token=token))
            if len(results) >= max_investigations:
                break
        return results
