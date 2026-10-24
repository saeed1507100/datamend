"""A Messages-API-shaped HTTP endpoint backed by the local Claude Code CLI.

POST /v1/messages runs `claude -p` headless inside a sandbox folder, with a fixed,
minimal tool set (Read, Glob, Grep, Edit: no shell, no web, no MCP), and returns
the answer in the Anthropic Messages API response shape.

Safety: binds to 127.0.0.1, requires a bearer token, and only runs in folders
under the workspace root.
"""

import hmac
import json
import os
import subprocess
import time
import uuid
from collections.abc import Callable
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS = "Read,Glob,Grep,Edit"
DENIED = "Bash,Write,NotebookEdit,WebFetch,WebSearch,Task,Agent"

Runner = Callable[[list[str], Path, int], dict]


def claude_command(prompt: str, system: str | None, model: str, max_turns: int,
                   max_budget_usd: float, bare: bool = False) -> list[str]:
    cmd = [
        "claude", "-p", prompt,
        "--output-format", "json",
        "--model", model,
        "--tools", TOOLS,
        "--allowedTools", TOOLS,
        "--disallowedTools", DENIED,
        "--permission-mode", "acceptEdits",
        "--max-turns", str(max_turns),
        "--max-budget-usd", str(max_budget_usd),
        "--setting-sources", "project",
        "--strict-mcp-config",
        "--disable-slash-commands",
        "--no-session-persistence",
    ]
    if system:
        cmd += ["--append-system-prompt", system]
    if bare:  # needs ANTHROPIC_API_KEY; also confines file tools to the working directory
        cmd.append("--bare")
    return cmd


def subprocess_runner(cmd: list[str], cwd: Path, timeout: int) -> dict:
    proc = subprocess.run(cmd, check=False, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"claude exited {proc.returncode}: {proc.stderr.strip()[:500]}")
    return json.loads(proc.stdout)


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b["text"] for b in content if b.get("type") == "text")


def handle(body: dict, workspace_root: Path, runner: Runner, bare: bool = False,
           timeout: int = 600) -> tuple[int, dict]:
    """Turn one Messages API request into one headless Claude Code run."""
    workspace = Path(body.get("metadata", {}).get("workspace", "")).resolve()
    if not workspace.is_dir() or not workspace.is_relative_to(workspace_root.resolve()):
        return 400, {"type": "error", "error": {
            "type": "invalid_request_error",
            "message": f"metadata.workspace must be a folder under {workspace_root}"}}
    prompt = "\n\n".join(_text(m["content"]) for m in body["messages"] if m["role"] == "user")
    cmd = claude_command(prompt, body.get("system"), body.get("model", "sonnet"),
                         int(body.get("max_turns", 12)), float(body.get("max_budget_usd", 1.0)),
                         bare)
    started = time.monotonic()
    out = runner(cmd, workspace, timeout)
    usage = out.get("usage", {})
    return 200, {
        "id": f"msg_{uuid.uuid4().hex[:24]}",
        "type": "message",
        "role": "assistant",
        "model": body.get("model", "sonnet"),
        "content": [{"type": "text", "text": out.get("result", "")}],
        "stop_reason": "end_turn" if not out.get("is_error") else "error",
        "usage": {"input_tokens": usage.get("input_tokens", 0),
                  "output_tokens": usage.get("output_tokens", 0)},
        "datamend": {
            "backend": "claude-code-cli",
            "tools": TOOLS.split(","),
            "num_turns": out.get("num_turns"),
            "cost_usd": out.get("total_cost_usd"),
            "duration_ms": int((time.monotonic() - started) * 1000),
            "subtype": out.get("subtype"),
        },
    }


def serve(port: int, workspace_root: Path, token_env: str = "DATAMEND_AGENT_TOKEN",
          bare: bool = False) -> None:
    token = os.environ.get(token_env)
    if not token:
        raise SystemExit(f"set {token_env} to a secret; clients send it as a bearer token")
    make_server(port, workspace_root, token, bare).serve_forever()


def make_server(port: int, workspace_root: Path, token: str, bare: bool = False,
                runner: Runner = subprocess_runner) -> ThreadingHTTPServer:

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, payload: dict):
            body = json.dumps(payload).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            if self.path != "/v1/messages":
                return self._reply(404, {"type": "error", "error": {"type": "not_found_error"}})
            sent = self.headers.get("Authorization", "").removeprefix("Bearer ")
            if not hmac.compare_digest(sent, token):
                return self._reply(401, {"type": "error",
                                         "error": {"type": "authentication_error"}})
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            try:
                self._reply(*handle(body, workspace_root, runner, bare))
            except Exception as exc:  # noqa: BLE001 - report it, don't crash the server
                self._reply(500, {"type": "error",
                                  "error": {"type": "api_error", "message": str(exc)[:500]}})

        def log_message(self, *args):  # keep prompts and paths out of stderr
            pass

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)
