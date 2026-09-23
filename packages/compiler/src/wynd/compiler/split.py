"""The rule-3 two-step split: rewriting `process.yaml` between `# wynd:split <node> begin/end` markers
(`$DRAFTS/05 §10`)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.process_doc import ProcessDoc


@dataclass
class SplitPlan:
    node: str                    # extract
    agentic_node: str            # extract_agentic
    agentic_use: str             # ./steps/extract_invoice_fields_agentic
    input_fields: list[str]      # the Input field names


@dataclass
class RemoveSplit:
    node: str                    # the deterministic node whose earlier split is undone


def apply_split(text: str, doc: ProcessDoc, plan: SplitPlan) -> str:
    raise NotImplementedError("PLAN §7")


def remove_split(text: str, doc: ProcessDoc, node: str) -> str:
    raise NotImplementedError("PLAN §7")


def rename_step_refs(expr: str, old: str, new: str) -> str:
    """Rename `steps.<old>.*` and `edges["<old>.*"]` via `wynd.spec.expr` reference positions."""
    raise NotImplementedError("PLAN §7")
