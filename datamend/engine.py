"""One cycle: check -> dedupe by root -> decide -> act -> audit."""

from datetime import datetime

import networkx as nx

from datamend import actions, audit, checks, lineage, policy
from datamend.catalog import Catalog


def run_cycle(con, catalog: Catalog, g: nx.DiGraph, now: datetime) -> list[policy.Decision]:
    audit.init(con)
    incidents = checks.run_all(con, catalog, now)
    stale = {i.dataset for i in incidents if i.kind == "stale"}
    roots = lineage.root_causes(g, stale)
    symptoms = [i for i in incidents if i.kind == "stale" and i.dataset not in roots]
    to_decide = [i for i in incidents if i not in symptoms]

    # Decide every root before acting, so a fix never reruns a dataset
    # that also depends on an escalated root.
    decisions = [
        policy.decide(
            inc, catalog.get(inc.dataset), catalog.policy,
            affected=[catalog.get(n) for n in lineage.downstream(g, inc.dataset)],
            last_fix_at=audit.last_auto_fix(con, inc.dataset),
        )
        for inc in to_decide
    ]
    escalated = {d.incident.dataset for d in decisions if d.verdict == "needs_approval"}
    blocked = set().union(*(nx.descendants(g, r) for r in escalated))

    for d in decisions:
        result = "escalated"
        if d.verdict == "auto_fix":
            fix = d.action.split(":")[0]
            runnable = [n for n in lineage.downstream(g, d.incident.dataset) if n not in blocked]
            result = actions.ACTIONS[fix](con, d.incident.dataset, runnable, now)
        audit.record(con, d, result, now)

    for inc in symptoms:
        upstream_roots = sorted(r for r in roots if r in nx.ancestors(g, inc.dataset))
        blockers = [r for r in upstream_roots if r in escalated]
        if blockers:
            audit.record_symptom(con, inc.id, inc.dataset, "blocked",
                                 f"waiting on {', '.join(blockers)}", "still stale", now)
        else:
            audit.record_symptom(con, inc.id, inc.dataset, "suppressed",
                                 f"downstream of {', '.join(upstream_roots)}", "covered by root", now)
    return decisions
