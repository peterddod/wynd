"""The in-controller release scheduler (PLAN §8.1, §15 item 51; `$DRAFTS/06 §5.14` "Scheduler").

Runs only inside `wynd serve-api` (at most one per workspace: `flock` on `.wynd/locks/scheduler.lock`); at-most-once
firing (`last_fire_key`, the UTC minute, is written before firing and only a later minute fires again); at most
`max_catch_up_minutes` of loop delay are caught up, and minutes missed while no scheduler ran are not.

`tick(now)` checks every minute in `(last, now]` for each enabled schedule release, in the release's timezone (UTC
when none), fires at most once per release per tick and hands the fire to a thread pool
(`ctl.releases.trigger(id, None, source="schedule")`, which records every fire itself).
"""

from __future__ import annotations

import fcntl
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, TextIO
from zoneinfo import ZoneInfo

from wynd.controller.errors import WyndError

if TYPE_CHECKING:
    from wynd.controller.controller import Controller

MINUTE = timedelta(minutes=1)
log = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        ctl: Controller,
        *,
        clock: Callable[[], datetime] | None = None,
        max_catch_up_minutes: int = 5,
        workers: int = 4,
    ) -> None:
        self.ctl = ctl
        self.clock = clock or ctl.ctx.clock
        self.max_catch_up_minutes = max_catch_up_minutes
        self.pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wynd-schedule")
        self.last = floor_minute(self.clock())
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._lock_file: TextIO | None = None

    def start(self) -> bool:
        """False if another process holds the scheduler lock."""
        path = self.ctl.ctx.state_dir / "locks" / "scheduler.lock"
        path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(path, "a")
        try:
            fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_file.close()
            return False
        self._lock_file = lock_file
        self.last = floor_minute(self.clock())
        self._thread = threading.Thread(target=self._loop, name="wynd-scheduler", daemon=True)
        self._thread.start()
        return True

    def stop(self) -> None:
        """Stop the loop, wait for fires in flight to be submitted, release the lock."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join()
            self._thread = None
        self.pool.shutdown(wait=True)
        if self._lock_file is not None:
            self._lock_file.close()                     # closing the descriptor releases the flock
            self._lock_file = None

    def tick(self, now: datetime) -> list[str]:
        """Testable core; -> the fired release ids."""
        from wynd.controller.releases.cron import CronExpr
        from wynd.controller.releases.store import RELEASES

        try:
            self.ctl.registries.refresh_tokens()
        except Exception as err:  # noqa: BLE001 — an OAuth refresh failure must not stop the schedules
            log.warning("refreshing MCP OAuth tokens failed: %s", err)
        now = floor_minute(now)
        first = max(self.last, now - self.max_catch_up_minutes * MINUTE) + MINUTE
        minutes = [first + n * MINUTE for n in range(int((now - first) / MINUTE) + 1)] if first <= now else []
        self.last = max(self.last, now)

        fired = []
        for doc in self.ctl.ctx.docs.list(RELEASES):
            trigger = doc.get("trigger") or {}
            if not doc.get("enabled") or trigger.get("kind") != "schedule":
                continue
            try:
                cron = CronExpr.parse(trigger["cron"])
                tz = ZoneInfo(trigger["timezone"]) if trigger.get("timezone") else UTC
            except (WyndError, KeyError, ValueError) as err:
                log.warning("release %s: unusable schedule: %s", doc["id"], err)
                continue
            for minute in minutes:
                key = minute.astimezone(UTC).isoformat()
                if key <= (doc.get("last_fire_key") or "") or not cron.matches(minute.astimezone(tz)):
                    continue
                self.ctl.ctx.docs.update(RELEASES, doc["id"], {"last_fire_key": key, "last_fired_at": self.clock()})
                self.pool.submit(self._fire, doc["id"])
                fired.append(doc["id"])
                break
        return fired

    def _fire(self, release_id: str) -> None:
        try:
            self.ctl.releases.trigger(release_id, None, source="schedule")
        except Exception as err:  # noqa: BLE001 — the fire record holds the error; the pool must keep running
            log.warning("scheduled fire of release %s failed: %s", release_id, err)

    def _loop(self) -> None:
        while not self._stop.wait(_until_next_minute(self.clock())):
            try:
                self.tick(self.clock())
            except Exception as err:  # noqa: BLE001 — one failing tick must not end the schedule
                log.warning("scheduler tick failed: %s", err)


def floor_minute(dt: datetime) -> datetime:
    return dt.replace(second=0, microsecond=0)


def _until_next_minute(now: datetime) -> float:
    """Seconds until 0.5 s past the next minute boundary."""
    return 60.0 - now.second - now.microsecond / 1e6 + 0.5
