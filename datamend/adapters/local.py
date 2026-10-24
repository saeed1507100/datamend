"""Local orchestrator: runs pipeline files from a folder against DuckDB.

Each job is `<pipelines>/<job>.py` with a function `run(con, now, mode)`.
"""

import importlib.util
from datetime import datetime
from pathlib import Path

from datamend.catalog import Dataset


def load_job(path: Path):
    spec = importlib.util.spec_from_file_location(f"datamend_job_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class LocalOrchestrator:
    def __init__(self, con, pipelines: Path):
        self.con = con
        self.pipelines = Path(pipelines)

    def trigger(self, dataset: Dataset, mode: str, now: datetime) -> str:
        load_job(self.pipelines / f"{dataset.job}.py").run(self.con, now, mode)
        return "fresh"
