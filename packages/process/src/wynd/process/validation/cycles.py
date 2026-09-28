"""Cycles and `max_traversals` (PLAN §6.1, §6.3 pass 4, §15 item 8; owner PROC-VAL).

Tarjan SCC over step keys; every branch whose endpoints share an SCC gets `max_traversals = DEFAULT_MAX_TRAVERSALS`
in the normalised definition only (never written to YAML), reported as `I201` per branch key. SCC membership does not
depend on visiting order, so neither does the fill.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from typing import TYPE_CHECKING

from wynd.spec.base import DEFAULT_MAX_TRAVERSALS
from wynd.spec.lockfiles import branch_key
from wynd.spec.process_doc import Limits

from ..workspace import diagnostic

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic
    from wynd.spec.process_doc import ProcessDoc


def strongly_connected(nodes: Iterable[str], successors: Mapping[str, Sequence[str]]) -> dict[str, int]:
    """Iterative Tarjan: node -> component number (nodes of one strongly connected component share it)."""
    index: dict[str, int] = {}
    low: dict[str, int] = {}
    component: dict[str, int] = {}
    stack: list[str] = []
    on_stack: set[str] = set()
    count = 0

    def visit(node: str) -> None:
        index[node] = low[node] = len(index)
        stack.append(node)
        on_stack.add(node)

    for root in nodes:
        if root in index:
            continue
        visit(root)
        work = [(root, iter(successors.get(root, ())))]
        while work:
            node, pending = work[-1]
            for succ in pending:
                if succ not in index:
                    visit(succ)
                    work.append((succ, iter(successors.get(succ, ()))))
                    break
                if succ in on_stack:
                    low[node] = min(low[node], index[succ])
            else:
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    while True:
                        member = stack.pop()
                        on_stack.discard(member)
                        component[member] = count
                        if member == node:
                            break
                    count += 1
    return component


def fill_cycles(pid: str, norm: ProcessDoc) -> list[Diagnostic]:
    """Fill `max_traversals` on every in-SCC branch of the normalised definition `norm` (mutated); one I201 each."""
    successors: dict[str, list[str]] = {}
    for edge in norm.edges:
        if edge.source_step in norm.steps:
            successors.setdefault(edge.source_step, []).extend(b.step for b in edge.to if b.step in norm.steps)
    component = strongly_connected(norm.steps, successors)

    out: list[Diagnostic] = []
    for i, edge in enumerate(norm.edges):
        source = edge.source_step
        for j, branch in enumerate(edge.to):
            if source not in component or component.get(branch.step) != component[source]:
                continue
            if branch.limits is None:
                branch.limits = Limits(max_traversals=DEFAULT_MAX_TRAVERSALS)
            elif branch.limits.max_traversals is None:
                branch.limits = branch.limits.model_copy(update={"max_traversals": DEFAULT_MAX_TRAVERSALS})
            else:
                continue
            out.append(diagnostic("I201", source=norm._source, loc=("edges", i, "to", j), process=pid,
                                  target=branch.step, n=DEFAULT_MAX_TRAVERSALS,
                                  **{"from": branch_key(edge.from_, j, branch.name)}))
    return out
