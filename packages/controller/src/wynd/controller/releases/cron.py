"""Five-field cron expressions, stdlib only (PLAN §8.1; `$DRAFTS/06 §5.14` "Cron"). Stub; CTL-REL.

Fields: minute 0-59, hour 0-23, day of month 1-31, month 1-12 or JAN-DEC, day of week 0-7 or SUN-SAT (7 = Sunday);
comma lists of `*`, `*/n`, `a`, `a-b`, `a-b/n`; macros `@yearly/@annually/@monthly/@weekly/@daily/@midnight/@hourly`;
Vixie day matching (either restricted day field matches); DST gaps skipped, folds fire once.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class CronExpr:
    minutes: frozenset[int]
    hours: frozenset[int]
    days: frozenset[int]
    months: frozenset[int]
    weekdays: frozenset[int]
    dom_star: bool
    dow_star: bool
    source: str

    @classmethod
    def parse(cls, text: str) -> CronExpr:
        """`Invalid` names the bad field."""
        raise NotImplementedError("PLAN §8.1")

    def matches(self, dt: datetime) -> bool:
        """Local wall time; seconds ignored."""
        raise NotImplementedError("PLAN §8.1")

    def next_after(self, dt: datetime) -> datetime:
        """Strictly after `dt`, in the tz of `dt`; `Invalid("schedule never fires")` after 5 years of search."""
        raise NotImplementedError("PLAN §8.1")
