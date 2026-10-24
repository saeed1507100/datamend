import sqlite3
from datetime import datetime

import pytest

from datamend import adapters, catalog
from datamend.adapters import aws, http_orchestrators, sql

NOW = datetime(2026, 10, 24, 12, 0)
DS = catalog.Dataset(name="raw_orders", cadence_minutes=60, job="load_raw_orders")


def test_dbapi_warehouse_works_on_sqlite():
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE raw_orders (id INTEGER, _loaded_at TEXT)")
    con.executemany("INSERT INTO raw_orders VALUES (?, ?)", [(1, "2026-10-24 11:00:00"),
                                                            (2, "2026-10-24 11:30:00")])
    assert sql.DbApiWarehouse(con).stats(DS) == (2, datetime(2026, 10, 24, 11, 30))


def test_dbapi_warehouse_uses_schema_qualified_table():
    con = sqlite3.connect(":memory:")
    con.execute("ATTACH ':memory:' AS mart")
    con.execute("CREATE TABLE mart.orders (id INTEGER, loaded TEXT)")
    ds = DS.model_copy(update={"table": "mart.orders"})
    assert sql.DbApiWarehouse(con, freshness_column="loaded").stats(ds) == (0, None)


class Recorder:
    def __init__(self, *responses):
        self.calls, self.responses = [], list(responses)

    def __call__(self, method, url, body, headers):
        self.calls.append((method, url, body))
        return self.responses.pop(0)


def test_airflow_triggers_dag_run_with_mode():
    send = Recorder({"dag_run_id": "manual__1"})
    out = http_orchestrators.Airflow("http://af:8080/", send=send).trigger(DS, "backfill", NOW)
    assert out == "triggered:manual__1"
    method, url, body = send.calls[0]
    assert (method, url) == ("POST", "http://af:8080/api/v2/dags/load_raw_orders/dagRuns")
    assert body["conf"]["mode"] == "backfill" and body["logical_date"] == "2026-10-24T12:00:00Z"


def test_prefect_resolves_deployment_then_creates_flow_run():
    send = Recorder({"id": "dep-1"}, {"id": "run-9"})
    ds = DS.model_copy(update={"job": "orders/nightly"})
    assert http_orchestrators.Prefect("http://pf/api", send=send).trigger(ds, "rerun", NOW) == "triggered:run-9"
    assert send.calls[0][1] == "http://pf/api/deployments/name/orders/nightly"
    assert send.calls[1][1:] == ("http://pf/api/deployments/dep-1/create_flow_run",
                                 {"parameters": {"mode": "rerun"}, "tags": ["datamend"]})


def test_glue_and_step_functions():
    class Fake:
        def start_job_run(self, **kw):
            self.kw = kw
            return {"JobRunId": "jr_1"}

        def start_execution(self, **kw):
            self.kw = kw
            return {"executionArn": "arn:exec"}

    glue, sfn = Fake(), Fake()
    assert aws.Glue(client=glue).trigger(DS, "rerun", NOW) == "triggered:jr_1"
    assert glue.kw == {"JobName": "load_raw_orders", "Arguments": {"--mode": "rerun"}}
    prefix = "arn:aws:states:eu-west-1:1:stateMachine:"
    assert aws.StepFunctions(prefix, client=sfn).trigger(DS, "rerun", NOW) == "triggered:arn:exec"
    assert sfn.kw["stateMachineArn"] == prefix + "load_raw_orders"


def test_unknown_adapter_is_rejected():
    cat = catalog.Catalog.model_validate({
        "platform": {"orchestrator": {"type": "cron"}},
        "datasets": [{"name": "a", "cadence_minutes": 1, "job": "j"}],
    })
    with pytest.raises(ValueError, match="unknown orchestrator type 'cron'"):
        adapters.build(cat, con=None)
