from datetime import datetime, timedelta

import duckdb
import pytest

from datamend import adapters, audit, catalog, checks, engine, lineage, openlineage, simulate, web
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
    assert failed_rules("raw_products") == ["not_manual"]


def test_cooldown_escalates():
    assert failed_rules("raw_orders", last_fix_at=NOW - timedelta(minutes=5)) == ["cooldown"]


def test_unregistered_fix_escalates():
    dq = Incident(dataset="raw_orders", kind="dq", detail="", detected_at=NOW)
    assert failed_rules("raw_orders", inc=dq) == ["registered_fix"]


def test_gap_is_backfilled_once():
    con = duckdb.connect()
    simulate.seed(con, CAT, NOW)
    simulate.inject_gap(con, "raw_customers")

    [d] = engine.run_cycle(con, CAT, G, NOW)
    assert (d.incident.id, d.action) == ("raw_customers:gap", "backfill:load_raw_customers")
    assert con.execute("SELECT count(*), count(DISTINCT id) FROM raw_customers").fetchone() == (200, 200)
    adapters.build(CAT, con).orchestrator.trigger(CAT.get("raw_customers"), "backfill", NOW)
    assert con.execute("SELECT count(*) FROM raw_customers").fetchone() == (200,)
    assert engine.run_cycle(con, CAT, G, NOW) == []


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


def test_dataset_with_escalated_root_is_blocked_not_rerun():
    con = duckdb.connect()
    simulate.seed(con, CAT, NOW)
    for name in ["raw_orders", "raw_payments"]:
        simulate.inject_stale(con, CAT, name, NOW, lineage.downstream(G, name))
    engine.run_cycle(con, CAT, G, NOW)

    log = {e["dataset"]: e for e in audit.entries(con)}
    assert log["revenue_daily"]["decision"] == "blocked"
    assert log["revenue_daily"]["reason"] == "waiting on raw_payments"
    assert log["orders"]["decision"] == "suppressed"

    stale_now = {i.dataset for i in checks.run_all(adapters.build(CAT, con).warehouse, CAT, NOW)}
    assert "revenue_daily" in stale_now
    assert "orders" not in stale_now


def test_mermaid_colors_nodes_by_decision():
    out = lineage.mermaid(G, {"raw_orders": "auto_fix", "revenue_daily": "blocked"})
    assert "    raw_orders --> orders" in out
    assert "    class raw_orders auto_fix" in out
    assert "    class revenue_daily blocked" in out


def test_openlineage_merges_complete_events_only():
    merged, skipped = openlineage.merge(CAT, "examples/openlineage.jsonl")
    assert merged.get("customer_ltv").upstreams == ["customers", "orders", "payments"]
    assert skipped == [("customers", "campaign_audience")]
    # The START event's revenue_daily input is ignored.
    assert "revenue_daily" not in merged.get("customer_ltv").upstreams
    assert "customer_ltv" in lineage.downstream(lineage.build(merged), "raw_payments")


def test_web_page_renders_audit_and_graph(tmp_path):
    db = tmp_path / "w.duckdb"
    with duckdb.connect(str(db)) as con:
        simulate.seed(con, CAT, NOW)
        simulate.inject_stale(con, CAT, "raw_payments", NOW, lineage.downstream(G, "raw_payments"))
        engine.run_cycle(con, CAT, G, NOW)
    page = web.render(CAT, db)
    assert "class raw_payments needs_approval" in page
    assert "waiting on raw_payments" in page
    assert "1 need approval" in page


def test_web_page_renders_without_db(tmp_path):
    assert "No cycles run yet." in web.render(CAT, tmp_path / "missing.duckdb")
