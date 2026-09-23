"""Deterministic step summaries (SPEC §3.6 step 5, PLAN §5.1; `$DRAFTS/02 §4.4`): never a model call; 200-char
cap; error summaries omit traceback/inputs/partial_outputs/child."""

from __future__ import annotations

import json
from typing import Any

from wynd.spec.base import RESERVED_EXIT
from wynd.spec.records import Summary

MAX_CHARS = 200
ERROR_OMITTED = ("traceback", "inputs", "partial_outputs", "child")


def summarise(step: str, exit: str, outputs: dict[str, Any], note: str = "") -> Summary:
    """Project every output field (declaration order kept); `outputs` are JSON-mode and exclude "exit"."""
    omitted = ERROR_OMITTED if exit == RESERVED_EXIT else ()
    key_outputs = {name: project(value) for name, value in outputs.items() if name not in omitted}
    return Summary(step=step, exit=exit, key_outputs=key_outputs, note=note)


def project(value: Any) -> Any:
    """Scalars as is; strings cut to 200 chars; a container whose JSON exceeds 200 chars becomes a marker."""
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_CHARS else value[: MAX_CHARS - 3] + "..."
    text = json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
    if len(text) <= MAX_CHARS:
        return value
    if isinstance(value, list):
        return f"<list: {len(value)} items>"
    if isinstance(value, dict):
        return f"<object: {len(value)} keys>"
    return f"<{type(value).__name__}>"
