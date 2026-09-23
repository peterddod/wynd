"""Compile state: the design/compiled inputs of derived status (PLAN §6.1, §15 item 62; owner PROC-GIT;
`$DRAFTS/04 §14`).

`compile_state` reads through the given workspace's Tree: callers pass a `CommitTree` workspace for per-commit status.
A process is in design when any closure step is not compiled (proto-only or stale), any child is in design, or an
agentic branch has a stale or missing `edges.lock.yaml` entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

if TYPE_CHECKING:
    from .workspace import Workspace


class StepCompileState(BaseModel):
    name: str
    use: str
    step_id: str | None
    proto_hash: str | None
    locked_proto_hash: str | None
    step_hash: str | None
    compiled: bool                        # phase == "compiled"


class CompileState(BaseModel):
    design: bool                          # any closure step not compiled, any child in design, or a stale edge lock
    reasons: list[str]                    # "step extract: proto-step changed since compile", "child finance/x is in design", …
    steps: list[StepCompileState]
    process_hash: str


def compile_state(ws: Workspace, pid: str) -> CompileState:
    raise NotImplementedError("PLAN §6.1 compile_state")
