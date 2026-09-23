"""`self.runtime`: the per-run handle a step sees (PLAN §5.2; `$DRAFTS/02 §3.6`).

`cache` is the only cross-run state; `http` refuses (cause `config`) unless the step declares the `network` effect.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from wynd.runtime.errors import StepFailure
from wynd.runtime.http import HttpClient

T = TypeVar("T")


class StepCache:
    """Explicit cross-run cache: one per step id per worker lifetime (lost on worker restart)."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        self._data[key] = value

    def get_or_set(self, key: str, factory: Callable[[], T]) -> T:
        if key not in self._data:
            self._data[key] = factory()
        return self._data[key]

    def delete(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


class StepTrace:
    """`event(name, **data)` -> `step.event`; `emit(type, **fields)` is for runtime internals (`model.call`, ...).

    Events are handed to `emit` without `seq/ts/run_id/step/span/parent`; the executor stamps those."""

    def __init__(self, emit: Callable[[dict[str, Any]], None]) -> None:
        self._emit = emit

    def event(self, name: str, **data: Any) -> None:
        self._emit({"type": "step.event", "name": name, "data": data})

    def emit(self, type: str, **fields: Any) -> None:
        self._emit({"type": type, **fields})


@dataclass
class RuntimeHandle:
    """`self.runtime` of one step run. `workspace` is also the cwd during pre/run/post; `logger` records become
    `step.log` events; `http` needs the `network` effect."""

    run_id: str
    step_path: str
    step_run: int
    workspace: Path
    logger: logging.Logger
    trace: StepTrace
    cache: StepCache
    effects: tuple[str, ...] = ()          # the step's declared effects (ExecPolicy.effects)
    http_client: HttpClient = field(default_factory=HttpClient, repr=False)

    @property
    def http(self) -> HttpClient:
        if "network" not in self.effects:
            raise StepFailure("config", "step does not declare effects: [network]")
        return self.http_client

    def env(self, name: str, default: str | None = None) -> str:
        """A set (present, non-empty) env var, else `default`; neither -> `MissingEnvVar` (cause `config`)."""
        value = os.environ.get(name)
        if value:
            return value
        if default is not None:
            return default
        from wynd.runtime.agentic.errors import MissingEnvVar

        raise MissingEnvVar(name)

    def log(self, message: str, **fields: Any) -> None:
        """Log at INFO through `logger` (-> a `step.log` event); `fields` are appended as sorted JSON."""
        if fields:
            message = f"{message} {json.dumps(fields, sort_keys=True, ensure_ascii=False, default=str)}"
        self.logger.info(message)
