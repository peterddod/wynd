"""Deterministic summaries (SPEC §3.6 step 5; `$DRAFTS/02 §4.4`)."""

import pytest

from wynd.runtime.summary import MAX_CHARS, project, summarise


@pytest.mark.parametrize("value", [None, True, False, 0, 12.5, "", "short", "x" * MAX_CHARS, [1, 2], {"a": "b"}])
def test_small_values_are_kept(value):
    assert project(value) == value


def test_long_strings_are_truncated_to_the_cap():
    projected = project("y" * 500)
    assert len(projected) == MAX_CHARS
    assert projected == "y" * (MAX_CHARS - 3) + "..."


def test_big_containers_become_markers():
    assert project(list(range(100))) == "<list: 100 items>"
    assert project({f"k{i}": "v" * 10 for i in range(30)}) == "<object: 30 keys>"


def test_container_size_is_measured_as_compact_json():
    fits = ["a" * 97, "b" * 96]                       # ["…","…"] = 2 + 97 + 3 + 96 + 2 = 200 characters
    assert project(fits) == fits
    assert project(["a" * 98, "b" * 96]) == "<list: 2 items>"


def test_summary_keeps_declaration_order_and_the_note():
    summary = summarise("sub.extract", "done", {"z": 1, "a": "x" * 300, "m": [1]}, note="looked fine")
    assert summary.step == "sub.extract" and summary.exit == "done" and summary.note == "looked fine"
    assert list(summary.key_outputs) == ["z", "a", "m"]
    assert summary.key_outputs["a"].endswith("...")


def test_error_summaries_omit_bulky_fields():
    outputs = {"cause": "exception", "message": "ValueError: bad", "type": "ValueError", "traceback": "Traceback…",
               "inputs": {"text": "…"}, "partial_outputs": {"a": 1}, "attempts": 1, "child": None}
    summary = summarise("parse", "error", outputs)
    assert summary.key_outputs == {"cause": "exception", "message": "ValueError: bad", "type": "ValueError",
                                   "attempts": 1}


def test_non_error_exits_keep_fields_named_like_error_fields():
    summary = summarise("fix", "done", {"inputs": [1], "traceback": "none"})
    assert summary.key_outputs == {"inputs": [1], "traceback": "none"}
