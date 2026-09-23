"""`ReleaseService` (PLAN §8.1 releases row; `$DRAFTS/06 §5.14`). Stub; CTL-REL.

Only built processes can be released. `trigger` resolves the env bindings, ensures the serving container
(`ServingBackend.ensure(release, manifest, env)` with env from `EnvService.resolve_image`), submits through the run
API client (injectable) and mirrors the run; every fire is recorded.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, Literal

from wynd.runtime.supervisor.client import RunApiClient

if TYPE_CHECKING:
    from wynd.controller.api.models_web import CreateReleaseRequest, Release, ReleasePatch
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import EnvCheckDTO, Run, TriggerFire


class ReleaseService:
    def __init__(
        self,
        ctx: ControllerContext,
        ctl: Controller,
        run_api_client: Callable[[str], RunApiClient] = RunApiClient,
    ) -> None:
        self.ctx = ctx
        self.ctl = ctl
        self.run_api_client = run_api_client

    def create(self, req: CreateReleaseRequest) -> Release:
        raise NotImplementedError("PLAN §8.1")

    def list(self, *, process_id: str | None = None) -> list[Release]:
        raise NotImplementedError("PLAN §8.1")

    def get(self, release_id: str) -> Release:
        raise NotImplementedError("PLAN §8.1")

    def update(self, release_id: str, patch: ReleasePatch) -> Release:
        raise NotImplementedError("PLAN §8.1")

    def delete(self, release_id: str) -> None:
        raise NotImplementedError("PLAN §8.1")

    def trigger(
        self,
        release_id: str,
        inputs: dict[str, Any] | None,
        *,
        source: Literal["manual", "schedule", "webhook"],
    ) -> Run:
        raise NotImplementedError("PLAN §8.1")

    def env_check(self, release_id: str) -> EnvCheckDTO:
        raise NotImplementedError("PLAN §8.1")

    def verify_webhook(self, release_id: str, presented: str | None) -> Release:
        raise NotImplementedError("PLAN §8.1")

    def fires(self, release_id: str, limit: int = 50) -> list[TriggerFire]:
        raise NotImplementedError("PLAN §8.1")
