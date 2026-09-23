"""Structural rules (PLAN §6.1, §6.3 pass 2; owner PROC-VAL).

`E204` (edge exit not on the source step's interface), `E208` (unrouted non-error exit; `$ignore` counts as routed),
`E219` (`on_error` step exit not a process output exit). Document-only rules live in spec `check_process_doc`.

Also the two views every later pass shares: `StepIface` (a step key's Input schema and exits, `None` when unknown)
and `normalise` (the definition with branches after the else dropped).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wynd.spec.base import RESERVED_EXIT
from wynd.spec.process_doc import Branch, Edge, ProcessDoc, is_else

from ..workspace import diagnostic

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic

    from ..loader import LoadedProcess


@dataclass(frozen=True)
class StepIface:
    input: dict | None                    # JSON Schema of the Input; None when unknown
    exits: dict[str, dict] | None         # exit -> JSON Schema, incl. the implicit "error"; None when unknown
    precise: bool                         # False: inferred from examples (field-level findings are warnings)


UNKNOWN = StepIface(None, None, True)


def step_ifaces(lp: LoadedProcess) -> dict[str, StepIface]:
    """Every `steps:` key of `lp` -> its interface; a `process:` reference uses the child's declared interface."""
    out: dict[str, StepIface] = {}
    for key, rs in lp.steps.items():
        interface, precise = None, True
        if rs.ref_kind == "process":
            child = lp.children.get(rs.child)
            interface = child.doc.interface() if child is not None else None
        elif rs.package is not None:
            interface, precise = rs.package.interface.interface, rs.package.interface.precise
        out[key] = UNKNOWN if interface is None else StepIface(interface.input, interface.with_error(), precise)
    return out


def effective(edge: Edge) -> list[Branch]:
    """The branches that can be taken: up to and including the first else."""
    for j, branch in enumerate(edge.to):
        if is_else(branch):
            return edge.to[: j + 1]
    return edge.to


def normalise(doc: ProcessDoc) -> ProcessDoc:
    """A deep copy with the branches after each edge's else dropped (the runtime ignores them too)."""
    norm = doc.model_copy(deep=True)
    for edge in norm.edges:
        edge.to = effective(edge)
    return norm


def check_structure(pid: str, doc: ProcessDoc, ifaces: dict[str, StepIface]) -> list[Diagnostic]:
    source = doc._source
    out: list[Diagnostic] = []
    routed: set[tuple[str, str]] = set()
    for i, edge in enumerate(doc.edges):
        step, exit = edge.source_step, edge.source_exit
        routed.add((step, exit))
        exits = ifaces[step].exits if step in ifaces else None
        if exits is None or exit in exits:
            continue
        out.append(diagnostic("E204", source=source, loc=("edges", i, "from"), process=pid, step=step, exit=exit,
                              exits=", ".join(exits), **{"from": edge.from_}))

    handlers = {doc.on_error, *(item.step for item in doc.finally_)}
    for key, iface in ifaces.items():
        if key in handlers or iface.exits is None:
            continue
        for exit in iface.exits:
            if exit != RESERVED_EXIT and (key, exit) not in routed:
                out.append(diagnostic("E208", source=source, loc=("steps", key), process=pid, step=key, exit=exit))

    handler = ifaces.get(doc.on_error) if doc.on_error is not None else None
    if handler is not None and handler.exits is not None:
        for exit in handler.exits:
            if exit != RESERVED_EXIT and exit not in doc.exits:
                out.append(diagnostic("E219", source=source, loc=("on_error",), process=pid, name=doc.on_error,
                                      exit=exit))
    return out
