"""JSON Schema helpers: normalisation, path walking, nullability (PLAN §4.1; $DRAFTS/01 §7.9, §8)."""

from collections.abc import Sequence
from typing import Any


class _Sentinel:
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


ANY = _Sentinel("ANY")  # schema_at: the path continues into an open object or unknown schema
MISSING = _Sentinel("MISSING")  # schema_at: the path does not exist in the schema

_DROPPED = {"title", "description", "examples", "$defs", "definitions"}
_SCHEMA_LISTS = {"anyOf", "oneOf", "allOf", "prefixItems"}
_SCHEMA_VALUES = {"items", "additionalProperties", "not", "contains", "propertyNames", "if", "then", "else"}
_REF_PREFIXES = ("#/$defs/", "#/definitions/")


def normalize_schema(schema: dict) -> dict:
    """Inline $ref, drop titles/descriptions/examples/$defs and the exit property's default/type, sort required.

    Also drops "exit" from `required` (a pydantic exit field has a default, a YAML-derived one is required; both mean
    the same const) and an empty `required`, so code- and YAML-derived schemas of one interface normalise equally.
    """
    return _normalize(schema, _definitions(schema), ())


def _definitions(schema: dict) -> dict:
    return {**schema.get("definitions", {}), **schema.get("$defs", {})}


def _ref_name(ref: str) -> str | None:
    for prefix in _REF_PREFIXES:
        if ref.startswith(prefix):
            return ref[len(prefix):]
    return None


def _normalize(schema: Any, defs: dict, inlining: tuple[str, ...]) -> Any:
    if not isinstance(schema, dict):
        return schema
    ref = schema.get("$ref")
    name = _ref_name(ref) if isinstance(ref, str) else None
    if name is not None and name in defs:
        if name in inlining:
            return {"$ref": ref}
        rest = {k: v for k, v in schema.items() if k != "$ref"}
        return _normalize({**defs[name], **rest}, defs, (*inlining, name))
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in _DROPPED:
            continue
        match key:
            case "properties" if isinstance(value, dict):
                out[key] = {prop: _property(prop, sub, defs, inlining) for prop, sub in value.items()}
            case "required" if isinstance(value, list):
                required = sorted(r for r in value if r != "exit")
                if required:
                    out[key] = required
            case _ if key in _SCHEMA_LISTS and isinstance(value, list):
                out[key] = [_normalize(sub, defs, inlining) for sub in value]
            case _ if key in _SCHEMA_VALUES:
                out[key] = _normalize(value, defs, inlining)
            case _:
                out[key] = value
    return out


def _property(name: str, schema: Any, defs: dict, inlining: tuple[str, ...]) -> Any:
    normalized = _normalize(schema, defs, inlining)
    if name == "exit" and isinstance(normalized, dict) and "const" in normalized:
        return {k: v for k, v in normalized.items() if k not in ("default", "type")}
    return normalized


def strip_keywords(schema: Any, keywords: set[str]) -> Any:
    """Remove schema keywords (never property names) everywhere; used by interfaces_equivalent."""
    if not isinstance(schema, dict):
        return schema
    out: dict[str, Any] = {}
    for key, value in schema.items():
        if key in keywords:
            continue
        match key:
            case "properties" if isinstance(value, dict):
                out[key] = {prop: strip_keywords(sub, keywords) for prop, sub in value.items()}
            case _ if key in _SCHEMA_LISTS and isinstance(value, list):
                out[key] = [strip_keywords(sub, keywords) for sub in value]
            case _ if key in _SCHEMA_VALUES:
                out[key] = strip_keywords(value, keywords)
            case _:
                out[key] = value
    return out


def schema_at(schema: dict, path: Sequence[str | int | None]) -> Any:
    """The sub-schema at `path`, or ANY, or MISSING.

    `$ref`s resolve against the root's `$defs` (a recursive ref is ANY); a nullable `anyOf` is unwrapped; a string
    segment takes an object property (MISSING if undeclared); an open object or `{}` is ANY for the rest of the path;
    an int or None (dynamic) segment takes an array's `items`; any other combination is MISSING.
    """
    return _walk(schema, list(path), _definitions(schema), ())


def _walk(schema: Any, path: list, defs: dict, seen: tuple[str, ...]) -> Any:
    if not isinstance(schema, dict):
        return ANY if schema is True else MISSING
    ref = schema.get("$ref")
    if isinstance(ref, str):
        name = _ref_name(ref)
        if name is None or name not in defs or name in seen:
            return ANY
        return _walk({**defs[name], **{k: v for k, v in schema.items() if k != "$ref"}}, path, defs, (*seen, name))
    members = schema.get("anyOf") or schema.get("oneOf")
    if isinstance(members, list):
        non_null = [m for m in members if not _is_null(m)]
        results = [_walk(m, path, defs, seen) for m in non_null]
        if any(r is ANY for r in results):
            return ANY
        found = [r for r in results if r is not MISSING]
        if not path and len(found) == len(non_null):
            return schema
        if not found:
            return MISSING
        return found[0] if len(found) == 1 else {"anyOf": found}
    if not path:
        return schema
    segment, rest = path[0], path[1:]
    types = _types(schema)
    if isinstance(segment, str):
        if types and "object" not in types:
            return MISSING
        properties = schema.get("properties")
        if not properties:
            return ANY if "object" in types or not types else MISSING
        if segment not in properties:
            return MISSING
        return _walk(properties[segment], rest, defs, seen)
    if "array" in types:
        items = schema.get("items")
        if items is None:
            return ANY
        return _walk(items, rest, defs, seen)
    return ANY if not types else MISSING


def _types(schema: dict) -> set[str]:
    kind = schema.get("type")
    if isinstance(kind, str):
        return {kind}
    if isinstance(kind, list):
        return set(kind)
    if "properties" in schema:
        return {"object"}
    if "items" in schema:
        return {"array"}
    return set()


def _is_null(schema: Any) -> bool:
    return isinstance(schema, dict) and schema.get("type") == "null"


def is_nullable(schema: dict) -> bool:
    """True when the schema explicitly admits null (`type: null`, a `null` type in a list, or a nullable union
    member). `{}` is unknown, not nullable."""
    kind = schema.get("type")
    if kind == "null" or (isinstance(kind, list) and "null" in kind):
        return True
    if "const" in schema and schema["const"] is None:
        return True
    if isinstance(schema.get("enum"), list) and None in schema["enum"]:
        return True
    members = schema.get("anyOf") or schema.get("oneOf") or []
    return any(isinstance(m, dict) and is_nullable(m) for m in members)


def strip_null(schema: dict) -> dict:
    """The schema without its null alternative; a single remaining `anyOf` member is returned merged with the outer
    keywords. A schema that is only null is returned unchanged."""
    kind = schema.get("type")
    if isinstance(kind, list) and "null" in kind:
        rest = [k for k in kind if k != "null"]
        if not rest:
            return schema
        return {**schema, "type": rest[0] if len(rest) == 1 else rest}
    for key in ("anyOf", "oneOf"):
        members = schema.get(key)
        if not isinstance(members, list):
            continue
        rest = [m for m in members if not (isinstance(m, dict) and m.get("type") == "null")]
        if not rest or len(rest) == len(members):
            return schema
        outer = {k: v for k, v in schema.items() if k != key and not (k == "default" and v is None)}
        if len(rest) == 1:
            return {**outer, **rest[0]}
        return {**outer, key: rest}
    return schema
