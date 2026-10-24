import subprocess
from datetime import datetime

import duckdb

from datamend.remote import compare, watch
from datamend.remote.config import Compare, Deployment, Shadow
from datamend.remote.prefect import FailedRun, PrefectSource
from datamend.remote.workspace import Workspace

NOW = datetime(2026, 10, 24, 9, 0)
DEP = Deployment(prod_schema="analytics",
                 shadow=Shadow(parameters={"target_schema": "dm_shadow_{run}"},
                               schema_name="dm_shadow_{run}"),
                 compare=[Compare(table="orders", key="order_id")],
                 allowed_paths=["flows/**", "tests/**"])
EXPL = {"root_cause": "r", "fix": "f", "rationale": "x", "risk": "low"}


def test_signature_ignores_values_that_change_per_run():
    a = watch.signature("orders-etl/daily", "ValueError: could not convert string to float: '$2,369.53'")
    b = watch.signature("orders-etl/daily", "ValueError: could not convert string to float: '$907.15'")
    c = watch.signature("orders-etl/daily", "KeyError: 'amount'")
    assert a == b != c


def test_prefect_source_skips_shadow_and_adhoc_runs():
    calls = []

    def send(method, url, body, headers):
        calls.append(url)
        if url.endswith("/flow_runs/filter"):
            assert body["flow_runs"]["state"]["type"]["any_"] == ["FAILED", "CRASHED"]
            return [
                {"id": "1", "name": "a", "flow_id": "f", "deployment_id": "d", "parameters": {"x": 2},
                 "state_type": "FAILED", "state": {"message": "boom"}, "tags": [], "start_time": None},
                {"id": "2", "name": "b", "flow_id": "f", "deployment_id": "d", "parameters": {},
                 "state_type": "FAILED", "state": {}, "tags": ["datamend-shadow"], "start_time": None},
                {"id": "3", "name": "c", "flow_id": "f", "deployment_id": None, "parameters": {},
                 "state_type": "CRASHED", "state": {}, "tags": [], "start_time": None},
            ]
        if "/deployments/" in url:
            return {"name": "daily", "entrypoint": "flows/o.py:f", "parameters": {"x": 1, "y": 1}}
        return {"name": "orders-etl"}

    [run] = PrefectSource("http://p/api", api_key="", send=send).failures(NOW)
    assert (run.id, run.deployment, run.parameters) == ("1", "orders-etl/daily", {"x": 2, "y": 1})


def test_guardrails():
    ok = watch.guardrails(["flows/orders.py"], "+x = 1\n", DEP, 60, EXPL, [])
    assert all(r["passed"] for r in ok)

    def failed(changed, diff, secrets=()):
        return {r["rule"] for r in watch.guardrails(changed, diff, DEP, 60, EXPL, list(secrets))
                if not r["passed"]}

    assert failed(["pyproject.toml"], "+x\n") == {"allowed_paths"}
    assert failed(["flows/a.py"], "+import subprocess\n") == {"no_risky_code"}
    assert failed(["flows/a.py"], '+con.execute("DROP TABLE x")\n') == {"no_risky_code"}
    assert failed(["flows/a.py"], "+key = 'ghp_" + "a" * 36 + "'\n") == {"no_secrets"}
    assert failed(["flows/a.py"], "+token = 'hunter2hunter2'\n", ["hunter2hunter2"]) == {"no_secrets"}
    assert failed(["flows/a.py"], "+x\n" * 70) == {"diff_size"}
    assert failed([], "") == {"changed_something", "diff_size"}


def test_compare_flags_schema_dupes_and_new_nulls():
    con = duckdb.connect()
    con.execute("CREATE SCHEMA analytics; CREATE SCHEMA dm_shadow_1")
    con.execute("CREATE TABLE analytics.orders AS SELECT range AS order_id, 1.5 AS amount FROM range(10)")
    con.execute("CREATE TABLE dm_shadow_1.orders AS SELECT range % 9 AS order_id, "
                "CASE WHEN range = 0 THEN NULL ELSE 1.5 END AS amount FROM range(10)")
    out = compare.compare_table(con, "analytics", "dm_shadow_1", Compare(table="orders", key="order_id"), 0.25)
    checks = {c["check"]: c["passed"] for c in out["checks"]}
    assert checks == {"orders.schema": True, "orders.volume": True,
                      "orders.unique_key": False, "orders.nulls_amount": False}
    assert {m["metric"] for m in out["metrics"]} >= {"rows", "sum(amount)"}
    assert "sum(order_id)" not in {m["metric"] for m in out["metrics"]}


