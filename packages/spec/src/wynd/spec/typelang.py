"""The type mini-language: parsing, rendering, JSON Schema, generated models, outputs normalisation (PLAN §3.4;
$DRAFTS/01 §5)."""

import keyword
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    PlainSerializer,
    TypeAdapter,
    WithJsonSchema,
    create_model,
)
from pydantic_core import PydanticCustomError

from wynd.spec.base import PROTO_TYPES, RESERVED_EXIT


@dataclass(frozen=True)
class TScalar:
    name: str  # string number integer boolean date datetime path object
    optional: bool = False


@dataclass(frozen=True)
class TList:
    item: "TypeNode"
    optional: bool = False


@dataclass(frozen=True)
class TObject:
    fields: tuple[tuple[str, "TypeNode"], ...]
    optional: bool = False


TypeNode = TScalar | TList | TObject

_TOKEN = re.compile(r"list\s*\[|[A-Za-z_][A-Za-z0-9_]*|\]|\?|\S")
_EXPECTED = f"expected one of {', '.join(sorted(PROTO_TYPES))}, list[...]"
_FIELD_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def field_name_error(name: Any) -> str | None:
    """Why `name` cannot be an input/output field name (it becomes a pydantic field and a Python identifier)."""
    if not isinstance(name, str) or not _FIELD_NAME.match(name):
        return f"{name!r} is not a valid field name (letters, digits and _, not starting with a digit)"
    if keyword.iskeyword(name):
        return f"'{name}' cannot be a field name: it is a Python keyword"
    if name.startswith("_") or name.startswith("model_"):
        return f"'{name}' cannot be a field name: a leading '_' or 'model_' is reserved by pydantic"
    return None


def parse_type(spec: str | Mapping | Sequence) -> TypeNode:
    """Parse a type string, nested mapping or one-item sequence; ValueError with a precise message."""
    match spec:
        case TScalar() | TList() | TObject():
            return spec
        case Mapping():
            fields = []
            for name, sub in spec.items():
                problem = field_name_error(name)
                if problem:
                    raise ValueError(problem)
                try:
                    fields.append((name, parse_type(sub)))
                except ValueError as err:
                    raise ValueError(f"field '{name}': {err}") from None
            return TObject(tuple(fields))
        case str():
            return _parse_type_string(spec)
        case list() | tuple():
            if len(spec) != 1:
                raise ValueError("a list type is written as a one-item list: [ <type> ]")
            return TList(parse_type(spec[0]))
    raise ValueError(f"expected a type name, got {spec!r}")


def _parse_type_string(text: str) -> TypeNode:
    tokens = [re.sub(r"\s+", "", t) for t in _TOKEN.findall(text)]
    if not tokens:
        raise ValueError(f"invalid type {text!r}: empty")
    node, rest = _parse_tokens(tokens, text)
    if rest:
        raise ValueError(f"invalid type {text!r}: unexpected {rest[0]!r}")
    return node


def _parse_tokens(tokens: list[str], text: str) -> tuple[TypeNode, list[str]]:
    if not tokens:
        raise ValueError(f"invalid type {text!r}: missing a type name")
    head, rest = tokens[0], tokens[1:]
    match head:
        case "list[":
            item, rest = _parse_tokens(rest, text)
            if not rest or rest[0] != "]":
                raise ValueError(f"invalid type {text!r}: missing ']'")
            node, rest = TList(item), rest[1:]
        case _ if head in PROTO_TYPES:
            node = TScalar(head)
        case _ if re.match(r"[A-Za-z_]", head):
            raise ValueError(f"unknown type {head!r} ({_EXPECTED})")
        case _:
            raise ValueError(f"invalid type {text!r}: unexpected {head!r}")
    if rest and rest[0] == "?":
        node, rest = replace(node, optional=True), rest[1:]
    return node, rest


def _parse_type_spec(value: Any) -> TypeNode:
    try:
        return parse_type(value)
    except ValueError as err:
        raise PydanticCustomError("E-TYPE", "{message}", {"message": str(err)}) from None


def render_type(node: TypeNode) -> str | dict | list:
    """Canonical YAML form (inverse of parse_type). Optional mappings and `[T]` lists cannot carry `?`; in-code nodes of
    that shape render without it."""
    mark = "?" if node.optional else ""
    match node:
        case TScalar():
            return node.name + mark
        case TList():
            inner = render_type(node.item)
            return f"list[{inner}]{mark}" if isinstance(inner, str) else [inner]
        case TObject():
            return {name: render_type(sub) for name, sub in node.fields}
    raise TypeError(f"not a type node: {node!r}")


TypeSpec = Annotated[
    Any,
    BeforeValidator(_parse_type_spec),
    PlainSerializer(render_type),
    WithJsonSchema({"type": ["string", "object", "array"]}),
]

