"""Run API models (PLAN §3.17; `$DRAFTS/03 §13.4`) and `SUPERVISOR_ENV`, the supervisor's part of every env
manifest."""

from __future__ import annotations

from typing import Any

from wynd.spec.fragments import EnvVar


class RunRequest:
    """`{inputs, run_id?, files: {name: {content_base64}}, metadata}`; input values may be `{"$file": name}`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.17")


class RunCreated:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.17")


class Run:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.17")


class RunSummary:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.17")


class ProcessInfo:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.17")


class ErrorBody:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.17")


def export_json_schema() -> dict[str, Any]:
    """The run API JSON Schema printed by `wynd-supervisor schema` (pinned as `run_api.schema.json`)."""
    raise NotImplementedError("PLAN §3.17")


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
