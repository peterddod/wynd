"""What fires schedules: `TriggerBackend`, `SchedulerTriggers` (PLAN §8.1 releases row; `$DRAFTS/06 §5.14`
"Backends"). Stub; CTL-REL.

Selected by `WYND_TRIGGER_BACKEND` (default `scheduler`) from entry-point group `wynd.trigger_backends`; factories
take `(*, env, workspace_root, state_dir, stores, handlers=None)`. `SchedulerTriggers.install/remove/reconcile` are
no-ops (the scheduler reads the release records every tick); `start(ctl)` runs the `Scheduler`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.controller.controller import Controller
    from wynd.runtime.storage import Stores


class TriggerBackend(Protocol):
    name: str

    def install(self, release: Release) -> None: ...
    def remove(self, release_id: str) -> None: ...
    def reconcile(self, releases: Sequence[Release]) -> None: ...   # serve-api startup and after every release change
    def start(self, ctl: Controller) -> None: ...                   # serve-api lifespan
    def stop(self) -> None: ...


class SchedulerTriggers:
    name = "scheduler"

    def __init__(
        self,
        *,
        env: Mapping[str, str],
        workspace_root: Path,
        state_dir: Path,
        stores: Stores,
        handlers: Mapping[str, str] | None = None,
    ) -> None:
        raise NotImplementedError("PLAN §8.1")

    def install(self, release: Release) -> None:
        raise NotImplementedError("PLAN §8.1")

    def remove(self, release_id: str) -> None:
        raise NotImplementedError("PLAN §8.1")

    def reconcile(self, releases: Sequence[Release]) -> None:
        raise NotImplementedError("PLAN §8.1")

    def start(self, ctl: Controller) -> None:
        raise NotImplementedError("PLAN §8.1")

    def stop(self) -> None:
        raise NotImplementedError("PLAN §8.1")


def open_trigger_backend(name: str, **kw: Any) -> TriggerBackend:
    """Entry point `name` of group `wynd.trigger_backends`, called with the backend factory keywords (PLAN §8)."""
    raise NotImplementedError("PLAN §8")
