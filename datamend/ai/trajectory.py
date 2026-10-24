"""An agent run as a list of steps: tool calls, files read, edits, validation, retries.

Live runs build steps from `claude -p --output-format stream-json --verbose` events.
Recordings made before steps were captured get steps reconstructed from what they hold
(the files the agent wrote and its final answer), marked `"source": "reconstructed"`.
"""

import difflib
import json

FILE_TOOLS = {"Read", "Edit", "Write", "MultiEdit", "NotebookEdit"}


def _summary(name: str, args: dict) -> str:
    if name in FILE_TOOLS:
        return args.get("file_path", "")
    if name in ("Glob", "Grep"):
        return " ".join(str(args[k]) for k in ("pattern", "path", "glob") if args.get(k))
    return json.dumps(args)[:200]


def _result_text(content) -> str:
    if isinstance(content, list):
        content = "\n".join(c.get("text", "") for c in content if isinstance(c, dict))
    return str(content or "")


def from_stream(events: list[dict], workspace: str | None = None) -> list[dict]:
    """Steps from stream-json events: one per tool call (with its result) and per text reply."""
    steps, by_id = [], {}

    def rel(path: str) -> str:
        if not workspace or not path:
            return path
        root = workspace.rstrip("/")
        return "." if path == root else path.removeprefix(root + "/")

    for ev in events:
        content = (ev.get("message") or {}).get("content") or []
        if ev.get("type") == "assistant":
            for block in content:
                if block.get("type") == "tool_use":
                    args = {k: rel(v) if k in ("file_path", "path") and isinstance(v, str) else v
                            for k, v in (block.get("input") or {}).items()}
                    step = {"kind": "tool_call", "tool": block.get("name"),
                            "target": _summary(block.get("name", ""), args),
                            "source": "live"}
                    if block.get("name") == "Edit":
                        step["edit"] = {"old": str(args.get("old_string", ""))[:600],
                                        "new": str(args.get("new_string", ""))[:600]}
                    steps.append(step)
                    by_id[block.get("id")] = step
                elif block.get("type") == "text" and block.get("text", "").strip():
                    steps.append({"kind": "message", "text": block["text"][:1500], "source": "live"})
        elif ev.get("type") == "user":
            for block in content if isinstance(content, list) else []:
                step = by_id.get(block.get("tool_use_id")) if isinstance(block, dict) else None
                if step is not None:
                    text = _result_text(block.get("content"))
                    step["ok"] = not block.get("is_error", False)
                    step["result"] = (f"{len(text.splitlines())} lines" if step["tool"] == "Read"
                                      else text[:300])
    return number(steps)


def reconstruct(files: dict[str, str], originals: dict[str, str], answer: str,
                context: list[str] = ()) -> list[dict]:
    """Plausible steps for a recording that has only its end state: read, then edit each file."""
    steps = [{"kind": "tool_call", "tool": "Glob", "target": "**/*.py", "ok": True,
              "source": "reconstructed"}]
    for path in dict.fromkeys([*context, *(f for f in files if f in originals)]):
        steps.append({"kind": "tool_call", "tool": "Read", "target": path, "ok": True,
                      "result": f"{len(originals.get(path, '').splitlines())} lines",
                      "source": "reconstructed"})
    for path, text in files.items():
        before = originals.get(path)
        if before is None:
            steps.append({"kind": "tool_call", "tool": "Write", "target": path, "ok": True,
                          "result": f"new file, {len(text.splitlines())} lines",
                          "source": "reconstructed"})
            continue
        for old, new in _hunks(before, text):
            steps.append({"kind": "tool_call", "tool": "Edit", "target": path, "ok": True,
                          "edit": {"old": old[:600], "new": new[:600]}, "source": "reconstructed"})
    steps.append({"kind": "message", "text": answer[:1500], "source": "reconstructed"})
    return number(steps)


def _hunks(a: str, b: str) -> list[tuple[str, str]]:
    al, bl = a.splitlines(keepends=True), b.splitlines(keepends=True)
    return [("".join(al[i1:i2]), "".join(bl[j1:j2]))
            for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, al, bl).get_opcodes()
            if tag != "equal"]


def number(steps: list[dict], start: int = 1) -> list[dict]:
    for i, s in enumerate(steps, start):
        s["n"] = i
    return steps


def extend(steps: list[dict], new: list[dict], **fields) -> list[dict]:
    """Append agent steps, renumbered to follow `steps`, each tagged with `fields`."""
    for s in new:
        steps.append({**s, **fields, "n": len(steps) + 1})
    return steps


def add(steps: list[dict], kind: str, **fields) -> dict:
    """Append a step datamend took itself (guardrails, validation, retry, decision)."""
    step = {"n": len(steps) + 1, "kind": kind, "source": "datamend", **fields}
    steps.append(step)
    return step


def describe(step: dict) -> str:
    """One line for a step, used in prompts and the CLI."""
    if step["kind"] == "tool_call":
        return f"{step['tool']} {step.get('target', '')}".strip()
    if step["kind"] == "message":
        return "answer: " + step.get("text", "")[:80]
    return f"{step['kind']}: {step.get('summary', '')}"
