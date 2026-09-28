"""Proto-step documents and examples (PLAN §3.3; $DRAFTS/01 §6.3, SPEC §6.1)."""

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal

from pydantic import PrivateAttr, ValidationError, field_validator, model_validator
from pydantic_core import PydanticCustomError

from wynd.spec.base import RESERVED_EXIT, ExitName, FieldName, Name, SpecModel
from wynd.spec.errors import Diagnostic, Loc
from wynd.spec.fragments import EnvFragment
from wynd.spec.interface import Interface, interface_from_fields
from wynd.spec.typelang import (
    ModelSet,
    TypeNode,
    TypeSpec,
    build_models,
    field_name_error,
    normalise_outputs,
    outputs_ambiguous,
    render_type,
)
from wynd.spec.yamlio import SourceMap, load_model, located


class Example(SpecModel):
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}  # fields of `exit`'s model, without the exit key; subset allowed
    exit: str = "done"  # a declared exit, or "error" (tests that the step fails loudly)
    description: str | None = None  # set by the compiler to the confirmed edge-case question
    env: dict[str, str] = {}  # process examples only: env for that run (values may use {tmp})


def normalise_document(data: dict) -> dict:
    """Shared `outputs`/`exits` normalisation of proto-step and process documents (E-OUTPUTS, E-SCHEMA field names)."""
    data = dict(data)
    try:
        exits, outputs = normalise_outputs(data.get("outputs"), data.get("exits"))
    except ValueError as err:
        exits = data.get("exits")
        about_exits = str(err).startswith("exits") or (isinstance(exits, list) and RESERVED_EXIT in exits)
        loc = ("exits",) if about_exits else ("outputs",)
        raise PydanticCustomError("E-OUTPUTS", "{message}", {"message": str(err), "loc": loc}) from None
    data["exits"], data["outputs"] = exits, outputs
    inputs = data.get("inputs")
    names: list[tuple[Any, Loc]] = [(name, ("inputs", name)) for name in inputs] if isinstance(inputs, Mapping) else []
    if isinstance(inputs, Mapping) and "exit" in inputs:
        raise PydanticCustomError(
            "E-SCHEMA", "{message}",
            {"message": "'exit' is reserved (the exit discriminator) and cannot be an input field",
             "loc": ("inputs", "exit")},
        )
    for exit, fields in outputs.items():
        if not isinstance(fields, Mapping):
            continue
        if "exit" in fields:
            raise PydanticCustomError(
                "E-OUTPUTS", "{message}",
                {"message": f"'exit' is reserved (the exit discriminator) and cannot be an output field of '{exit}'",
                 "loc": ("outputs", exit, "exit")},
            )
        names += [(name, ("outputs", exit, name)) for name in fields]
    for name, loc in names:
        problem = field_name_error(name)
        if problem:
            raise PydanticCustomError("E-SCHEMA", "{message}", {"message": problem, "loc": loc})
    return data


def authoring_outputs(exits: list[str], outputs: Mapping[str, Mapping[str, TypeNode]]) -> tuple[dict, list | None]:
    """(outputs, exits or None) as written: flat and without `exits` for a done-only document, unless every field is a
    nested mapping (a flat form that would read back as nested by exit, or as ambiguous)."""
    rendered = {exit: {name: render_type(node) for name, node in fields.items()} for exit, fields in outputs.items()}
    done = rendered.get("done", {})
    if exits == ["done"] and not (done and all(isinstance(v, dict) for v in done.values())):
        return done, None
    return {exit: rendered[exit] for exit in exits}, list(exits)


def example_authoring(example: Example) -> dict:
    out: dict[str, Any] = {"inputs": dict(example.inputs)}
    if example.outputs:
        out["outputs"] = dict(example.outputs)
    out["exit"] = example.exit
    if example.description is not None:
        out["description"] = example.description
    if example.env:
        out["env"] = dict(example.env)
    return out


