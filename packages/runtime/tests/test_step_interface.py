"""Step classes, `interface_of`, `process_interface` and worker descriptions (PLAN §3.4, §5.1–§5.2)."""

from typing import Annotated, Literal, Union

import pytest
from pydantic import BaseModel, Field, ValidationError

from support.rt_step_doubles import use_agentic_checks
from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.interface import StepDescription, describe_process, describe_step, interface_of, process_interface
from wynd.runtime.step import AgenticStep, DeterministicStep, ProcessStep, ShellStep, Step, step_kind
from wynd.spec.hashing import interface_hash
from wynd.spec.interface import interface_from_models
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.records import STEP_ERROR_SCHEMA, StepError


class In(BaseModel):
    text: str
    limit: int = 3


class Done(BaseModel):
    exit: Literal["done"] = "done"
    value: int


class Missing(BaseModel):
    exit: Literal["missing"] = "missing"
    reason: str


class Pipe(DeterministicStep):
    """Parse a number."""

    Input = In
    Output = Done | Missing

    def run(self, input):
        return Done(value=1)


class Plain(DeterministicStep):
    Input = In
    Output = Done

    def run(self, input):
        return Done(value=1)


def _step(output, input_model=In, name="Custom"):
    return type(name, (DeterministicStep,), {"Input": input_model, "Output": output, "run": lambda self, i: None})


@pytest.mark.parametrize("output", [
    Done | Missing,
    Union[Done, Missing],
    Annotated[Union[Done, Missing], Field(discriminator="exit")],
])
def test_union_forms_are_accepted(output):
    iface = interface_of(_step(output))
    assert list(iface.exits) == ["done", "missing"]
    assert iface.exit_model("missing") is Missing
    assert iface.exit_model("error") is StepError
    assert iface.input_fields() == ["text", "limit"]
    assert iface.kind == "deterministic"


def test_plain_done_model_is_the_done_exit():
    iface = interface_of(Plain)
    assert list(iface.exits) == ["done"]
    assert iface.validate_output({"value": 3}) == Done(value=3)


class NotDone(BaseModel):
    exit: Literal["partial"] = "partial"


class NoExit(BaseModel):
    value: int


class StrExit(BaseModel):
    exit: str = "done"


class TwoValues(BaseModel):
    exit: Literal["done", "other"] = "done"


class Done2(BaseModel):
    exit: Literal["done"] = "done"


class ErrorExit(BaseModel):
    exit: Literal["error"] = "error"


class WrongDefault(BaseModel):
    exit: Literal["done"] = "other"


@pytest.mark.parametrize(("output", "input_model", "needle"), [
    (NotDone, In, "plain Output model is the 'done' exit"),
    (NoExit, In, "need an `exit` field"),
    (StrExit | Done, In, "exactly one string value"),
    (TwoValues, In, "exactly one string value"),
    (Done | Done2, In, "declared twice"),
    (Done | ErrorExit, In, "implicit"),
    (WrongDefault, In, "must default"),
    (Done, dict, "Input must be a pydantic model class"),
])
def test_output_and_input_rule_violations_name_the_class(output, input_model, needle):
    with pytest.raises(StepDefinitionError) as err:
        interface_of(_step(output, input_model, name="BadStep"))
    assert "BadStep" in str(err.value)
    assert needle in str(err.value)


class Unresolved(BaseModel):
    exit: Literal["done"] = "done"
    item: "NotDefinedAnywhere"  # noqa: F821


def test_models_that_cannot_build_a_schema_are_definition_errors():
    with pytest.raises(StepDefinitionError, match="Unfinished: .*not fully defined"):
        interface_of(_step(Unresolved, name="Unfinished"))


def test_missing_output_is_a_definition_error():
    cls = type("NoOutput", (DeterministicStep,), {"Input": In, "run": lambda self, i: None})
    with pytest.raises(StepDefinitionError, match="NoOutput.Output must be declared"):
        interface_of(cls)


def test_interface_is_cached_and_matches_the_spec_snapshot():
    iface = interface_of(Pipe)
    assert interface_of(Pipe) is iface
    assert iface.interface == interface_from_models(In, Done | Missing)
    assert interface_hash(iface.interface) == interface_hash(interface_from_models(In, Done | Missing))


def test_validate_output_discriminates_and_rejects():
    iface = interface_of(Pipe)
    assert iface.validate_output(Missing(reason="none")) == Missing(reason="none")
    assert iface.validate_output({"exit": "done", "value": "7"}) == Done(value=7)
    with pytest.raises(ValidationError):
        iface.validate_output({"exit": "done"})
    with pytest.raises(ValidationError):
        iface.validate_output({"exit": "elsewhere"})
    with pytest.raises(TypeError, match="got int"):
        iface.validate_output(7)
    assert iface.output_json_schema()["discriminator"]["propertyName"] == "exit"


