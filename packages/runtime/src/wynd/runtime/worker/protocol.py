"""Venv worker JSON-RPC 2.0 messages (PLAN §3.12; `$DRAFTS/02 §5.3`).

Newline-delimited JSON-RPC over the worker's claimed stdio. `ExecPolicy` and `CassetteConfig` live in
`wynd.runtime.policy`.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from wynd.runtime.policy import CassetteConfig, ExecPolicy
from wynd.runtime.usage import ModelInfo, Usage
from wynd.spec.hashing import interface_hash
from wynd.spec.lockfiles import StepKind
from wynd.spec.records import Summary

if TYPE_CHECKING:
    from wynd.spec.plan import PlanStep

PROTOCOL_VERSION = 1

# JSON-RPC error codes
PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603
EDGE_CHECK_ERROR = -32001          # edge.check failed; data = {"cause": EdgeCheckCause, "message": str}
NOT_INITIALISED = 1001
UNKNOWN_STEP = 1002
PROTOCOL_MISMATCH = 1003


class InitStep(BaseModel):
    id: str
    entrypoint: str
    package_dir: str | None
    kind: StepKind                          # = lock.kind
    interface_hash: str | None = None       # = interface_hash(lock.interface) when lock.interface is set
    context: list[str] = []                 # = lock.context
    exit_codes: dict[str, str] | None = None   # shell only: lock.shell.exit_codes with str keys


class RunStepParams(BaseModel):
    run_id: str
    step_path: str
    step_run: int
    step_id: str
    inputs: dict[str, Any]
    context: dict[str, Any] | None = None
    workspace: str
    policy: ExecPolicy
    cassette: CassetteConfig = CassetteConfig()


class StepTimings(BaseModel):
    started_at: datetime
    ended_at: datetime
    worker_ms: float
    pre_ms: float | None = None
    run_ms: float | None = None
    post_ms: float | None = None


class RunStepResult(BaseModel):
    exit: str
    outputs: dict[str, Any]
    summary: Summary
    attempts: int
    validation_failures: int = 0
    timings: StepTimings
    usage: Usage | None = None
    model: ModelInfo | None = None
    replayed: bool = False


def init_step(step: PlanStep) -> InitStep:
    """The `init` entry of one plan step; the snapshot fields come from its lock (PLAN §3.6)."""
    lock = step.lock
    return InitStep(
        id=step.id,
        entrypoint=step.entrypoint,
        package_dir=step.package_dir,
        kind=lock.kind,
        interface_hash=interface_hash(lock.interface) if lock.interface is not None else None,
        context=list(lock.context),
        exit_codes={str(code): exit for code, exit in lock.shell.exit_codes.items()} if lock.shell else None,
    )
