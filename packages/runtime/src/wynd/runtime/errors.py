"""Runtime exceptions shared by the step API, middleware, loop and executor (PLAN §3.9, §5.1).

The step `error` exit payload itself is `wynd.spec.records.StepError`; `StepFailure` is how code inside a run asks
for that exit with a specific cause.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.usage import Usage
    from wynd.spec.records import StepErrorCause


class StepDefinitionError(TypeError):
    """A step class violates the step contract (Input/Output rules, agentic class checks, tool declarations)."""


class StepFailure(Exception):
    """Resolves the running step to its `error` exit with `cause` (PLAN §3.9 decision table).

    Carries the running `usage` so failed runs still account for cost.
    """

    def __init__(
        self,
        cause: StepErrorCause,
        message: str,
        *,
        partial_outputs: dict[str, Any] | None = None,
        usage: Usage | None = None,
        attempts: int | None = None,
    ) -> None:
        super().__init__(message)
        self.cause = cause
        self.message = message
        self.partial_outputs = partial_outputs
        self.usage = usage
        self.attempts = attempts


class InvalidProcessInputs(ValueError):
    """Top-level process inputs failed validation at the boundary, before any run record exists (HTTP 422).

    `errors` are pydantic error dicts (`loc`, `msg`, `type`, ...).
    """

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        lines = [f"{'.'.join(str(p) for p in e.get('loc', ())) or '<inputs>'}: {e.get('msg', '')}" for e in errors]
        super().__init__("invalid process inputs: " + "; ".join(lines))
        self.errors = errors
