# datamend

[![ci](https://github.com/saeed1507100/datamend/actions/workflows/ci.yml/badge.svg)](https://github.com/saeed1507100/datamend/actions/workflows/ci.yml)

**Auto-fix the safe data incidents, escalate the rest — with an audit trail.**

`datamend` watches dataset freshness and quality, walks the lineage graph to
find the blast radius of a failure, and repairs the incidents that are known
to be safe to repair. Everything else goes to a human. Every decision lands in
an audit log.

> Status: v0.1 (Oct 2026). Runs end to end on synthetic data.

## Why

Past a few hundred datasets, three things break:

- **Stale data goes unnoticed.** A table stops updating and nobody knows until
  a dashboard is wrong.
- **Blast radius is guessed.** When an upstream fails, someone walks the
  dependency chain by hand to find what else is affected.
- **Alerts don't fix anything.** Most incidents are routine — a job that needs
  a re-run, a gap that needs a backfill — yet each one pages a person.

Freshness and data-quality alerting tools exist. Bounded self-correction mostly
doesn't. That is the gap `datamend` targets.

## How it works

```
 datasets.yaml ──► Lineage graph ──► Freshness + DQ checks
 (or OpenLineage)                          │ incident
                                           ▼
                                  Guardrail policy
                                 ┌─────────┴─────────┐
                           known-safe           anything else
                                 │                   │
                          Fix action           needs_approval
                       (rerun / backfill)       (human queue)
                                 └─────────┬─────────┘
                                           ▼
                                      Audit log
```

1. **Catalog** — each dataset declares its upstreams, expected cadence, and
   the checks it must pass.
2. **Lineage** — dependencies form a directed graph, so `datamend` can answer
   "what is downstream of this?" and detect cascading staleness.
3. **Checks** — freshness (last update vs. cadence) and data quality (row
   count, nulls, schema) raise incidents.
4. **Guardrails** — a policy decides whether an incident is in a known-safe
   class. Only then does `datamend` act.
5. **Audit** — every incident, decision, and action is recorded with the
   reason.

## Quickstart

```bash
git clone https://github.com/saeed1507100/datamend.git
cd datamend
make demo          # seeds synthetic datasets, injects failures, runs a cycle
```

Or step by step:

```bash
uv sync
uv run datamend seed                  # synthetic warehouse + catalog
uv run datamend inject stale raw_orders     # break something
uv run datamend inject gap raw_customers    # drop rows
uv run datamend run                         # check → decide → fix/escalate
uv run datamend audit                       # what happened and why
uv run datamend lineage raw_orders          # blast radius
uv run datamend lineage --mermaid           # whole graph, colored by state
uv run datamend serve                       # read-only page at 127.0.0.1:8000
```

`make demo` breaks three datasets. Two are auto-fixed: `raw_orders` (rerun)
and `raw_customers` (backfill). `raw_payments` escalates because its job isn't
idempotent. Its downstream datasets are logged as `blocked`, not fixed.

To add lineage from OpenLineage run events, pass a JSON-lines file:

```bash
uv run datamend lineage raw_payments --events examples/openlineage.jsonl
```

Only `COMPLETE` events count. Edges to datasets missing from the catalog are
skipped with a warning.

## The guardrail model

An auto-fix runs only when **all** of these hold:

| Rule | Why |
| --- | --- |
| The incident type has a registered fix (`stale → rerun`, `gap → backfill`) | No improvised fixes |
| The target job is declared idempotent | A re-run must not duplicate data |
| The blast radius is under the configured limit | Wide impact needs a human |
| The fix hasn't been tried in the cooldown window | Stops retry loops |
| The dataset isn't tagged `critical: manual` | Owners can opt out |

Anything that fails a rule becomes `needs_approval`, with the failed rule as
the reason. Sample audit entry:

```json
{"incident": "orders:stale", "decision": "auto_fix", "action": "rerun:load_orders",
 "blast_radius": 3, "rules_passed": 5, "result": "fresh", "at": "2026-10-24T10:42:11Z"}
```

## Design decisions

- **A graph, not a dependency list.** Impact analysis and cascading staleness
  both need traversal. Once a dataset has two upstreams, a lookup table is
  wrong.
- **Fix the root, not the symptom.** When several datasets are stale, the
  graph finds the furthest-upstream failure and fixes only that one.
- **Never paper over an escalation.** A dataset that depends on an escalated
  root isn't rerun, even if another of its roots was fixed. It's logged as
  `blocked`, with the root it waits on.
- **Allowlist, not denylist.** The agent can only do what is registered as
  safe. Unknown means escalate.
- **Audit everything.** An auto-fix nobody can explain is worse than an alert.

## Built from production experience

The mechanism comes from a system I built and ran on ~1,250 production
datasets, where freshness and lineage visibility also helped cut storage cost
by ~93% by safely retiring unused data. This repo is a from-scratch
reimplementation on synthetic data. It contains no proprietary code, data, or
company details.

## Roadmap

See [docs/PLAN.md](docs/PLAN.md).

## License

MIT
