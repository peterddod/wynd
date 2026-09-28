"""M5 agentic-edge validation (PLAN §3.2, §6.1, §6.3 pass 7; owner EDGE-PROC, M5; `$DRAFTS/08 §4.3`).

Codes (verbatim from the draft): `E-CHECK-NOT-AGENTIC`, `E-AGENTIC-NO-CHECK`, `E-CHECK-EMPTY`, `E-CHECK-CONTEXT`,
`W-AGENTIC-NO-ELSE`, `W-AGENTIC-EDGE-FAST`, `W-EDGE-LOCK-MISSING`, `W-EDGE-LOCK-STALE`, `W-EDGE-LOCK-ORPHAN`,
`W-EDGE-PROVIDER-UNKNOWN`.

Readings of this plan:
- Context entries use the spec pull-context grammar (`wynd.spec.context`, PLAN §15 item 26), which also accepts
  `process.inputs` and field paths below `steps.<k>.outputs`. "Completed on every path" is the dataflow state the
  branch's check runs in (after its `when:` atoms refine it): the step must not be `NOT_RUN` there.
- The check runs only for this process (`lp`): `W-AGENTIC-EDGE-FAST` uses the process's own `latency:`, and
  `W-EDGE-PROVIDER-UNKNOWN` covers a lock entry's `provider:` override; with no override the branch uses the ROOT's
  default provider, which `W207` already covers.
- Lock findings (`W-EDGE-LOCK-*`) consider the agentic branches of `edges_lock.agentic_branches` (checks up to the
  else); a lock entry of any other branch is an orphan.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from wynd.spec.context import parse_context_entry
from wynd.spec.errors import Diagnostic
from wynd.spec.lockfiles import branch_key, check_hash
from wynd.spec.process_doc import is_else
from wynd.spec.workspace import EDGES_LOCK_FILE
from wynd.spec.yamlio import located

from ..edges_lock import agentic_branches

if TYPE_CHECKING:
    from wynd.spec.errors import Loc, Severity
    from wynd.spec.lockfiles import EdgesLock
    from wynd.spec.process_doc import ProcessDoc

    from ..loader import LoadedProcess

MAX_CHECK_CHARS = 2000

CODES: dict[str, tuple[Severity, str]] = {
    "E-CHECK-NOT-AGENTIC": ("error", "branch '{key}' has {fields} but edge '{edge}' is not kind: agentic"),
    "E-AGENTIC-NO-CHECK": ("error", "agentic edge '{edge}' has no branch with check:"),
    "E-CHECK-EMPTY": ("error", "check of branch '{key}' is {problem}"),
    "E-CHECK-CONTEXT": ("error", "branch '{key}': {problem}"),
    "W-AGENTIC-NO-ELSE": (
        "warning", "agentic edge '{edge}' has no else branch: a negative verdict routes to the process error handler",
    ),
    "W-AGENTIC-EDGE-FAST": (
        "warning", "process is latency: fast but edge '{edge}' is agentic (a model call per check on the fast path)",
    ),
    "W-EDGE-LOCK-MISSING": (
        "warning",
        "agentic branch '{key}' has no entry in edges.lock.yaml: defaults used at run time (cheap, low, 2 retries); "
        "run `wynd compile` to lock",
    ),
    "W-EDGE-LOCK-STALE": (
        "warning",
        "agentic branch '{key}': check edited since it was locked; process cassettes will miss; re-run "
        "`wynd compile` or `wynd test --live`",
    ),
    "W-EDGE-LOCK-ORPHAN": ("warning", "edges.lock.yaml entry '{key}' has no matching agentic branch"),
    "W-EDGE-PROVIDER-UNKNOWN": (
        "warning", "agentic branch '{key}': provider '{provider}' is not installed (entry point group wynd.providers)",
    ),
}


def _finding(code: str, source: Any, loc: Loc, pid: str, file: str | None = None, **fields: Any) -> Diagnostic:
    """A diagnostic from `CODES`, located through `source`; `file` when the document has no source map."""
    severity, template = CODES[code]
    message = template.format(**fields)
    if source is None:
        return Diagnostic(severity, code, message, file=file, loc=tuple(loc), process=pid)
    return located(source, severity, code, message, loc, process=pid)


def _installed(provider: str) -> bool:
    from wynd.runtime.providers import provider_info

    try:
        provider_info(provider)
    except KeyError:
        return False
    return True


def _check_states(lp: LoadedProcess) -> dict[tuple[int, int], dict[str, frozenset[str]]]:
    """(edge index, branch index) -> the dataflow state a branch's check runs in (its `when:` holds); absent when
    the branch cannot be taken or comes after its edge's else."""
    from .dataflow import analyse
    from .structure import normalise, step_ifaces

    return analyse(normalise(lp.doc), step_ifaces(lp)).with_