def test_step_kind_by_subclass(monkeypatch):
    use_agentic_checks(monkeypatch)

    class Agent(AgenticStep):
        """Do it."""

    class Shell(ShellStep):
        pass

    class Child(ProcessStep):
        process_id = "child"

    class Bare(Step):
        def run(self, input):
            return None

    assert [step_kind(c) for c in (Pipe, Agent, Shell, Child, Bare)] == [
        "deterministic", "agentic", "shell", "process", "deterministic",
    ]


def test_agentic_subclass_runs_the_class_checks(monkeypatch):
    checked = use_agentic_checks(monkeypatch)

    class Extract(AgenticStep):
        """Extract fields."""

        def run(self, input): ...

    assert checked == ["Extract"]

    def refuse(cls):
        raise StepDefinitionError(f"{cls.__name__}: AgenticStep.run must be `...`")

    monkeypatch.setattr("wynd.runtime.agentic.checks.check_agentic_class", refuse)
    with pytest.raises(StepDefinitionError, match="Concrete: AgenticStep.run must be"):
        class Concrete(AgenticStep):
            """Bad."""

            def run(self, input):
                return 1


def test_process_step_is_never_called():
    class Child(ProcessStep):
        process_id = "child"
        Input = In
        Output = Done

    with pytest.raises(RuntimeError, match="executed by the executor"):
        Child().run(In(text="x"))


def test_describe_deterministic_step_includes_the_error_exit():
    description = describe_step(Pipe, "proc#pipe")
    assert isinstance(description, StepDescription)
    assert description.id == "proc#pipe"
    assert description.cls == f"{__name__}:Pipe"
    assert description.kind == "deterministic"
    assert description.doc == "Parse a number."
    assert description.input_fields == ["text", "limit"]
    assert description.required_inputs == ["text"]
    assert list(description.exits) == ["done", "missing", "error"]
    assert description.exits["error"] == STEP_ERROR_SCHEMA
    assert (description.context, description.agentic, description.shell) == ([], None, None)
    assert describe_step(Plain, "p#plain").doc is None      # DeterministicStep's docstring is not the step's


def test_describe_shell_and_agentic_steps(monkeypatch):
    use_agentic_checks(monkeypatch)

    class Count(ShellStep):
        Input = In
        Output = Done | Missing
        exit_codes = {0: "done", 3: "missing", "*": "error"}

    class Extract(AgenticStep):
        """Extract the number."""

        context = ["process.goal", "previous.outputs"]
        Input = In
        Output = Done

        def run(self, input): ...

    shell = describe_step(Count, "p#count")
    assert (shell.kind, shell.shell) == ("shell", {"exit_codes": {"0": "done", "3": "missing", "*": "error"}})
    agentic = describe_step(Extract, "p#extract")
    assert agentic.kind == "agentic"
    assert agentic.context == ["process.goal", "previous.outputs"]
    assert agentic.agentic == {"instruction": "Extract the number.", "completed_by_loop": True}
    assert StepDescription.model_validate_json(agentic.model_dump_json()) == agentic


def _process_doc() -> ProcessDoc:
    return ProcessDoc.model_validate({
        "kind": "process", "name": "child", "goal": "Classify a number.", "entry": "a",
        "steps": {"a": {"use": "./steps/a"}}, "inputs": {"x": "integer", "note": "string?"},
        "exits": ["done", "partial", "rejected"],
        "outputs": {"done": {"y": "integer"}, "partial": {"y": "integer?"}, "rejected": {}},
    })


def test_process_interface_wraps_the_process_doc():
    doc = _process_doc()
    iface = process_interface("finance/child", doc)
    assert iface.kind == "process"
    assert list(iface.exits) == ["done", "partial", "rejected"]
    assert iface.interface == doc.interface()
    assert iface.input_fields() == ["x", "note"]
    assert iface.validate_output({"exit": "rejected"}).exit == "rejected"
    assert iface.validate_output({"exit": "done", "y": "4"}).y == 4
    assert iface.exit_model("error") is StepError
    with pytest.raises(ValidationError):
        iface.input_model.model_validate({"x": 1, "extra": True})


def test_describe_process():
    description = describe_process("finance/child", _process_doc())
    assert (description.id, description.cls, description.kind) == ("process:finance/child",) * 2 + ("process",)
    assert description.doc == "Classify a number."
    assert description.required_inputs == ["x"]
    assert list(description.exits) == ["done", "partial", "rejected", "error"]
