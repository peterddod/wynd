"""Helpers for step tests (PLAN §3.20): load a step package, run it in-process through the full chain, compare
outputs with the shared comparison semantics."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from pydantic import BaseModel

    from wynd.runtime.step import Step
    from wynd.spec.lockfiles import RetryPolicy, StepLock


class StepResult:
    """Result of `run_step`: exit, output, outputs, summary, attempts, usage, model, events, workspace."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.20")


class Mismatch:
    """One difference found by `match_outputs`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.20")


def load_step(step_dir: str | Path, cls: str | None = None) -> type[Step]:
    raise NotImplementedError("PLAN §3.20")


def run_step(
    step: type[Step],
    input: BaseModel | Mapping[str, Any],
    *,
    mode: Literal["live", "record", "replay"] | None = None,
    cassettes: str | Path | None = None,
    lock: StepLock | Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    workspace: str | Path | None = None,
    retry: RetryPolicy | None = None,
) -> StepResult:
    raise NotImplementedError("PLAN §3.20")


def expect(
    result: StepResult, *, exit: str, outputs: Mapping[str, Any] | None = None, present: Iterable[str] = ()
) -> None:
    raise NotImplementedError("PLAN §3.20")


def match_outputs(expected: Any, actual: Any, *, present: Iterable[str] = ()) -> list[Mismatch]:
    raise NotImplementedError("PLAN §3.20")


def sub_tmp(value: Any, tmp: str | Path) -> Any:
    raise NotImplementedError("PLAN §3.20")
