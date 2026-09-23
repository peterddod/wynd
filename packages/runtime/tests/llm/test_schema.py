"""The provider-facing output-schema envelope (PLAN §1.4, §3.15; `$DRAFTS/03 §6.4`), on real pydantic Output
schemas as `StepInterface.output_json_schema()` produces them."""

from __future__ import annotations

import copy
from datetime import date
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from wynd.runtime.agentic.schema import strict_compatible, unwrap_output, wrap_output_schema
from wynd.spec.interface import output_adapter


class Line(BaseModel):
    title: str                                  # a property named like a dropped keyword
    amount: float = Field(ge=0)


class Done(BaseModel):
    """The text is an invoice; fields extracted."""

    exit: Literal["done"] = "done"
    invoice_number: str = Field(pattern=r"^INV-\d+$", min_length=5)
    due_date: date
    source: Path
    lines: list[Line] = Field(max_length=50)
    note: str | None = None


class NotAnInvoice(BaseModel):
    exit: Literal["not_an_invoice"] = "not_an_invoice"


UNION = output_adapter(Done | NotAnInvoice).json_schema()


def test_union_is_wrapped_under_output_with_defs_hoisted():
    wrapped = wrap_output_schema(UNION)
    assert wrapped == {
        "type": "object",
        "properties": {"output": {"anyOf": [{"$ref": "#/$defs/Done"}, {"$ref": "#/$defs/NotAnInvoice"}]}},
        "required": ["output"],
        "additionalProperties": False,
        "$defs": {
            "Done": {
                "description": "The text is an invoice; fields extracted.",
                "type": "object",
                "properties": {
                    "exit": {"const": "done", "type": "string"},
                    "invoice_number": {"type": "string"},
                    "due_date": {"format": "date", "type": "string"},
                    "source": {"type": "string"},
                    "lines": {"items": {"$ref": "#/$defs/Line"}, "type": "array"},
                    "note": {"anyOf": [{"type": "string"}, {"type": "null"}]},
                },
                "required": ["exit", "invoice_number", "due_date", "source", "lines", "note"],
                "additionalProperties": False,
            },
            "Line": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "amount": {"type": "number"}},
                "required": ["title", "amount"],
                "additionalProperties": False,
            },
            "NotAnInvoice": {
                "type": "object",
                "properties": {"exit": {"const": "not_an_invoice", "type": "string"}},
                "required": ["exit"],
                "additionalProperties": False,
            },
        },
    }


def test_the_input_schema_is_not_modified():
    before = copy.deepcopy(UNION)
    wrap_output_schema(UNION)
    assert UNION == before
    assert "discriminator" in UNION and "oneOf" in UNION


def test_a_single_model_output_has_no_defs_key_when_it_needs_none():
    class Only(BaseModel):
        exit: Literal["done"] = "done"
        total: float
        tags: list[str] = []

    wrapped = wrap_output_schema(output_adapter(Only).json_schema())
    assert "$defs" not in wrapped
    assert wrapped["properties"]["output"] == {
        "type": "object",
        "properties": {"exit": {"const": "done", "type": "string"}, "total": {"type": "number"},
                       "tags": {"items": {"type": "string"}, "type": "array"}},
        "required": ["exit", "total", "tags"],
        "additionalProperties": False,
    }


def test_standard_formats_are_kept():
    schema = {"type": "object", "properties": {f: {"type": "string", "format": f} for f in
                                               ("date-time", "uuid", "email", "uri", "binary")}}
    kept = wrap_output_schema(schema)["properties"]["output"]["properties"]
    assert {name: prop.get("format") for name, prop in kept.items()} == {
        "date-time": "date-time", "uuid": "uuid", "email": "email", "uri": "uri", "binary": None}


def test_unwrap_output():
    assert unwrap_output({"output": {"exit": "done", "total": 1}}) == {"exit": "done", "total": 1}
    assert unwrap_output({"output": 1, "exit": "done"}) == {"output": 1, "exit": "done"}
    assert unwrap_output({"exit": "done"}) == {"exit": "done"}
    assert unwrap_output([1]) == [1]
    assert unwrap_output(None) is None


def test_strict_compatible():
    assert strict_compatible(wrap_output_schema(UNION))

    class Loose(BaseModel):
        exit: Literal["done"] = "done"
        meta: dict[str, Any]

    class Counts(BaseModel):
        exit: Literal["counted"] = "counted"
        by_word: dict[str, int]

    assert not strict_compatible(wrap_output_schema(output_adapter(Loose).json_schema()))
    assert not strict_compatible(wrap_output_schema(output_adapter(Done | Counts).json_schema()))


def test_strict_compatible_looks_inside_defs():
    class Nested(BaseModel):
        blob: dict[str, Any]

    class Holder(BaseModel):
        exit: Literal["done"] = "done"
        nested: Nested

    wrapped = wrap_output_schema(output_adapter(Holder).json_schema())
    assert wrapped["$defs"]["Nested"]["properties"]["blob"] == {"additionalProperties": True, "type": "object"}
    assert not strict_compatible(wrapped)
