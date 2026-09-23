"""The single evaluation context of a process instance; spec-owned, maintained by the runtime (PLAN §3.5)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.spec.process_doc import ProcessDoc


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class StepState:
    runs: int = 0  # completed runs in this process instance (any exit)
    exit: str | None = None  # latest completed run's exit; None if never ran
    outputs: dict[str, Any] | None = None  # latest run's fields, JSON-mode, WITHOUT "exit"; None if never ran
    summary: dict[str, Any] | None = None  # latest Summary as JSON


@dataclass
class EdgeState:
    taken: list[int]  # per branch index
    names: dict[str, int]  # branch name -> index


@dataclass
class Scope:
    steps: dict[str, StepState]  # every step key of this process instance
    edges: dict[str, EdgeState]  # every edge key ("validate.done") of this instance
    process_inputs: dict[str, Any]
    env: Mapping[str, str]
    run_id: str
    previous: str | None = None  # key of the most recently completed step
    clock: Callable[[], datetime] = utc_now

    def complete(self, step: str, exit: str, outputs: Mapping[str, Any], summary: Mapping[str, Any] | None) -> None:
        """runs += 1; set exit/outputs/summary; previous = step."""
        raise NotImplementedError("PLAN §3.5")

    def take(self, edge: str, branch: int) -> int:
        """taken[branch] += 1; returns the new count."""
        raise NotImplementedError("PLAN §3.5")


def new_scope(
    doc: ProcessDoc,
    *,
    inputs: Mapping[str, Any],
    env: Mapping[str, str],
    run_id: str,
    clock: Callable[[], datetime] = utc_now,
) -> Scope:
    raise NotImplementedError("PLAN §3.5")
