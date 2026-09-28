"""Step and process interfaces as the runtime sees them (PLAN §3.4, §5.1; `$DRAFTS/02 §3.2`).

The Output rules have one implementation: `wynd.spec.interface.split_output` / `output_adapter`.
"""

from __future__ import annotations

import functools
import inspect
from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, PydanticUserError, TypeAdapter

from wynd.runtime.errors import StepDefinitionError
from wynd.spec.base import RESERVED_EXIT
from wynd.spec.interface import Interface, interface_from_models, output_adapter, split_output
from wynd.spec.lockfiles import TraceStepKind
from wynd.spec.records import StepError

if TYPE_CHECKING:
    from wynd.runtime.step import Step
    from wynd.spec.process_doc import ProcessDoc


@dataclass(frozen=True)
class StepInterface:
    """Declared exits, the discriminated output adapter and the input model of one step class or process."""

    kind: TraceStepKind
    input_model: type[BaseModel]
    exits: dict[str, type[BaseModel]]      # declared exits in declaration order; never contains "error"
    output_adapter: TypeAdapter            # discriminated on "exit" (or the single done model)
    interface: Interface                   # the JSON-schema snapshot (what step.lock.yaml records)

    def exit_model(self, name: str) -> type[BaseModel]:
        """The model of exit `name`; "error" is `StepError`; KeyError for an undeclared exit."""
        if name == RESERVED_EXIT:
            return StepError
        return self.exits[name]

    def input_fields(self) -> list[str]:
        return list(self.input_model.model_fields)

    def output_json_schema(self) -> dict[str, Any]:
        return self.output_adapter.json_schema()

    def validate_output(self, value: BaseModel | Mapping[str, Any]) -> BaseModel:
        """Validate what a step returned (a model instance or a mapping); pydantic.ValidationError or TypeError."""
        match value:
            case BaseModel():
                data = value.model_dump(mode="json")
            case Mapping():
                data = dict(value)
            case _:
                raise TypeError(
                    f"a step must return an Output model or a mapping, got {type(value).__name__}"
                )
        return self.output_adapter.validate_python(data)


@functools.cache
def interface_of(cls: type[Step]) -> StepInterface:
    """Introspect a step class once; StepDefinitionError names the class on any Input/Output rule violation."""
    from wynd.runtime.step import step_kind

    name = cls.__qualname__
    input_model = getattr(cls, "Input", None)
    if not (isinstance(input_model, type) and issubclass(input_model, BaseModel)):
        raise StepDefinitionError(f"{name}.Input must be a pydantic model class")
    if not hasattr(cls, "Output"):
        raise StepDefinitionError(f"{name}.Output must be declared (a model or a union of models, one per exit)")
    try:
        exits = split_output(cls.Output)
        adapter = output_adapter(cls.Output)
        snapshot = interface_from_models(input_model, cls.Output)
    except (TypeError, NameError, PydanticUserError) as err:     # NameError: unresolved forward references
        raise StepDefinitionError(f"{name}: {err}") from err
    return StepInterface(step_kind(cls), input_model, exits, adapter, snapshot)


def process_interface(process_id: str, doc: ProcessDoc) -> StepInterface:
    """A process seen as a step: models from `doc.models()`, snapshot from `ProcessDoc.interface()` (the only
    process-interface builder). A single exit of any name is allowed here."""
    models = doc.models()
    return StepInterface("process", models.input, dict(models.outputs), models.adapter, doc.interface())


class StepDescription(BaseModel):
    """Worker `describe` payload for one step (PLAN §3.12)."""

    id: str
    cls: str                               # "wynd_steps.<module name>.<module>:<Class>" or "process:<id>"
    kind: TraceStepKind
    doc: str | None
    input_schema: dict[str, Any]
    input_fields: list[str]
    required_inputs: list[str]
    exits: dict[str, dict[str, Any]]       # exit -> JSON schema of its model, including "error"
    context: list[str] = []
    agentic: dict[str, Any] | None = None  # wynd.runtime.agentic.checks.describe_agentic(cls)
    shell: dict[str, Any] | None = None    # {"exit_codes": {"0": "done", "*": "error"}}


def describe_step(cls: type[Step], step_id: str) -> StepDescription:
    iface = interface_of(cls)
    agentic = shell = None
    context: list[str] = []
    match iface.kind:
        case "agentic":
            from wynd.runtime.agentic.checks import describe_agentic

            agentic = describe_agentic(cls)
            context = list(cls.context)
        case "shell":
            shell = {"exit_codes": {str(code): exit for code, exit in cls.exit_codes.items()}}
    return StepDescription(
        id=step_id,
        cls=f"{cls.__module__}:{cls.__qualname__}",
        kind=iface.kind,
        doc=class_doc(cls),
        input_schema=iface.interface.input,
        input_fields=iface.input_fields(),
        required_inputs=_required(iface.input_model),
        exits=iface.interface.with_error(),
        context=context,
        agentic=agentic,
        shell=shell,
    )


def describe_process(process_id: str, doc: ProcessDoc) -> StepDescription:
    iface = process_interface(process_id, doc)
    return StepDescription(
        id=f"process:{process_id}",
        cls=f"process:{process_id}",
        kind="process",
        doc=doc.goal,
        input_schema=iface.interface.input,
        input_fields=iface.input_fields(),
        required_inputs=_required(iface.input_model),
        exits=iface.interface.with_error(),
    )


def class_doc(cls: type) -> str | None:
    """The class's own docstring, cleaned; an inherited docstring (e.g. DeterministicStep's) does not count."""
    doc = cls.__dict__.get("__doc__")
    return inspect.cleandoc(doc) if isinstance(doc, str) else None


def _required(model: type[BaseModel]) -> list[str]:
    return [name for name, info in model.model_fields.items() if info.is_required()]
