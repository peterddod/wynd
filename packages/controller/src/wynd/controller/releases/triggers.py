"""What fires schedules: `TriggerBackend`, `SchedulerTriggers` (PLAN §8.1 releases row; `$DRAFTS/06 §5.14`
"Backends").

Selected by `WYND_TRIGGER_BACKEND` (default `scheduler`) from entry-point group `wynd.trigger_backends`; factories
take `(*, env, workspace_root, state_dir, stores, handlers=None)`. `SchedulerTriggers.install/remove/reconcile` are
no-ops (the scheduler reads the release records every tick); `start(ctl)` runs the `Scheduler` (only `wynd serve-api`
calls it), `stop()` stops it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from wynd.controller.errors import Invalid

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.controller.controller import Controller
    from wynd.controller.releases.scheduler import Scheduler
    from wynd.runtime.storage import Stores

GROUP = "wynd.trigger_backends"
log = logging.getLogger(__name__)


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
        self.scheduler: Scheduler | None = None

    def install(self, release: Release) -> None:
        return

    def remove(self, release_id: str) -> None:
        return

    def reconcile(self, releases: Sequence[Release]) -> None:
        return

    def start(self, ctl: Controller) -> None:
        """Start the `Scheduler` thread; when another process holds the scheduler lock, that one fires instead."""
        from wynd.controller.releases.scheduler import Scheduler

        if self.scheduler is not None:
            return
        scheduler = Scheduler(ctl)
        if not scheduler.start():
            log.warning("release schedules are fired by another process (the scheduler lock is held)")
            return
        self.scheduler = scheduler

    def stop(self) -> None:
        if self.scheduler is not None:
            self.scheduler.stop()
            self.scheduler = None


def open_trigger_backend(name: str, **kw: Any) -> TriggerBackend:
    """Entry point `name` of group `wynd.trigger_backends`, called with the backend factory keywords (PLAN §8)."""
    found = list(metadata.entry_points(group=GROUP, name=name))
    if not found:
        known = sorted(ep.name for ep in metadata.entry_points(group=GROUP))
        raise Invalid(f"unknown trigger backend {name!r} (WYND_TRIGGER_BACKEND); installed: {', '.join(known)}")
    return found[0].load()(**kw)
