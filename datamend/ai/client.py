"""Clients for the agent endpoint: live HTTP, or replay of a recorded run."""

import json
import os
import urllib.request
from pathlib import Path


class HttpAgent:
    name = "http"

    def __init__(self, endpoint: str, token_env: str = "DATAMEND_AGENT_TOKEN", timeout: int = 660):
        self.url = endpoint.rstrip("/") + "/v1/messages"
        self.token = os.environ.get(token_env, "")
        self.timeout = timeout

    def messages(self, body: dict) -> dict:
        req = urllib.request.Request(
            self.url, data=json.dumps(body).encode(), method="POST",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.token}"},
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read())


class ReplayAgent:
    """Replays a recorded run: writes the recorded file edits, returns the recorded response.

    Used in CI and on the showcase site, so they show a real AI run without calling a model.
    """

    name = "replay"

    def __init__(self, path: str | Path):
        self.recording = json.loads(Path(path).read_text())

    def messages(self, body: dict) -> dict:
        workspace = Path(body["metadata"]["workspace"])
        for rel, text in self.recording["files"].items():
            (workspace / rel).write_text(text)
        return self.recording["response"]
