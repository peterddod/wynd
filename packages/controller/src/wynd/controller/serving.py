"""`ServeService`: warm process containers for `wynd serve` and image runs (PLAN §8.1; `$DRAFTS/06 §5.12`).
Stub; CTL-M2.

Container env = `EnvService.resolve_image(pid)` filtered to the manifest's vars, plus `WYND_REGISTRY_JSON` whenever
the current snapshot is non-empty and `WYND_RUN_API_TOKEN`. Records `.wynd/serve/<container>.json` carry
`started_at`/`stopped_at`; container names are `wynd-<slug(pid)>-<commit[:7]>`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from wynd.runtime.supervisor.client import RunApiClient

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import ServedContainer


class ServeService:
    def __init__(
        self,
        ctx: ControllerContext,
        ctl: Controller,
        run_api_client: Callable[[str], RunApiClient] = RunApiClient,
    ) -> None:
        self.ctx = ctx
        self.ctl = ctl
        self.run_api_client = run_api_client

    def serve(
        self,
        image: str,
        *,
        port: int | None = None,
        extra_env_files: Sequence[Path] = (),
        timeout: float = 120.0,
        register: bool = True,
        name: str | None = None,
    ) -> ServedContainer:
        raise NotImplementedError("PLAN §8.1")

    def acquire(self, image: str) -> tuple[str, bool]:
        """-> (run-API base URL, ephemeral); a ready registered container is found over HTTP only."""
        raise NotImplementedError("PLAN §8.1")

    def stop(self, name_or_image: str) -> list[str]:
        raise NotImplementedError("PLAN §8.1")

    def list(self) -> list[ServedContainer]:
        raise NotImplementedError("PLAN §8.1")
