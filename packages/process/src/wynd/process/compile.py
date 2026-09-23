"""Compile state: the design/compiled inputs of derived status (PLAN §6.1, §15 item 62; owner PROC-GIT;
`$DRAFTS/04 §14`).

`compile_state` reads through the given workspace's Tree: callers pass a `CommitTree` workspace for per-commit status.
A process is in design when any closure step is not compiled (proto-only or stale), any child is in design, or an
agentic branch has a stale or missing `edges.lock.yaml` entry.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel

from wynd.spec.errors import SpecError
from wynd.spec.lockfiles import EdgesLock, branch_key, check_hash
from wynd.spec.process_doc import is_else
from wynd.spec.workspace import EDGES_LOCK_FILE
from wynd.spec.yamlio import parse_model

from .hashing import process_hash, step_hash
from .workspace import join, read_text

if TYPE_CHECKING:
    from .loader import LoadedProcess
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
    """The compile state of process `pid` as `ws`'s tree holds it. `steps` lists the process's own `steps:` entries
    (a `process:` entry is compiled iff its child is not in design); `process_hash` covers the whole closure."""
    return _state(ws, ws.load_process(pid), {})


def _state(ws: Workspace, lp: LoadedProcess, memo: dict[str, CompileState]) -> CompileState:
    if lp.id in memo:
        return memo[lp.id]
    steps: list[StepCompileState] = []
    reasons: list[str] = []
    for name, rs in lp.steps.items():
        entry = StepCompileState(name=name, use=rs.use, step_id=None, proto_hash=None, locked_proto_hash=None,
                                 step_hash=None, compiled=False)
        if rs.ref_kind == "process":
            child = lp.children.get(rs.child)
            if child is None:
                reasons.append(f"step {name}: process '{rs.child}' could not be loaded")
            elif _state(ws, child, memo).design:
                reasons.append(f"child {child.id} is in design")
            else:
                entry.compiled = True
        elif rs.package is None:
            reasons.append(f"step {name}: '{rs.use}' does not resolve to a step package or proto-step")
        else:
            pkg = rs.package
            entry.step_id, entry.proto_hash, entry.compiled = pkg.id, pkg.proto_hash, pkg.phase == "compiled"
            if pkg.lock is not None:
                entry.locked_proto_hash, entry.step_hash = pkg.lock.proto_hash, step_hash(ws.tree, pkg)
            if pkg.stale:
                reasons.append(f"step {name}: proto-step changed since compile")
            elif not entry.compiled:
                reasons.append(f"step {name}: proto-step not compiled")
        steps.append(entry)
    reasons += _edge_lock_reasons(ws, lp)
    memo[lp.id] = CompileState(design=bool(reasons), reasons=reasons, steps=steps,
                               process_hash=process_hash(ws.tree, lp))
    return memo[lp.id]


def _edge_lock_reasons(ws: Workspace, lp: LoadedProcess) -> list[str]:
    """One reason per agentic branch (a branch with `check:` on an agentic edge, up to the else) whose
    `edges.lock.yaml` entry is missing or was locked for a different check."""
    branches = []
    for edge in lp.doc.edges:
        if edge.kind != "agentic":
            continue
        for index, branch in enumerate(edge.to):
            if branch.check is not None:
                branches.append((branch_key(edge.from_, index, branch.name), branch))
            if is_else(branch):
                break                     # later branches are unreachable and dropped from the definition
    if not branches:
        return []
    path = join(lp.dir, EDGES_LOCK_FILE)
    lock = EdgesLock()
    if path in ws.tree.files():
        try:
            lock = parse_model(read_text(ws.tree, path), EdgesLock, path)
        except SpecError as err:
            return [f"{path} is invalid: {err.diagnostics[0].message}"]
    reasons = []
    for key, branch in branches:
        entry = lock.edges.get(key)
        if entry is None:
            reasons.append(f"agentic branch {key}: no {EDGES_LOCK_FILE} entry")
        elif entry.check_hash != check_hash(branch.check, branch.context):
            reasons.append(f"agentic branch {key}: check changed since it was locked")
    return reasons
