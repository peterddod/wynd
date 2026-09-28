"""Cron expressions (CTL-REL; PLAN §8.1 releases row, `$DRAFTS/06 §5.14` "Cron", §11.2 `test_cron.py`)."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from wynd.controller.errors import Invalid
from wynd.controller.releases.cron import CronExpr

LONDON = ZoneInfo("Europe/London")


def utc(*args: int) -> datetime:
    return datetime(*args, tzinfo=UTC)


# --- parse --------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "field", "expected"), [
    ("*/15 * * * *", "minutes", {0, 15, 30, 45}),
    ("0 9-17/2 * * *", "hours", {9, 11, 13, 15, 17}),
    ("0 0 1,15,28-31 * *", "days", {1, 15, 28, 29, 30, 31}),
    ("0 0 * JAN,jul * ", "months", {1, 7}),
    ("0 0 * 3-5 *", "months", {3, 4, 5}),
    ("0 0 * * MON-FRI", "weekdays", {1, 2, 3, 4, 5}),
    ("0 0 * * 5-7", "weekdays", {5, 6, 0}),                     # 7 is Sunday
    ("0 0 * * 7", "weekdays", {0}),
    ("0 0 * * */2", "weekdays", {0, 2, 4, 6}),
    ("5,10-12 * * * *", "minutes", {5, 10, 11, 12}),
])
def test_field_values(text, field, expected):
    assert getattr(CronExpr.parse(text), field) == frozenset(expected)


@pytest.mark.parametrize(("macro", "fields"), [
    ("@yearly", "0 0 1 1 *"), ("@annually", "0 0 1 1 *"), ("@monthly", "0 0 1 * *"), ("@weekly", "0 0 * * 0"),
    ("@daily", "0 0 * * *"), ("@midnight", "0 0 * * *"), ("@hourly", "0 * * * *"), ("@DAILY", "0 0 * * *"),
])
def test_macros_expand_to_their_fields(macro, fields):
    expr, plain = CronExpr.parse(macro), CronExpr.parse(fields)
    assert expr.source == macro
    assert (expr.minutes, expr.hours, expr.days, expr.months, expr.weekdays, expr.dom_star, expr.dow_star) == \
        (plain.minutes, plain.hours, plain.days, plain.months, plain.weekdays, plain.dom_star, plain.dow_star)


@pytest.mark.parametrize(("text", "message"), [
    ("61 * * * *", "minute field '61': 61 is out of range 0-59"),
    ("* 24 * * *", "hour field '24': 24 is out of range 0-23"),
    ("* * 0 * *", "day-of-month field '0': 0 is out of range 1-31"),
    ("* * * FOO *", "month field 'FOO': 'FOO' is not a number or a name"),
    ("* * * * 8", "day-of-week field '8': 8 is out of range 0-7"),
    ("*/0 * * * *", "minute field '*/0': step '0' must be a positive number"),
    ("5-1 * * * *", "minute field '5-1': range '5-1' runs backwards"),
    ("5/2 * * * *", "minute field '5/2': a step needs '*' or a range before it"),
    ("1,,2 * * * *", "minute field '1,,2': '' is not a number"),
    ("* * * *", "needs 5 fields"),
    ("* * * * * *", "needs 5 fields"),
    ("@reboot", "unknown cron macro '@reboot'"),
])
def test_bad_expressions_name_the_field(text, message):
    with pytest.raises(Invalid) as caught:
        CronExpr.parse(text)
    assert message in caught.value.message


# --- matches ------------------------------------------------------------------------------------------------------------

def test_matches_ignores_seconds():
    expr = CronExpr.parse("*/15 * * * *")
    assert expr.matches(utc(2026, 9, 22, 12, 15, 42))
    assert not expr.matches(utc(2026, 9, 22, 12, 7))


def test_day_of_month_or_day_of_week_when_both_are_restricted():
    expr = CronExpr.parse("0 0 13 * 5")                        # the 13th, or any Friday (Vixie)
    assert expr.matches(utc(2026, 9, 13))                       # a Sunday the 13th
    assert expr.matches(utc(2026, 9, 4))                        # a Friday
    assert not expr.matches(utc(2026, 9, 5))                    # a Saturday the 5th


def test_an_unrestricted_day_field_leaves_the_other_one_alone():
    mondays = CronExpr.parse("0 0 * * 1")
    assert mondays.matches(utc(2026, 9, 21)) and not mondays.matches(utc(2026, 9, 22))
    stepped = CronExpr.parse("0 0 */2 * 1")                     # a '*' day field is unrestricted: AND
    assert stepped.matches(utc(2026, 9, 21)) and not stepped.matches(utc(2026, 9, 28))


def test_the_repeat_of_an_ambiguous_wall_time_does_not_match():
    expr = CronExpr.parse("30 1 * * *")
    first = utc(2026, 10, 25, 0, 30).astimezone(LONDON)         # 01:30 BST
    second = utc(2026, 10, 25, 1, 30).astimezone(LONDON)        # 01:30 GMT
    assert (first.hour, first.minute, first.fold, second.hour, second.minute, second.fold) == (1, 30, 0, 1, 30, 1)
    assert expr.matches(first) and not expr.matches(second)


# --- next_after ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("text", "after", "expected"), [
    ("*/15 * * * *", utc(2026, 9, 22, 12, 7), utc(2026, 9, 22, 12, 15)),
    ("*/15 * * * *", utc(2026, 9, 22, 12, 15), utc(2026, 9, 22, 12, 30)),        # strictly after
    ("*/15 * * * *", utc(2026, 9, 22, 12, 14, 30), utc(2026, 9, 22, 12, 15)),
    ("*/15 * * * *", utc(2026, 9, 22, 23, 50), utc(2026, 9, 23, 0, 0)),
    ("0 9-17/2 * * *", utc(2026, 9, 22, 12, 0), utc(2026, 9, 22, 13, 0)),
    ("0 0 * JAN,JUL *", utc(2026, 9, 22), utc(2027, 1, 1)),
    ("0 0 * * MON-FRI", utc(2026, 9, 25, 12), utc(2026, 9, 28)),                 # Friday -> Monday
    ("0 0 13 * 5", utc(2026, 9, 1), utc(2026, 9, 4)),                            # a Friday before the 13th
    ("@weekly", utc(2026, 9, 22), utc(2026, 9, 27)),                             # the next Sunday
    ("@monthly", utc(2026, 12, 15), utc(2027, 1, 1)),
    ("@yearly", utc(2026, 9, 22), utc(2027, 1, 1)),
    ("0 0 29 2 *", utc(2026, 1, 1), utc(2028, 2, 29)),                           # the next leap day
])
def test_next_after(text, after, expected):
    assert CronExpr.parse(text).next_after(after) == expected


def test_next_after_keeps_the_timezone_of_its_argument():
    found = CronExpr.parse("0 7 * * 1-5").next_after(utc(2026, 9, 22, 12).astimezone(LONDON))
    assert found == datetime(2026, 9, 23, 7, tzinfo=LONDON)
    assert found.astimezone(UTC) == utc(2026, 9, 23, 6)


def test_a_wall_time_in_the_spring_forward_gap_is_skipped():
    expr = CronExpr.parse("30 1 * * *")                         # 01:30 does not exist in London on 2026-03-29
    found = expr.next_after(datetime(2026, 3, 28, 12, tzinfo=LONDON))
    assert found == datetime(2026, 3, 30, 1, 30, tzinfo=LONDON)


def test_an_ambiguous_wall_time_fires_once_at_its_first_occurrence():
    expr = CronExpr.parse("30 1 * * *")
    first = expr.next_after(datetime(2026, 10, 24, 12, tzinfo=LONDON))
    assert (first.fold, first.astimezone(UTC)) == (0, utc(2026, 10, 25, 0, 30))
    assert expr.next_after(first) == datetime(2026, 10, 26, 1, 30, tzinfo=LONDON)


def test_inside_the_repeated_hour_the_passed_first_occurrences_are_not_found_again():
    expr = CronExpr.parse("45 * * * *")
    during = utc(2026, 10, 25, 1, 10).astimezone(LONDON)       # 01:10 GMT, the second pass through 01:xx
    assert during.fold == 1
    # 01:45 already fired at its first occurrence (00:45 UTC), so the next time is 02:45 GMT
    assert expr.next_after(during).astimezone(UTC) == utc(2026, 10, 25, 2, 45)


def test_naive_datetimes_stay_naive():
    assert CronExpr.parse("0 * * * *").next_after(datetime(2026, 9, 22, 12, 30)) == datetime(2026, 9, 22, 13)


def test_a_schedule_that_never_fires_is_invalid():
    with pytest.raises(Invalid) as caught:
        CronExpr.parse("0 0 31 2 *").next_after(utc(2026, 1, 1))
    assert "schedule never fires" in caught.value.message