_SCALAR_SCHEMAS = {
    "string": {"type": "string"},
    "number": {"type": "number"},
    "integer": {"type": "integer"},
    "boolean": {"type": "boolean"},
    "date": {"type": "string", "format": "date"},
    "datetime": {"type": "string", "format": "date-time"},
    "path": {"type": "string", "format": "path"},
    "object": {"type": "object"},
}


def type_schema(node: TypeNode) -> dict:
    match node:
        case TScalar():
            schema = dict(_SCALAR_SCHEMAS[node.name])
        case TList():
            schema = {"type": "array", "items": type_schema(node.item)}
        case TObject():
            schema = fields_schema(dict(node.fields))
    if node.optional:
        return {"anyOf": [schema, {"type": "null"}]}
    return schema


def fields_schema(fields: Mapping[str, TypeNode], exit: str | None = None) -> dict:
    """Closed object schema; with `exit`, properties start with {"exit": {"const": exit}} and "exit" is required
    first."""
    properties = {name: type_schema(node) for name, node in fields.items()}
    required = [name for name, node in fields.items() if not node.optional]
    if exit is not None:
        properties = {"exit": {"const": exit}, **properties}
        required = ["exit", *required]
    return {"type": "object", "properties": properties, "required": required, "additionalProperties": False}


@dataclass(frozen=True)
class ModelSet:
    input: type[BaseModel]  # <Pascal(name)>Input, extra="forbid"
    outputs: dict[str, type[BaseModel]]  # exit -> <Pascal(name)><Pascal(exit)> with exit: Literal[exit] = exit
    output: Any  # single model, or Annotated[Union[...], Field(discriminator="exit")]
    adapter: TypeAdapter  # validates {"exit": ..., **fields} for any declared exit


class _Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


_PYTHON_TYPES = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
    "date": date,
    "datetime": datetime,
    "path": Path,
    "object": dict[str, Any],
}


def _pascal(text: str) -> str:
    return "".join(part[:1].upper() + part[1:] for part in re.split(r"[^A-Za-z0-9]+", text) if part)


def _python_type(node: TypeNode, name: str) -> Any:
    match node:
        case TScalar():
            tp = _PYTHON_TYPES[node.name]
        case TList():
            tp = list[_python_type(node.item, name + "Item")]
        case TObject():
            tp = _model(name, dict(node.fields))
    return Optional[tp] if node.optional else tp


def _model(name: str, fields: Mapping[str, TypeNode], exit: str | None = None) -> type[BaseModel]:
    definitions: dict[str, Any] = {}
    if exit is not None:
        definitions["exit"] = (Literal[exit], exit)
    for field, node in fields.items():
        tp = _python_type(node, name + _pascal(field))
        definitions[field] = (tp, None) if node.optional else (tp, ...)
    return create_model(name, __base__=_Closed, **definitions)


def build_models(
    name: str, inputs: Mapping[str, TypeNode], outputs: Mapping[str, Mapping[str, TypeNode]]
) -> ModelSet:
    base = _pascal(name)
    input_model = _model(f"{base}Input", inputs)
    output_models = {exit: _model(f"{base}{_pascal(exit)}", fields, exit=exit) for exit, fields in outputs.items()}
    models = tuple(output_models.values())
    if len(models) == 1:
        output = models[0]
    else:
        output = Annotated[Union[models], Field(discriminator="exit")]
    return ModelSet(input=input_model, outputs=output_models, output=output, adapter=TypeAdapter(output))


_ERROR_DECLARED = f"`{RESERVED_EXIT}` is implicit on every step/process and cannot be declared"


