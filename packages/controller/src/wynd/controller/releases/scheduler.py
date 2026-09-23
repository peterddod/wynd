"""The in-controller release scheduler (PLAN §8.1, §15 item 51; `$DRAFTS/06 §5.14` "Scheduler"). Stub; CTL-REL.

Runs only inside `wynd serve-api` (at most one per workspace: `flock` on `.wynd/locks/scheduler.lock`); at-most-once
firing (`last_fire_key` is written before firing); at most `max_catch_up_minutes` of catch-up.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.controller import Controller


class Scheduler:
    def __init__(
        self,
        ctl: Controller,
        *,
        clock: Callable[[], datetime] | None = None,
        max_catch_up_minutes: int = 5,
        workers: int = 4,
    ) -> None:
        raise NotImplementedError("PLAN §8.1")

    def start(self) -> bool:
        """False if another process holds the scheduler lock."""
        raise NotImplementedError("PLAN §8.1")

    def stop(self) -> None:
        raise NotImplementedError("PLAN §8.1")

    def tick(self, now: datetime) -> list[str]:
        """Testable core; -> the fired release ids."""
        raise NotImplementedError("PLAN §8.1")
