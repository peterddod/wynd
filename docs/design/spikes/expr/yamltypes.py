"""Spike: YAML loading with line marks, YAML-1.2 booleans, type mini-language, dynamic models."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any, Literal, Optional, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, create_model


# ------------------------------------------------------------------ YAML
class WyndLoader(yaml.SafeLoader):
    pass


# YAML 1.2 core booleans only: drop yes/no/on/off/y/n from the implicit bool resolver.
WyndLoader.yaml_implicit_resolvers = {
    k: [(tag, rx) for tag, rx in v if tag != "tag:yaml.org,2002:bool"]
    for k, v in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
WyndLoader.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)

Loc = tuple[str | int, ...]


class YamlError(ValueError):
    def __init__(self, message, line, column):
        self.line, self.column = line, column
        super().__init__(f"{line}:{column}: {message}")


def load_yaml(text: str) -> tuple[Any, dict[Loc, tuple[int, int]]]:
    """Return (data, marks). marks maps a document path to the 1-based (line, col) of its value."""
    try:
        node = yaml.compose(text, Loader=WyndLoader)
    except yaml.MarkedYAMLError as e:
        m = e.problem_mark
        raise YamlError(e.problem or str(e), m.line + 1, m.column + 1) from None
    marks: dict[Loc, tuple[int, int]] = {}
    if node is None:
        return None, marks
    loader = WyndLoader("")
    data = _build(node, (), marks, loader)
    return data, marks


def _build(node, loc, marks, loader):
    marks[loc] = (node.start_mark.line + 1, node.start_mark.column + 1)
    if isinstance(node, yaml.MappingNode):
        out = {}
        for k, v in node.value:
            key = loader.construct_object(k, deep=True)
            if key in out:
                raise YamlError(f"duplicate key {key!r}", k.start_mark.line + 1, k.start_mark.column + 1)
            out[key] = _build(v, loc + (key,), marks, loader)
        return out
    if isinstance(node, yaml.SequenceNode):
        return [_build(v, loc + (i,), marks, loader) for i, v in enumerate(node.value)]
    return loader.construct_object(node, deep=True)


def line_for(marks, loc: Loc) -> tuple[int, int] | None:
    loc = tuple(loc)
    while True:
        if loc in marks:
            return marks[loc]
        if not loc:
            return None
        loc = loc[:-1]


# ------------------------------------------------------------------ type mini-language
SCALARS = {"string", "number", "integer", "boolean", "date", "datetime", "path", "object"}


@dataclass(frozen=True)
class TScalar:
    name: str
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
_TYPE_RX = re.compile(r"\s*(list\s*\[|[a-z]+|\]|\?)\s*")


def parse_type(spec: Any) -> TypeNode:
    if isinstance(spec, dict):
        return TObject(tuple((str(k), parse_type(v)) for k, v in spec.items()))
    if isinstance(spec, list):
        if len(spec) != 1:
            raise ValueError("a list type is written as a one-item list: [ <type> ]")
        return TList(parse_type(spec[0]))
    if not isinstance(spec, str):
        raise ValueError(f"expected a type name, got {spec!r}")
    toks = [t.replace(" ", "") for t in _TYPE_RX.findall(spec)]
    if "".join(toks) != re.sub(r"\s+", "", spec):
        raise ValueError(f"invalid type {spec!r}")
    node, rest = _parse_toks(toks, spec)
    if rest:
        raise ValueError(f"invalid type {spec!r}: unexpected {''.join(rest)!r}")
    return node


def _parse_toks(toks, spec):
    if not toks:
        raise ValueError(f"invalid type {spec!r}")
    t, rest = toks[0], toks[1:]
    if t == "list[":
        item, rest = _parse_toks(rest, spec)
        if not rest or rest[0] != "]":
            raise ValueError(f"invalid type {spec!r}: missing ']'")
        node, rest = TList(item), rest[1:]
    elif t in SCALARS:
        node = TScalar(t)
    else:
        raise ValueError(f"unknown type {t!r} (expected one of {', '.join(sorted(SCALARS))}, list[...])")
    if rest and rest[0] == "?":
        node, rest = _opt(node), rest[1:]
    return node, rest


def _opt(n):
    return type(n)(*[getattr(n, f) for f in n.__dataclass_fields__ if f != "optional"], optional=True)


def render_type(n: TypeNode) -> Any:
    q = "?" if n.optional else ""
    if isinstance(n, TScalar):
        return n.name + q
    if isinstance(n, TList):
        inner = render_type(n.item)
        return f"list[{inner}]{q}" if isinstance(inner, str) else [inner]
    return {k: render_type(v) for k, v in n.fields}


_JS = {
    "string": {"type": "string"}, "number": {"type": "number"}, "integer": {"type": "integer"},
    "boolean": {"type": "boolean"}, "date": {"type": "string", "format": "date"},
    "datetime": {"type": "string", "format": "date-time"}, "path": {"type": "string", "format": "path"},
    "object": {"type": "object"},
}


def to_json_schema(n: TypeNode) -> dict:
    if isinstance(n, TScalar):
        s = dict(_JS[n.name])
    elif isinstance(n, TList):
        s = {"type": "array", "items": to_json_schema(n.item)}
    else:
        s = fields_schema(dict(n.fields))
    return {"anyOf": [s, {"type": "null"}]} if n.optional else s


def fields_schema(fields: dict[str, TypeNode], exit: str | None = None) -> dict:
    props = {k: to_json_schema(v) for k, v in fields.items()}
    req = [k for k, v in fields.items() if not v.optional]
    if exit is not None:
        props = {"exit": {"const": exit}, **props}
        req = ["exit", *req]
    return {"type": "object", "properties": props, "required": req, "additionalProperties": False}


_PY = {"string": str, "number": float, "integer": int, "boolean": bool, "date": date,
       "datetime": datetime, "path": Path, "object": dict[str, Any]}


def _pascal(s):
    return "".join(p[:1].upper() + p[1:] for p in re.split(r"[_\-/]+", s) if p)


def to_py(n: TypeNode, name: str):
    if isinstance(n, TScalar):
        t = _PY[n.name]
    elif isinstance(n, TList):
        t = list[to_py(n.item, name + "Item")]
    else:
        t = build_model(name, dict(n.fields))
    return Optional[t] if n.optional else t


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


def build_model(name: str, fields: dict[str, TypeNode], exit: str | None = None) -> type[BaseModel]:
    defs: dict[str, Any] = {}
    if exit is not None:
        defs["exit"] = (Literal[exit], exit)
    for k, v in fields.items():
        t = to_py(v, name + _pascal(k))
        defs[k] = (t, None) if v.optional else (t, ...)
    return create_model(name, __base__=_Strict, **defs)


def output_union(models: dict[str, type[BaseModel]]):
    ms = tuple(models.values())
    if len(ms) == 1:
        return ms[0]
    return Annotated[Union[ms], Field(discriminator="exit")]
