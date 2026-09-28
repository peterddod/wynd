"""Derived process status (PLAN §8.1 status row, §15 item 62; `$DRAFTS/06 §5.5`).

The process HEAD `H` is the last commit at or before the working tree's `HEAD` touching the process's reference
closure as `HEAD` holds it. Every per-commit input is read at `H` through `load_workspace(root, CommitTree(root, H))`:
design = `compile_state(ws_at_H, pid).design`; compiled = not design and `tests_status(...)["status"] == "passed"`;
built = `artefacts.get_build(pid, H)`; released = any enabled release document of the process in `ctx.docs`, each
listed with `behind = count_touching(X, H, closure)` for its commit X. Working-tree dirt is only reported in `dirty`
and never changes a flag; a process with no commit yet is in design with `head = None`. Never stored.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.controller import ControllerContext
    from wynd.controller.models import ProcessStatus
    from wynd.process.workspace import Workspace

TESTS = {"passed": "passed", "failed": "failed", "missing": "unknown"}


def derive_status(ctx: ControllerContext, ws: Workspace, pid: str) -> ProcessStatus:
    from wynd.controller.models import HeadInfo, ProcessStatus, ReleaseRef
    from wynd.controller.releases.store import RELEASES
    from wynd.process.compile import compile_state
    from wynd.process.git import count_touching, dirty_paths, log_paths
    from wynd.process.testing import tests_status
    from wynd.process.workspace import CommitTree

    dirty = dirty_paths(ctx.root, working_closure(ws, pid))
    found = process_head(ctx, pid)
    if found is None:
        return ProcessStatus(design=True, compiled=False, built=False, released=False, tests="unknown", dirty=dirty)
    head, closure = found
    commit = log_paths(ctx.root, closure, 1)[0]
    at_head = ctx.workspace(CommitTree(ctx.root, head))
    state = compile_state(at_head, pid)
    tests = TESTS[tests_status(ctx.stores.runs, at_head, pid, head)["status"]]
    docs = ctx.docs.list(RELEASES, where={"process_id": pid})
    releases = [
        ReleaseRef(
            id=doc["id"], commit=doc["commit"], short=doc["commit"][:7],
            behind=count_touching(ctx.root, doc["commit"], head, closure),
            trigger=doc["trigger"]["kind"], state=doc.get("state") or ("serving" if doc.get("enabled") else "stopped"),
        )
        for doc in docs
    ]
    return ProcessStatus(
        head=HeadInfo(commit=head, short=commit["short"], at=commit["at"], subject=commit["subject"]),
        design=state.design,
        compiled=not state.design and tests == "passed",
        built=ctx.artefacts.get_build(pid, head) is not None,
        released=any(doc.get("enabled") for doc in docs),
        tests=tests,
        design_steps=[step.name for step in state.steps if not step.compiled],
        releases=releases,
        dirty=dirty,
    )


def process_head(ctx: ControllerContext, pid: str) -> tuple[str, list[str]] | None:
    """(H, the reference closure at `HEAD`), or None when `HEAD` has no commit or does not contain the process."""
    from wynd.process.errors import GitError
    from wynd.process.git import closure_head, reference_closure
    from wynd.process.workspace import CommitTree

    try:
        tree = CommitTree(ctx.root, "HEAD")
    except GitError:
        return None                                   # unborn branch
    at_head = ctx.workspace(tree)
    if pid not in at_head.processes:
        return None
    closure = reference_closure(at_head, pid)
    head = closure_head(ctx.root, closure, tree.sha)
    return None if head is None else (head, closure)


def working_closure(ws: Workspace, pid: str) -> list[str]:
    """The reference closure in the working tree; just `wynd.yaml` and the process dir when it does not load."""
    from wynd.process.errors import LoadError
    from wynd.process.git import reference_closure
    from wynd.spec.workspace import WORKSPACE_FILE

    try:
        return reference_closure(ws, pid)
    except LoadError:
        return sorted({WORKSPACE_FILE, ws.processes[pid].dir})
