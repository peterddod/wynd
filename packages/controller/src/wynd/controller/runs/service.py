"""`RunService` (PLAN §8.1; `$DRAFTS/06 §5.10`). Stub; CTL-CORE.

Env gate: before any local or image run, `ctl.env.check(pid, mode="local"|"image")`; errors raise `EnvMissing` and
no `RunRecord` is created. Local target = `wynd.process.local.run_local(...)` with metadata
`{"trigger", "release_id", "target"}`; image/release targets call `wynd.controller.runs.image`. `Run` is the
`RunRecord` projection.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import CreateRunRequest, Run, RunTarget, RunTrigger


class RunService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def run(
        self,
        pid: str,
        inputs: dict[str, Any],
        *,
        target: RunTarget | None = None,
        on_event: Callable[[dict], None] | None = None,
        trigger: RunTrigger = "api",
    ) -> Run:
        """Blocking; `target=None` is the local target."""
        raise NotImplementedError("PLAN §8.1")

    def start(self, req: CreateRunRequest, *, trigger: RunTrigger = "api") -> Run:
        """Background thread; returns the run in state `running`."""
        raise NotImplementedError("PLAN §8.1")

    def get(self, run_id: str) -> Run:
        raise NotImplementedError("PLAN §8.1")

    def list(self, *, process_id: str | None = None, release_id: str | None = None, limit: int = 50) -> list[Run]:
        raise NotImplementedError("PLAN §8.1")

    def events(self, run_id: str, since: int = 0) -> list[dict]:
        """PLAN §3.13 events with `seq > since`, passed through unchanged."""
        raise NotImplementedError("PLAN §8.1")

    def follow(
        self,
        run_id: str,
        *,
        since: int = 0,
        on_event: Callable[[dict], None],
        poll: float = 0.2,
        timeout: float | None = None,
    ) -> Run:
        raise NotImplementedError("PLAN §8.1")
