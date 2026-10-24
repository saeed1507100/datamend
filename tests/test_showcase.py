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


def test_scenario_carries_trajectory_and_a_feedback_lesson_pair():
    data = showcase.export(ROOT / "examples/ai/raw_payments_stale.json", ROOT / "examples")
    ai = {s["key"]: s for s in data["steps"]}["investigate"]["ai"]
    assert ai["trajectory_source"] == "reconstructed"
    assert {s["kind"] for s in ai["trajectory"]} >= {"tool_call", "guardrails", "validation"}
    ex = data["feedback_example"]
    assert ex["lesson"]["scope"] == "dataset:raw_payments"
    assert ex["feedback"]["text"] in ex["next_prompt_excerpt"]
