"""Read failed flow runs, their logs, and their code location from the Prefect REST API."""

import os
from datetime import datetime

from pydantic import BaseModel

from datamend.adapters.http_orchestrators import Send, urllib_send

SHADOW_TAG = "datamend-shadow"


class FailedRun(BaseModel):
    id: str
    name: str
    deployment: str  # "<flow>/<deployment>"
    entrypoint: str  # "flows/orders.py:orders_etl"
    parameters: dict
    state_type: str
    message: str
    start_time: datetime | None


class PrefectSource:
    def __init__(self, api_url: str | None = None, api_key: str | None = None,
                 send: Send = urllib_send):
        self.url = (api_url or os.environ["PREFECT_API_URL"]).rstrip("/")
        key = api_key if api_key is not None else os.environ.get("PREFECT_API_KEY")
        self.headers = {"Authorization": f"Bearer {key}"} if key else {}
        self.send = send

    def _post(self, path: str, body: dict):
        return self.send("POST", f"{self.url}{path}", body, self.headers)

    def _get(self, path: str):
        return self.send("GET", f"{self.url}{path}", None, self.headers)

    def failures(self, since: datetime, limit: int = 50) -> list[FailedRun]:
        """Failed or crashed deployment runs since `since`, newest first. Skips datamend's own."""
        runs = self._post("/flow_runs/filter", {
            "flow_runs": {"state": {"type": {"any_": ["FAILED", "CRASHED"]}},
                          "start_time": {"after_": since.isoformat()}},
            "sort": "START_TIME_DESC", "limit": limit,
        })
        out, deployments, flows = [], {}, {}
        for r in runs:
            if not r.get("deployment_id") or SHADOW_TAG in (r.get("tags") or []):
                continue
            dep = deployments.get(r["deployment_id"]) or self._get(f"/deployments/{r['deployment_id']}")
            deployments[r["deployment_id"]] = dep
            flow = flows.get(r["flow_id"]) or self._get(f"/flows/{r['flow_id']}")
            flows[r["flow_id"]] = flow
            out.append(FailedRun(
                id=r["id"], name=r["name"], deployment=f"{flow['name']}/{dep['name']}",
                entrypoint=dep["entrypoint"], parameters={**dep.get("parameters", {}),
                                                          **(r.get("parameters") or {})},
                state_type=r["state_type"], message=(r.get("state") or {}).get("message") or "",
                start_time=r.get("start_time"),
            ))
        return out

    def logs(self, run_id: str, min_level: int = 40, limit: int = 50) -> str:
        """Error-level logs for a run, oldest first."""
        logs = self._post("/logs/filter", {
            "logs": {"flow_run_id": {"any_": [run_id]}, "level": {"ge_": min_level}},
            "sort": "TIMESTAMP_ASC", "limit": limit,
        })
        return "\n".join(log["message"] for log in logs)

    def variable(self, name: str) -> str:
        return self._get(f"/variables/name/{name}")["value"]