def _check_problem(check: str | None) -> str | None:
    if check is None:
        return None
    text = check.strip()
    if not text:
        return "blank"
    if len(text) > MAX_CHECK_CHARS:
        return f"{len(text)} characters long (at most {MAX_CHECK_CHARS})"
    return None


def _context_problem(entry: str, doc: ProcessDoc, edge: str, state: dict[str, frozenset[str]] | None) -> str | None:
    from .dataflow import NOT_RUN

    try:
        ref = parse_context_entry(entry)
    except ValueError as err:
        return str(err)
    if ref.step is None:
        return None
    if ref.step not in doc.steps:
        return f"context entry '{entry}' names unknown step '{ref.step}' (steps: {', '.join(doc.steps)})"
    if state is not None and NOT_RUN in state.get(ref.step, frozenset()):
        return f"context entry '{entry}': step '{ref.step}' has not completed on every path reaching '{edge}'"
    return None


def check_agentic_edges(lp: LoadedProcess, edges_lock: EdgesLock) -> list[Diagnostic]:
    pid, doc = lp.id, lp.doc
    source = doc._source
    out: list[Diagnostic] = []

    states = _check_states(lp) if any(branch.context for edge in doc.edges for branch in edge.to) else {}

    for i, edge in enumerate(doc.edges):
        agentic = edge.kind == "agentic"
        for j, branch in enumerate(edge.to):
            key = branch_key(edge.from_, j, branch.name)
            base = ("edges", i, "to", j)
            fields = [name for name in ("check", "context") if getattr(branch, name) is not None]
            if fields and not agentic:
                out.append(_finding("E-CHECK-NOT-AGENTIC", source, (*base, fields[0]), pid, key=key,
                                    fields=" and ".join(f"{name}:" for name in fields), edge=edge.from_))
            problem = _check_problem(branch.check)
            if problem is not None:
                out.append(_finding("E-CHECK-EMPTY", source, (*base, "check"), pid, key=key, problem=problem))
            for k, entry in enumerate(branch.context or []):
                problem = _context_problem(entry, doc, edge.from_, states.get((i, j)))
                if problem is not None:
                    out.append(_finding("E-CHECK-CONTEXT", source, (*base, "context", k), pid, key=key,
                                        problem=problem))
        if not agentic:
            continue
        if all(branch.check is None for branch in edge.to):
            out.append(_finding("E-AGENTIC-NO-CHECK", source, ("edges", i, "kind"), pid, edge=edge.from_))
        if not any(is_else(branch) for branch in edge.to):
            out.append(_finding("W-AGENTIC-NO-ELSE", source, ("edges", i, "to"), pid, edge=edge.from_))
        if doc.latency == "fast":
            out.append(_finding("W-AGENTIC-EDGE-FAST", source, ("edges", i, "kind"), pid, edge=edge.from_))

    lock_source = edges_lock._source
    lock_file = f"{lp.dir}/{EDGES_LOCK_FILE}"
    locked: set[str] = set()
    for i, j, key, branch in agentic_branches(doc):
        locked.add(key)
        loc = ("edges", i, "to", j, "check")
        entry = edges_lock.edges.get(key)
        if entry is None:
            out.append(_finding("W-EDGE-LOCK-MISSING", source, loc, pid, key=key))
            continue
        if entry.check_hash != check_hash(branch.check, branch.context):
            out.append(_finding("W-EDGE-LOCK-STALE", source, loc, pid, key=key))
        if entry.provider is not None and not _installed(entry.provider):
            out.append(_finding("W-EDGE-PROVIDER-UNKNOWN", lock_source, ("edges", key, "provider"), pid,
                                file=lock_file, key=key, provider=entry.provider))
    for key in edges_lock.edges:
        if key not in locked:
            out.append(_finding("W-EDGE-LOCK-ORPHAN", lock_source, ("edges", key), pid, file=lock_file, key=key))
    return out
