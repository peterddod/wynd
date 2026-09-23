"""The client side of the venv protocol: spawn, `init`, calls with notifications, kill (PLAN §3.12;
`$DRAFTS/02 §5.4–§5.5`)."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.worker.protocol import InitStep


class WorkerCrashed(RuntimeError):
    """The worker process exited (EOF on its protocol stdout) during a call or `init`."""

    def __init__(self, message: str, *, returncode: int | None = None) -> None:
        super().__init__(message)
        self.returncode = returncode


class StepTimeout(TimeoutError):
    """No response within the call's timeout; the pool kills the worker."""


class WorkerRpcError(RuntimeError):
    """A JSON-RPC error response (protocol fault, or `edge.check` failure with code -32001)."""

    def __init__(self, message: str, *, code: int, data: Any = None) -> None:
        super().__init__(message)
        self.code = code
        self.data = data


class WorkerClient:
    """One worker subprocess: `[python, "-m", "wynd.runtime.worker"]` with `env + PYTHONUNBUFFERED=1`."""

    def __init__(self, python: str, steps: list[InitStep], *, env: Mapping[str, str], cwd: str | None = None) -> None:
        raise NotImplementedError("PLAN §3.12")

    def start(self) -> dict[str, Any]:
        """Spawn and `init`; returns the init result. Raises `WorkerCrashed`."""
        raise NotImplementedError("PLAN §3.12")

    def call(
        self,
        method: str,
        params: dict[str, Any],
        *,
        on_event: Callable[[dict[str, Any]], None],
        timeout: float | None,
    ) -> dict[str, Any]:
        """Send one request; route `event` notifications to `on_event`. Raises `WorkerCrashed`, `StepTimeout`,
        `WorkerRpcError`."""
        raise NotImplementedError("PLAN §3.12")

    def kill(self) -> None:
        raise NotImplementedError("PLAN §3.12")

    def close(self) -> None:
        raise NotImplementedError("PLAN §3.12")

    @property
    def alive(self) -> bool:
        raise NotImplementedError("PLAN §3.12")
