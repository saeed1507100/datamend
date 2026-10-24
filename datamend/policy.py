"""Guardrails: an auto-fix runs only if every rule passes."""

from datetime import datetime, timedelta
from typing import Literal

from pydantic import BaseModel

from datamend.actions import FIXES
from datamend.catalog import Dataset, Policy
from datamend.checks import Incident


class RuleResult(BaseModel):
    rule: str
    passed: bool
    reason: str


class Decision(BaseModel):
    incident: Incident
    verdict: Literal["auto_fix", "needs_approval"]
    action: str | None
    blast_radius: int
    rules: list[RuleResult]

    @property
    def failed(self) -> list[RuleResult]:
        return [r for r in self.rules if not r.passed]


def decide(
    incident: Incident,
    dataset: Dataset,
    policy: Policy,
    affected: list[Dataset],
    last_fix_at: datetime | None,
) -> Decision:
    blast_radius = len(affected)
    manual = [d.name for d in [dataset, *affected] if d.critical == "manual"]
    fix = FIXES.get(incident.kind)
    cooldown = timedelta(minutes=policy.cooldown_minutes)
    in_cooldown = last_fix_at is not None and incident.detected_at - last_fix_at < cooldown
    rules = [
        RuleResult(rule="registered_fix", passed=fix is not None,
                   reason=f"{incident.kind} -> {fix or 'none'}"),
        RuleResult(rule="idempotent", passed=dataset.idempotent,
                   reason=f"{dataset.job} idempotent={dataset.idempotent}"),
        RuleResult(rule="blast_radius", passed=blast_radius <= policy.max_blast_radius,
                   reason=f"{blast_radius} <= {policy.max_blast_radius}"),
        RuleResult(rule="cooldown", passed=not in_cooldown,
                   reason=f"last fix {last_fix_at or 'never'}, window {policy.cooldown_minutes} min"),
        RuleResult(rule="not_manual", passed=not manual,
                   reason=f"manual: {', '.join(manual) or 'none'}"),
    ]
    ok = all(r.passed for r in rules)
    return Decision(
        incident=incident,
        verdict="auto_fix" if ok else "needs_approval",
        action=f"{fix}:{dataset.job}" if ok else None,
        blast_radius=blast_radius,
        rules=rules,
    )
