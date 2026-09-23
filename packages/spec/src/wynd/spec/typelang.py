"""The type mini-language: parsing, rendering, JSON Schema, generated models, outputs normalisation (PLAN §3.4;
$DRAFTS/01 §5)."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, PlainSerializer, TypeAdapter, WithJsonSchema


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


def parse_type(spec: str | Mapping | Sequence) -> TypeNode:
    """Parse a type string, nested mapping or one-item sequence; ValueError with a precise message."""
    raise NotImplementedError("PLAN §3.4")


def render_type(node: TypeNode) -> str | dict | list:
    """Canonical YAML form (inverse of parse_type)."""
    raise NotImplementedError("PLAN §3.4")


TypeSpec = Annotated[
    Any,
    BeforeValidator(parse_type),
    PlainSerializer(render_type),
    WithJsonSchema({"type": ["string", "object", "array"]}),
]


def type_schema(node: TypeNode) -> dict:
    raise NotImplementedError("PLAN §3.4")


def fields_schema(fields: Mapping[str, TypeNode], exit: str | None = None) -> dict:
    """Closed object schema; with `exit`, properties start with {"exit": {"const": exit}} and "exit" is required
    first."""
    raise NotImplementedError("PLAN §3.4")


@dataclass(frozen=True)
class ModelSet:
    input: type[BaseModel]  # <Pascal(name)>Input, extra="forbid"
    outputs: dict[str, type[BaseModel]]  # exit -> <Pascal(name)><Pascal(exit)> with exit: Literal[exit] = exit
    output: Any  # single model, or Annotated[Union[...], Field(discriminator="exit")]
    adapter: TypeAdapter  # validates {"exit": ..., **fields} for any declared exit


def build_models(
    name: str, inputs: Mapping[str, TypeNode], outputs: Mapping[str, Mapping[str, TypeNode]]
) -> ModelSet:
    raise NotImplementedError("PLAN §3.4")


def normalise_outputs(
    outputs: Mapping | None, exits: Sequence[str] | None
) -> tuple[list[str], dict[str, dict[str, Any]]]:
    """(exits, outputs by exit) per the $DRAFTS/01 §5.3 table; ValueError (E-OUTPUTS)."""
    raise NotImplementedError("PLAN §3.3")


def describe_type(node: TypeNode) -> str:
    raise NotImplementedError("PLAN §3.4")


def describe_fields(fields: Mapping[str, TypeNode]) -> list[str]:
    """Plain-language lines such as "due_date — a date (YYYY-MM-DD)"."""
    raise NotImplementedError("PLAN §3.4")


def infer_fields(samples: Sequence[Mapping[str, Any]]) -> dict[str, TypeNode]:
    """Deterministic field types unified across example values ($DRAFTS/01 §5.4)."""
    raise NotImplementedError("PLAN §3.4")
