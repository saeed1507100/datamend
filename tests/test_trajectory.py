import json
from pathlib import Path

import duckdb
from typer.testing import CliRunner

from datamend.ai import client, lessons, server, trajectory
from datamend.cli import app
from datamend.remote import watch
from tests.test_ai import NOW, RECORDING, EditAgent, env, investigate  # noqa: F401 - fixture
from tests.test_remote import DEP

STREAM = [
    {"type": "system", "subtype": "init"},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t1", "name": "Read", "input": {"file_path": "/ws/flows/o.py"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t1", "content": "a\nb\nc"}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t2", "name": "Edit",
         "input": {"file_path": "/ws/flows/o.py", "old_string": "float(x)", "new_string": "parse(x)"}}]}},
    {"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "t2", "content": "old_string not found", "is_error": True}]}},
    {"type": "assistant", "message": {"content": [
        {"type": "tool_use", "id": "t3", "name": "Grep", "input": {"pattern": "amount", "path": "/ws"}}]}},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": "{\"fix\": \"f\"}"}]}},
    {"type": "result", "subtype": "success", "result": "{\"fix\": \"f\"}", "num_turns": 3,
     "total_cost_usd": 0.02, "usage": {"input_tokens": 1, "output_tokens": 2}},
]


def test_stream_json_becomes_steps(tmp_path):
    out = server.parse_stream("\n".join(json.dumps(e) for e in STREAM) + "\n")
    assert out["result"] == "{\"fix\": \"f\"}" and out["num_turns"] == 3
    steps = trajectory.from_stream(out["events"], "/ws")
    assert [(s["n"], s["kind"], s.get("tool"), s.get("target"), s.get("ok")) for s in steps] == [
        (1, "tool_call", "Read", "flows/o.py", True),
        (2, "tool_call", "Edit", "flows/o.py", False),
        (3, "tool_call", "Grep", "amount .", None),
        (4, "message", None, None, None)]
    assert steps[0]["result"] == "3 lines" and steps[1]["edit"]["new"] == "parse(x)"
    assert "--verbose" in server.claude_command("p", None, "sonnet", 1, 1.0)

    ws = tmp_path / "root" / "w"
    ws.mkdir(parents=True)
    status, body = server.handle({"messages": [{"role": "user", "content": "hi"}],
                                  "metadata": {"workspace": str(ws)}},
                                 tmp_path / "root", lambda cmd, cwd, t: out)
    assert status == 200 and len(body["datamend"]["trajectory"]) == 4


def test_replay_carries_recorded_or_reconstructed_steps(tmp_path):
    rec = json.loads(RECORDING.read_text())
    assert rec["trajectory_source"] == "reconstructed"
    (tmp_path / "pipelines").mkdir()
    (tmp_path / "pipelines" / "load_raw_payments.py").write_text("x = 1\n")
    body = {"metadata": {"workspace": str(tmp_path)}}
    steps = client.ReplayAgent(RECORDING).messages(body)["datamend"]["trajectory"]
    assert steps == rec["trajectory"]

    bare = tmp_path / "bare.json"
    bare.write_text(json.dumps({k: v for k, v in rec.items() if not k.startswith("trajectory")}))
    (tmp_path / "pipelines" / "load_raw_payments.py").write_text("x = 1\n")
    steps = client.ReplayAgent(bare).messages(body)["datamend"]["trajectory"]
    assert [s["tool"] for s in steps if s["kind"] == "tool_call"][:2] == ["Glob", "Read"]
    assert "Edit" in {s.get("tool") for s in steps}
    assert {s["source"] for s in steps} == {"reconstructed"}


def test_ai_run_stores_agent_and_datamend_steps(env):  # noqa: F811
    run = investigate(env, client.ReplayAgent(RECORDING))
    steps = json.loads(run["trajectory"])
    assert [s["n"] for s in steps] == list(range(1, len(steps) + 1))
    kinds = [s["kind"] for s in steps]
    assert kinds[-3:] == ["guardrails", "validation", "decision"] and "tool_call" in kinds
    assert steps[-1]["summary"].startswith("proposed")


