import io
import urllib.error
from datetime import datetime

import pytest

from datamend.remote.airflow import AirflowSource, error_message
from datamend.remote.workspace import dag_test_result

NOW = datetime(2026, 10, 24, 9, 0)
RUNS = {"dag_runs": [
    {"dag_id": "signups_daily", "dag_run_id": "manual__1", "state": "failed", "conf": {},
     "start_date": "2026-10-24T08:00:00Z"},
    {"dag_id": "signups_daily", "dag_run_id": "manual__2", "state": "failed",
     "conf": {"target_schema": "dm_shadow_x", "datamend_shadow": "x"}, "start_date": None},
]}
V2_LOG = {"content": [
    {"event": "Task failed with exception"},
    {"event": "Task failed with exception", "error_detail": [{
        "exc_type": "ValueError", "exc_value": "Invalid isoformat string: '23/10/2026'",
        "frames": [{"filename": "/opt/airflow/dags/signups.py", "lineno": 14, "name": "parse_row"}]}]},
], "continuation_token": None}


def fake(api="v2", log=V2_LOG, dag=None):
    calls = []
    dag = dag or {"dag_id": "signups_daily", "fileloc": "/opt/airflow/dags/signups.py",
                  "relative_fileloc": "signups.py", "params": {"target_schema": {"value": "analytics"}}}

    def send(method, url, body, headers):
        calls.append((method, url, headers))
        if url.endswith("/auth/token"):
            return {"access_token": "jwt"}
        if f"/api/{api}/" not in url:
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, io.BytesIO(b""))
        path = url.split(f"/api/{api}", 1)[1]
        if path.startswith("/dags/~/dagRuns?"):
            assert "state=failed" in path and "order_by=-start_date" in path
            return RUNS
        if path.endswith("/details"):
            return dag
        if path.endswith("/taskInstances"):
            return {"task_instances": [{"task_id": "load_signups", "state": "failed", "try_number": 1},
                                       {"task_id": "report", "state": "upstream_failed"}]}
        if "/logs/1" in path:
            assert "full_content=true" in path
            return log
        if path.startswith("/variables/"):
            return {"key": "warehouse_path", "value": "/data/w.duckdb"}
        raise AssertionError(path)
    return send, calls


def test_failed_dag_runs_v2_skip_shadow_runs_and_map_fileloc():
    send, calls = fake()
    src = AirflowSource("http://af:8080", username="u", password="p", send=send)
    [run] = src.failures(NOW)
    assert src.api == "v2"
    assert (run.id, run.deployment, run.entrypoint) == (
        "signups_daily/manual__1", "signups_daily", "dags/signups.py:signups_daily")
    assert run.parameters == {"target_schema": "analytics"}
    assert run.message == "ValueError: Invalid isoformat string: '23/10/2026'"
    assert calls[0][1] == "http://af:8080/auth/token"
    assert all(h == {"Authorization": "Bearer jwt"} for _, _, h in calls[1:])
    logs = src.logs(run.id)
    assert 'File "/opt/airflow/dags/signups.py", line 14, in parse_row' in logs
    assert src.variable("warehouse_path") == "/data/w.duckdb"


def test_falls_back_to_v1_with_string_logs_and_absolute_fileloc():
    v1_log = {"content": "[2026-10-24] INFO - start\nTraceback...\nKeyError: 'email'\n"}
    dag = {"dag_id": "signups_daily", "fileloc": "/home/airflow/repo/dags/etl/signups.py"}
    send, calls = fake(api="v1", log=v1_log, dag=dag)
    src = AirflowSource("http://af:8080/api/v2", token="t", send=send)
    [run] = src.failures(NOW)
    assert src.api == "v1" and calls[0][1].startswith("http://af:8080/api/v2/")
    assert run.entrypoint == "dags/etl/signups.py:signups_daily"
    assert run.message == "KeyError: 'email'"


def test_unmappable_fileloc_is_an_error():
    with pytest.raises(ValueError, match="can't map"):
        AirflowSource("http://af", token="", send=None).repo_path({"fileloc": "/x/y.py"})


def test_error_message_and_dags_test_result():
    assert error_message(["ok", "ValueError: bad", "x"], "t") == "ValueError: bad"
    assert error_message(["nothing here"], "load") == "task load failed"
    failed = "ValueError: Invalid isoformat string: '23/10/2026'\nDagRun Finished: dag_id=d, state=failed, run_type=manual\n"
    assert dag_test_result(0, failed) == {"ok": False, "traceback": failed,
                                          "error": "ValueError: Invalid isoformat string: '23/10/2026'"}
    assert dag_test_result(0, "DagRun Finished: dag_id=d, state=success, run_type=manual")["ok"]
    assert not dag_test_result(1, "boom")["ok"]
