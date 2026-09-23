"""YAML loading with marks, YAML 1.2 booleans, duplicate keys, located pydantic errors, dumping and yaml_to_json
($DRAFTS/01 §4.3, §12.7; PLAN §4.1)."""

import json
from datetime import date
from pathlib import Path

import pytest

from wynd.spec import (
    ProcessDoc,
    ProtoStep,
    SpecError,
    StepLock,
    dump_yaml,
    load_model,
    parse_model,
    parse_yaml,
    read_yaml,
    yaml_to_json,
)
from wynd.spec.yamlio import Mark, mark_at, raw_loc

FIXTURES = Path(__file__).parent / "fixtures"


def test_yaml12_booleans_and_scalars():
    data, _ = parse_yaml("a: yes\nb: no\nc: on\nd: off\ne: True\nf: false\ng: y\nh: 2026-10-01\ni: 1.5\nj: null\n")
    assert data == {"a": "yes", "b": "no", "c": "on", "d": "off", "e": True, "f": False, "g": "y",
                    "h": date(2026, 10, 1), "i": 1.5, "j": None}


def test_exit_code_keys_load_as_int_and_str():
    data, _ = parse_yaml('exit_codes: {0: done, "*": error}\n')
    assert data == {"exit_codes": {0: "done", "*": "error"}}


def test_marks_for_every_node_and_style():
    data, source = parse_yaml("a:\n  b: [1, 'two']\n  c: |\n    text\n", "f.yaml")
    assert source.file == "f.yaml"
    assert source.marks[()] == Mark(1, 1, None)
    assert source.marks[("a", "b", 1)] == Mark(2, 10, "'")
    assert source.marks[("a", "c")] == Mark(3, 6, "|")
    assert source.position(("a", "b", 5, "x")) == source.marks[("a", "b")]
    assert source.position(("zzz",)) == source.marks[()]


def test_expr_position_exact_only_for_single_line_scalars():
    text = "p: steps.x.outputs.y\nq: 'a == 1'\nr: \"a\\tb\"\ns: first\n   second\nt: |\n  block\n"
    _, source = parse_yaml(text)
    assert source.expr_position(("p",), 1, 7) == (1, 10, True)
    assert source.expr_position(("q",), 1, 3) == (2, 7, True)  # +1 for the opening quote
    assert source.expr_position(("r",), 1, 2) == (3, 4, False)  # an escape changes the length: scalar start
    assert source.expr_position(("s",), 1, 2) == (4, 4, False)  # multi-line plain scalar
    assert source.expr_position(("t",), 1, 1) == (6, 4, False)  # block scalar
    assert source.expr_position(("p",), 2, 1) == (1, 4, False)  # expression line 2 of a one-line scalar


def test_anchors_and_merge_keys():
    data, _ = parse_yaml("a: &x {k: 1}\nb: *x\nc: &c\n  <<: *x\n  k: 2\nd: *c\n")
    assert data == {"a": {"k": 1}, "b": {"k": 1}, "c": {"k": 2}, "d": {"k": 2}}


def test_duplicate_keys_are_all_reported():
    with pytest.raises(SpecError) as err:
        parse_yaml("a: 1\nb: 2\na: 3\nm:\n  x: 1\n  x: 2\n", "d.yaml")
    assert [(d.code, d.line, d.column, d.loc) for d in err.value.diagnostics] == [
        ("E-YAML-DUP", 3, 1, ("a",)),
        ("E-YAML-DUP", 6, 3, ("m", "x")),
    ]


def test_syntax_error_and_flow_hint():
    with pytest.raises(SpecError) as err:
        parse_yaml("inputs: {items: list[string]}\n", "x.yaml")
    [diag] = err.value.diagnostics
    assert (diag.code, diag.file, diag.line, diag.column) == ("E-YAML", "x.yaml", 1, 21)
    assert diag.message.endswith('quote types such as "date?" or "list[string]"; block style needs no quotes')
    with pytest.raises(SpecError) as err:
        parse_yaml("a: [1, 2\n")
    assert "quote types" not in err.value.diagnostics[0].message


def test_impossible_date_is_a_yaml_error():
    with pytest.raises(SpecError) as err:
        parse_yaml("due: 2026-13-45\n")
    [diag] = err.value.diagnostics
    assert (diag.code, diag.line, diag.column) == ("E-YAML", 1, 6)


def test_root_must_be_a_mapping():
    for text, got in (("", "an empty document"), ("- a\n", "a list"), ("just text\n", "a str")):
        with pytest.raises(SpecError) as err:
            parse_model(text, StepLock, "r.yaml")
        [diag] = err.value.diagnostics
        assert (diag.code, diag.message) == ("E-YAML-ROOT", f"the document must be a mapping, got {got}")


def test_missing_field_is_located_at_its_parent():
    with pytest.raises(SpecError) as err:
        parse_model("wynd: 1\nname: x\nkind: shell\n", StepLock, "l.yaml")
    [diag] = err.value.diagnostics
    assert diag.format() == "l.yaml:1:1: error[E-SCHEMA] entrypoint: missing required field 'entrypoint'"


def test_pattern_mismatch_wording_names_the_identifier():
    text = "kind: process\nname: p\nentry: read\nsteps:\n  Read: { use: ./steps/read }\n"
    with pytest.raises(SpecError) as err:
        parse_model(text, ProcessDoc)
    messages = [(d.code, d.loc, d.message) for d in err.value.diagnostics]
    message = "'Read' is not a valid step name (lowercase letters, digits and _)"
    assert ("E-SCHEMA", ("steps", "Read"), message) in messages


