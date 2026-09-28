"""Per-run and per-process-instance executor state (PLAN §5.4; `$DRAFTS/02 §7.2`): `Instance{plan, prefix,
parent_span, scope, deadline, history}`, completed-run records, deadlines with owner tokens, the run context."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from wynd.runtime.usage import Usage

if TYPE_CHECKING:
    from wynd.runtime.storage.base import Registry
    from wynd.runtime.trace import TraceEmitter
    from wynd.spec.expr.scope import Scope
    from wynd.spec.plan import PlanProcess, RunPlan
    from wynd.spec.records import Summary


@dataclass(frozen=True)
class StepRecord:
    """One completed node run: name, path, run number, exit, inputs, outputs (JSON-mode, no "exit"), summary."""

    name: str
    path: str
    run: int
    exit: str
    inputs: dict[str, Any]
    outputs: dict[str, Any]
    summary: Summary


@dataclass(frozen=True)
class Deadline:
    """A monotonic deadline plus the token of the frame that owns it (`$DRAFTS/02 §7.7`)."""

    at: float                                   # time.monotonic() value
    owner: object                               # token of the frame whose branch timeout set it

    @classmethod
    def after(cls, seconds: float, owner: object) -> Deadline:
        return cls(time.monotonic() + seconds, owner)

    def remaining(self) -> float:
        return self.at - time.monotonic()


def earliest(outer: Deadline | None, own: Deadline | None) -> Deadline | None:
    """The binding deadline: whichever expires first (the outer one on a tie)."""
    if outer is None:
        return own
    if own is None:
        return outer
    return outer if outer.at <= own.at else own


class DeadlineExceeded(Exception):
    """A deadline expired; unwinds (through `finally` steps) to the frame that owns it."""

    def __init__(self, deadline: Deadline) -> None:
        super().__init__("deadline exceeded")
        self.deadline = deadline


class LocalTimeout(Exception):
    """The current frame's own branch timeout expired: route to this instance's error handler (cause `timeout`)."""


@dataclass
class RunCtx:
    """State shared by every instance of one run: run id, workspace, emitter, plan, registry, cassette settings and
    the run's usage total (leaf steps and edge checks, each counted once)."""

    run_id: str
    workspace: Path
    emitter: TraceEmitter
    plan: RunPlan
    registry: Registry
    cassette_mode: Literal["live", "record", "replay"] = "live"
    cassette_root: Path | None = None           # replay/record source: <root>/<step_module_name>/ and <root>/edges/
    record_root: Path | None = None             # record staging, same layout
    cassette_literals: dict[str, str] = field(default_factory=dict)
    usage: Usage = field(default_factory=Usage)


@dataclass
class Instance:
    """One process instance (the root, and each ProcessStep invocation) with its own spec `Scope`."""

    plan: PlanProcess
    ctx: RunCtx
    prefix: str                                 # "" for the root, "sub." for the child of node "sub"
    parent_span: int | None                     # span of the ProcessStep node; None for the root
    scope: Scope
    deadline: Deadline | None = None            # inherited from the enclosing ProcessStep node, if any
    history: list[StepRecord] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)  # this instance including its children
