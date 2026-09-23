"""The evaluation scope: new_scope, complete, take and counter timing (PLAN §3.5; $DRAFTS/01 §7.5)."""

from datetime import datetime, timezone

from wynd.spec.expr.evaluator import evaluate, evaluate_condition, evaluate_with
from wynd.spec.expr.scope import EdgeState, StepState, new_scope, utc_now
from wynd.spec.process_doc import ProcessDoc

SUMMARY = {"step": "validate", "exit": "done", "key_outputs": {"valid": False}, "note": ""}


def dogfood_doc() -> ProcessDoc:
    """The dogfood graph shape (M5 branch names), normalised: every `to:` is a list of branches."""
    return ProcessDoc.model_validate({
        "kind": "process",
        "name": "process_supplier_invoice",
        "entry": "read",
        "exits": ["done"],
        "outputs": {"done": {}},
        "steps": {key: {"use": f"./steps/{key}"} for key in ("read", "extract", "validate", "fix", "save", "escalate")},
        "edges": [
            {"from": "read.done", "to": [{"step": "extract", "with": {"invoice_text": "steps.read.outputs.text"}}]},
            {
                "from": "validate.done",
                "to": [
                    {"step": "save", "name": "save", "when": "steps.validate.outputs.valid"},
                    {
                        "step": "fix",
                        "name": "fix",
                        "when": "steps.validate.outputs.fixable and steps.fix.runs < 3",
                        "with": {"fields": "steps.validate.outputs.fields"},
                    },
                    {"step": "escalate"},
                ],
            },
            {"from": "fix.done", "to": [{"step": "validate", "with": {"fields": "steps.fix.outputs"}}]},
        ],
    })


def test_new_scope():
    inputs = {"pdf_path": "/w/in.pdf"}
    env = {"RECORDS_DIR": "/records"}
    scope = new_scope(dogfood_doc(), inputs=inputs, env=env, run_id="run-1")
    assert scope.steps == {key: StepState() for key in ("read", "extract", "validate", "fix", "save", "escalate")}
    assert scope.edges == {
        "read.done": EdgeState(taken=[0], names={}),
        "validate.done": EdgeState(taken=[0, 0, 0], names={"save": 0, "fix": 1}),
        "fix.done": EdgeState(taken=[0], names={}),
    }
    assert scope.process_inputs == inputs and scope.process_inputs is not inputs
    assert scope.env is env
    assert (scope.run_id, scope.previous) == ("run-1", None)
    assert scope.clock is utc_now

    def clock() -> datetime:
        return datetime(2026, 1, 1, tzinfo=timezone.utc)

    assert new_scope(dogfood_doc(), inputs={}, env={}, run_id="r", clock=clock).clock is clock


def test_complete_and_take_drive_the_references():
    scope = new_scope(dogfood_doc(), inputs={"pdf_path": "/w/in.pdf"}, env={}, run_id="run-1")
    assert evaluate("previous.outputs", scope) is None
    assert evaluate("steps.validate.runs", scope) == 0

    outputs = {"valid": False, "fixable": True, "fields": {"total": 10}}
    scope.complete("validate", "done", outputs, SUMMARY)
    assert scope.previous == "validate"
    assert scope.steps["validate"] == StepState(1, "done", dict(outputs), SUMMARY)
    outputs["valid"] = True  # the scope keeps its own copy
    assert scope.steps["validate"].outputs["valid"] is False
    assert evaluate("previous.outputs.valid", scope) is False
    assert evaluate("previous.summary.exit", scope) == "done"

    # when: conditions see the counts from before the current resolution; take() returns the new count.
    fix = dogfood_doc().edges[1].to[1]
    assert evaluate_condition(fix.when, scope) is True
    assert evaluate_with(fix.with_, scope) == {"fields": {"total": 10}}
    assert scope.take("validate.done", 1) == 1
    assert scope.take("validate.done", 1) == 2
    assert evaluate('edges["validate.done"].fix.taken', scope) == 2
    assert evaluate('edges["validate.done"][1].taken + edges["validate.done"][0].taken', scope) == 2

    scope.complete("fix", "done", {"total": 12}, None)
    assert (scope.previous, evaluate("previous.summary", scope)) == ("fix", None)
    assert evaluate("steps.fix.runs < 3 and steps.fix.exit == \"done\"", scope) is True

    scope.complete("validate", "error", {"cause": "exception", "message": "boom"}, None)
    assert evaluate("steps.validate", scope) == {
        "runs": 2,
        "exit": "error",
        "outputs": {"cause": "exception", "message": "boom"},
    }


def test_utc_now_is_aware_utc():
    now = utc_now()
    assert now.tzinfo is not None and now.utcoffset().total_seconds() == 0
