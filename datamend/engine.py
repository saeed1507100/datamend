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

    decisions = []
    for inc in incidents:
        if inc.kind == "stale" and inc.dataset not in roots:
            root = next(r for r in roots if inc.dataset in nx.descendants(g, r))
            audit.record_suppressed(con, inc.id, inc.dataset, root, now)
            continue

        dataset = catalog.get(inc.dataset)
        affected = lineage.downstream(g, inc.dataset)
        decision = policy.decide(
            inc, dataset, catalog.policy,
            affected=[catalog.get(n) for n in affected],
            last_fix_at=audit.last_auto_fix(con, inc.dataset),
        )
        result = "escalated"
        if decision.verdict == "auto_fix":
            fix = decision.action.split(":")[0]
            result = actions.ACTIONS[fix](con, inc.dataset, affected, now)
        audit.record(con, decision, result, now)
        decisions.append(decision)
    return decisions