def test_changed_files_handles_first_line_and_untracked(tmp_path):
    """Regression: stripping `git status` output once turned flows/x.py into lows/x.py."""
    repo = tmp_path / "r"
    (repo / "flows").mkdir(parents=True)
    (repo / "flows" / "orders.py").write_text("a\n")
    g = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@t"]
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run([*g, "add", "-A"], check=True)
    subprocess.run([*g, "commit", "-qm", "i"], check=True)
    (repo / "flows" / "orders.py").write_text("b\n")
    (repo / "flows" / "new.py").write_text("c\n")
    assert Workspace(repo).changed_files() == ["flows/new.py", "flows/orders.py"]


def test_pr_body_carries_evidence_and_signature():
    run = FailedRun(id="1", name="happy-uakari", deployment="orders-etl/daily", entrypoint="f.py:f",
                    parameters={}, state_type="FAILED", message="ValueError: bad", start_time=None)
    row = {"run_id": "rm_1", "signature": "abc123", "score": 1.0, "attempts": 1,
           "explanation": EXPL, "usage": {"num_turns": 3, "cost_usd": 0.05},
           "validation": [{"check": "shadow_run", "passed": True, "detail": "ok"}],
           "comparison": [{"table": "orders", "metrics": [{"metric": "rows", "prod": 45, "shadow": 50}]}],
           "guardrails": [{"rule": "allowed_paths", "passed": True, "detail": "ok"}]}
    body = watch.pr_body(run, row, "logs")
    assert "| rows | 45 | 50 |" in body and "<!-- datamend-signature: abc123 -->" in body
    assert "datamend never merges" in body


def test_watch_takes_newest_failure_per_deployment_and_skips_handled(tmp_path, monkeypatch):
    runs = [FailedRun(id=str(i), name=f"r{i}", deployment=d, entrypoint="f.py:f", parameters={},
                      state_type="FAILED", message=m, start_time=None)
            for i, (d, m) in enumerate([("a/x", "KeyError: 'k'"), ("a/x", "KeyError: 'k'"),
                                        ("b/y", "ValueError: v")])]

    class Source:
        def failures(self, since):
            return runs

    seen = []

    def fake_investigate(run, *, con, **kw):
        seen.append(run.id)
        row = {"run_id": f"rm_{run.id}", "flow_run_id": run.id, "deployment": run.deployment,
               "signature": watch.signature(run.deployment, run.message), "status": "pr_opened",
               "reason": "", "created_at": NOW}
        watch._save(con, row)
        return row

    monkeypatch.setattr(watch, "investigate", fake_investigate)
    kw = {"source": Source(), "github": None, "clone_url": "", "base": "main", "agent": None,
              "state": tmp_path / "s.duckdb", "runs_dir": tmp_path, "now": NOW}
    assert [r["flow_run_id"] for r in watch.watch(**kw)] == ["0", "2"]
    assert watch.watch(**kw) == []  # both signatures already have PRs
    assert seen == ["0", "2"]


def _tables(con):
    con.execute("CREATE SCHEMA analytics; CREATE SCHEMA dm_shadow_1")
    con.execute("CREATE TABLE analytics.orders AS SELECT range AS order_id, 10.0 AS amount, "
                "'x' AS customer FROM range(45)")
    con.execute("CREATE TABLE dm_shadow_1.orders AS SELECT range AS order_id, 10.0 AS amount, "
                "'x' AS customer FROM range(50)")


