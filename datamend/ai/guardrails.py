"""Static guardrails on an AI-proposed patch. Each returns (passed, detail)."""

import ast
import difflib
import re

ALLOWED_IMPORTS = {"datamend.pipeline", "datetime"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__", "getattr", "setattr", "globals"}
# Statements a pipeline job must never run. DELETE and CREATE OR REPLACE are allowed:
# idempotent loads need them.
FORBIDDEN_SQL = re.compile(
    r"\b(DROP|TRUNCATE|ATTACH|DETACH|COPY|EXPORT|IMPORT|INSTALL|LOAD|PRAGMA|ALTER|GRANT|SET)\b",
    re.IGNORECASE,
)


def unified_diff(before: str, after: str, path: str) -> str:
    return "".join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                                        f"a/{path}", f"b/{path}"))


def diff_lines(diff: str) -> int:
    return sum(1 for line in diff.splitlines()
               if line[:1] in "+-" and not line.startswith(("+++", "---")))


def static_safety(source: str) -> tuple[bool, str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, f"does not parse: {exc.msg} (line {exc.lineno})"
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            bad = [a.name for a in node.names if a.name not in ALLOWED_IMPORTS]
            if bad:
                return False, f"import not allowed: {', '.join(bad)}"
        if isinstance(node, ast.ImportFrom) and node.module not in ALLOWED_IMPORTS:
            return False, f"import not allowed: {node.module}"
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id in FORBIDDEN_CALLS):
            return False, f"call not allowed: {node.func.id}()"
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "execute" and node.args):
            sql = _literal(node.args[0])
            if sql is None:
                return False, f"SQL on line {node.lineno} is not a literal string"
            m = FORBIDDEN_SQL.search(sql)
            if m:
                return False, f"SQL keyword not allowed: {m.group(0).upper()} (line {node.lineno})"
    if not any(isinstance(n, ast.FunctionDef) and n.name == "run" for n in tree.body):
        return False, "no top-level run(con, now, mode)"
    return True, "imports and calls allowed; all SQL is literal and free of DDL/admin keywords"


def _literal(node: ast.expr) -> str | None:
    """The string value of a literal, or of literals joined with +. None if it's computed."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _literal(node.left), _literal(node.right)
        return None if left is None or right is None else left + right
    return None
