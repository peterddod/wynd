"""The type mini-language, generated models and outputs normalisation ($DRAFTS/01 §5, §12.6; PLAN §3.4)."""

from datetime import date, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from wynd.spec import (
    TList,
    TObject,
    TScalar,
    build_models,
    describe_fields,
    describe_type,
    fields_schema,
    infer_fields,
    normalise_outputs,
    parse_type,
    render_type,
    type_schema,
)
from wynd.spec.typelang import outputs_ambiguous

SCALARS = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "integer": {"type": "integer"},
    "boolean": {"type": "boolean"},
    "date": {"type": "string", "format": "date"},
    "datetime": {"type": "string", "format": "date-time"},
    "path": {"type": "string", "format": "path"},
    "object": {"type": "object"},
}


@pytest.mark.parametrize("name", sorted(SCALARS))
def test_scalars_parse_render_and_map_to_json_schema(name):
    assert parse_type(name) == TScalar(name)
    assert render_type(parse_type(name)) == name
    assert type_schema(parse_type(name)) == SCALARS[name]
    assert type_schema(parse_type(name + "?")) == {"anyOf": [SCALARS[name], {"type": "null"}]}


@pytest.mark.parametrize(
    ("spec", "node", "rendered"),
    [
        ("list[list[date?]]", TList(TList(TScalar("date", True))), "list[list[date?]]"),
        ("list[ integer ]?", TList(TScalar("integer"), True), "list[integer]?"),
        ("list [string]", TList(TScalar("string")), "list[string]"),
        ([{"sku": "string", "qty": "integer?"}],
         TList(TObject((("sku", TScalar("string")), ("qty", TScalar("integer", True))))),
         [{"sku": "string", "qty": "integer?"}]),
        ({"a": {"b": "date"}}, TObject((("a", TObject((("b", TScalar("date")),))),)), {"a": {"b": "date"}}),
        ({}, TObject(()), {}),
        (["string"], TList(TScalar("string")), "list[string]"),
    ],
)
def test_composite_types_round_trip(spec, node, rendered):
    assert parse_type(spec) == node
    assert render_type(node) == rendered
    assert parse_type(render_type(node)) == node


def test_composite_schemas():
    assert type_schema(parse_type([{"sku": "string", "qty": "integer?"}])) == {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {"sku": {"type": "string"}, "qty": {"anyOf": [{"type": "integer"}, {"type": "null"}]}},
            "required": ["sku"],
            "additionalProperties": False,
        },
    }
    assert fields_schema({"x": TScalar("string")}, exit="done") == {
        "type": "object",
        "properties": {"exit": {"const": "done"}, "x": {"type": "string"}},
        "required": ["exit", "x"],
        "additionalProperties": False,
    }


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ("strng", "unknown type 'strng' (expected one of boolean, date, datetime, integer, number, object, path, "
                  "string, list[...])"),
        ("list[string", "invalid type 'list[string': missing ']'"),
        ("list[string]]", "invalid type 'list[string]]': unexpected ']'"),
        ("string??", "invalid type 'string??': unexpected '?'"),
        (["a", "b"], "a list type is written as a one-item list: [ <type> ]"),
        (5, "expected a type name, got 5"),
        ({"a": "strng"}, "field 'a': unknown type 'strng' (expected one of boolean, date, datetime, integer, number, "
                         "object, path, string, list[...])"),
        ({"class": "string"}, "'class' cannot be a field name: it is a Python keyword"),
        ({"a": {"_x": "string"}}, "field 'a': '_x' cannot be a field name: a leading '_' or 'model_' is reserved by "
                                  "pydantic"),
        ("", "invalid type '': empty"),
    ],
)
def test_parse_errors(spec, message):
    with pytest.raises(ValueError) as err:
        parse_type(spec)
    assert str(err.value) == message


def test_generated_models_validate_lax_and_closed():
    models = build_models(
        "extract_invoice_fields",
        {"text": TScalar("string"), "pdf": TScalar("path"), "tags": TList(TScalar("string"), True)},
        {
            "done": {"total": TScalar("number"), "due": TScalar("date"),
                     "items": TList(TObject((("sku", TScalar("string")),)))},
            "not_an_invoice": {},
        },
    )
    assert models.input.__name__ == "ExtractInvoiceFieldsInput"
    assert {e: m.__name__ for e, m in models.outputs.items()} == {
        "done": "ExtractInvoiceFieldsDone", "not_an_invoice": "ExtractInvoiceFieldsNotAnInvoice"}
    parsed = models.input.model_validate({"text": "x", "pdf": "a/b.pdf"})
    assert parsed.pdf == Path("a/b.pdf") and parsed.tags is None
    with pytest.raises(ValidationError) as err:
        models.input.model_validate({"text": "x", "pdf": "a", "extra": 1})
    assert err.value.errors()[0]["type"] == "extra_forbidden"

    done = models.adapter.validate_python(
        {"exit": "done", "total": "12.5", "due": "2026-10-01", "items": [{"sku": "A"}]})
    assert (done.exit, done.total, done.due) == ("done", 12.5, date(2026, 10, 1))
    assert done.model_dump(mode="json") == {"exit": "done", "total": 12.5, "due": "2026-10-01",
                                            "items": [{"sku": "A"}]}
    assert type(done.items[0]).__name__ == "ExtractInvoiceFieldsDoneItemsItem"
    assert models.adapter.validate_python({"exit": "not_an_invoice"}).exit == "not_an_invoice"
    with pytest.raises(ValidationError) as err:
        models.adapter.validate_python({"exit": "nope"})
    assert err.value.errors()[0]["type"] == "union_tag_invalid"
    with pytest.raises(ValidationError):
        models.adapter.validate_python({"exit": "done", "total": 1, "due": "2026-10-01", "items": [], "more": 1})


