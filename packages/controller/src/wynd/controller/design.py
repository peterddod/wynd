"""`DesignService`: design reads, saves and boundary commits (PLAN §8.1; `$DRAFTS/06 §5.7`, `$DRAFTS/07 §12.3`).
Stub; CTL-DESIGN.

Design scope = the process's `process.yaml`, proto YAML under its own directory, and the proto YAML of every step-root
step it references. Revisions are `sha256:`; YAML via `yaml_to_json`/`dump_yaml`; commits via `process.git.commit_only`
so a commit never spans two processes.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.controller.api.models_web import DesignDoc, SaveRequest, SaveResult
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import CommitInfo


class DesignService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def get(self, pid: str) -> DesignDoc:
        raise NotImplementedError("PLAN §8.1")

    def save(
        self,
        pid: str,
        req: SaveRequest,
        *,
        origin: Literal["web", "chat", "cli"] = "web",
        lock_owner: tuple[str, str] | None = None,
    ) -> SaveResult:
        raise NotImplementedError("PLAN §8.1")

    def commit(
        self,
        pid: str,
        *,
        reason: str,
        summary: str,
        origin: str = "web",
        extra_trailers: Mapping[str, str] | None = None,
    ) -> CommitInfo | None:
        raise NotImplementedError("PLAN §8.1")

    def scope(self, pid: str) -> list[str]:
        raise NotImplementedError("PLAN §8.1")

    def lock(self, pid: str, chat_id: str, turn_id: str) -> None:
        raise NotImplementedError("PLAN §8.1")

    def unlock(self, pid: str, turn_id: str) -> None:
        raise NotImplementedError("PLAN §8.1")

    def locked_by(self, pid: str) -> tuple[str, str] | None:
        raise NotImplementedError("PLAN §8.1")
