"""`self.runtime`: the per-run handle a step sees (PLAN §5.2; `$DRAFTS/02 §3.6`).

`cache` is the only cross-run state; `http` refuses (cause `config`) unless the step declares the `network` effect.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TypeVar

T = TypeVar("T")


class StepCache:
    """Explicit cross-run cache: one per step id per worker lifetime (lost on worker restart)."""

    def get(self, key: str, default: Any = None) -> Any:
        raise NotImplementedError("PLAN §5.2")

    def set(self, key: str, value: Any) -> None:
        raise NotImplementedError("PLAN §5.2")

    def get_or_set(self, key: str, factory: Callable[[], T]) -> T:
        raise NotImplementedError("PLAN §5.2")

    def delete(self, key: str) -> None:
        raise NotImplementedError("PLAN §5.2")

    def clear(self) -> None:
        raise NotImplementedError("PLAN §5.2")


class StepTrace:
    """`event(name, **data)` -> `step.event`; `emit(type, **fields)` is for runtime internals (`model.call`, ...)."""

    def event(self, name: str, **data: Any) -> None:
        raise NotImplementedError("PLAN §5.2")

    def emit(self, type: str, **fields: Any) -> None:
        raise NotImplementedError("PLAN §5.2")


class RuntimeHandle:
    """`run_id, step_path, step_run, workspace, logger, trace, cache, http`, plus `env(name, default)` and `log`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.2")

    def env(self, name: str, default: str | None = None) -> str:
        raise NotImplementedError("PLAN §5.2")

    def log(self, message: str, **fields: Any) -> None:
        raise NotImplementedError("PLAN §5.2")