def test_expectations_check_expected_against_actual():
    from datamend.remote.config import Expectations

    con = duckdb.connect()
    _tables(con)
    spec = Compare(table="orders", key="order_id", expectations=Expectations(
        row_count="at_least_prod", keys="superset_of_prod", nulls={"amount": 0},
        sums_within={"amount": 0.001}))
    out = compare.compare_table(con, "analytics", "dm_shadow_1", spec, 0.25)
    assert [(e["check"], e["passed"]) for e in out["expectations"]] == [
        ("orders.expect_row_count", True), ("orders.expect_keys", True),
        ("orders.expect_nulls_amount", True), ("orders.expect_sum_amount", True)]
    assert "on shared keys" in out["expectations"][-1]["expected"]

    strict = Compare(table="orders", key="order_id",
                     expectations=Expectations(row_count="prod", keys="prod"))
    out = compare.compare_table(con, "analytics", "dm_shadow_1", strict, 0.25)
    assert [e["passed"] for e in out["expectations"]] == [False, False]
    assert out["expectations"][1]["actual"] == "0 missing, 5 extra"

    con.execute("UPDATE dm_shadow_1.orders SET amount = 20 WHERE order_id = 1")
    drift = Compare(table="orders", key="order_id",
                    expectations=Expectations(sums_within={"amount": 0.001}))
    [e] = compare.compare_table(con, "analytics", "dm_shadow_1", drift, 0.25)["expectations"]
    assert not e["passed"] and "+2.22%" in e["actual"]


def test_confidence_weighs_hard_soft_and_expectations():
    checks = [{"check": "shadow_run", "passed": True, "detail": "ok", "hard": True},
              {"check": "orders.volume", "passed": False, "detail": "+30%", "hard": False}]
    comparison = [{"table": "orders", "expectations": [
        {"check": "orders.expect_keys", "passed": True, "detail": "ok"}]}]
    score, items = watch.confidence(checks, comparison, {"confidence": 0.9})
    assert score == round(2 / 2.5, 4)
    assert [i["source"] for i in items] == ["validation", "comparison", "expectation", "agent"]
    assert watch.confidence(checks[:1], comparison)[0] == 1.0


class FakeGitHub:
    """GitHub client over a fake transport that records every request."""

    def __init__(self, fail_reviewers=False):
        from datamend.remote.github import GitHub

        self.calls = []

        def send(method, url, body, headers):
            self.calls.append((method, url.removeprefix("https://api.github.com"), body))
            if url.endswith("/requested_reviewers"):
                if fail_reviewers:
                    raise RuntimeError("HTTP 422: review cannot be requested from pull request author")
                return {"requested_reviewers": [{"login": r} for r in body["reviewers"]]}
            if url.endswith("/pulls") and method == "POST":
                return {"number": 7, "html_url": "https://github.com/o/r/pull/7"}
            return []

        self.gh = GitHub("o/r", token="t", send=send)


def test_pr_requests_reviewers_and_labels():
    fake = FakeGitHub()
    pr = fake.gh.create_pr("datamend/fix", "main", "t", "b")
    review = watch.request_review(fake.gh, pr["number"], ["saeed1507100"], ["datamend"])
    assert review["requested"] == ["saeed1507100"] and not review["errors"]
    assert fake.calls[1:] == [
        ("POST", "/repos/o/r/pulls/7/requested_reviewers", {"reviewers": ["saeed1507100"]}),
        ("POST", "/repos/o/r/issues/7/labels", {"labels": ["datamend"]})]

    failing = FakeGitHub(fail_reviewers=True)
    review = watch.request_review(failing.gh, 7, ["me"], ["datamend"])
    assert review["requested"] == [] and "422" in review["errors"][0]
    assert failing.calls[-1][1] == "/repos/o/r/issues/7/labels"  # labels still applied


def test_pr_body_shows_confidence_breakdown():
    run = FailedRun(id="1", name="n", deployment="d/x", entrypoint="f.py:f", parameters={},
                    state_type="FAILED", message="ValueError: bad", start_time=None)
    score, items = watch.confidence([{"check": "shadow_run", "passed": True, "detail": "ok"}], [])
    row = {"run_id": "rm_1", "signature": "s", "score": 1.0, "attempts": 1, "explanation": EXPL,
           "validation": [], "comparison": [], "guardrails": [], "confidence": score,
           "confidence_breakdown": items, "min_confidence": 1.0}
    assert "### Confidence: 1.00 (PRs open only at 1.00 or above)" in watch.pr_body(run, row, "")
