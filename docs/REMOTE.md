# datamend for remote platforms: Prefect first

Goal: a company runs its pipelines on Prefect (or Airflow, Step Functions, dbt).
datamend runs inside the company's own cloud on a schedule. It finds failed runs,
reads their logs, has an AI agent fix the code, proves the fix on shadow data,
compares before and after, and opens a pull request that explains all of it.
A person reviews and merges. datamend never merges and never writes to production
tables.

## Status (2026-10-24)

Steps 1 to 4 of the build order below work for Prefect. A real pass against a local
Prefect 3.9 server fixed a real failure (`ValueError` on vendor amounts like
`'$2,369.53'`) in 39 s and 12 agent turns ($0.09), with validation 1.00, and wrote
the PR description. Production stayed at 45 rows, and every shadow schema was
dropped. Opening the real GitHub PR is the one step not run yet: it needs the
demo repo on GitHub.

Command: `datamend watch prefect --repo owner/name` (see `--help`). Code:
`datamend/remote/`. A recorded run for tests and demos:
`examples/remote/orders_etl_valueerror.json`.

## Try it locally

```bash
# In the demo pipeline repo (saeed1507100/datamend-prefect-demo)
prefect server start &
export PREFECT_API_URL=http://127.0.0.1:4200/api
prefect variable set warehouse_path "$PWD/warehouse.duckdb"
python flows/orders.py &                  # serves orders-etl/daily
prefect deployment run orders-etl/daily   # fails on '$2,369.53'

# In datamend
export DATAMEND_AGENT_TOKEN=$(openssl rand -hex 16)
datamend agent serve --workspace-root .datamend/remote &
GITHUB_TOKEN=... datamend watch prefect --repo saeed1507100/datamend-prefect-demo
# or, without a model or GitHub: --no-publish --replay examples/remote/orders_etl_valueerror.json
```

## Flow of one pass

```
 schedule (ECS Fargate task, or a Prefect deployment)
   │
   ▼
 1. Source      failed flow runs since the last pass, with logs, deployment,
                entrypoint, and parameters                     (Prefect REST API)
 2. Dedupe      one investigation per deployment + error signature; skip if an
                open datamend PR already covers it                 (state DB, GitHub)
 3. Workspace   clone the repo at the base branch; build a venv with the repo's
                own dependencies                                  (git, uv)
 4. Reproduce   run the ORIGINAL flow in shadow mode; it must fail the same way
 5. Agent       sandboxed agent reads logs and code, edits allowed paths only
 6. Guardrails  allowed paths, diff size, no secrets in the diff, explanation
 7. Validate    run the PATCHED flow in shadow mode twice; tests; compare shadow
                output with production: schema, rows, keys, nulls, aggregates
                (on failure, feed results back to the agent, up to N attempts)
 8. Publish     branch + PR with root cause, fix, logs, validation, comparison
 9. Audit       every step in the state DB; PR links back to the run id
```

## Shadow mode: how a fix is tested without touching production

The flow runs for real, with the company's Prefect variables, secrets, and blocks,
but writes somewhere else. Each deployment declares how in `datamend.yaml`:

```yaml
deployments:
  orders-etl/daily:
    shadow:
      parameters: {target_schema: "dm_shadow_{run}"}   # the flow writes here instead
    compare:
      - {prod: analytics.orders, shadow: "dm_shadow_{run}.orders", key: order_id}
    allowed_paths: ["flows/**", "etl/**"]
```

If a flow can't be redirected by a parameter, datamend refuses to validate it and
says why. That's the contract a team signs up to: make outputs redirectable.

## Interfaces

| Interface | Prefect (now) | Airflow | Step Functions / Glue | dbt |
| --- | --- | --- | --- | --- |
| `Source.failures(since)` | `POST /flow_runs/filter` | `GET /dags/~/dagRuns?state=failed` | `ListExecutions(FAILED)` | `run_results.json` |
| `Source.logs(run)` | `POST /logs/filter` | task instance logs | CloudWatch Logs | `dbt.log` |
| `Source.code(run)` | deployment `entrypoint` | DAG `fileloc` | Glue `ScriptLocation` / repo map | model path |
| `Runner.shadow_run` | import the flow, call it with shadow params | `dags test` with conf | invoke locally with shadow args | `dbt build --select m --target shadow` |
| `CodeHost` | GitHub PR (GitLab later) | same | same | same |

## Deployment in a company's AWS

- **Image:** `datamend` plus `uv`, `git`, and the Claude Code CLI. The company's
  Python dependencies install into a venv per run, from their lockfile.
- **Schedule:** EventBridge Scheduler runs an ECS Fargate task daily. Prefect shops
  can instead run `datamend watch` as a Prefect deployment.
- **Secrets:** from AWS Secrets Manager into env vars: `PREFECT_API_URL`,
  `PREFECT_API_KEY`, `GITHUB_TOKEN` (fine-grained: contents + pull requests on one
  repo), `ANTHROPIC_API_KEY`, and warehouse credentials scoped to read production
  and write only `dm_shadow_*` schemas.
- **Network:** egress only to Prefect, GitHub, the Anthropic API, and the warehouse.
- **State:** a small database (DuckDB on EFS, or Postgres) for checkpoints and audit.

## Guardrails added for remote work

- The PR is the only output. No merges, no pushes to the base branch.
- Warehouse credentials can't write outside shadow schemas. This is enforced by
  the database, not by datamend.
- The agent never sees secrets. It gets logs (with secrets masked), code, and
  validation results.
- One open PR per deployment and error signature. No retry storms.
- Budget per pass: max investigations, max agent turns, max spend.

## Build order

1. Prefect source, GitHub client, `datamend.yaml` config, state DB. Unit tests with
   fake HTTP.
2. Workspace and shadow runner. Reproduce, then validate patched code, then compare.
3. `datamend watch`: the pass above, with retry-on-validation-failure.
4. End-to-end: local Prefect server, demo repo `saeed1507100/datamend-prefect-demo`
   with a real bug, a real agent run, a real PR.
5. Dockerfile, plus Terraform for ECS Fargate and EventBridge Scheduler.
6. Airflow, then Step Functions, then dbt adapters behind the same interfaces.
