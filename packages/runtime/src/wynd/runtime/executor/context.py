"""Pull-based structured context for agentic steps and edge checks (SPEC §3.7, PLAN §5.4, §15 item 26):
`full_trace` = this instance's history; other entries read the scope; unavailable -> null."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wynd.spec.context import ContextRef, parse_context_entry

if TYPE_CHECKING:
    from wynd.runtime.executor.instance import Instance, RunCtx


def assemble_context(entries: list[str], instance: Instance, ctx: RunCtx) -> dict[str, Any]:
    """`{entry: value}` for each declared entry (ValueError for an entry that is not a context reference)."""
    return {entry: _value(parse_context_entry(entry), instance) for entry in entries}


def _value(ref: ContextRef, instance: Instance) -> Any:
    scope = instance.scope
    match ref.kind:
        case "full_trace":
            return _full_trace(instance)
        case "process.goal":
            return instance.plan.definition.goal
        case "process.inputs":
            return scope.process_inputs
        case "previous.summary" | "previous.outputs":
            if scope.previous is None:
                return None
            state = scope.steps[scope.previous]
            return state.summary if ref.kind == "previous.summary" else state.outputs
        case "step.summary":
            state = scope.steps.get(ref.step)
            return state.summary if state else None
        case "step.outputs":
            state = scope.steps.get(ref.step)
            value = state.outputs if state else None
            for key in ref.path:
                value = value.get(key) if isinstance(value, dict) else None
            return value
    raise AssertionError(ref.kind)


def _full_trace(instance: Instance) -> dict[str, Any]:
    doc = instance.plan.definition
    return {
        "process": {"name": doc.name, "goal": doc.goal, "inputs": instance.scope.process_inputs},
        "steps": [
            {"step": rec.path, "run": rec.run, "exit": rec.exit, "inputs": rec.inputs, "outputs": rec.outputs,
             "summary": rec.summary.model_dump(mode="json")}
            for rec in instance.history
        ],
    }
