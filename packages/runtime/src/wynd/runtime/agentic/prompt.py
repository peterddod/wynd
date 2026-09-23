"""Prompt text shared by both provider kinds, so swapping providers never changes the prompt (PLAN §5.5;
`$DRAFTS/03 §6.3`, exact text)."""

from __future__ import annotations

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


def render_prompt(
    instruction: str, context: Mapping[str, Any], input: Mapping[str, Any], *, raw_prompt: str | None = None
) -> tuple[str, str]:
    """-> (system, user)."""
    raise NotImplementedError("PLAN §5.5")


def format_validation_errors(err: ValidationError, limit: int = 20) -> str:
    raise NotImplementedError("PLAN §5.5")
