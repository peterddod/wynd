"""The provider-facing output-schema envelope, the only strictness transform (PLAN §3.15, §1.4;
`$DRAFTS/03 §6.4`): `{"type":"object","properties":{"output": <schema>},"required":["output"],
"additionalProperties":false}`, `oneOf` -> `anyOf`, `$defs` hoisted, every object closed with all properties required."""

from __future__ import annotations

from typing import Any


def wrap_output_schema(schema: dict[str, Any]) -> dict[str, Any]:
    raise NotImplementedError("PLAN §3.15")


def unwrap_output(value: Any) -> Any:
    raise NotImplementedError("PLAN §3.15")


def strict_compatible(wrapped: dict[str, Any]) -> bool:
    raise NotImplementedError("PLAN §3.15")
