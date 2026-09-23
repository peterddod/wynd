"""Records stored through `RunRegistry` (PLAN §3.14). `JobRecord` (kind "job") lives in `wynd.process.jobs`."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from wynd.runtime.usage import Usage


class RunRecord(BaseModel):
    id: str
    kind: Literal["run"] = "run"
    process: str
    status: Literal["queued", "running", "succeeded", "failed"]      # failed iff exit == "error" (or failed to start)
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    ref: str | None = None
    mode: Literal["local", "image"] | None = None
    inputs: dict[str, Any] = {}
    exit: str | None = None
    outputs: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    trace: str | None = None
    workspace: str | None = None
    duration_ms: float | None = None
    usage: Usage | None = None             # §3.13 Usage totals
    trace_bytes: int | None = None         # storage accounting (SPEC §15), set at run end
    workspace_bytes: int | None = None
    meta: dict[str, Any] = {}              # = dict(metadata) of the run


class TestResult(BaseModel):
    __test__ = False                       # not a pytest test class

    commit: str                            # closure HEAD
    key: str                               # "step:<step_id>:<step_hash>" | "process:<pid>:<process_hash>"
    passed: bool
    counts: dict[str, int]                 # passed/failed/error/skipped
    ran_at: datetime
    job_id: str | None = None
    report: dict[str, Any] | None = None
