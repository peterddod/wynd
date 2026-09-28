"""Run API models (PLAN §3.17; `$DRAFTS/03 §13.4`) and `SUPERVISOR_ENV`, the supervisor's part of every env
manifest."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator
from pydantic.json_schema import models_json_schema

from wynd.runtime.ids import valid_id
from wynd.spec.fragments import EnvVar

API_VERSION = "1"
RunStatus = Literal["queued", "running", "succeeded", "failed"]      # failed iff exit == "error"
_ID_RULE = "must match ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$"


class FileUpload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content_base64: str


class RunRequest(BaseModel):
    """`{inputs, run_id?, files: {name: {content_base64}}, metadata}`; input values may be `{"$file": name}`."""

    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, Any]
    run_id: str | None = None                                # idempotency key
    files: dict[str, FileUpload] = {}
    metadata: dict[str, Any] = {}                            # PLAN §3.14 "Run metadata"; RunRecord.meta

    @field_validator("run_id")
    @classmethod
    def _run_id(cls, value: str | None) -> str | None:
        if value is not None and not valid_id(value):
            raise ValueError(f"invalid run id: {_ID_RULE}")
        return value

    @field_validator("files")
    @classmethod
    def _file_names(cls, value: dict[str, FileUpload]) -> dict[str, FileUpload]:
        for name in value:
            if not valid_id(name):
                raise ValueError(f"invalid file name {name!r}: {_ID_RULE}")
        return value


class Links(BaseModel):
    self: str
    events: str
    outputs: str


class RunCreated(BaseModel):
    run_id: str
    status: RunStatus
    links: Links


class UsageTotals(BaseModel):                                # runtime Usage totals over the run (PLAN §3.13)
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float | None
    latency_ms: float
    calls: int


class Run(BaseModel):
    api_version: Literal["1"] = "1"
    run_id: str
    process: str
    commit: str | None
    runtime_version: str
    mode: Literal["image", "local"]
    status: RunStatus
    exit: str | None                                         # set when terminal
    outputs: dict[str, Any] | None                           # outputs of that exit (without "exit")
    error: dict[str, Any] | None                             # ProcessError JSON
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: float | None
    usage: UsageTotals | None
    metadata: dict[str, Any]
    links: Links


class RunSummary(BaseModel):
    run_id: str
    status: RunStatus
    exit: str | None
    created_at: datetime
    finished_at: datetime | None


class ProcessInfo(BaseModel):
    api_version: Literal["1"] = "1"
    process: str
    name: str
    goal: str | None
    commit: str | None
    runtime_version: str
    inputs_schema: dict[str, Any]                            # JSON Schema of the process inputs
    outputs_schema: dict[str, dict[str, Any]]                # per exit
    steps: list[str]
    limits: dict[str, int]                                   # max_concurrent_runs, max_queued_runs, max_body_mb


class ErrorBody(BaseModel):
    error: dict[str, Any]                                    # {"code": str, "message": str, "details": Any}


def links(run_id: str) -> Links:
    base = f"/v1/runs/{run_id}"
    return Links(self=base, events=f"{base}/events", outputs=f"{base}/outputs")


def export_json_schema() -> dict[str, Any]:
    """The run API JSON Schema printed by `wynd-supervisor schema` (pinned as `run_api.schema.json`)."""
    models = [(RunRequest, "validation"), (RunCreated, "serialization"), (Run, "serialization"),
              (RunSummary, "serialization"), (ProcessInfo, "serialization"), (ErrorBody, "serialization")]
    _, schema = models_json_schema(models, title=f"Wynd run API v{API_VERSION}")
    return {"$schema": "https://json-schema.org/draft/2020-12/schema", "api_version": API_VERSION, **schema}


SUPERVISOR_ENV: list[EnvVar] = [
    EnvVar(
        name="WYND_RUN_API_TOKEN",
        description="If set, every /v1/* request needs `Authorization: Bearer <token>` (health probes are exempt).",
        secret=True,
        required=False,
        used_by=["runtime"],
    ),
    EnvVar(
        name="WYND_HOST",
        description="Run API listen address.",
        required=False,
        default="0.0.0.0",
        used_by=["runtime"],
    ),
    EnvVar(
        name="WYND_PORT",
        description="Run API listen port.",
        required=False,
        default="8080",
        used_by=["runtime"],
    ),
    EnvVar(
        name="WYND_MAX_CONCURRENT_RUNS",
        description="Runs executing at once (they interleave at step granularity).",
        required=False,
        default="4",
        used_by=["runtime"],
    ),
    EnvVar(
        name="WYND_MAX_QUEUED_RUNS",
        description="Runs queued beyond the concurrency limit before POST /v1/runs answers 429.",
        required=False,
        default="64",
        used_by=["runtime"],
    ),
    EnvVar(
        name="WYND_RUN_RETENTION",
        description="Finished runs kept in memory for GET and SSE (the run registry keeps every record).",
        required=False,
        default="1000",
        used_by=["runtime"],
    ),
    EnvVar(
        name="WYND_DRAIN_TIMEOUT_S",
        description="Seconds to wait for running runs on SIGTERM before stopping the workers.",
        required=False,
        default="30",
        used_by=["runtime"],
    ),
    EnvVar(
        name="WYND_MAX_BODY_MB",
        description="Request body cap in MB (uploaded files are base64).",
        required=False,
        default="32",
        used_by=["runtime"],
    ),
]
