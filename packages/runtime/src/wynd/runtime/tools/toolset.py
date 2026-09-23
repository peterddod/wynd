"""The per-run tool set: invocation, retry policy (idempotent tools only), tracing, cassette replay of network/MCP
calls, MCP connect-and-verify (PLAN §5.1, §5.5; `$DRAFTS/03 §9.2, §10.5`)."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.handle import RuntimeHandle
    from wynd.runtime.providers.types import ToolHandle


class ToolSet:
    """`for_step(step, call, cassettes)`, `schemas()`, `handles()`, `mcp_refs()`, `connect_mcp()`, `invoke(name,
    arguments)`, `trace_harness_calls(transcript)`, `close()`; `calls` counts every requested invocation."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


def is_transient(exc: BaseException) -> bool:
    raise NotImplementedError("PLAN §5.1")


def current_runtime() -> RuntimeHandle:
    """The handle of the step run a builtin/method tool is executing in; outside a run -> RuntimeError."""
    raise NotImplementedError("PLAN §5.1")


def handles_for(fns: Iterable[Callable[..., Any]]) -> list[ToolHandle]:
    """ToolHandles for `@tool` functions outside a step run (e.g. the controller chat)."""
    raise NotImplementedError("PLAN §5.1")
