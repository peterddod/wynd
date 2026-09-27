import datetime

import pytest
import yaml

from wynd.compiler.yamledit import append_block_items, append_mapping_entries, has_marker, remove_marked

PROTO = """\
kind: proto_step          # the kind
name: parse_amount
instruction: Parse an amount.
inputs: { text: string }
outputs: { amount: number }
examples:
  # the common case
  - inputs: { text: "£12.50" }     # pounds
    outputs: { amount: 12.5 }

  # trailing comment inside the block
env: {}
"""

ITEM = {"inputs": {"text": "EUR 3"}, "outputs": {"amount": 3.0}, "exit": "done", "description": "What about euros?"}


def test_append_to_block_list_keeps_comments():
    out = append_block_items(PROTO, "examples", [ITEM], comment="confirmed during wynd compile (session job_1)")
    assert out == PROTO.replace(
        "    outputs: { amount: 12.5 }\n",
        "    outputs: { amount: 12.5 }\n"
        "  # confirmed during wynd compile (session job_1)\n"
        "  - inputs: {text: EUR 3}\n"
        "    outputs: {amount: 3.0}\n"
        "    exit: done\n"
        "    description: What about euros?\n",
    )
    data = yaml.safe_load(out)
    assert data["examples"][-1] == ITEM
    assert data["env"] == {}


def test_append_to_zero_indent_list():
    text = "examples:\n- inputs: {a: 1}\n  exit: done\nenv: {}\n"
    out = append_block_items(text, "examples", [{"inputs": {"a": 2}}], comment="")
    assert out == "examples:\n- inputs: {a: 1}\n  exit: done\n- inputs: {a: 2}\nenv: {}\n"


def test_flow_empty_list_is_replaced():
    text = "name: x\nexamples: []   # none yet\nenv: {}\n"
    out = append_block_items(text, "examples", [{"inputs": {"a": 1}}], comment="c")
    assert out == "name: x\nexamples:\n  # c\n  - inputs: {a: 1}\nenv: {}\n"


def test_missing_key_is_appended_at_eof():
    out = append_block_items("name: x", "examples", [{"inputs": {"a": 1}}], comment="")
    assert out == "name: x\nexamples:\n  - inputs: {a: 1}\n"
    assert yaml.safe_load(out)["examples"] == [{"inputs": {"a": 1}}]


def test_empty_block_at_eof():
    out = append_block_items("name: x\nexamples:\n", "examples", [{"inputs": {}}], comment="")
    assert yaml.safe_load(out)["examples"] == [{"inputs": {}}]


def test_dates_dump_unquoted_and_tmp_strings_quoted():
    item = {"inputs": {"due": datetime.date(2026, 10, 1), "dest": "{tmp}/notes"}}
    out = append_block_items("examples: []\n", "examples", [item], comment="")
    assert "due: 2026-10-01" in out
    assert "'{tmp}/notes'" in out
    assert yaml.safe_load(out)["examples"][0] == item


def test_non_empty_flow_list_is_refused():
    with pytest.raises(ValueError):
        append_block_items("examples: [{inputs: {}}]\n", "examples", [{"inputs": {}}], comment="")


def test_nothing_to_append_is_identity():
    assert append_block_items(PROTO, "examples", [], comment="x") == PROTO


def test_mapping_entries_with_markers_and_removal():
    text = "steps:\n  a: { use: ./steps/a }   # first\n\n# edges follow\nedges: []\n"
    out = append_mapping_entries(text, "steps", {"a_agentic": {"use": "./steps/a_agentic"}}, marker="wynd:split a",
                                 note="added by wynd compile")
    assert out == (
        "steps:\n  a: { use: ./steps/a }   # first\n"
        "  # wynd:split a begin (added by wynd compile)\n"
        "  a_agentic: {use: ./steps/a_agentic}\n"
        "  # wynd:split a end\n"
        "\n# edges follow\nedges: []\n"
    )
    assert has_marker(out, "wynd:split a")
    assert not has_marker(out, "wynd:split ab")
    assert remove_marked(out, "wynd:split a") == text


def test_list_markers_and_removal_of_several_blocks():
    text = "edges:\n  - from: a.done\n    to: b\n"
    out = append_block_items(text, "edges", [{"from": "c.done", "to": "d"}], comment="", marker="wynd:split c")
    out = append_block_items(out, "edges", [{"from": "e.done", "to": "f"}], comment="", marker="wynd:split c")
    assert [e["from"] for e in yaml.safe_load(out)["edges"]] == ["a.done", "c.done", "e.done"]
    assert remove_marked(out, "wynd:split c") == text


def test_remove_marked_without_markers_is_identity():
    assert remove_marked(PROTO, "wynd:split x") == PROTO


def test_remove_marked_unterminated_raises():
    with pytest.raises(ValueError):
        remove_marked("a: 1\n# wynd:split x begin\nb: 2\n", "wynd:split x")