def test_union_tags_are_not_part_of_the_location():
    text = (
        "kind: process\nname: p\nentry: a\nsteps: {a: {use: ./steps/a}}\n"
        "edges:\n  - from: a.done\n    to:\n      - step: a\n        limits: {max_traversals: [1]}\n"
    )
    with pytest.raises(SpecError) as err:
        parse_model(text, ProcessDoc)
    locs = {d.loc for d in err.value.diagnostics}
    assert locs == {("edges", 0, "to", 0, "limits", "max_traversals")}
    assert {d.line for d in err.value.diagnostics} == {9}


def test_flat_outputs_errors_point_at_the_written_field():
    text = "kind: proto_step\nname: p\ninstruction: x\noutputs:\n  ok: boolean\n  total: nmber\n"
    with pytest.raises(SpecError) as err:
        parse_model(text, ProtoStep)
    [diag] = err.value.diagnostics
    assert (diag.code, diag.loc, diag.line, diag.column) == ("E-TYPE", ("outputs", "total"), 6, 10)


def test_raw_loc_mapping():
    data = {
        "edges": [{"from": "a.done", "to": "b", "limits": {"timeout": -1}}],
        "outputs": {"x": "string"},
        "steps": {"Read": {"use": "./steps/read"}},
    }
    assert raw_loc(data, ("edges", 0, "to", 0, "limits", "timeout", "float")) == ("edges", 0, "limits", "timeout")
    assert raw_loc(data, ("outputs", "done", "x")) == ("outputs", "x")
    assert raw_loc(data, ("steps", "Read", "[key]")) == ("steps", "Read")
    assert raw_loc(data, ("edges", 0, "missing_field")) == ("edges", 0, "missing_field")


def test_mark_at_handles_shorthand_edges_and_flat_outputs():
    text = "outputs:\n  total: number\nedges:\n  - from: a.done\n    to: b\n    with: {x: steps.a.outputs.y}\n"
    _, source = parse_yaml(text)
    assert mark_at(source, ("outputs", "done", "total")) == source.marks[("outputs", "total")]
    assert mark_at(source, ("edges", 0, "to", 0, "with", "x")) == source.marks[("edges", 0, "with", "x")]
    assert mark_at(source, ("edges", 0, "to", 0, "step")) == source.marks[("edges", 0, "to")]
    assert mark_at(None, ("a",)) is None


def test_load_model_sets_source_and_reads_files(tmp_path):
    path = tmp_path / "step.lock.yaml"
    path.write_text("wynd: 1\nname: read_pdf\nkind: deterministic\nentrypoint: read_pdf:ReadPdf\n")
    lock = load_model(path, StepLock)
    assert lock._source.file == str(path)
    data, source = read_yaml(path)
    assert data["name"] == "read_pdf" and source.marks[("name",)] == Mark(2, 7, None)
    assert StepLock(name="x", kind="shell", entrypoint="x:X")._source is None


def test_dump_yaml_style():
    text = dump_yaml({"b": 1, "a": {"empty": {}, "list": [], "multi": "line one\nline two\n", "yes": "yes",
                                    "date": "2026-10-01", "expr": "if a then b else c"}})
    assert text == (
        "b: 1\na:\n  empty: {}\n  list: []\n  multi: |\n    line one\n    line two\n  'yes': 'yes'\n"
        "  date: '2026-10-01'\n  expr: if a then b else c\n"
    )
    assert parse_yaml(text)[0]["a"]["yes"] == "yes"


def test_dump_never_wraps_long_expressions():
    expr = "if " + " and ".join(f"steps.s{i}.outputs.ok" for i in range(40)) + " then 1 else 2"
    assert dump_yaml({"when": expr}) == f"when: {expr}\n"


def test_yaml_to_json_of_the_dogfood_documents():
    process, diag = yaml_to_json((FIXTURES / "process/dogfood.yaml").read_text(), "process.yaml")
    assert diag is None
    record = process["examples"][0]["outputs"]["record"]
    assert record["due_date"] == "2026-10-01"
    assert process["examples"][1]["env"]["RECORDS_DIR"] == "{tmp}/records"
    json.dumps(process)
    proto, _ = yaml_to_json((FIXTURES / "proto/exit_codes_unknown.yaml").read_text(), "p.yaml")
    assert proto["exit_codes"] == {0: "done", 1: "not_found", 2: "missing", "*": "error"}


def test_yaml_to_json_reports_the_first_problem():
    assert yaml_to_json("a: 1\na: 2\n", "x.yaml")[1].code == "E-YAML-DUP"
    assert yaml_to_json("- 1\n", "x.yaml")[1].code == "E-YAML-ROOT"
    data, diag = yaml_to_json("a: [1\n", "x.yaml")
    assert data is None and diag.code == "E-YAML"


@pytest.mark.parametrize(
    ("rel", "model"),
    [("process/dogfood.yaml", ProcessDoc), ("proto/exit_codes_unknown.yaml", ProtoStep),
     ("proto/extract_invoice_fields.yaml", ProtoStep), ("locks/extract.lock.yaml", StepLock)],
)
def test_yaml_to_json_round_trips_through_dump_yaml(rel, model, expr_double):
    text = (FIXTURES / rel).read_text()
    data, diag = yaml_to_json(text, rel)
    assert diag is None
    again = parse_model(dump_yaml(data), model)
    assert again.model_dump(mode="json") == parse_model(text, model).model_dump(mode="json")
