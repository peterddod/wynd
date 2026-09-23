"""`step.lock.yaml` construction and rendering, the step `pyproject.toml`, dependency locking, and the compiler's
provenance model for `StepLock.compiled` (`$DRAFTS/05 §9.1–§9.2`, PLAN §3.6, §7 item 1).

`StepLock.compiled` is an opaque dict to everyone else; only the compiler validates it, with `CompiledInfo`.
`retries = DEFAULT_RETRIES[kind]`; the compiler never writes `provider` (PLAN §15 item 19).
"""

from __future__ import annotations

from typing import Literal

from wynd.spec.base import SpecModel


class CompiledSplit(SpecModel):
    role: Literal["deterministic", "agentic"]
    node: str                        # this package's node in process.yaml
    partner: str                     # the other half's package dir name
    partner_node: str
    handled: list[int] = []          # example numbers (deterministic half)
    deferred: list[int] = []


class CompiledInfo(SpecModel):
    session: str
    job: str
    compiler: str                    # "wynd-compiler <version>"
    model: str                       # "<provider>/<tier>" used by the compiler for codegen
    decision: dict                   # {kind, rule, reason}
    inferred_interface: bool = False
    free_text: list[str] = []        # output fields tested for presence only
    guidance: list[str] = []         # clarification answers
    rejected: list[str] = []         # rejected edge-case questions
    split: CompiledSplit | None = None
