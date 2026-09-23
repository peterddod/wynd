"""Pull-context entries of agentic steps and agentic branches (PLAN §4.1; $DRAFTS/01 §7.9, SPEC §3.7)."""

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


def parse_context_entry(text: str) -> ContextRef:
    """full_trace, process.goal, process.inputs, previous.summary, previous.outputs, steps.<k>.outputs[.<f>…],
    steps.<k>.summary; ValueError (E-CONTEXT) otherwise."""
    raise NotImplementedError("PLAN §4.1")
