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
    upstreams: list[str] = []
    idempotent: bool = True
    critical: Literal["auto", "manual"] = "auto"
    checks: Checks = Checks()


class Policy(BaseModel):
    max_blast_radius: int = 5
    cooldown_minutes: int = 30


class Catalog(BaseModel):
    policy: Policy = Policy()
    datasets: list[Dataset]

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
    return Catalog.model_validate(yaml.safe_load(Path(path).read_text()))
