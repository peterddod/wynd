"""Graph validation of a loaded process and its reference closure (PLAN §6.3; owner PROC-VAL).

Pass order per closure process (memoised): loader diagnostics + spec `check_process_doc`; structure; reachability;
cycles; bindings; dataflow + expression walk (then `typecheck.check_types`); child/provider rules, runtime lint and
`agentic.check_agentic_edges`; `latency.latency_warnings` when `stats` is given. Closure-wide on the root: W206.

The file-based rules (W128, runtime lint, `edges.lock.yaml` for the M5 hook) read through the workspace's `Tree`:
`validate_process` passes `ws.tree`; `validate(lp)` uses the tree the loader attached to `lp` as `_tree`, and skips
those rules when there is none.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from wynd.spec.errors import Diagnostic
from wynd.spec.process_doc import ProcessDoc

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo

    from ..loader import LoadedProcess
    from ..optimise import ProcessStats
    from ..workspace import Tree, Workspace


class ValidationReport(BaseModel):
    process: str
    diagnostics: list[Diagnostic]                        # sorted (process, file, line, code)
    normalized: dict[str, ProcessDoc]                    # per closure process: to-lists, max_traversals filled,
                                                         # after-else dropped
    env_refs: dict[str, list[str]]                       # env var -> ["edge:<pid>:<branch_key>.<field>", …]

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def validate(
    lp: LoadedProcess,
    *,
    providers: Callable[[str], ProviderInfo] | None = None,
    stats: ProcessStats | None = None,
) -> ValidationReport:
    return _validate(lp, getattr(lp, "_tree", None), providers=providers, stats=stats)


def validate_process(ws: Workspace, pid: str, **kw: Any) -> ValidationReport:
    """`validate(ws.load_process(pid), **kw)`, reading step files through `ws.tree`. Raises ProcessNotFound."""
    return _validate(ws.load_process(pid), ws.tree, **kw)


def format_diagnostic(d: Diagnostic) -> str:
    """`Diagnostic.format()`, prefixed with `[<process>]` when the finding has no file."""
    text = d.format()
    return f"[{d.process}] {text}" if d.file is None and d.process else text


def _sort_key(d: Diagnostic) -> tuple:
    return d.process or "", d.file or "", d.line or 0, d.code


def _validate(
    lp: LoadedProcess,
    tree: Tree | None,
    *,
    providers: Callable[[str], ProviderInfo] | None = None,
    stats: ProcessStats | None = None,
) -> ValidationReport:
    from . import rules

    if providers is None:
        from wynd.runtime.providers import provider_info as providers

    diagnostics: list[Diagnostic] = []
    normalized: dict[str, ProcessDoc] = {}
    env_refs: dict[str, list[str]] = {}
    for pid, process in lp.closure_processes().items():
        norm, found, refs = _validate_one(process, lp, tree)
        normalized[pid] = norm
        diagnostics += found
        for name, usages in refs.items():
            known = env_refs.setdefault(name, [])
            known += [usage for usage in usages if usage not in known]
    diagnostics += rules.provider_rules(lp, providers)
    if tree is not None:
        diagnostics += rules.package_rules(lp, tree)
    if stats is not None:
        from ..latency import latency_warnings

        diagnostics += latency_warnings(lp, stats)
    return ValidationReport(process=lp.id, diagnostics=sorted(diagnostics, key=_sort_key), normalized=normalized,
                            env_refs=env_refs)


def _validate_one(
    lp: LoadedProcess, root: LoadedProcess, tree: Tree | None
) -> tuple[ProcessDoc, list[Diagnostic], dict[str, list[str]]]:
    """Passes 1–7 for one closure process: (normalised definition, diagnostics, env_refs)."""
    from wynd.spec.process_doc import check_process_doc

    from . import agentic, rules, typecheck
    from .bindings import check_bindings
    from .cycles import fill_cycles
    from .dataflow import analyse, typed_sites
    from .exprcheck import check_sites
    from .reach import check_reach
    from .structure import check_structure, normalise, step_ifaces

    pid, doc = lp.id, lp.doc
    found = list(lp.diagnostics) + [replace(d, process=pid) for d in check_process_doc(doc)]
    ifaces = step_ifaces(lp)
    norm = normalise(doc)
    found += check_structure(pid, norm, ifaces)
    found += check_reach(pid, doc)
    found += fill_cycles(pid, norm)
    found += check_bindings(pid, norm, ifaces)
    sites = typed_sites(pid, norm, analyse(norm, ifaces), ifaces)
    references, env_refs = check_sites(pid, norm, sites, ifaces)
    found += references
    found += typecheck.check_types(lp, sites)
    found += rules.binding_rules(lp, root)
    edges_lock, lock_found = _edges_lock(lp, tree)
    found += lock_found
    found += agentic.check_agentic_edges(lp, edges_lock)
    return norm, found, env_refs


def _edges_lock(lp: LoadedProcess, tree: Tree | None) -> tuple[Any, list[Diagnostic]]:
    """The process's `edges.lock.yaml` read through `tree` (empty when missing or without a tree), and the spec
    diagnostics of a lock that does not load."""
    from wynd.spec.errors import SpecError
    from wynd.spec.lockfiles import EdgesLock
    from wynd.spec.workspace import EDGES_LOCK_FILE
    from wynd.spec.yamlio import parse_model

    from ..workspace import read_text

    path = f"{lp.dir}/{EDGES_LOCK_FILE}"
    if tree is None or path not in tree.files():
        return EdgesLock(), []
    try:
        return parse_model(read_text(tree, path), EdgesLock, path), []
    except SpecError as err:
        return EdgesLock(), [replace(d, process=lp.id) for d in err.diagnostics]