def test_single_exit_output_is_the_plain_model():
    models = build_models("p", {}, {"done": {"x": TScalar("integer")}})
    assert models.output is models.outputs["done"]
    assert models.adapter.validate_python({"x": "3"}).x == 3


@pytest.mark.parametrize(
    ("outputs", "exits", "expected"),
    [
        (None, None, (["done"], {"done": {}})),
        ({}, None, (["done"], {"done": {}})),
        ({"done": {"a": "string"}, "skip": {}}, None, (["done", "skip"], {"done": {"a": "string"}, "skip": {}})),
        ({"a": "string", "b": {"c": "date"}}, None, (["done"], {"done": {"a": "string", "b": {"c": "date"}}})),
        ({"x": {"a": "string"}}, None, (["done"], {"done": {"x": {"a": "string"}}})),
        (None, ["done", "no"], (["done", "no"], {"done": {}, "no": {}})),
        ({"no": {"why": "string"}}, ["done", "no"], (["done", "no"], {"done": {}, "no": {"why": "string"}})),
        ({"a": "string"}, ["done"], (["done"], {"done": {"a": "string"}})),
    ],
)
def test_normalise_outputs_table(outputs, exits, expected):
    assert normalise_outputs(outputs, exits) == expected


@pytest.mark.parametrize(
    ("outputs", "exits", "message"),
    [
        ({"a": "string"}, ["done", "no"],
         "outputs keys a are not declared exits; nest outputs by exit (done: {...}) — a flat mapping means a "
         "done-only step"),
        ({"done": {"a": "string"}, "total": "number"}, ["done", "no"],
         "outputs mixes exit names (done) with field names (total)"),
        ({"done": {}}, ["done", "done"], "exits must be unique (duplicate done)"),
        ({}, [], "exits must not be empty"),
        ({}, ["done", "error"], "`error` is implicit on every step/process and cannot be declared"),
        ({"error": {}}, None, "`error` is implicit on every step/process and cannot be declared"),
        ({"done": "string"}, ["done"], "outputs.done must be a mapping of fields (outputs are nested by exit)"),
        ("text", None, "outputs must be a mapping, got str"),
    ],
)
def test_normalise_outputs_errors(outputs, exits, message):
    with pytest.raises(ValueError) as err:
        normalise_outputs(outputs, exits)
    assert str(err.value) == message


def test_outputs_ambiguous():
    assert outputs_ambiguous({"x": {"a": "string"}}, None)
    assert not outputs_ambiguous({"x": {"a": "string"}}, ["done"])
    assert not outputs_ambiguous({"done": {"a": "string"}}, None)
    assert not outputs_ambiguous({"x": "string"}, None)
    assert not outputs_ambiguous({}, None)


def test_describe():
    assert describe_type(parse_type("date")) == "a date (YYYY-MM-DD)"
    assert describe_type(parse_type("list[string]?")) == "a list of text (optional)"
    assert describe_type(parse_type("list[list[integer]]")) == "a list of lists of whole numbers"
    assert describe_type(parse_type({"a": "string", "b": "number"})) == "a group with fields a, b"
    assert describe_fields({"due_date": parse_type("date"), "tags": parse_type("list[string]?")}) == [
        "due_date — a date (YYYY-MM-DD)",
        "tags — a list of text (optional)",
    ]


def test_infer_fields():
    samples = [
        {"n": 1, "total": 1, "due": "2026-10-01", "at": "2026-10-01T10:00:00Z", "ok": True, "code": "A",
         "items": [{"sku": "A", "qty": 1}], "tags": [], "mixed": 1, "blob": 1, "note": "x"},
        {"n": 2, "total": 2.5, "due": date(2026, 11, 1), "at": datetime(2026, 1, 1), "ok": False, "code": 7,
         "items": [{"sku": "B"}], "tags": [], "mixed": [1], "blob": {"a": 1}},
    ]
    assert infer_fields(samples) == {
        "n": TScalar("integer"),
        "total": TScalar("number"),
        "due": TScalar("date"),
        "at": TScalar("datetime"),
        "ok": TScalar("boolean"),
        "code": TScalar("string"),
        "items": TList(TObject((("sku", TScalar("string")), ("qty", TScalar("integer", True))))),
        "tags": TList(TScalar("string")),
        "mixed": TScalar("object"),
        "blob": TScalar("object"),
        "note": TScalar("string", True),
    }


def test_inferred_optional_containers_stay_renderable():
    inferred = infer_fields([{"a": {"x": 1}, "b": [{"y": 1}]}, {}])
    assert inferred == {"a": TScalar("object", True), "b": TList(TScalar("object"), True)}
    assert {k: render_type(v) for k, v in inferred.items()} == {"a": "object?", "b": "list[object]?"}
