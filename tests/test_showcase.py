from pathlib import Path

from datamend import showcase

ROOT = Path(__file__).parent.parent


def test_scenario_tells_the_whole_story():
    data = showcase.export(ROOT / "examples/ai/raw_payments_stale.json", ROOT / "examples")
    assert [s["key"] for s in data["steps"]] == [
        "load", "detect", "lineage", "decide", "investigate", "approve"]
    steps = {s["key"]: s for s in data["steps"]}
    assert {r["dataset"] for r in steps["lineage"]["roots"]} == {"raw_orders", "raw_payments"}
    assert steps["decide"]["states"]["revenue_daily"] == "blocked"
    assert steps["investigate"]["ai"]["score"] == 1.0
    assert steps["approve"]["approval"]["result"] == "healthy"
    assert all(v != "stale" for v in steps["approve"]["states"].values())
    assert [e["decision"] for e in data["audit"]][-2:] == ["ai_proposed", "ai_fix"]
