"""Structured completion (PLAN §5.5; `$DRAFTS/03 §6.2, §6.5–§6.8`).

`complete(step, call)` is a thin wrapper over `complete_structured(StructuredCall(...))`; M5 edge checks call
`complete_structured` with `tools=None`. Validation retries continue the conversation; transport restarts happen only
before any tool call (§3.9).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.middleware import AgentCall, AgentResult
    from wynd.runtime.step import AgenticStep


class StructuredCall:
    """`unit, instruction, context, input, adapter, output_schema, tools, policy, cassette, runtime`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.5")


class ModelLoop:
    """The runtime-owned loop over a ModelProvider (`$DRAFTS/03 §6.5`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.5")

    def run(self) -> AgentResult:
        raise NotImplementedError("PLAN §5.5")


class AgentLoop:
    """The loop around an AgentProvider harness (`$DRAFTS/03 §6.6`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.5")

    def run(self) -> AgentResult:
        raise NotImplementedError("PLAN §5.5")


def complete_structured(call: StructuredCall) -> AgentResult:
    raise NotImplementedError("PLAN §5.5")


def complete(step: AgenticStep, call: AgentCall) -> AgentResult:
    raise NotImplementedError("PLAN §5.5")