def test_feedback_becomes_a_lesson_that_later_prompts_include(env, tmp_path):  # noqa: F811
    run = investigate(env, client.ReplayAgent(RECORDING))
    con = env["con"]
    store = duckdb.connect(str(tmp_path / "lessons.duckdb"))
    steps = json.loads(run["trajectory"])
    edit = next(s for s in steps if s.get("tool") == "Edit")
    step_lesson = lessons.add(store, run_id=run["run_id"], now=NOW, step=edit,
                              scope="dataset:raw_payments",
                              feedback="Prefer DELETE + INSERT on the load window over DROP")
    lessons.add(store, run_id=run["run_id"], now=NOW, scope="global",
                feedback="Always say which downstream tables a fix touches")
    lessons.add(store, run_id="x", now=NOW, scope="dataset:other", feedback="Unrelated")
    assert step_lesson["step"] == edit["n"]
    assert "reviewer comment on a past step: Edit pipelines/load_raw_payments.py" in step_lesson["text"]

    # Re-escalate and investigate again: the prompt now carries both applicable lessons.
    from datamend import engine, lineage, simulate

    simulate.inject_stale(con, env["cat"], "raw_payments", NOW, lineage.downstream(
        lineage.build(env["cat"]), "raw_payments"))
    engine.run_cycle(con, env["cat"], lineage.build(env["cat"]), NOW)
    seen = []

    class Spy(EditAgent):
        def messages(self, body):
            seen.append(body["messages"][0]["content"])
            return super().messages(body)

    from datamend.ai import fixer

    fixer.investigate(con, env["cat"], "raw_payments:stale", Spy({}), NOW, env["runs"],
                      lessons=lessons.all_lessons(store))
    prompt = seen[0]
    assert "## Lessons from past reviews" in prompt
    assert "Prefer DELETE + INSERT on the load window over DROP" in prompt
    assert "Always say which downstream tables a fix touches" in prompt
    assert "Unrelated" not in prompt

    assert lessons.forget(store, step_lesson["lesson_id"])
    assert "Prefer DELETE" not in lessons.prompt_section(
        lessons.select(lessons.all_lessons(store), "dataset:raw_payments"))
    md = lessons.export(store, tmp_path / "lessons.md").read_text()
    assert "## global" in md and "Prefer DELETE" not in md


def test_watch_prompt_includes_deployment_lessons():
    from datamend.remote.prefect import FailedRun

    run = FailedRun(id="1", name="n", deployment="orders-etl/daily", entrypoint="flows/o.py:f",
                    parameters={}, state_type="FAILED", message="ValueError", start_time=None)
    rows = [{"scope": "deployment:orders-etl/daily", "text": "Never drop rows with bad amounts"},
            {"scope": "deployment:other/x", "text": "nope"}]
    text = watch.build_prompt(run, DEP, "", {"error": "ValueError"}, [],
                              lessons.select(rows, "deployment:orders-etl/daily"))
    assert "Never drop rows with bad amounts" in text and "nope" not in text


def test_cli_feedback_on_a_step_and_chat_style(env, tmp_path):  # noqa: F811
    run = investigate(env, client.ReplayAgent(RECORDING))
    db = Path(env["con"].execute("SELECT path FROM duckdb_databases() WHERE database_name = "
                                 "current_database()").fetchone()[0])
    env["con"].close()
    store = tmp_path / "ls" / "lessons.duckdb"
    cli = CliRunner()
    base = ["--db", str(db), "--state", str(tmp_path / "none.duckdb"), "--lessons", str(store)]
    r = cli.invoke(app, ["ai", "feedback", run["run_id"], "Check the backfill path too",
                         "--step", "2", *base])
    assert r.exit_code == 0, r.output
    assert "dataset:raw_payments" in r.output and "Read pipelines/load_raw_payments.py" in r.output
    r = cli.invoke(app, ["ai", "feedback", run["run_id"], "Good fix, keep it this small",
                         "--scope", "global", *base])
    assert r.exit_code == 0 and "global" in r.output
    r = cli.invoke(app, ["ai", "feedback", run["run_id"], "x", "--step", "999", *base])
    assert r.exit_code != 0
    res = cli.invoke(app, ["ai", "lessons", "list", "--lessons", str(store)])
    assert res.exit_code == 0, repr(res.exception)
    listed = res.output
    assert listed.count("ls_") == 2
    assert "Good fix" in (store.with_name("lessons.md")).read_text()
    lesson_id = listed.split()[0]
    assert cli.invoke(app, ["ai", "lessons", "forget", lesson_id, "--lessons", str(store)]).exit_code == 0
    assert cli.invoke(app, ["ai", "lessons", "list", "--lessons", str(store)]).output.count("ls_") == 1


def test_airflow_recording_has_a_live_trajectory():
    rec = json.loads((RECORDING.parent.parent / "remote" / "signups_daily_valueerror.json").read_text())
    assert rec["trajectory_source"] == "live"
    assert {s["source"] for s in rec["trajectory"]} == {"live"}
    assert any(s.get("tool") == "Edit" and s["target"] == "dags/signups.py" for s in rec["trajectory"])
