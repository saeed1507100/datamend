"""Load and validate datasets.yaml into Dataset models."""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, model_validator


class Checks(BaseModel):
    min_rows: int | None = None


class Dataset(BaseModel):
    name: str
    cadence_minutes: int
    job: str
    table: str | None = None
    upstreams: list[str] = []
    idempotent: bool = True
    critical: Literal["auto", "manual"] = "auto"
    checks: Checks = Checks()


class Policy(BaseModel):
    max_blast_radius: int = 5
    cooldown_minutes: int = 30


class PlatformConfig(BaseModel):
    warehouse: dict = {"type": "duckdb"}
    orchestrator: dict = {"type": "local", "pipelines": "pipelines"}


class AiConfig(BaseModel):
    """Guardrails for AI-proposed fixes. See docs/AI.md."""

    endpoint: str = "http://127.0.0.1:8787"
    model: str = "sonnet"
    # Only incidents escalated for these rules may go to the AI. An owner opt-out
    # (not_manual), a wide blast radius, or a cooldown always stays with a person.
    eligible_rules: list[str] = ["idempotent"]
    max_diff_lines: int = 40
    max_turns: int = 12
    max_budget_usd: float = 1.0
    min_score: float = 1.0
    apply: Literal["approve", "auto"] = "approve"
    git_branch: bool = True
    git_push: bool = False


class Catalog(BaseModel):
    policy: Policy = Policy()
    platform: PlatformConfig = PlatformConfig()
    ai: AiConfig = AiConfig()
    datasets: list[Dataset]
    base_dir: Path = Path(".")

    @model_validator(mode="after")
    def _upstreams_exist(self) -> "Catalog":
        names = {d.name for d in self.datasets}
        if len(names) != len(self.datasets):
            raise ValueError("duplicate dataset names")
        for d in self.datasets:
            missing = set(d.upstreams) - names
            if missing:
                raise ValueError(f"{d.name}: unknown upstreams {sorted(missing)}")
        return self

    def get(self, name: str) -> Dataset:
        for d in self.datasets:
            if d.name == name:
                return d
        raise KeyError(name)


def load(path: str | Path) -> Catalog:
    """Relative paths in the catalog (like the pipelines folder) resolve against its folder."""
    path = Path(path)
    return Catalog.model_validate({**yaml.safe_load(path.read_text()), "base_dir": path.parent})
