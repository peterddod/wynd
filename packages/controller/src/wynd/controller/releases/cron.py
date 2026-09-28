"""Five-field cron expressions, stdlib only (PLAN §8.1; `$DRAFTS/06 §5.14` "Cron").

Fields: minute 0-59, hour 0-23, day of month 1-31, month 1-12 or JAN-DEC, day of week 0-7 or SUN-SAT (7 = Sunday);
comma lists of `*`, `*/n`, `a`, `a-b`, `a-b/n`; macros `@yearly/@annually/@monthly/@weekly/@daily/@midnight/@hourly`.
Day matching follows Vixie cron: a day field whose text starts with `*` is unrestricted, and when both day fields are
restricted a day matches if either does. `next_after` walks naive wall time and localises with the tz of its argument:
a wall time inside a DST gap is skipped, and an ambiguous (fold) wall time fires once, at its first occurrence
(`fold=0`); `matches` is false for the second occurrence, so a per-minute scheduler fires it once too.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo

from wynd.controller.errors import Invalid

MINUTE = timedelta(minutes=1)
SEARCH_DAYS = 366 * 5
MACROS = {
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
    "@monthly": "0 0 1 * *",
    "@weekly": "0 0 * * 0",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@hourly": "0 * * * *",
}
MONTHS = {name: n for n, name in enumerate(
    ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), start=1)}
WEEKDAYS = {name: n for n, name in enumerate(("SUN", "MON", "TUE", "WED", "THU", "FRI", "SAT"))}


@dataclass(frozen=True)
class _Field:
    name: str
    lo: int
    hi: int
    names: dict[str, int]


FIELDS = (
    _Field("minute", 0, 59, {}),
    _Field("hour", 0, 23, {}),
    _Field("day-of-month", 1, 31, {}),
    _Field("month", 1, 12, MONTHS),
    _Field("day-of-week", 0, 7, WEEKDAYS),
)


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
        source = text.strip()
        expanded = source
        if source.startswith("@"):
            if source.lower() not in MACROS:
                raise Invalid(f"unknown cron macro {source!r} (known: {', '.join(MACROS)})")
            expanded = MACROS[source.lower()]
        parts = expanded.split()
        if len(parts) != 5:
            raise Invalid(f"cron expression {text!r} needs 5 fields (minute hour day-of-month month day-of-week), "
                          f"got {len(parts)}")
        minutes, hours, days, months, weekdays = (_parse_field(part, spec) for part, spec in zip(parts, FIELDS))
        return cls(
            minutes=minutes, hours=hours, days=days, months=months,
            weekdays=frozenset(day % 7 for day in weekdays),       # 7 is Sunday
            dom_star=parts[2].startswith("*"), dow_star=parts[4].startswith("*"), source=source,
        )

    def matches(self, dt: datetime) -> bool:
        """Local wall time; seconds ignored."""
        if dt.fold and dt.replace(fold=0).utcoffset() != dt.utcoffset():
            return False                                        # the repeat of an ambiguous wall time
        return (dt.minute in self.minutes and dt.hour in self.hours and dt.month in self.months
                and self._day_matches(dt))

    def next_after(self, dt: datetime) -> datetime:
        """Strictly after `dt`, in the tz of `dt`; `Invalid("schedule never fires")` after 5 years of search."""
        wall = dt.replace(tzinfo=None, second=0, microsecond=0, fold=0) + MINUTE
        limit = wall + timedelta(days=SEARCH_DAYS)
        while wall <= limit:
            if wall.month not in self.months:
                wall = datetime(wall.year + wall.month // 12, wall.month % 12 + 1, 1)
            elif not self._day_matches(wall):
                wall = datetime(wall.year, wall.month, wall.day) + timedelta(days=1)
            elif wall.hour not in self.hours:
                wall = wall.replace(minute=0) + timedelta(hours=1)
            elif wall.minute not in self.minutes:
                wall += MINUTE
            else:
                found = _localise(wall, dt.tzinfo, dt)
                if found is not None:
                    return found
                wall += MINUTE
        raise Invalid(f"schedule never fires: {self.source!r} matches no time in the next 5 years")

    def _day_matches(self, dt: datetime) -> bool:
        dom = dt.day in self.days
        dow = (dt.weekday() + 1) % 7 in self.weekdays           # cron: Sunday = 0
        if self.dom_star or self.dow_star:
            return dom and dow
        return dom or dow


def _localise(wall: datetime, tz: tzinfo | None, after: datetime) -> datetime | None:
    """`wall` in `tz` (first occurrence), or None when it falls in a DST gap or is not strictly after `after`
    (compared in UTC: aware datetimes sharing a tzinfo compare by wall time, ignoring `fold`)."""
    if tz is None:
        return wall
    local = wall.replace(tzinfo=tz)
    utc = local.astimezone(UTC)
    if utc.astimezone(tz).replace(tzinfo=None) != wall:
        return None
    if utc <= after.astimezone(UTC):
        return None
    return local


def _parse_field(text: str, spec: _Field) -> frozenset[int]:
    def bad(problem: str) -> Invalid:
        return Invalid(f"cron {spec.name} field {text!r}: {problem}")

    def value(token: str) -> int:
        if token.isdigit():
            number = int(token)
        elif token.upper() in spec.names:
            number = spec.names[token.upper()]
        else:
            raise bad(f"{token!r} is not a number" + (" or a name" if spec.names else ""))
        if not spec.lo <= number <= spec.hi:
            raise bad(f"{number} is out of range {spec.lo}-{spec.hi}")
        return number

    values: set[int] = set()
    for item in text.split(","):
        base, slash, step_text = item.partition("/")
        step = 1
        if slash:
            if not step_text.isdigit() or int(step_text) < 1:
                raise bad(f"step {step_text!r} must be a positive number")
            step = int(step_text)
        if base == "*":
            lo, hi = spec.lo, spec.hi
        elif "-" in base:
            first, _, last = base.partition("-")
            lo, hi = value(first), value(last)
            if lo > hi:
                raise bad(f"range {base!r} runs backwards")
        elif slash:
            raise bad(f"a step needs '*' or a range before it, got {item!r}")
        else:
            lo = hi = value(base)
        values.update(range(lo, hi + 1, step))
    return frozenset(values)
