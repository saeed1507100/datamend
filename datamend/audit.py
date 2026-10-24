"""Append-only audit table: every incident, decision, and action."""

from datetime import datetime

import duckdb

from datamend.policy import Decision

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit (
    incident VARCHAR, dataset VARCHAR, decision VARCHAR, action VARCHAR,
    blast_radius INTEGER, rules_passed INTEGER, reason VARCHAR, result VARCHAR, "at" TIMESTAMP
)
"""


def init(con: duckdb.DuckDBPyConnection) -> None:
    con.execute(SCHEMA)


def record(con, decision: Decision, result: str, at: datetime) -> None:
    failed = decision.failed
    reason = "; ".join(f"{r.rule}: {r.reason}" for r in failed) or "all rules passed"
    con.execute(
        "INSERT INTO audit VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [decision.incident.id, decision.incident.dataset, decision.verdict, decision.action,
         decision.blast_radius, len(decision.rules) - len(failed), reason, result, at],
    )


def record_symptom(con, incident_id: str, dataset: str, decision: str, reason: str,
                   result: str, at: datetime) -> None:
    """A stale dataset handled through its root: suppressed (root fixed) or blocked (root escalated)."""
    con.execute(
        "INSERT INTO audit VALUES (?, ?, ?, NULL, NULL, NULL, ?, ?, ?)",
        [incident_id, dataset, decision, reason, result, at],
    )


def last_auto_fix(con, dataset: str) -> datetime | None:
    return con.execute(
        "SELECT max(\"at\") FROM audit WHERE dataset = ? AND decision = 'auto_fix'", [dataset]
    ).fetchone()[0]


def entries(con) -> list[dict]:
    cur = con.execute('SELECT * FROM audit ORDER BY "at", incident')
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]
