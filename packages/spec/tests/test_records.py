"""Runtime records and context entries (PLAN §3.8, §4.1; $DRAFTS/01 §12.9)."""

import pytest
from pydantic import ValidationError

from wynd.spec import (
    ANY,
    MISSING,
    STEP_ERROR_SCHEMA,
    ContextRef,
    ProcessError,
    StepError,
    Summary,
    TracePointer,
    normalize_schema,
    parse_context_entry,
    schema_at,
)


def child_error() -> ProcessError:
    return ProcessError(run_id="run_1", process="finance/child", step="extract", cause="step_error", message="boom",
                        inputs={"text": "x"}, step_error=StepError(cause="exception", message="boom", type="KeyError"),
                        trace=TracePointer(run_id="run_1", uri="file:///t/run_1.jsonl", seq=9))


def test_step_error_nests_a_process_error_and_round_trips():
    error = StepError(cause="child_process", message="child failed", inputs={"pdf_path": "a.pdf"}, attempts=1,
                      child=child_error())
    data = error.model_dump(mode="json")
    assert data["exit"] == "error"
    assert data["child"]["step_error"]["cause"] == "exception"
    assert StepError.model_validate(data) == error
    assert StepError.model_validate_json(error.model_dump_json()) == error


def test_process_error_carries_handler_errors_and_detail():
    error = ProcessError(run_id="r", process="p", step=None, cause="edge_check", message="checker failed",
                         edge="validate.done[save]", detail={"branch_key": "validate.done[save]",
                                                             "error_cause": "timeout"},
                         handler_error=StepError(cause="hook", message="x"), workspace="file:///w")
    assert ProcessError.model_validate(error.model_dump()) == error
    with pytest.raises(ValidationError):
        ProcessError(run_id="r", process="p", step=None, cause="nope", message="m")
    with pytest.raises(ValidationError):
        ProcessError(run_id="r", process="p", cause="internal", message="m")  # step is required (may be None)


def test_step_error_schema():
    # StepError and ProcessError refer to each other, so the schema is a root $ref into $defs
    assert {"StepError", "ProcessError"} <= set(STEP_ERROR_SCHEMA["$defs"])
    assert schema_at(STEP_ERROR_SCHEMA, ["exit"])["const"] == "error"
    assert schema_at(STEP_ERROR_SCHEMA, ["cause"])["enum"][0] == "input_validation"
    assert schema_at(STEP_ERROR_SCHEMA, ["inputs", "anything"]) is ANY
    assert schema_at(STEP_ERROR_SCHEMA, ["child", "step_error", "message"]) is ANY  # recursive
    assert schema_at(STEP_ERROR_SCHEMA, ["child", "cause"])["enum"][0] == "step_error"
    assert schema_at(STEP_ERROR_SCHEMA, ["nope"]) is MISSING
    assert set(normalize_schema(STEP_ERROR_SCHEMA)["required"]) == {"cause", "message"}


def test_summary():
    assert Summary(step="validate", exit="done").model_dump() == {"step": "validate", "exit": "done",
                                                                   "key_outputs": {}, "note": ""}


@pytest.mark.parametrize(
    ("text", "ref"),
    [
        ("full_trace", ContextRef("full_trace")),
        ("process.goal", ContextRef("process.goal")),
        ("process.inputs", ContextRef("process.inputs")),
        ("previous.summary", ContextRef("previous.summary")),
        (" previous.outputs ", ContextRef("previous.outputs")),
        ("steps.classify.outputs", ContextRef("step.outputs", "classify")),
        ("steps.read.outputs.text", ContextRef("step.outputs", "read", ("text",))),
        ("steps.read.outputs.meta.pages", ContextRef("step.outputs", "read", ("meta", "pages"))),
        ("steps.validate.summary", ContextRef("step.summary", "validate")),
    ],
)
def test_parse_context_entry(text, ref):
    assert parse_context_entry(text) == ref


@pytest.mark.parametrize(
    "text",
    ["steps.read.text", "steps.Read.outputs", "steps.read.summary.note", "previous.outputs.x", "process", "trace",
     "steps.read.outputs[0]", "", "process.inputs.x"],
)
def test_parse_context_entry_rejects(text):
    with pytest.raises(ValueError) as err:
        parse_context_entry(text)
    assert str(err.value).startswith(f"invalid context entry {text!r}: expected full_trace, process.goal")
