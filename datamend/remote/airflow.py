"""Read failed DAG runs, their task logs, and their code location from the Airflow REST API.

Speaks the Airflow 3 API (`/api/v2`) and falls back to Airflow 2 (`/api/v1`) when v2
isn't there. Same interface as `PrefectSource`, so `watch` doesn't care which it gets.
"""

import base64
import os
import re
import urllib.error
import urllib.parse
from datetime import datetime

from datamend.adapters.http_orchestrators import Send, urllib_send
from datamend.remote.prefect import FailedRun

SHADOW_CONF_KEY = "datamend_shadow"  # set in the conf of every shadow run; those are skipped
ERROR_LINE = re.compile(r"\b[A-Za-z_][\w.]*(Error|Exception|Exit)\b.*")


def _log_lines(content) -> list[str]:
    """Airflow 2 returns one string; Airflow 3 returns structured events."""
    if isinstance(content, str):
        return content.splitlines()
    lines = []
    for item in content or []:
        if isinstance(item, str):
            lines.append(item)
            continue
        if item.get("event"):
            lines.append(str(item["event"]))
        for err in item.get("error_detail") or []:
            lines += [f"  File \"{f.get('filename')}\", line {f.get('lineno')}, in {f.get('name')}"
                      for f in err.get("frames") or []]
            lines.append(f"{err.get('exc_type', 'Error')}: {err.get('exc_value', '')}")
    return lines


def error_message(lines: list[str], task_id: str) -> str:
    """The last line that names an exception, or a plain 'task failed'."""
    found = [m.group(0).strip() for line in lines for m in [ERROR_LINE.search(line)] if m]
    return found[-1] if found else f"task {task_id} failed"


class AirflowSource:
    def __init__(self, api_url: str | None = None, token: str | None = None,
                 username: str | None = None, password: str | None = None,
                 api: str | None = None, repo_dags_dir: str = "dags", send: Send = urllib_send):
        """`api_url` is the server root, e.g. http://127.0.0.1:8080. `api` forces v1 or v2."""
        self.url = (api_url or os.environ["AIRFLOW_API_URL"]).rstrip("/").removesuffix("/api/v2") \
            .removesuffix("/api/v1")
        self.token = token if token is not None else os.environ.get("AIRFLOW_TOKEN")
        self.username = username or os.environ.get("AIRFLOW_USERNAME")
        self.password = password or os.environ.get("AIRFLOW_PASSWORD")
        self.api, self.repo_dags_dir, self.send = api, repo_dags_dir.strip("/"), send
        self._headers: dict | None = None

    # --- transport ------------------------------------------------------------
    def headers(self) -> dict:
        if self._headers is None:
            if self.token:
                self._headers = {"Authorization": f"Bearer {self.token}"}
            elif self.username and self.api == "v1":
                basic = base64.b64encode(f"{self.username}:{self.password}".encode()).decode()
                self._headers = {"Authorization": f"Basic {basic}"}
            elif self.username:  # Airflow 3 trades credentials for a JWT
                got = self.send("POST", f"{self.url}/auth/token",
                                {"username": self.username, "password": self.password}, {})
                self._headers = {"Authorization": f"Bearer {got['access_token']}"}
            else:
                self._headers = {}
        return self._headers

    def _get(self, path: str, **query):
        qs = ("?" + urllib.parse.urlencode({k: v for k, v in query.items() if v is not None})
              if query else "")
        if self.api is None:  # detect once: v2 first, then v1
            try:
                out = self.send("GET", f"{self.url}/api/v2{path}{qs}", None, self.headers())
                self.api = "v2"
                return out
            except urllib.error.HTTPError as exc:
                if exc.code != 404:
                    raise
                self.api, self._headers = "v1", None
        return self.send("GET", f"{self.url}/api/{self.api}{path}{qs}", None, self.headers())

    # --- Source interface -----------------------------------------------------
    def failures(self, since: datetime, limit: int = 50) -> list[FailedRun]:
        """Failed DAG runs since `since`, newest first. Skips datamend's own shadow runs."""
        iso = since.isoformat() + ("Z" if since.tzinfo is None else "")
        runs = self._get("/dags/~/dagRuns", state="failed", start_date_gte=iso,
                         order_by="-start_date", limit=limit).get("dag_runs", [])
        out, dags = [], {}
        for r in runs:
            conf = r.get("conf") or {}
            if SHADOW_CONF_KEY in conf:
                continue
            dag_id = r["dag_id"]
            dag = dags.get(dag_id) or self._get(f"/dags/{dag_id}/details")
            dags[dag_id] = dag
            lines, task = self._failed_task_logs(dag_id, r["dag_run_id"])
            out.append(FailedRun(
                id=f"{dag_id}/{r['dag_run_id']}", name=r["dag_run_id"], deployment=dag_id,
                entrypoint=f"{self.repo_path(dag)}:{dag_id}",
                parameters={**_param_defaults(dag.get("params") or {}), **conf},
                state_type="FAILED", message=error_message(lines, task or "?"),
                start_time=r.get("start_date"),
            ))
        return out

    def _failed_task_logs(self, dag_id: str, run_id: str) -> tuple[list[str], str | None]:
        tis = self._get(f"/dags/{dag_id}/dagRuns/{urllib.parse.quote(run_id)}/taskInstances")
        failed = [t for t in tis.get("task_instances", []) if t.get("state") == "failed"]
        lines = []
        for t in failed:
            got = self._get(f"/dags/{dag_id}/dagRuns/{urllib.parse.quote(run_id)}/taskInstances/"
                            f"{t['task_id']}/logs/{max(t.get('try_number') or 1, 1)}",
                            full_content="true")
            lines += [f"[{t['task_id']}] {line}" for line in _log_lines(got.get("content"))]
        return lines, failed[0]["task_id"] if failed else None

    def logs(self, run_id: str, min_level: int = 40, limit: int = 200) -> str:
        """Logs of the run's failed task instances (Airflow doesn't filter by level)."""
        dag_id, dag_run_id = run_id.split("/", 1)
        return "\n".join(self._failed_task_logs(dag_id, dag_run_id)[0][-limit:])

    def variable(self, name: str) -> str:
        return self._get(f"/variables/{name}")["value"]

    def repo_path(self, dag: dict) -> str:
        """DAG file path inside the repo, from `relative_fileloc` or the absolute `fileloc`."""
        if dag.get("relative_fileloc"):
            return f"{self.repo_dags_dir}/{dag['relative_fileloc']}"
        fileloc = dag.get("fileloc", "")
        marker = f"/{self.repo_dags_dir}/"
        if marker in fileloc:
            return self.repo_dags_dir + "/" + fileloc.rsplit(marker, 1)[1]
        raise ValueError(f"can't map DAG file {fileloc!r} into the repo; "
                         f"expected a /{self.repo_dags_dir}/ folder")

    def run_shadow(self, ws, entrypoint: str, params: dict, tag: str, variables: dict) -> dict:
        """`airflow dags test` in the workspace's venv, against a throwaway metadata DB."""
        path, dag_id = entrypoint.split(":")
        return ws.run_dag(path, dag_id, {**params, SHADOW_CONF_KEY: tag}, variables)


def _param_defaults(params: dict) -> dict:
    """DAG params as returned by the API ({name: {"value": v, ...}} or {name: v})."""
    return {k: (v.get("value") if isinstance(v, dict) and "value" in v else v)
            for k, v in params.items()}
