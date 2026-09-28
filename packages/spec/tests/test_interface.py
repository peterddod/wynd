"""Interface snapshots and the Output rules ($DRAFTS/01 §6.10, §12.9; PLAN §3.4)."""

from datetime import date
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

import pytest
from pydantic import BaseModel, Field

from wynd.spec import (
    STEP_ERROR_SCHEMA,
    interface_from_fields,
    interface_from_models,
    interface_hash,
    interfaces_equivalent,
    output_adapter,
    parse_type,
    split_output,
)


class Input(BaseModel):
    """Hand-written step input."""

    pdf_path: Path
    tags: Optional[list[str]] = None


class Item(BaseModel):
    sku: str


class Done(BaseModel):
    exit: Literal["done"] = "done"
    total: float
    due_date: date
    items: list[Item]
    meta: dict[str, Any]


class NotAnInvoice(BaseModel):
    exit: Literal["not_an_invoice"] = "not_an_invoice"


PROTO_INPUTS = {"pdf_path": parse_type("path"), "tags": parse_type("list[string]?")}
PROTO_OUTPUTS = {
    "done": {"total": parse_type("number"), "due_date": parse_type("date"), "items": parse_type([{"sku": "string"}]),
             "meta": parse_type("object")},
    "not_an_invoice": {},
}


@pytest.mark.parametrize(
    "output",
    [Done | NotAnInvoice, Union[Done, NotAnInvoice], Annotated[Union[Done, NotAnInvoice], Field(discriminator="exit")]],
)
def test_split_output_accepts_unions(output):
    assert split_output(output) == {"done": Done, "not_an_invoice": NotAnInvoice}


def test_split_output_accepts_a_plain_done_model():
    assert split_output(Done) == {"done": Done}


class Spam(BaseModel):
    exit: Literal["spam"] = "spam"


class NoExit(BaseModel):
    x: int


class StrExit(BaseModel):
    exit: str = "done"


class TwoValues(BaseModel):
    exit: Literal["a", "b"] = "a"


class WrongDefault(BaseModel):
    exit: Literal["b"] = "c"


class Error(BaseModel):
    exit: Literal["error"] = "error"


class OtherDone(BaseModel):
    exit: Literal["done"] = "done"


class BadName(BaseModel):
    exit: Literal["Bad"] = "Bad"


@pytest.mark.parametrize(
    ("output", "fragment"),
    [
        (Spam, "Spam: a plain Output model is the 'done' exit"),
        (NoExit, "NoExit: Output models need an `exit` field"),
        (StrExit | Spam, "StrExit: `exit` must be annotated Literal"),
        (TwoValues | Spam, "TwoValues: `exit` must be annotated Literal"),
        (WrongDefault | Spam, "WrongDefault: `exit` must default to its Literal value 'b'"),
        (Done | OtherDone, "OtherDone: exit 'done' is declared twice"),
        (Done | Error, "Error: the 'error' exit is implicit"),
        (Done | BadName, "BadName: exit 'Bad' is not a valid exit name"),
        (Union[Done, int], "Output member <class 'int'> is not a pydantic model"),
    ],
)
def test_split_output_rejects(output, fragment):
    with pytest.raises(TypeError) as err:
        split_output(output)
    assert fragment in str(err.value)


def test_output_adapter_round_trips_each_exit():
    adapter = output_adapter(Done | NotAnInvoice)
    done = adapter.validate_python({"exit": "done", "total": "1.5", "due_date": "2026-10-01", "items": [], "meta": {}})
    assert isinstance(done, Done) and done.total == 1.5
    assert adapter.dump_python(done, mode="json")["due_date"] == "2026-10-01"
    assert isinstance(adapter.validate_python({"exit": "not_an_invoice"}), NotAnInvoice)
    assert output_adapter(Done | NotAnInvoice) is adapter  # cached per union
    single = output_adapter(Done).validate_python({"total": 1, "due_date": "2026-10-01", "items": [], "meta": {}})
    assert single.exit == "done"


def test_code_and_yaml_interfaces_are_equivalent():
    code = interface_from_models(Input, Done | NotAnInvoice)
    yaml = interface_from_fields(PROTO_INPUTS, PROTO_OUTPUTS)
    assert code.exits == yaml.exits == ["done", "not_an_invoice"]
    assert code.outputs["done"]["properties"]["exit"]["const"] == "done"
    assert interfaces_equivalent(code, yaml) == []


def test_interfaces_equivalent_reports_differences():
    code = interface_from_models(Input, Done | NotAnInvoice)
    changed = interface_from_fields(
        {"pdf_path": parse_type("string")},
        {"done": {**PROTO_OUTPUTS["done"], "total": parse_type("integer"), "extra": parse_type("string")},
         "skipped": {}},
    )
    assert interfaces_equivalent(code, changed) == [
        'input: field \'pdf_path\': {"format": "path", "type": "string"} != {"type": "string"}',
        "input: field 'tags' is only in the first interface",
        "exit 'not_an_invoice' is only in the first interface",
        "exit 'skipped' is only in the second interface",
        'exit \'done\': field \'total\': {"type": "number"} != {"type": "integer"}',
        "exit 'done': field 'extra' is only in the second interface",
    ]


def test_interface_hash_is_stable_and_changes_with_fields():
    first = interface_hash(interface_from_models(Input, Done | NotAnInvoice))
    assert first == interface_hash(interface_from_models(Input, Done | NotAnInvoice))
    assert first.startswith("sha256:") and len(first) == 71
    wider = interface_from_fields({**PROTO_INPUTS, "more": parse_type("string")}, PROTO_OUTPUTS)
    assert interface_hash(wider) != interface_hash(interface_from_fields(PROTO_INPUTS, PROTO_OUTPUTS))


def test_interface_hash_ignores_titles_and_the_exit_default():
    base = interface_from_fields(PROTO_INPUTS, PROTO_OUTPUTS)
    titled = base.model_copy(deep=True)
    titled.input["title"] = "Input"
    titled.outputs["done"]["properties"]["exit"].update({"default": "done", "type": "string", "title": "Exit"})
    assert interface_hash(titled) == interface_hash(base)


def test_with_error_adds_the_step_error_schema():
    iface = interface_from_fields(PROTO_INPUTS, PROTO_OUTPUTS)
    assert list(iface.with_error()) == ["done", "not_an_invoice", "error"]
    assert iface.with_error()["error"] is STEP_ERROR_SCHEMA
