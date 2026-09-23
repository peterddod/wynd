"""The provider-facing output-schema envelope, the only strictness transform (PLAN §3.15, §1.4;
`$DRAFTS/03 §6.4`): `{"type":"object","properties":{"output": <schema>},"required":["output"],
"additionalProperties":false}`, `oneOf` -> `anyOf`, `$defs` hoisted, every object closed with all properties required.

Callers (steps, edge checks, the compiler, the chat) pass the plain Output schema; providers wrap it here and unwrap
the result with `unwrap_output`.
"""

from __future__ import annotations

from typing import Any

DROP = frozenset({
    "discriminator", "title", "default", "examples", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum",
    "multipleOf", "minLength", "maxLength", "pattern", "minItems", "maxItems", "uniqueItems", "minProperties",
    "maxProperties",
})
FORMATS = frozenset({"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"})


def wrap_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """`{"type":"object","properties":{"output": clean(schema minus $defs)},"required":["output"],
    "additionalProperties":false, "$defs": {name: clean(def)}}` (`$defs` only when present; `#/$defs/...` refs stay
    valid because the definitions move to the root)."""
    body = {key: value for key, value in schema.items() if key != "$defs"}
    wrapped: dict[str, Any] = {
        "type": "object",
        "properties": {"output": _clean(body)},
        "required": ["output"],
        "additionalProperties": False,
    }
    if "$defs" in schema:
        wrapped["$defs"] = {name: _clean(sub) for name, sub in schema["$defs"].items()}
    return wrapped


def unwrap_output(value: Any) -> Any:
    return value["output"] if isinstance(value, dict) and set(value) == {"output"} else value


def strict_compatible(wrapped: dict[str, Any]) -> bool:
    """False if any object node has no `properties` (a free-form `dict[str, X]` / `object` field): strict structured
    outputs need `additionalProperties: false` on every object. Used by the anthropic provider."""
    match wrapped:
        case dict():
            if wrapped.get("type") == "object" and "properties" not in wrapped:
                return False
            return all(strict_compatible(sub) for sub in wrapped.values())
        case list():
            return all(strict_compatible(sub) for sub in wrapped)
    return True


def _clean(node: Any) -> Any:
    """Drop unsupported keywords and non-standard formats, `oneOf` -> `anyOf`, and close every object: all its
    properties required (the spec never makes a field optional for another exit, and `exit` must be present for the
    discriminator even though it has a default). Property names are never dropped (a field may be called `title`)."""
    match node:
        case dict():
            out: dict[str, Any] = {}
            for key, value in node.items():
                if key in DROP or (key == "format" and value not in FORMATS):
                    continue
                if key == "properties":
                    out[key] = {name: _clean(sub) for name, sub in value.items()}
                else:
                    out["anyOf" if key == "oneOf" else key] = _clean(value)
            if "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        case list():
            return [_clean(item) for item in node]
    return node
