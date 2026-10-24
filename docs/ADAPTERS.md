# Plug datamend into a data platform

datamend needs two things from a platform:

| Interface | Question it answers | Method |
| --- | --- | --- |
| `Warehouse` | How many rows does this table have, and when was it last loaded? | `stats(dataset) -> (rows, last_loaded)` |
| `Orchestrator` | Run this dataset's job in `rerun` or `backfill` mode. | `trigger(dataset, mode, now) -> result` |

Lineage, checks, guardrails, the AI fixer, and the audit log are the same on every
platform. You pick adapters in the `platform:` block of `datasets.yaml`.

## Shipped adapters

| Type | Kind | `job` means | Config |
| --- | --- | --- | --- |
| `duckdb` | Warehouse | — | `freshness_column` (default `_loaded_at`) |
| `postgres` | Warehouse | — | `dsn_env` (default `DATAMEND_PG_DSN`). Needs `uv sync --extra postgres`. |
| `local` | Orchestrator | File `<pipelines>/<job>.py` with `run(con, now, mode)` | `pipelines` folder, relative to the catalog |
| `airflow` | Orchestrator | DAG id. `mode` goes in the run `conf`. | `url`, `token_env`, `api` (`v2` for Airflow 3, `v1` for Airflow 2) |
| `prefect` | Orchestrator | `<flow>/<deployment>`. `mode` is a flow parameter. | `url` (the API URL), `key_env` |
| `aws_glue` | Orchestrator | Glue job name. `mode` is passed as `--mode`. | `region`. Needs `uv sync --extra aws`. |
| `aws_stepfunctions` | Orchestrator | State machine name | `state_machine_prefix` (ARN up to the name), `region` |

Any table can point at a schema-qualified name with `table: mart.orders`.
Lineage can also come from OpenLineage run events with `--events events.jsonl`.

## Examples

Airflow 3 with a Postgres warehouse:

```yaml
platform:
  warehouse: {type: postgres, dsn_env: WAREHOUSE_DSN}
  orchestrator: {type: airflow, url: https://airflow.internal, token_env: AIRFLOW_TOKEN}

datasets:
  - {name: raw_orders, table: raw.orders, job: load_raw_orders, cadence_minutes: 60}
```

Prefect Cloud:

```yaml
platform:
  orchestrator:
    type: prefect
    url: https://api.prefect.cloud/api/accounts/<account>/workspaces/<workspace>
    key_env: PREFECT_API_KEY
```

AWS Glue (or Step Functions) over Redshift, through any DB-API driver:

```yaml
platform:
  orchestrator: {type: aws_glue, region: eu-west-1}
```

## What changes on a remote orchestrator

- **Fixes are asynchronous.** `trigger` returns `triggered:<run id>`, and the audit
  log records that. The next `datamend run` confirms the data is fresh.
- **Your jobs read `mode`.** A DAG, flow, or Glue job must handle `rerun` and
  `backfill`. Declare it `idempotent: true` only if a rerun can't duplicate data.
- **AI fixes need the code.** The AI fixer edits and validates pipeline files. On a
  remote orchestrator, point the fixer at the repository that holds the job code, and
  let your normal CI deploy the branch it creates. Shadow validation runs the job on a
  DuckDB copy, so today it supports jobs written as `run(con, now, mode)`. See
  [AI.md](AI.md).

## Write your own adapter

Implement the method, then register the type in `datamend/adapters/__init__.py`:

```python
class Dagster:
    def __init__(self, url: str):
        self.url = url

    def trigger(self, dataset, mode, now) -> str:
        run_id = ...  # launch the job named dataset.job with {"mode": mode}
        return f"triggered:{run_id}"
```

Test it the way `tests/test_adapters.py` tests the others: inject a fake HTTP sender
or SDK client and assert on the calls.
