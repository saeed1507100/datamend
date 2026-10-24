"""Airflow and Prefect over their REST APIs. Runs are async: the next cycle confirms freshness."""

import json
import os
import urllib.request
from collections.abc import Callable
from datetime import datetime

from datamend.catalog import Dataset

Send = Callable[[str, str, dict | None, dict], dict]


def urllib_send(method: str, url: str, body: dict | None, headers: dict) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


class Airflow:
    """Triggers a DAG run. The dataset's `job` is the DAG id; `mode` goes in the run conf."""

    def __init__(self, url: str, token_env: str = "AIRFLOW_TOKEN", api: str = "v2",
                 send: Send = urllib_send):
        self.url, self.api, self.send = url.rstrip("/"), api, send
        self.headers = {"Authorization": f"Bearer {os.environ.get(token_env, '')}"}

    def trigger(self, dataset: Dataset, mode: str, now: datetime) -> str:
        body = {"conf": {"mode": mode, "triggered_by": "datamend"}}
        if self.api == "v2":  # Airflow 3 requires a logical date
            body["logical_date"] = now.isoformat() + "Z"
        run = self.send("POST", f"{self.url}/api/{self.api}/dags/{dataset.job}/dagRuns",
                        body, self.headers)
        return f"triggered:{run['dag_run_id']}"


class Prefect:
    """Creates a flow run from a deployment. The dataset's `job` is `<flow>/<deployment>`."""

    def __init__(self, url: str, key_env: str = "PREFECT_API_KEY", send: Send = urllib_send):
        self.url, self.send = url.rstrip("/"), send
        key = os.environ.get(key_env)
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}

    def trigger(self, dataset: Dataset, mode: str, now: datetime) -> str:
        dep = self.send("GET", f"{self.url}/deployments/name/{dataset.job}", None, self.headers)
        run = self.send("POST", f"{self.url}/deployments/{dep['id']}/create_flow_run",
                        {"parameters": {"mode": mode}, "tags": ["datamend"]}, self.headers)
        return f"triggered:{run['id']}"
