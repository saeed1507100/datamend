# Implementation plan

Goal for v0.1: someone clones the repo, runs `make demo`, and watches one
incident auto-fixed and one escalated, with lineage and an audit log.

## Stack

| Choice | Why |
| --- | --- |
| Python 3.12 + `uv` | Fast setup, one tool |
| DuckDB | The "warehouse" and the state store in one file, no server |
| `networkx` | Graph traversal without writing it |
| Typer | CLI with almost no code |
| Pydantic | Validates `datasets.yaml` at the boundary |
| pytest | Tests the guardrail rules |

No Docker in v0.1: `uv sync` is already one command. Add a `Dockerfile` later
if reviewers ask.

## Layout

```
datamend/
├── catalog.py      # load + validate datasets.yaml → Dataset models
├── lineage.py      # build graph, downstream(), root_cause()
├── checks.py       # freshness, row_count, null_rate → Incident
├── policy.py       # guardrail rules → Decision(auto_fix | needs_approval, reason)
├── actions.py      # registered fixes: rerun, backfill
├── audit.py        # append + query audit table
├── engine.py       # one cycle: check → dedupe by root → decide → act → audit
├── simulate.py     # seed synthetic warehouse, inject failures
└── cli.py          # seed, inject, run, audit, lineage
examples/datasets.yaml
tests/
```

## Core types

```python
Dataset(name, upstreams, cadence_minutes, job, idempotent, critical, checks)
Incident(dataset, kind: stale|gap|dq, detail, detected_at)
Decision(incident, verdict: auto_fix|needs_approval, rules: list[RuleResult])
AuditEntry(incident, decision, action, result, at)
```

## Session 1 (today, 2 h) — the demo slice

| # | Step | Time | Done when |
| --- | --- | --- | --- |
| 1 | Repo, `pyproject`, layout, this plan | 15 min | Pushed |
| 2 | `catalog.py` + `examples/datasets.yaml` (8 datasets, 3 layers) | 15 min | Loads and validates |
| 3 | `lineage.py`: downstream + root cause | 15 min | Test passes |
| 4 | `simulate.py`: seed tables with `_loaded_at`, inject stale/gap | 20 min | `datamend seed` works |
| 5 | `checks.py`: freshness + row count | 15 min | Injected failure is detected |
| 6 | `policy.py` + `actions.py` (rerun only) | 20 min | Rule tests pass |
| 7 | `engine.py` + `audit.py` + CLI | 15 min | `make demo` end to end |
| 8 | Buffer, README check | 5 min | — |

Cut order if time runs short: drop `gap/backfill`, then the `null_rate`
check, then the cooldown rule. Never cut the audit log or the escalation path.

## Status (2026-10-24)

Sessions 1 and 2 are done, except recording the GIF: `docs/demo.tape` is
ready, run `vhs docs/demo.tape`. In Session 3, OpenLineage input and the web
view are done. The AWS deploy, demo video, and post are still open.

Session 4 (Oct 24) added platform adapters (DuckDB, DB-API, Airflow, Prefect,
AWS Glue, Step Functions), real pipeline job files, the guardrailed AI fixer
with a Claude Code agent endpoint, and the showcase site. Next: deploy the site
(see DEPLOY.md), and decide whether AI fixes should open pull requests.

## Next phase

The ordered next steps, and which ones need Saeed, are in [LOG.md](LOG.md#next-steps).

## Session 2 (next Sat) — make it convincing

- Backfill action + gap incident.
- `datamend lineage` renders a Mermaid graph with incident colors.
- Record the README GIF (`vhs` or asciinema).
- GitHub Actions: ruff + pytest.

## Session 3 (Nov) — reach

- Read lineage from OpenLineage events, not only YAML.
- Optional `deploy/aws/`: freshness events via Kinesis → Lambda → S3 Iceberg
  audit table (ties to AWS DEA prep).
- Small read-only web view: graph, incidents, audit.
- Demo video, publish post.

## Out of scope

Alert routing (Slack/PagerDuty), a scheduler, auth, multi-tenant anything,
ML anomaly detection, a full observability UI.
