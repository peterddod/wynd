"""Name-level binding rules (PLAN §6.1, §6.3 pass 5; owner PROC-VAL).

`E210` entry Input <-> `process.inputs`; `E211` `$exit` binds exactly the declared fields; `E212` `with:` keys within
the target Input and covering its required fields (branches and `finally[].with`); `E215` finally inputs within
`process.inputs ∪ {run_id, exit}` (no `with`); `E223` every required Input field of the `on_error` step is a
`ProcessError` field name. Against an interface inferred from examples, E210/E212/E215/E223 are warnings with the
provisional suffix; against an unknown interface they are skipped (W129 says why).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wynd.spec.records import ProcessError
from wynd.spec.yamlio import located

from ..errors import CODES

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic, Loc
    from wynd.spec.process_doc import ProcessDoc

    from .structure import StepIface

PROVISIONAL = " [provisional: interface inferred from examples]"
PROCESS_ERROR_FIELDS = frozenset(ProcessError.model_fields)
FINALLY_NAMES = ("run_id", "exit")


def names(items: list[str]) -> str:
    return ", ".join(items) if items else "none"


def finding(code: str, variant: int, doc: ProcessDoc, loc: Loc, pid: str, precise: bool, **fields: str) -> Diagnostic:
    """A diagnostic from one alternative of the code's template ("A / B"); a warning with the provisional suffix when
    the interface was inferred from examples."""
    severity, template = CODES[code]
    message = template.split(" / ")[variant].format(**fields)
    if not precise:
        severity, message = "warning", message + PROVISIONAL
    return located(doc._source, severity, code, message, loc, process=pid)


def _fields(schema: dict) -> tuple[list[str], list[str]]:
    """(property names, required names) of an object schema."""
    return list(schema.get("properties", {})), list(schema.get("required", []))


def _with_findings(target: str, bound: list[str], iface: StepIface, doc: ProcessDoc, loc: Loc,
                   pid: str) -> list[Diagnostic]:
    properties, required = _fields(iface.input)
    out = []
    extra = [key for key in bound if key not in properties]
    if extra:
        out.append(finding("E212", 0, doc, loc, pid, iface.precise, target=target, extra=names(extra)))
    missing = [key for key in required if key not in bound]
    if missing:
        out.append(finding("E212", 1, doc, loc, pid, iface.precise, target=target, missing=names(missing)))
    return out


def check_bindings(pid: str, doc: ProcessDoc, ifaces: dict[str, StepIface]) -> list[Diagnostic]:
    """`doc` is the normalised definition (branches after an else are never bound)."""
    out: list[Diagnostic] = []
    entry = ifaces.get(doc.entry)
    if entry is not None and entry.input is not None:
        properties, required = _fields(entry.input)
        extra = [name for name in doc.inputs if name not in properties]
        if extra:
            out.append(finding("E210", 0, doc, ("inputs",), pid, entry.precise, extra=names(extra), entry=doc.entry))
        missing = [name for name in required if name not in doc.inputs]
        if missing:
            out.append(finding("E210", 1, doc, ("entry",), pid, entry.precise, missing=names(missing),
                               entry=doc.entry))

    for i, edge in enumerate(doc.edges):
        for j, branch in enumerate(edge.to):
            loc = ("edges", i, "to", j, "with")
            bound = list(branch.with_)
            exit = branch.exit_target
            if exit is not None and exit in doc.outputs:
                declared = list(doc.outputs[exit])
                missing = [name for name in declared if name not in bound]
                extra = [name for name in bound if name not in declared]
                if missing or extra:
                    out.append(finding("E211", 0, doc, loc, pid, True, exit=exit, declared=names(declared),
                                       missing=names(missing), extra=names(extra)))
            elif branch.step in ifaces and ifaces[branch.step].input is not None:
                out += _with_findings(branch.step, bound, ifaces[branch.step], doc, loc, pid)

    for i, item in enumerate(doc.finally_):
        iface = ifaces.get(item.step)
        if iface is None or iface.input is None:
            continue
        if item.with_:
            out += _with_findings(item.step, list(item.with_), iface, doc, ("finally", i, "with"), pid)
            continue
        available = {*doc.inputs, *FINALLY_NAMES}
        missing = [name for name in _fields(iface.input)[1] if name not in available]
        if missing:
            out.append(finding("E215", 0, doc, ("finally", i), pid, iface.precise, name=item.step,
                               missing=names(missing)))

    handler = ifaces.get(doc.on_error) if doc.on_error is not None else None
    if handler is not None and handler.input is not None:
        missing = [name for name in _fields(handler.input)[1] if name not in PROCESS_ERROR_FIELDS]
        if missing:
            out.append(finding("E223", 0, doc, ("on_error",), pid, handler.precise, step=doc.on_error,
                               missing=names(missing)))
    return out
