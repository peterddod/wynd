"""Prompt text shared by both provider kinds, so swapping providers never changes the prompt (PLAN §5.5;
`$DRAFTS/03 §6.3`, exact text)."""

from __future__ import annotations

import inspect
import json
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pydantic import ValidationError

SYSTEM_PREAMBLE = """\
You are one step in an automated, tested process. No human is available to answer questions.
Carry out the task below using only the tools provided, then reply with the step's structured output.
The `exit` field selects the outcome: choose the exit that matches what happened and fill in exactly that exit's fields.

# Task
"""

RETRY_VALIDATION = """\
Your structured output did not validate against the step's Output schema:
{errors}
Reply again with corrected structured output. Only call tools again if you need information you do not have."""
RETRY_NO_OUTPUT = "You replied without structured output. Reply with the step's structured output now."
RETRY_TRUNCATED = ("Your previous reply was cut off at the output token limit. "
                   "Reply again, more concisely, with the step's structured output.")

INPUT_REPR_LIMIT = 80


def render_prompt(
    instruction: str, context: Mapping[str, Any], input: Mapping[str, Any], *, raw_prompt: str | None = None
) -> tuple[str, str]:
    """-> (system, user). Deterministic: JSON with sort_keys, indent=2, ensure_ascii=False, default=str.

    Raw mode (`raw_prompt` set: the compiler, the chat) returns `instruction` and `raw_prompt` verbatim."""
    if raw_prompt is not None:
        return instruction, raw_prompt
    system = SYSTEM_PREAMBLE + inspect.cleandoc(instruction)
    parts = []
    if context:
        parts.append("# Context\n```json\n" + _json(context) + "\n```")
    parts.append("# Input\n```json\n" + _json(input) + "\n```")
    return system, "\n\n".join(parts)


def format_validation_errors(err: ValidationError, limit: int = 20) -> str:
    """One line per error, at most `limit`: `- output.<loc joined by '.'>: <msg> (got <repr(input)[:80]>)`."""
    lines = []
    for error in err.errors()[:limit]:
        where = ".".join(["output", *(str(part) for part in error["loc"])])
        lines.append(f"- {where}: {error['msg']} (got {repr(error.get('input'))[:INPUT_REPR_LIMIT]})")
    return "\n".join(lines)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=str)
