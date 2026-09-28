"""Proto-step documents ($DRAFTS/01 §6.3, §12.6–§12.7; PLAN §3.3)."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from wynd.spec import (
    Example,
    ProtoStep,
    SpecError,
    TScalar,
    check_proto_step,
    dump_yaml,
    load_proto_step,
    parse_model,
    proto_hash,
)

FIXTURES = Path(__file__).parent / "fixtures" / "proto"


def codes(proto: ProtoStep) -> list[tuple[str, tuple]]:
    return [(d.code, d.loc) for d in check_proto_step(proto)]


def test_load_nested_proto():
    proto = load_proto_step(FIXTURES / "extract_invoice_fields.yaml")
    assert proto.exits == ["done", "not_an_invoice"]
    assert proto.outputs["done"]["due_date"] == TScalar("date")
    assert proto.outputs["not_an_invoice"] == {}
    assert proto.examples[0].outputs["due_date"] == date(2026, 10, 1)
    assert proto._source.file == str(FIXTURES / "extract_invoice_fields.yaml")
    assert check_proto_step(proto) == []


def test_flat_outputs_mean_done_only_and_error_examples_are_allowed():
    proto = load_proto_step(FIXTURES / "read_pdf.yaml")
    assert proto.exits == ["done"]
    assert set(proto.outputs["done"]) == {"text", "pages"}
    assert proto.examples[1].exit == "error"
    assert proto.env.deps == ["pypdf>=6,<7"]
    assert check_proto_step(proto) == []  # a subset of outputs is enough


def test_flat_and_declared_done_forms_are_the_same_model():
    flat = parse_model("kind: proto_step\nname: p\ninstruction: x\noutputs: {a: string}\n", ProtoStep)
    nested = parse_model(
        "kind: proto_step\nname: p\ninstruction: x\nexits: [done]\noutputs:\n  done:\n    a: string\n", ProtoStep)
    assert flat == nested
    assert proto_hash(flat) == proto_hash(nested)


def test_models_and_interface():
    proto = load_proto_step(FIXTURES / "extract_invoice_fields.yaml")
    models = proto.models()
    assert models.input.__name__ == "ExtractInvoiceFieldsInput"
    assert models.adapter.validate_python({"exit": "not_an_invoice"}).exit == "not_an_invoice"
    assert proto.interface().exits == ["done", "not_an_invoice"]
    assert proto.interface().input["required"] == ["invoice_text"]


@pytest.mark.parametrize("name", ["extract_invoice_fields.yaml", "read_pdf.yaml", "exit_codes_unknown.yaml"])
def test_to_authoring_round_trip(name):
    proto = load_proto_step(FIXTURES / name)
    again = parse_model(dump_yaml(proto.to_authoring()), ProtoStep)
    assert again == proto
    assert proto_hash(again) == proto_hash(proto)


def test_to_authoring_shapes():
    read = load_proto_step(FIXTURES / "read_pdf.yaml").to_authoring()
    assert read["outputs"] == {"text": "string", "pages": "integer"} and "exits" not in read
    extract = load_proto_step(FIXTURES / "extract_invoice_fields.yaml").to_authoring()
    assert extract["exits"] == ["done", "not_an_invoice"]
    assert extract["outputs"]["not_an_invoice"] == {}
    assert list(extract) == ["kind", "name", "instruction", "inputs", "outputs", "exits", "examples"]


def test_done_only_proto_whose_fields_are_all_mappings_keeps_its_exits():
    proto = ProtoStep(kind="proto_step", name="p", instruction="x", exits=["done"],
                      outputs={"done": {"done": {"a": "string"}}})
    authoring = proto.to_authoring()
    assert authoring["exits"] == ["done"]
    again = parse_model(dump_yaml(authoring), ProtoStep)
    assert again == proto
    assert codes(again) == [("W-PROTO-NO-EXAMPLES", ("examples",))]


def test_check_warnings():
    proto = parse_model(
        "kind: proto_step\nname: p\ninstruction: x\nexits: [done, skipped]\n"
        "outputs:\n  done: {a: string}\nexamples:\n  - inputs: {}\n    outputs: {a: x}\n",
        ProtoStep,
    )
    assert codes(proto) == [("W-PROTO-EXIT-UNTESTED", ("outputs", "skipped"))]
    bare = parse_model("kind: proto_step\nname: p\ninstruction: x\n", ProtoStep)
    assert codes(bare) == [("W-PROTO-NO-EXAMPLES", ("examples",))]


def test_outputs_ambiguous_warning():
    proto = parse_model(
        "kind: proto_step\nname: p\ninstruction: x\noutputs:\n  ok: {a: string}\n  no: {}\n"
        "examples:\n  - outputs: {ok: {a: y}, no: {}}\n",
        ProtoStep,
        "p.yaml",
    )
    assert proto.exits == ["done"] and set(proto.outputs["done"]) == {"ok", "no"}
    [diag] = check_proto_step(proto)
    assert diag.format() == (
        "p.yaml:5:3: warning[W-OUTPUTS-AMBIGUOUS] outputs: outputs looks nested by exit but has no `done`; declare "
        "`exits:` to nest"
    )


def test_example_errors():
    proto = parse_model(
        "kind: proto_step\nname: p\ninstruction: x\ninputs: {a: string, n: 'integer?'}\noutputs: {b: date}\n"
        "examples:\n"
        "  - inputs: {n: 1}\n"
        "    outputs: {b: 2026-10-01}\n"
        "  - inputs: {a: x, z: 1}\n"
        "    outputs: {b: soon, c: 1}\n"
        "    exit: weird\n"
        "  - inputs: {a: x}\n"
        "    env: {X: '1'}\n"
        "    exit: error\n",
        ProtoStep,
    )
    assert [(d.code, d.loc, d.message) for d in check_proto_step(proto)] == [
        ("E-EXAMPLE", ("examples", 0, "inputs", "a"), "missing required input 'a'"),
        ("E-EXAMPLE", ("examples", 1, "exit"), "example exit 'weird' is not a declared exit (done) or 'error'"),
        ("E-EXAMPLE", ("examples", 1, "inputs", "z"), "'z' is not an input"),
        ("E-EXAMPLE", ("examples", 2, "env"), "env: is only allowed on process examples"),
    ]


def test_example_output_type_and_unknown_fields():
    proto = parse_model(
        "kind: proto_step\nname: p\ninstruction: x\noutputs: {b: date}\n"
        "examples:\n  - outputs: {b: soon, c: 1}\n",
        ProtoStep,
    )
    assert [(d.loc, d.message) for d in check_proto_step(proto)] == [
        (("examples", 0, "outputs", "b"), "Input should be a valid date or datetime, input is too short"),
        (("examples", 0, "outputs", "c"), "'c' is not a field of the output of exit 'done'"),
    ]


def test_schema_rules():
    base = {"kind": "proto_step", "name": "p", "outputs": {"a": "string"}}
    with pytest.raises(ValidationError) as err:
        ProtoStep.model_validate({**base, "instruction": "   "})
    assert err.value.errors()[0]["type"] == "E-SCHEMA"
    with pytest.raises(ValidationError) as err:
        ProtoStep.model_validate({**base, "instruction": "x", "exit_codes": {256: "done"}})
    assert err.value.errors()[0]["msg"] == "exit code keys are 0..255 or '*' (got 256)"
    ok = ProtoStep.model_validate({**base, "instruction": "x", "exit_codes": {"0": "done", "*": "error"}})
    assert ok.exit_codes == {0: "done", "*": "error"}
    with pytest.raises(ValidationError) as err:
        ProtoStep.model_validate({**base, "instruction": "x", "hints": "no"})
    assert err.value.errors()[0]["type"] == "extra_forbidden"


@pytest.mark.parametrize(
    ("outputs", "code", "loc"),
    [
        ({"done": {"exit": "string"}}, "E-OUTPUTS", ("outputs", "done", "exit")),
        ({"class": "string"}, "E-SCHEMA", ("outputs", "class")),
        ({"model_x": "string"}, "E-SCHEMA", ("outputs", "model_x")),
    ],
)
def test_reserved_field_names(outputs, code, loc):
    with pytest.raises(SpecError) as err:
        parse_model(dump_yaml({"kind": "proto_step", "name": "p", "instruction": "x", "outputs": outputs}), ProtoStep)
    [diag] = err.value.diagnostics
    assert (diag.code, diag.loc) == (code, loc)


def test_exit_is_not_an_input_field():
    with pytest.raises(SpecError) as err:
        parse_model(dump_yaml({"kind": "proto_step", "name": "p", "instruction": "x", "inputs": {"exit": "string"}}),
                    ProtoStep)
    [diag] = err.value.diagnostics
    assert (diag.code, diag.loc) == ("E-SCHEMA", ("inputs", "exit"))


def test_error_exit_cannot_be_declared():
    with pytest.raises(SpecError) as err:
        parse_model("kind: proto_step\nname: p\ninstruction: x\nexits: [done, error]\n", ProtoStep, "p.yaml")
    [diag] = err.value.diagnostics
    assert diag.format() == (
        "p.yaml:4:8: error[E-OUTPUTS] exits: `error` is implicit on every step/process and cannot be declared"
    )


def test_example_defaults():
    assert Example().model_dump() == {"inputs": {}, "outputs": {}, "exit": "done", "description": None, "env": {}}
