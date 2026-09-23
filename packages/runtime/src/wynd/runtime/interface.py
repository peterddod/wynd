"""Step and process interfaces as the runtime sees them (PLAN §3.4, §5.1; `$DRAFTS/02 §3.2`).

The Output rules have one implementation: `wynd.spec.interface.split_output` / `output_adapter`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.step import Step
    from wynd.spec.process_doc import ProcessDoc


class StepInterface:
    """Declared exits, the discriminated output adapter and the input model of one step class (cached)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


class StepDescription:
    """Worker `describe` payload: `{id, cls, kind, doc, input_schema, input_fields, required_inputs, exits,
    context, agentic, shell}` (PLAN §3.12)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.12")


def interface_of(cls: type[Step]) -> StepInterface:
    raise NotImplementedError("PLAN §5.1")


def process_interface(process_id: str, doc: ProcessDoc) -> StepInterface:
    """Wraps `ProcessDoc.interface()`."""
    raise NotImplementedError("PLAN §5.1")


def describe_step(cls: type[Step], step_id: str) -> StepDescription:
    raise NotImplementedError("PLAN §5.1")


def describe_process(process_id: str, doc: ProcessDoc) -> StepDescription:
    raise NotImplementedError("PLAN §5.1")
