# datamend

[![ci](https://github.com/saeed1507100/datamend/actions/workflows/ci.yml/badge.svg)](https://github.com/saeed1507100/datamend/actions/workflows/ci.yml)

**Auto-fix the safe data incidents, escalate the rest — with an audit trail.**

When a fix isn't provably safe, a guardrailed AI agent investigates, proposes a
patch, proves it on a shadow copy of the warehouse, and waits for a person to
approve it. datamend plugs into DuckDB, Postgres, Airflow, Prefect, and AWS.

`datamend` watches dataset freshness and quality, walks the lineage graph to
find the blast radius of a failure, and repairs the incidents that are known
to be safe to repair. Everything else goes to a human. Every decision lands in
an audit log.

> Status: v0.2 (Oct 2026). Runs end to end on synthetic data. Showcase site in [`site/`](site/).

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

## AI investigation

`raw_payments` escalates because its job appends a full export on every run, so a
rerun would duplicate payments and double revenue. `datamend ai investigate`
sends that incident to a Claude Code agent behind a local, Messages-API-shaped
endpoint:

```bash
export DATAMEND_AGENT_TOKEN=$(openssl rand -hex 16)
uv run datamend agent serve &
uv run datamend ai investigate raw_payments:stale   # proposed, validation 7/7
uv run datamend ai approve <run_id> --by you         # branch, apply, rerun, re-check
```

Or replay a recorded real run without calling a model: `make demo-ai`.

The agent can only read and edit files in a sandbox copy. Its patch must pass
static guardrails (one file, small diff, allowlisted imports, literal SQL with no
DDL) and a shadow validation (reruns and backfills change nothing, ids stay
unique, schema and volume hold, downstream checks pass). Every run, whatever its
outcome, is recorded with its prompt, explanation, diff, checks, score, cost, and
approver. Details: [docs/AI.md](docs/AI.md).

## Plug into your platform

datamend needs a `Warehouse` (row count and last load time) and an
`Orchestrator` (run a job in `rerun` or `backfill` mode). Pick them in
`datasets.yaml`:

```yaml
platform:
  warehouse:    {type: postgres, dsn_env: WAREHOUSE_DSN}
  orchestrator: {type: airflow, url: https://airflow.internal}
```

Shipped: DuckDB and any DB-API warehouse; local Python jobs, Airflow, Prefect,
AWS Glue, and AWS Step Functions orchestrators; lineage from YAML or OpenLineage.
Details: [docs/ADAPTERS.md](docs/ADAPTERS.md).

## Watch a Prefect workspace

`datamend watch prefect` reads failed flow runs from the Prefect API. For each
one, it clones the pipeline repo, reproduces the failure in shadow mode, has the
agent fix it, and validates the fix against the real Prefect variables and data,
writing only to a throwaway schema. It then compares the result with production
and opens a pull request with the evidence. The repo declares its shadow contract
in `datamend.yaml`. Design and status: [docs/REMOTE.md](docs/REMOTE.md).

## Showcase site

[`site/`](site/) is a static page that replays a real run: load, failures,
lineage and blast radius, guardrail decisions, the AI investigation, approval,
and the audit trail. `uv run datamend export-scenario` regenerates its data from
the real system. Deployment plan: [docs/DEPLOY.md](docs/DEPLOY.md).

```bash
cd site && npm install && npm run dev
```

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
