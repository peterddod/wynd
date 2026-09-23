"""The middleware chain around one step run (SPEC §3.6, PLAN §5.1–§5.2; `$DRAFTS/02 §4.2–§4.3`).

Fresh instance per attempt; env/cwd snapshotted before `pre` and restored after `post`; `run` retries for non-agentic
steps only (agentic validation/transport retries live in the loop, `wynd.runtime.agentic.loop.complete`).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.handle import StepCache
    from wynd.runtime.step import Step
    from wynd.runtime.worker.protocol import RunStepParams


class AgentCall:
    """What the chain hands the agentic loop: step path, validated input, assembled context, interface, policy,
    cassette config and the runtime handle."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


class AgentResult:
    """What the agentic loop returns: validated output, note, usage, attempts, model info."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


class ChainResult:
    """Outcome of one chain: exit, output, JSON outputs, summary, attempts, timings, usage, model."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


def run_chain(
    cls: type[Step], params: RunStepParams, *, emit: Callable[[dict[str, Any]], None], cache: StepCache
) -> ChainResult:
    raise NotImplementedError("PLAN §5.1")
