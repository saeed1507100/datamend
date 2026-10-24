from datetime import datetime, timedelta

import duckdb
import pytest

from datamend import catalog, engine, lineage, simulate
from datamend.checks import Incident
from datamend.policy import decide

CAT = catalog.load("examples/datasets.yaml")
G = lineage.build(CAT)
NOW = datetime(2026, 10, 24, 12, 0)


def stale(name: str) -> Incident:
    return Incident(dataset=name, kind="stale", detail="", detected_at=NOW)


def failed_rules(name: str, last_fix_at=None, inc=None) -> list[str]:
    d = decide(inc or stale(name), CAT.get(name), CAT.policy,
               affected=[CAT.get(n) for n in lineage.downstream(G, name)],
               last_fix_at=last_fix_at)
    return [r.rule for r in d.failed]


def test_downstream_in_run_order():
    assert lineage.downstream(G, "raw_orders")[0] == "orders"
    assert set(lineage.downstream(G, "raw_orders")) == {"orders", "revenue_daily", "customer_ltv"}


def test_root_cause_picks_furthest_upstream():
    assert lineage.root_causes(G, ["raw_orders", "orders", "revenue_daily"]) == ["raw_orders"]


def test_unknown_upstream_rejected():
    with pytest.raises(ValueError, match="unknown upstreams"):
        catalog.Catalog.model_validate(
            {"datasets": [{"name": "a", "cadence_minutes": 1, "job": "j", "upstreams": ["b"]}]})


def test_safe_incident_passes_all_rules():
    assert failed_rules("raw_orders") == []


def test_non_idempotent_escalates():
    assert failed_rules("raw_payments") == ["idempotent"]


def test_manual_in_blast_radius_escalates():
    assert failed_rules("raw_customers") == ["not_manual"]


def test_cooldown_escalates():
    assert failed_rules("raw_orders", last_fix_at=NOW - timedelta(minutes=5)) == ["cooldown"]


def test_unregistered_fix_escalates():
    gap = Incident(dataset="raw_orders", kind="gap", detail="", detected_at=NOW)
    assert failed_rules("raw_orders", inc=gap) == ["registered_fix"]


def test_blast_radius_limit():
    tight = CAT.model_copy(update={"policy": CAT.policy.model_copy(update={"max_blast_radius": 2})})
    d = decide(stale("raw_orders"), CAT.get("raw_orders"), tight.policy,
               affected=[CAT.get(n) for n in lineage.downstream(G, "raw_orders")], last_fix_at=None)
    assert [r.rule for r in d.failed] == ["blast_radius"]


def test_cycle_fixes_root_and_escalates_unsafe():
    con = duckdb.connect()
    simulate.seed(con, CAT, NOW)
    for name in ["raw_orders", "raw_payments"]:
        simulate.inject_stale(con, CAT, name, NOW, lineage.downstream(G, name))

    decisions = {d.incident.id: d.verdict for d in engine.run_cycle(con, CAT, G, NOW)}
    assert decisions == {"raw_orders:stale": "auto_fix", "raw_payments:stale": "needs_approval"}

    # raw_orders chain is fresh again; only the escalated chain stays stale.
    again = {d.incident.dataset for d in engine.run_cycle(con, CAT, G, NOW)}
    assert again == {"raw_payments"}
