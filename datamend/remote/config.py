"""`datamend.yaml`, kept in the pipeline repository: the contract between a team and datamend.

It says, per deployment, how to run the flow without touching production (shadow mode),
which tables to compare, and which files the agent may change.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel


class Warehouse(BaseModel):
    """Where flows write. `duckdb` reads its path from a Prefect variable or a literal."""

    type: str = "duckdb"
    path: str | None = None
    path_variable: str | None = None
    dsn_env: str | None = None


class Shadow(BaseModel):
    """Parameters that redirect every write. `{run}` becomes a unique id per validation."""

    parameters: dict
    schema_name: str  # the schema the parameters point at, e.g. "dm_shadow_{run}"


class Expectations(BaseModel):
    """What the patched shadow output must look like, checked on top of the prod comparison.

    `prod` means "the same as the production table". Sums compare only rows whose key is in
    both tables, so a fix that recovers previously failing rows can still pass.
    """

    row_count: Literal["prod", "at_least_prod"] | int | None = None
    keys: Literal["prod", "superset_of_prod"] | None = None
    nulls: dict[str, Literal["prod"] | int] = {}  # shadow nulls must be <= this
    sums_within: dict[str, float] = {}  # column -> relative tolerance, e.g. 0.001


class Compare(BaseModel):
    table: str  # e.g. "orders"; compared as <prod_schema>.<table> vs <shadow schema>.<table>
    key: str | None = None
    expectations: Expectations | None = None


class Deployment(BaseModel):
    prod_schema: str
    shadow: Shadow
    compare: list[Compare]
    allowed_paths: list[str]
    tests: str | None = "pytest -q"


class Limits(BaseModel):
    lookback_hours: int = 24
    max_investigations: int = 3
    max_attempts: int = 2
    max_diff_lines: int = 60
    max_row_delta: float = 0.25  # warn if row count moves more than this fraction
    min_confidence: float = 1.0  # below this, escalate to a person instead of opening a PR


class Agent(BaseModel):
    endpoint: str = "http://127.0.0.1:8787"
    model: str = "sonnet"
    max_turns: int = 20
    max_budget_usd: float = 2.0


class RepoConfig(BaseModel):
    warehouse: Warehouse
    deployments: dict[str, Deployment]  # key: "<flow name>/<deployment name>", or a DAG id
    limits: Limits = Limits()
    agent: Agent = Agent()
    reviewers: list[str] = []  # GitHub logins requested as reviewers on every PR
    labels: list[str] = ["datamend"]  # added to every PR


def load(path: Path) -> RepoConfig:
    return RepoConfig.model_validate(yaml.safe_load(Path(path).read_text()))