class ProtoStep(SpecModel):
    kind: Literal["proto_step"]
    name: Name
    instruction: str  # non-empty after strip
    inputs: dict[FieldName, TypeSpec] = {}
    outputs: dict[ExitName, dict[FieldName, TypeSpec]]  # normalised: one entry per exit
    exits: list[ExitName]  # normalised
    exit_codes: dict[int | Literal["*"], str] | None = None  # shell only; keys 0..255 or "*"; digit strings coerced
    examples: list[Example] = []
    env: EnvFragment = EnvFragment()

    _outputs_ambiguous: bool = PrivateAttr(default=False)

    @model_validator(mode="wrap")
    @classmethod
    def _normalise(cls, data: Any, handler: Any) -> "ProtoStep":
        if not isinstance(data, dict):
            return handler(data)
        ambiguous = outputs_ambiguous(data.get("outputs"), data.get("exits"))
        proto = handler(normalise_document(data))
        proto._outputs_ambiguous = ambiguous
        return proto

    @field_validator("instruction")
    @classmethod
    def _instruction(cls, value: str) -> str:
        if not value.strip():
            raise PydanticCustomError("E-SCHEMA", "instruction must not be empty")
        return value

    @field_validator("exit_codes")
    @classmethod
    def _exit_codes(cls, value: dict | None) -> dict | None:
        for code in value or {}:
            if code != "*" and not 0 <= code <= 255:
                raise PydanticCustomError("E-SCHEMA", "exit code keys are 0..255 or '*' (got {code})", {"code": code})
        return value

    def models(self) -> ModelSet:
        return build_models(self.name, self.inputs, self.outputs)

    def interface(self) -> Interface:
        return interface_from_fields(self.inputs, self.outputs)

    def to_authoring(self) -> dict:
        """Flat outputs and no `exits` when exits == ["done"]; otherwise nested outputs (every exit) + `exits`."""
        outputs, exits = authoring_outputs(self.exits, self.outputs)
        out: dict[str, Any] = {
            "kind": self.kind,
            "name": self.name,
            "instruction": self.instruction,
            "inputs": {name: render_type(node) for name, node in self.inputs.items()},
            "outputs": outputs,
        }
        if exits is not None:
            out["exits"] = exits
        if self.exit_codes is not None:
            out["exit_codes"] = dict(self.exit_codes)
        out["examples"] = [example_authoring(example) for example in self.examples]
        env = self.env.model_dump(mode="python", exclude_defaults=True)
        if env:
            out["env"] = env
        return out


def load_proto_step(path: Path) -> ProtoStep:
    return load_model(path, ProtoStep)


def check_examples(
    examples: Sequence[Example], models: ModelSet, exits: Sequence[str], source: SourceMap | None
) -> list[Diagnostic]:
    """E-EXAMPLE: the exit is declared (or "error"), inputs validate completely, outputs validate as a subset."""
    out: list[Diagnostic] = []
    for i, example in enumerate(examples):
        base = ("examples", i)
        if example.exit != RESERVED_EXIT and example.exit not in exits:
            out.append(located(source, "error", "E-EXAMPLE",
                               f"example exit '{example.exit}' is not a declared exit ({', '.join(exits)}) or 'error'",
                               (*base, "exit")))
        out += _model_errors(models.input, example.inputs, (*base, "inputs"), "input", source, subset=False)
        model = models.outputs.get(example.exit)
        if model is not None:
            out += _model_errors(model, example.outputs, (*base, "outputs"), f"output of exit '{example.exit}'",
                                 source, subset=True)
    return out


def _model_errors(
    model: Any, data: dict, base: Loc, what: str, source: SourceMap | None, *, subset: bool
) -> list[Diagnostic]:
    try:
        model.model_validate(data)
    except ValidationError as err:
        errors = err.errors(include_url=False)
    else:
        return []
    out = []
    for error in errors:
        loc = tuple(error["loc"])
        name = loc[-1] if loc else ""
        match error["type"]:
            case "missing" if subset:
                continue
            case "missing":
                message = f"missing required {what} '{name}'"
            case "extra_forbidden":
                message = f"'{name}' is not an {what}" if not subset else f"'{name}' is not a field of the {what}"
            case _:
                message = error["msg"]
        out.append(located(source, "error", "E-EXAMPLE", message, (*base, *loc)))
    return out


def check_proto_step(p: ProtoStep) -> list[Diagnostic]:
    """Document-only checks: E-EXAMPLE, E-EXIT-CODES, W-PROTO-NO-EXAMPLES, W-PROTO-EXIT-UNTESTED,
    W-OUTPUTS-AMBIGUOUS."""
    source: SourceMap | None = p._source
    out = check_examples(p.examples, p.models(), p.exits, source)
    for i, example in enumerate(p.examples):
        if example.env:
            out.append(located(source, "error", "E-EXAMPLE", "env: is only allowed on process examples",
                               ("examples", i, "env")))
    for code, exit in (p.exit_codes or {}).items():
        if exit != RESERVED_EXIT and exit not in p.exits:
            out.append(located(source, "error", "E-EXIT-CODES",
                               f"exit code {code} maps to '{exit}', which is not a declared exit "
                               f"({', '.join(p.exits)}) or 'error'", ("exit_codes", code)))
    if not p.examples:
        out.append(located(source, "warning", "W-PROTO-NO-EXAMPLES",
                           "no examples: the compiler needs examples to generate tests", ("examples",)))
    else:
        covered = {example.exit for example in p.examples}
        for exit in p.exits:
            if exit not in covered:
                out.append(located(source, "warning", "W-PROTO-EXIT-UNTESTED", f"no example covers exit '{exit}'",
                                   ("outputs", exit)))
    if p._outputs_ambiguous:
        out.append(located(source, "warning", "W-OUTPUTS-AMBIGUOUS", AMBIGUOUS_MESSAGE, ("outputs",)))
    return out


AMBIGUOUS_MESSAGE = "outputs looks nested by exit but has no `done`; declare `exits:` to nest"
