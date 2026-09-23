"""Interface snapshots and the Output rules (PLAN §3.4; $DRAFTS/01 §6.10)."""

import json
import re
import types
from collections.abc import Mapping
from functools import lru_cache
from typing import Annotated, Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, Field, TypeAdapter

from wynd.spec.base import RESERVED_EXIT, ExitName, SpecModel
from wynd.spec.records import STEP_ERROR_SCHEMA
from wynd.spec.schemas import normalize_schema, strip_keywords
from wynd.spec.typelang import TypeNode, fields_schema

_EXIT_NAME = re.compile(r"^[a-z_][a-z0-9_]*$")


class Interface(SpecModel):  # the step/process contract snapshot
    input: dict[str, Any]  # JSON Schema of Input
    outputs: dict[ExitName, dict[str, Any]]  # exit -> JSON Schema of that exit's model, incl. "exit" const

    @property
    def exits(self) -> list[str]:
        return list(self.outputs)

    def with_error(self) -> dict[str, dict]:
        """Outputs plus the implicit `error` exit."""
        return {**self.outputs, RESERVED_EXIT: STEP_ERROR_SCHEMA}


def split_output(output: Any) -> dict[str, type[BaseModel]]:
    """Exit -> model for a model or a union of models; TypeError naming the class on a rule violation."""
    if get_origin(output) is Annotated:
        output = get_args(output)[0]
    union = get_origin(output) in (Union, types.UnionType)
    members = get_args(output) if union else (output,)
    result: dict[str, type[BaseModel]] = {}
    for member in members:
        if not (isinstance(member, type) and issubclass(member, BaseModel)):
            raise TypeError(f"Output member {member!r} is not a pydantic model")
        exit = _exit_of(member)
        if exit in result:
            raise TypeError(f"{member.__name__}: exit '{exit}' is declared twice in Output")
        result[exit] = member
    if not union and list(result) != ["done"]:
        raise TypeError(
            f"{output.__name__}: a plain Output model is the 'done' exit (exit: Literal[\"done\"] = \"done\"); "
            "use a union of models for other exits"
        )
    return result


def _exit_of(model: type[BaseModel]) -> str:
    name = model.__name__
    field = model.model_fields.get("exit")
    if field is None:
        raise TypeError(f'{name}: Output models need an `exit` field annotated Literal["<name>"]')
    annotation = field.annotation
    values = get_args(annotation) if get_origin(annotation) is Literal else ()
    if len(values) != 1 or not isinstance(values[0], str):
        raise TypeError(f'{name}: `exit` must be annotated Literal["<name>"] with exactly one string value')
    exit = values[0]
    if field.default != exit:
        raise TypeError(f"{name}: `exit` must default to its Literal value '{exit}'")
    if not _EXIT_NAME.match(exit):
        raise TypeError(f"{name}: exit '{exit}' is not a valid exit name (lowercase letters, digits and _)")
    if exit == RESERVED_EXIT:
        raise TypeError(f"{name}: the '{RESERVED_EXIT}' exit is implicit on every step and cannot be declared")
    return exit


def output_adapter(output: Any) -> TypeAdapter:
    """TypeAdapter discriminated on "exit"."""
    return _adapter(tuple(split_output(output).values()))


@lru_cache(maxsize=256)
def _adapter(models: tuple[type[BaseModel], ...]) -> TypeAdapter:
    if len(models) == 1:
        return TypeAdapter(models[0])
    return TypeAdapter(Annotated[Union[models], Field(discriminator="exit")])


def interface_from_models(input_model: type[BaseModel], output: Any) -> Interface:
    return Interface(
        input=input_model.model_json_schema(mode="validation"),
        outputs={exit: model.model_json_schema(mode="validation") for exit, model in split_output(output).items()},
    )


def interface_from_fields(
    inputs: Mapping[str, TypeNode], outputs: Mapping[str, Mapping[str, TypeNode]]
) -> Interface:
    return Interface(
        input=fields_schema(inputs),
        outputs={exit: fields_schema(fields, exit=exit) for exit, fields in outputs.items()},
    )


_IGNORED = {"additionalProperties", "default"}


def interfaces_equivalent(a: Interface, b: Interface) -> list[str]:
    """Human-readable differences; [] when equal modulo titles/defaults/additionalProperties."""
    differences = _schema_differences("input", _comparable(a.input), _comparable(b.input))
    for exit in a.outputs:
        if exit not in b.outputs:
            differences.append(f"exit '{exit}' is only in the first interface")
    for exit in b.outputs:
        if exit not in a.outputs:
            differences.append(f"exit '{exit}' is only in the second interface")
    for exit in a.outputs:
        if exit in b.outputs:
            differences += _schema_differences(
                f"exit '{exit}'", _comparable(a.outputs[exit]), _comparable(b.outputs[exit])
            )
    return differences


def _comparable(schema: dict) -> dict:
    return strip_keywords(normalize_schema(schema), _IGNORED)


def _schema_differences(where: str, a: dict, b: dict) -> list[str]:
    if a == b:
        return []
    props_a, props_b = a.get("properties"), b.get("properties")
    if not (isinstance(props_a, dict) and isinstance(props_b, dict)):
        return [f"{where}: {_text(a)} != {_text(b)}"]
    out = []
    for name in props_a:
        if name not in props_b:
            out.append(f"{where}: field '{name}' is only in the first interface")
        elif props_a[name] != props_b[name]:
            out.append(f"{where}: field '{name}': {_text(props_a[name])} != {_text(props_b[name])}")
    out += [f"{where}: field '{name}' is only in the second interface" for name in props_b if name not in props_a]
    required_a, required_b = set(a.get("required", [])), set(b.get("required", []))
    for name in sorted(required_a ^ required_b):
        if name in props_a and name in props_b:
            side = "first" if name in required_a else "second"
            out.append(f"{where}: field '{name}' is required only in the {side} interface")
    rest_a = {k: v for k, v in a.items() if k not in ("properties", "required")}
    rest_b = {k: v for k, v in b.items() if k not in ("properties", "required")}
    if rest_a != rest_b:
        out.append(f"{where}: {_text(rest_a)} != {_text(rest_b)}")
    return out


def _text(schema: Any) -> str:
    return json.dumps(schema, sort_keys=True)
