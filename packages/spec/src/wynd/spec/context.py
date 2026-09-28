"""Pull-context entries of agentic steps and agentic branches (PLAN §4.1; $DRAFTS/01 §7.9, SPEC §3.7)."""

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class ContextRef:
    kind: Literal[
        "full_trace", "process.goal", "process.inputs", "previous.summary", "previous.outputs", "step.outputs",
        "step.summary",
    ]
    step: str | None = None  # step key for "step.outputs" / "step.summary"
    path: tuple[str, ...] = ()  # field path below outputs


_FIXED = ("full_trace", "process.goal", "process.inputs", "previous.summary", "previous.outputs")
_STEP = re.compile(r"^steps\.([a-z_][a-z0-9_]*)\.(outputs|summary)((?:\.[A-Za-z_][A-Za-z0-9_]*)*)$")
_ACCEPTED = (
    "full_trace, process.goal, process.inputs, previous.summary, previous.outputs, steps.<step>.outputs[.<field>…] "
    "or steps.<step>.summary"
)


def parse_context_entry(text: str) -> ContextRef:
    """full_trace, process.goal, process.inputs, previous.summary, previous.outputs, steps.<k>.outputs[.<f>…],
    steps.<k>.summary; ValueError (E-CONTEXT) otherwise."""
    entry = text.strip() if isinstance(text, str) else text
    if entry in _FIXED:
        return ContextRef(entry)
    match = _STEP.match(entry) if isinstance(entry, str) else None
    if match is None or (match.group(2) == "summary" and match.group(3)):
        raise ValueError(f"invalid context entry {text!r}: expected {_ACCEPTED}")
    step, member, rest = match.groups()
    path = tuple(rest.split(".")[1:])
    return ContextRef("step.outputs" if member == "outputs" else "step.summary", step, path)
