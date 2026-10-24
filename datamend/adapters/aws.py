"""AWS Glue jobs and Step Functions state machines. Needs `uv sync --extra aws`."""

import json
from datetime import datetime

from datamend.catalog import Dataset


def _client(service: str, region: str | None, client):
    if client is not None:
        return client
    import boto3

    return boto3.client(service, region_name=region)


class Glue:
    """The dataset's `job` is the Glue job name; mode is passed as `--mode`."""

    def __init__(self, region: str | None = None, client=None):
        self.glue = _client("glue", region, client)

    def trigger(self, dataset: Dataset, mode: str, now: datetime) -> str:
        run = self.glue.start_job_run(JobName=dataset.job, Arguments={"--mode": mode})
        return f"triggered:{run['JobRunId']}"


class StepFunctions:
    """The dataset's `job` is the state machine name, in `state_machine_prefix`."""

    def __init__(self, state_machine_prefix: str, region: str | None = None, client=None):
        self.prefix = state_machine_prefix
        self.sfn = _client("stepfunctions", region, client)

    def trigger(self, dataset: Dataset, mode: str, now: datetime) -> str:
        run = self.sfn.start_execution(
            stateMachineArn=f"{self.prefix}{dataset.job}",
            input=json.dumps({"dataset": dataset.name, "mode": mode}),
        )
        return f"triggered:{run['executionArn']}"
