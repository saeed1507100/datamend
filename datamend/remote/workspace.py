"""A working copy of the pipeline repo with its own dependencies, where flows run in shadow mode."""

import base64
import json
import os
import subprocess
import textwrap
from pathlib import Path

from datamend.remote.prefect import SHADOW_TAG

# Runs inside the workspace's own venv, so the flow sees its real dependencies and the
# company's Prefect variables, secrets, and blocks. Tagged so datamend ignores it later.
SHADOW_SCRIPT = textwrap.dedent('''
    import importlib.util, json, sys, traceback
    from pathlib import Path
    from prefect import tags

    entrypoint, params, out, run_tag = sys.argv[1], json.loads(sys.argv[2]), sys.argv[3], sys.argv[4]
    path, name = entrypoint.split(":")
    sys.path.insert(0, str(Path.cwd()))
    spec = importlib.util.spec_from_file_location("datamend_shadow_flow", path)
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
        with tags("{tag}", run_tag):
            result = getattr(module, name)(**params)
        report = {{"ok": True, "result": repr(result)[:500]}}
    except BaseException as exc:
        report = {{"ok": False, "error": f"{{type(exc).__name__}}: {{exc}}"[:1000],
                  "traceback": traceback.format_exc()[-4000:]}}
    Path(out).write_text(json.dumps(report))
''').format(tag=SHADOW_TAG)


class Workspace:
    def __init__(self, path: Path, token: str | None = None):
        self.path = Path(path)
        self.token = token

    # --- git ----------------------------------------------------------------
    def _auth(self) -> list[str]:
        if not self.token:
            return []
        basic = base64.b64encode(f"x-access-token:{self.token}".encode()).decode()
        return ["-c", f"http.extraheader=AUTHORIZATION: basic {basic}"]

    def git(self, *args: str, check: bool = True) -> str:
        proc = subprocess.run(["git", *self._auth(), "-C", str(self.path), *args],
                              capture_output=True, text=True, check=False)
        if check and proc.returncode != 0:
            raise RuntimeError(f"git {args[0]} failed: {proc.stderr.strip()[:500]}")
        return proc.stdout.strip()

    @classmethod
    def clone(cls, url: str, base: str, dest: Path, token: str | None = None) -> "Workspace":
        ws = cls(dest, token)
        dest.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["git", *ws._auth(), "clone", "--quiet", "--depth", "20",
                               "--branch", base, url, str(dest)],
                              capture_output=True, text=True, check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"git clone failed: {proc.stderr.strip()[:500]}")
        return ws

    def changed_files(self) -> list[str]:
        modified = self.git("diff", "--name-only", "HEAD").splitlines()
        untracked = self.git("ls-files", "--others", "--exclude-standard").splitlines()
        return sorted({f for f in modified + untracked if f})

    def diff(self) -> str:
        self.git("add", "--intent-to-add", "--all")
        return self.git("diff")

    def snapshot(self) -> dict[str, str]:
        return {f: (self.path / f).read_text() for f in self.changed_files()
                if (self.path / f).is_file()}

    def commit_and_push(self, branch: str, files: list[str], message: str) -> str:
        self.git("checkout", "-q", "-b", branch)
        self.git("add", "--", *files)
        self.git("-c", "user.name=datamend[bot]", "-c", "user.email=datamend@users.noreply.github.com",
                 "commit", "-q", "-m", message)
        self.git("push", "-q", "origin", branch)
        return self.git("rev-parse", "HEAD")

    # --- python env ---------------------------------------------------------
    def _env(self) -> dict:
        env = {k: v for k, v in os.environ.items() if k not in ("VIRTUAL_ENV", "UV_PROJECT_ENVIRONMENT")}
        return {**env, "PYTHONDONTWRITEBYTECODE": "1"}

    def install(self) -> str:
        """Install the repo's own dependencies into its own .venv."""
        if (self.path / "pyproject.toml").exists():
            cmd = ["uv", "sync", "--quiet", "--project", str(self.path)]
        elif (self.path / "requirements.txt").exists():
            subprocess.run(["uv", "venv", "--quiet", str(self.path / ".venv")], check=True,
                           env=self._env())
            cmd = ["uv", "pip", "install", "--quiet", "--python", str(self.path / ".venv"),
                   "-r", str(self.path / "requirements.txt")]
        else:
            raise RuntimeError("no pyproject.toml or requirements.txt in the repo")
        proc = subprocess.run(cmd, cwd=self.path, capture_output=True, text=True, env=self._env(),
                              check=False)
        if proc.returncode != 0:
            raise RuntimeError(f"dependency install failed: {proc.stderr.strip()[:800]}")
        return " ".join(cmd[:2])

    def run_flow(self, entrypoint: str, parameters: dict, run_tag: str,
                 timeout: int = 900) -> dict:
        """Run the flow through the Prefect engine with shadow parameters."""
        script = self.path.parent / f"{self.path.name}-shadow.py"
        out = self.path.parent / f"{self.path.name}-shadow-{run_tag}.json"
        script.write_text(SHADOW_SCRIPT)
        out.unlink(missing_ok=True)
        proc = subprocess.run(
            ["uv", "run", "--quiet", "--project", str(self.path), "python", str(script),
             entrypoint, json.dumps(parameters), str(out), run_tag],
            cwd=self.path, capture_output=True, text=True, timeout=timeout, env=self._env(),
            check=False)
        if not out.exists():
            return {"ok": False, "error": f"runner exited {proc.returncode}",
                    "traceback": proc.stderr[-4000:]}
        return json.loads(out.read_text())

    def run_tests(self, command: str, timeout: int = 600) -> dict:
        proc = subprocess.run(["uv", "run", "--quiet", "--project", str(self.path), *command.split()],
                              cwd=self.path, capture_output=True, text=True, timeout=timeout,
                              env=self._env(), check=False)
        tail = (proc.stdout + proc.stderr).strip().splitlines()[-15:]
        return {"ok": proc.returncode == 0, "command": command, "output": "\n".join(tail)}