def normalise_outputs(
    outputs: Mapping | None, exits: Sequence[str] | None
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """(exits, outputs by exit) per the $DRAFTS/01 §5.3 table; ValueError (E-OUTPUTS)."""
    outputs = {} if outputs is None else outputs
    if not isinstance(outputs, Mapping):
        raise ValueError(f"outputs must be a mapping, got {type(outputs).__name__}")
    if RESERVED_EXIT in outputs:
        raise ValueError(_ERROR_DECLARED)
    if exits is None:
        if not outputs:
            return ["done"], {"done": {}}
        if "done" in outputs and all(isinstance(v, Mapping) for v in outputs.values()):
            return list(outputs), {exit: dict(fields) for exit, fields in outputs.items()}
        return ["done"], {"done": dict(outputs)}

    if isinstance(exits, str) or not isinstance(exits, Sequence):
        raise ValueError("exits must be a list of exit names")
    exits = list(exits)
    if not exits:
        raise ValueError("exits must not be empty")
    if RESERVED_EXIT in exits:
        raise ValueError(_ERROR_DECLARED)
    duplicates = sorted({e for e in exits if exits.count(e) > 1}, key=str)
    if duplicates:
        raise ValueError(f"exits must be unique (duplicate {', '.join(map(str, duplicates))})")
    if not outputs:
        return exits, {exit: {} for exit in exits}
    exit_keys = [k for k in outputs if k in exits]
    field_keys = [k for k in outputs if k not in exits]
    if not field_keys:
        for key in exit_keys:
            if not isinstance(outputs[key], Mapping):
                raise ValueError(f"outputs.{key} must be a mapping of fields (outputs are nested by exit)")
        return exits, {exit: dict(outputs.get(exit, {})) for exit in exits}
    if exits == ["done"] and not exit_keys:
        return exits, {"done": dict(outputs)}
    if exit_keys:
        raise ValueError(
            f"outputs mixes exit names ({', '.join(exit_keys)}) with field names ({', '.join(map(str, field_keys))})"
        )
    raise ValueError(
        f"outputs keys {', '.join(map(str, field_keys))} are not declared exits; nest outputs by exit "
        "(done: {...}) — a flat mapping means a done-only step"
    )


def outputs_ambiguous(outputs: Any, exits: Any) -> bool:
    """W-OUTPUTS-AMBIGUOUS: no `exits:`, every value a mapping, but no `done` key (read as flat done-only)."""
    return (
        exits is None
        and isinstance(outputs, Mapping)
        and bool(outputs)
        and "done" not in outputs
        and all(isinstance(v, Mapping) for v in outputs.values())
    )


_DESCRIPTIONS = {
    "string": ("text", "text"),
    "number": ("a number", "numbers"),
    "integer": ("a whole number", "whole numbers"),
    "boolean": ("yes/no", "yes/no values"),
    "date": ("a date (YYYY-MM-DD)", "dates (YYYY-MM-DD)"),
    "datetime": ("a date and time", "dates and times"),
    "path": ("a file path", "file paths"),
    "object": ("a set of named values", "sets of named values"),
}


def _describe(node: TypeNode, plural: bool) -> str:
    match node:
        case TScalar():
            return _DESCRIPTIONS[node.name][1 if plural else 0]
        case TList():
            return ("lists of " if plural else "a list of ") + _describe(node.item, True)
        case TObject():
            names = ", ".join(name for name, _ in node.fields)
            if not names:
                return "empty groups" if plural else "an empty group"
            return f"groups with fields {names}" if plural else f"a group with fields {names}"
    raise TypeError(f"not a type node: {node!r}")


def describe_type(node: TypeNode) -> str:
    text = _describe(node, False)
    return f"{text} (optional)" if node.optional else text


def describe_fields(fields: Mapping[str, TypeNode]) -> list[str]:
    """Plain-language lines such as "due_date — a date (YYYY-MM-DD)"."""
    return [f"{name} — {describe_type(node)}" for name, node in fields.items()]


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_DATETIME = re.compile(r"^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:?\d{2})?$")
_MISSING = object()


def infer_fields(samples: Sequence[Mapping[str, Any]]) -> dict[str, TypeNode]:
    """Deterministic field types unified across example values ($DRAFTS/01 §5.4)."""
    names: list[str] = []
    for sample in samples:
        names.extend(name for name in sample if name not in names)
    return {name: _infer([sample.get(name, _MISSING) for sample in samples]) for name in names}


def _infer(values: list[Any]) -> TypeNode:
    present = [v for v in values if v is not _MISSING and v is not None]
    optional = len(present) < len(values)
    kinds = {_kind(v) for v in present}
    if not kinds:
        return TScalar("string", True)
    if kinds == {"mapping"}:
        node: TypeNode = TObject(tuple(infer_fields(present).items()))
    elif kinds == {"list"}:
        items = [item for v in present for item in v]
        node = TList(_infer(items) if items else TScalar("string"))
    elif kinds & {"mapping", "list"}:
        node = TScalar("object")
    elif kinds <= {"integer", "number"}:
        node = TScalar("number" if "number" in kinds else "integer")
    elif len(kinds) == 1:
        node = TScalar(kinds.pop())
    else:
        node = TScalar("string")
    if not optional:
        return node
    match node:
        case TObject():  # a nested mapping cannot carry `?`
            return TScalar("object", True)
        case TList(item=TObject() | TList(item=TObject())):
            return TList(TScalar("object"), True)
    return replace(node, optional=True)


def _kind(value: Any) -> str:
    match value:
        case bool():
            return "boolean"
        case int():
            return "integer"
        case float():
            return "number"
        case datetime():
            return "datetime"
        case date():
            return "date"
        case str() if _DATE.match(value):
            return "date"
        case str() if _DATETIME.match(value):
            return "datetime"
        case Mapping():
            return "mapping"
        case list() | tuple():
            return "list"
    return "string"
