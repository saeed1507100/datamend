"""Open pull requests on GitHub. Git itself runs in the workspace (see workspace.py)."""

import json
import os
import urllib.request

from datamend.adapters.http_orchestrators import Send


def _send(method: str, url: str, body: dict | None, headers: dict) -> dict | list:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28",
        "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read() or b"{}")


class GitHub:
    def __init__(self, repo: str, token: str | None = None, send: Send = _send,
                 api: str = "https://api.github.com"):
        self.repo, self.api, self.send = repo, api, send
        self.token = token if token is not None else os.environ.get("GITHUB_TOKEN", "")
        self.headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def open_prs(self) -> list[dict]:
        return self.send("GET", f"{self.api}/repos/{self.repo}/pulls?state=open&per_page=100",
                         None, self.headers)

    def has_open_pr_for(self, signature: str) -> str | None:
        """URL of an open datamend PR whose body carries this error signature."""
        marker = f"datamend-signature: {signature}"
        for pr in self.open_prs():
            if pr["head"]["ref"].startswith("datamend/") and marker in (pr.get("body") or ""):
                return pr["html_url"]
        return None

    def create_pr(self, branch: str, base: str, title: str, body: str, draft: bool = False) -> str:
        pr = self.send("POST", f"{self.api}/repos/{self.repo}/pulls",
                       {"title": title, "head": branch, "base": base, "body": body,
                        "draft": draft, "maintainer_can_modify": True}, self.headers)
        return pr["html_url"]
