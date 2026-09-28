"""Fixtures for the expression tests: the evaluation scope of $DRAFTS/01 §12.1 and the hand-built TypeEnvs of
§12.4 (the dogfood process as the validator would see it at the site `validate.done[0].with.dest`)."""

from datetime import datetime, timezone

import pytest

from wynd.spec.expr.analysis import StepView, TypeEnv
from wynd.spec.expr.scope import EdgeState, Scope, StepState
from wynd.spec.records import STEP_ERROR_SCHEMA

FROZEN = datetime(2026, 9, 22, 21, 50, 3, 123456, tzinfo=timezone.utc)


@pytest.fixture
def scope() -> Scope:
    return Scope(
        steps={
            "read": StepState(1, "done", {"text": "INVOICE 42"}),
            "extract": StepState(
                1,
                "done",
                {
                    "invoice_number": "INV-1042",
                    "total": 1200.5,
                    "currency": "GBP",
                    "label": None,
                    "items": [{"sku": "A", "qty": 2}],
                },
            ),
            "validate": StepState(
                2,
                "done",
                {"valid": False, "fixable": True, "fields": {"total": 12000}, "errors": ["bad currency"]},
                {"step": "validate", "exit": "done", "key_outputs": {"valid": False}, "note": ""},
            ),
            "fix": StepState(1, "done", {"total": 1}),
            "save": StepState(),
        },
        edges={"validate.done": EdgeState(taken=[0, 1, 0], names={"retry": 1})},
        process_inputs={"pdf_path": "/w/in.pdf"},
        env={"REVIEW_DIR": "/review", "RECORDS_DIR": "/records"},
        run_id="run-123",
        previous="validate",
        clock=lambda: FROZEN,
    )


def exit_schema(exit: str, required: dict, optional: dict | None = None) -> dict:
    """The JSON Schema interface_from_fields produces for one exit: a closed object with the exit const."""
    return {
        "type": "object",
        "properties": {"exit": {"const": exit, "type": "string"}, **required, **(optional or {})},
        "required": ["exit", *required],
        "additionalProperties": False,
    }


STRING = {"type": "string"}
NUMBER = {"type": "number"}
BOOLEAN = {"type": "boolean"}
OBJECT = {"type": "object"}
INVOICE_FIELDS = {
    "invoice_number": STRING,
    "total": NUMBER,
    "currency": STRING,
    "due_date": {"type": "string", "format": "date"},
}
VALIDATE_DONE = exit_schema(
    "done",
    {"valid": BOOLEAN, "fixable": BOOLEAN, "fields": OBJECT, "errors": {"type": "array", "items": STRING}},
    {"record": {"anyOf": [OBJECT, {"type": "null"}]}},
)
VALIDATE_INVALID = exit_schema("invalid", {"valid": BOOLEAN, "errors": {"type": "array", "items": STRING}})
PROCESS_INPUTS = {
    "type": "object",
    "properties": {"pdf_path": {"type": "string", "format": "path"}},
    "required": ["pdf_path"],
    "additionalProperties": False,
}
EDGES = {
    "read.done": [None],
    "extract.done": [None],
    "extract.not_an_invoice": [None],
    "validate.done": [None, "retry", None],
    "fix.done": [None],
    "save.done": [None],
    "escalate.done": [None],
}


def dogfood_env(validate: StepView) -> TypeEnv:
    return TypeEnv(
        steps={
            "read": StepView({"done": exit_schema("done", {"text": STRING, "pages": {"type": "integer"}})}),
            "extract": StepView({"done": exit_schema("done", INVOICE_FIELDS)}),
            "validate": validate,
            "fix": StepView({"done": exit_schema("done", INVOICE_FIELDS)}, may_be_unrun=True),
            "save": StepView({}, may_be_unrun=True),
            "escalate": StepView({}, may_be_unrun=True),
        },
        edges=EDGES,
        process_inputs=PROCESS_INPUTS,
        previous=validate,
        env_declared=frozenset({"RECORDS_DIR", "REVIEW_DIR"}),
    )


@pytest.fixture
def site_env() -> TypeEnv:
    """validate: {done}; fix: {done} or unrun; extract: {done}; save: unrun only."""
    return dogfood_env(StepView({"done": VALIDATE_DONE}))


@pytest.fixture
def exit_aware_env() -> TypeEnv:
    """validate may arrive via done or invalid; invalid declares no `record`."""
    return dogfood_env(StepView({"done": VALIDATE_DONE, "invalid": VALIDATE_INVALID}))


@pytest.fixture
def error_edge_env() -> TypeEnv:
    """At an edge `from: read.error`: read's latest exit is error, whose outputs are the StepError fields."""
    read = StepView({"error": STEP_ERROR_SCHEMA})
    return TypeEnv(steps={"read": read}, edges={"read.error": [None]}, process_inputs=PROCESS_INPUTS, previous=read)
