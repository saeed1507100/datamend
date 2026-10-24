import json
import shutil
import subprocess
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import duckdb
import pytest

from datamend import audit, catalog, engine, lineage, simulate
from datamend.ai import client, fixer, guardrails, server

NOW = datetime(2026, 10, 24, 12, 0)
EXAMPLES = Path(__file__).parent.parent / "examples"
RECORDING = EXAMPLES / "ai" / "raw_payments_stale.json"
JOB = "pipelines/load_raw_payments.py"


@pytest.fixture
def env(tmp_path):
    """A demo copy of examples/ in its own git repo, with raw_payments escalated."""
    root = tmp_path / "demo"
    shutil.copytree(EXAMPLES, root)
    git = ["git", "-C", str(root), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run([*git, "add", "-A"], check=True)
    subprocess.run([*git, "commit", "-qm", "init"], check=True)
    cat = catalog.load(root / "datasets.yaml")
    g = lineage.build(cat)
    con = duckdb.connect(str(tmp_path / "w.duckdb"))
    simulate.seed(con, cat, NOW)
    simulate.inject_stale(con, cat, "raw_payments", NOW, lineage.downstream(g, "raw_payments"))
    engine.run_cycle(con, cat, g, NOW)
    return {"root": root, "cat": cat, "con": con, "runs": tmp_path / "runs"}


class EditAgent:
    """Fake agent: applies `edits` ({relpath: text or fn(old)->new}) and replies `reply`."""

    name = "fake"

    def __init__(self, edits: dict, reply: dict | None = None):
        self.edits = edits
        self.reply = reply or {"root_cause": "r", "fix": "f", "rationale": "x", "risk": "low"}

    def messages(self, body):
        ws = Path(body["metadata"]["workspace"])
        for rel, edit in self.edits.items():
            p = ws / rel
            p.write_text(edit(p.read_text()) if callable(edit) else edit)
        return {"content": [{"type": "text", "text": json.dumps(self.reply)}], "usage": {}}


def investigate(env, agent, incident="raw_payments:stale"):
    return fixer.investigate(env["con"], env["cat"], incident, agent, NOW, env["runs"])


def test_recorded_fix_is_proposed_then_applied(env):
    run = investigate(env, client.ReplayAgent(RECORDING))
    assert run["status"] == "proposed", run["status_reason"]
    assert run["score"] == 1.0
    assert {v["check"] for v in json.loads(run["validation"])} == {
        "runs_clean", "idempotent_rerun", "idempotent_backfill", "unique_ids",
        "schema_stable", "volume", "post_checks"}
    # Nothing has shipped yet.
    assert "BEGIN TRANSACTION" not in (env["root"] / JOB).read_text()

    done = fixer.approve(env["con"], env["cat"], run["run_id"], "reviewer", NOW, env["runs"])
    assert (done["status"], done["result"], done["approved_by"]) == ("applied", "healthy", "reviewer")
    assert "BEGIN TRANSACTION" in (env["root"] / JOB).read_text()
    assert "idempotent: true" in (env["root"] / "datasets.yaml").read_text()

    branch = json.loads(done["commit_ref"])["branch"]
    files = subprocess.run(["git", "-C", str(env["root"]), "show", "--name-only", "--format=", branch],
                           capture_output=True, text=True, check=True).stdout.split()
    assert sorted(files) == ["datasets.yaml", JOB]
    current = subprocess.run(["git", "-C", str(env["root"]), "branch", "--show-current"],
                             capture_output=True, text=True, check=True).stdout.strip()
    assert current != branch  # the checked-out branch is untouched

    decisions = [e["decision"] for e in audit.entries(env["con"]) if e["dataset"] == "raw_payments"]
    assert decisions[-2:] == ["ai_proposed", "ai_fix"]


def test_rerunning_the_buggy_job_duplicates_rows(env):
    """The bug the AI is asked to fix is real."""
    from datamend.adapters.local import load_job

    load_job(env["root"] / JOB).run(env["con"], NOW, "rerun")
    assert env["con"].execute("SELECT count(*), count(DISTINCT id) FROM raw_payments").fetchone() == (400, 200)


def test_comment_only_fix_fails_validation_and_live_data_is_untouched(env):
    before = env["con"].execute("SELECT count(*) FROM raw_payments").fetchone()
    run = investigate(env, EditAgent({JOB: lambda s: s.replace("# The export", "# Note: the export")}))
    assert run["status"] == "rejected"
    failed = {v["check"] for v in json.loads(run["validation"]) if not v["passed"]}
    assert {"idempotent_rerun", "unique_ids"} <= failed
    assert env["con"].execute("SELECT count(*) FROM raw_payments").fetchone() == before
    assert "shadow" not in {r[0] for r in env["con"].execute("SHOW DATABASES").fetchall()}


@pytest.mark.parametrize(("edits", "rule"), [
    ({JOB: lambda s: s + "\n", "pipelines/build_payments.py": lambda s: s + "\n"}, "single_file"),
    ({JOB: lambda s: "import os\n" + s}, "static_safety"),
    ({JOB: lambda s: s.replace('con.execute(\n        "INSERT', 'sql = "DROP TABLE x"\n    con.execute(sql)\n    con.execute(\n        "INSERT')}, "static_safety"),
    ({JOB: lambda s: s + "\n" + "# pad\n" * 50}, "diff_size"),
])
def test_guardrails_reject_unsafe_patches(env, edits, rule):
    run = investigate(env, EditAgent(edits))
    assert run["status"] == "rejected"
    assert rule in run["status_reason"]
    assert fixer.get(env["con"], run["run_id"])["validation"] is None  # no code ran


def test_missing_explanation_is_rejected(env):
    agent = EditAgent({JOB: lambda s: s + "\n"}, reply={"fix": "trust me"})
    assert "explained" in investigate(env, agent)["status_reason"]


def test_owner_opt_out_never_goes_to_ai(env):
    simulate.inject_stale(env["con"], env["cat"], "raw_products", NOW, ["products"])
    engine.run_cycle(env["con"], env["cat"], lineage.build(env["cat"]), NOW)
    run = investigate(env, EditAgent({}), incident="raw_products:stale")
    assert run["status"] == "refused" and "not_manual" in run["status_reason"]


def test_agent_failure_is_recorded(env):
    class Down:
        name = "http"

        def messages(self, body):
            raise ConnectionRefusedError("endpoint down")

    run = investigate(env, Down())
    assert run["status"] == "failed" and "endpoint down" in run["status_reason"]
    assert fixer.get(env["con"], run["run_id"])["status"] == "failed"


def test_approve_refuses_if_job_changed_since_investigation(env):
    run = investigate(env, client.ReplayAgent(RECORDING))
    (env["root"] / JOB).write_text((env["root"] / JOB).read_text() + "\n# hotfix\n")
    with pytest.raises(ValueError, match="changed since"):
        fixer.approve(env["con"], env["cat"], run["run_id"], "r", NOW, env["runs"])


def test_failed_git_step_writes_nothing(env):
    run = investigate(env, client.ReplayAgent(RECORDING))
    shutil.rmtree(env["root"] / ".git" / "objects")  # break the repo
    (env["root"] / ".git" / "objects").mkdir()
    before = (env["root"] / JOB).read_text()
    with pytest.raises(subprocess.CalledProcessError):
        fixer.approve(env["con"], env["cat"], run["run_id"], "r", NOW, env["runs"])
    assert (env["root"] / JOB).read_text() == before
    assert fixer.get(env["con"], run["run_id"])["status"] == "proposed"


def test_static_safety_ignores_docstrings():
    ok, _ = guardrails.static_safety('"""Load the export."""\n\ndef run(con, now, mode):\n    pass\n')
    assert ok


# --- agent endpoint ---------------------------------------------------------

def fake_runner(calls):
    def run(cmd, cwd, timeout):
        calls.append((cmd, cwd))
        return {"result": "done", "num_turns": 2, "total_cost_usd": 0.01,
                "usage": {"input_tokens": 5, "output_tokens": 7}}
    return run


def test_command_locks_down_tools():
    cmd = server.claude_command("p", "sys", "sonnet", 5, 0.5)
    assert cmd[cmd.index("--tools") + 1] == "Read,Glob,Grep,Edit"
    assert "Bash" in cmd[cmd.index("--disallowedTools") + 1]
    assert {"--strict-mcp-config", "--no-session-persistence"} <= set(cmd)


def test_endpoint_requires_token_and_workspace_under_root(tmp_path):
    ws = tmp_path / "root" / "run1"
    ws.mkdir(parents=True)
    calls = []
    srv = server.make_server(0, tmp_path / "root", "s3cret", runner=fake_runner(calls))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{srv.server_address[1]}/v1/messages"

    def post(body, token):
        req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                     headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    msg = {"messages": [{"role": "user", "content": "hi"}], "metadata": {"workspace": str(ws)}}
    try:
        assert post(msg, "wrong")[0] == 401
        assert post({**msg, "metadata": {"workspace": str(tmp_path)}}, "s3cret")[0] == 400
        status, body = post(msg, "s3cret")
        assert status == 200
        assert body["type"] == "message" and body["content"][0]["text"] == "done"
        assert body["usage"] == {"input_tokens": 5, "output_tokens": 7}
        assert calls[0][1] == ws.resolve()
    finally:
        srv.shutdown()
