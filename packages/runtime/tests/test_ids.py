"""`new_id` / `valid_id` (PLAN §3.1)."""

import re
from datetime import UTC, datetime, timedelta

import pytest

from wynd.runtime import ids
from wynd.runtime.ids import new_id, valid_id

ID_FORMAT = re.compile(r"(?P<prefix>[a-z]+)_(?P<ts>\d{8}T\d{9})_(?P<rand>[0-9a-f]{6})")


def fixed_clock(monkeypatch, when: datetime) -> None:
    monkeypatch.setattr(ids, "_utc_now", lambda: when)


def test_format_with_fixed_clock(monkeypatch):
    fixed_clock(monkeypatch, datetime(2026, 9, 22, 21, 50, 1, 100_000, tzinfo=UTC))
    value = new_id("run")
    m = ID_FORMAT.fullmatch(value)
    assert m, value
    assert m["prefix"] == "run"
    assert m["ts"] == "20260922T215001100"


def test_milliseconds_are_zero_padded_and_truncated(monkeypatch):
    fixed_clock(monkeypatch, datetime(2026, 1, 2, 3, 4, 5, 7_999, tzinfo=UTC))
    assert new_id("job").startswith("job_20260102T030405007_")


def test_timestamp_is_utc_now():
    before = datetime.now(UTC).replace(microsecond=0)
    value = new_id("job")
    after = datetime.now(UTC)
    ts = datetime.strptime(ID_FORMAT.fullmatch(value)["ts"][:15], "%Y%m%dT%H%M%S").replace(tzinfo=UTC)
    assert before <= ts <= after


def test_ids_sort_by_time(monkeypatch):
    start = datetime(2026, 12, 31, 23, 59, 58, 995_000, tzinfo=UTC)
    # crosses ms padding (5 -> 10 -> 100 ms), second, minute, hour, day and year boundaries
    offsets = [0, 4, 5, 15, 105, 1_005, 1_006, 2_000, 61_000, 3_601_000, 86_400_000]
    generated = []
    for ms in offsets:
        fixed_clock(monkeypatch, start + timedelta(milliseconds=ms))
        generated.append(new_id("run"))
    assert sorted(generated) == generated
    assert len(set(generated)) == len(generated)


def test_random_suffix_differs_at_the_same_instant(monkeypatch):
    fixed_clock(monkeypatch, datetime(2026, 9, 22, tzinfo=UTC))
    a, b = new_id("run"), new_id("run")
    assert a[:-6] == b[:-6]
    assert a != b


@pytest.mark.parametrize("prefix", ["run", "job", "rel", "chat", "fire", "turn", "it"])
def test_new_ids_are_valid(prefix):
    assert valid_id(new_id(prefix))


@pytest.mark.parametrize(
    "value",
    ["a", "0", "run_20260922T215001100_a1b2c3", "run-example-1", "A.b-c_d", "abc..def", "x" * 128],
)
def test_valid_ids(value):
    assert valid_id(value)


@pytest.mark.parametrize(
    "value",
    ["", "_x", "-x", ".hidden", "..", "a/b", "../etc", "a b", "a\n", "a\\b", "a:b", "café", "x" * 129],
)
def test_invalid_ids(value):
    assert not valid_id(value)


@pytest.mark.parametrize("value", [None, 12, b"abc"])
def test_non_strings_are_invalid(value):
    assert not valid_id(value)
