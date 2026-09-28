"""M3 type checks over the typed sites (PLAN §6.1, §6.3 pass 6; owner PROC-TYPES).

Every expression site: `infer_type` in the site's exit-aware scope (`E-TYPE-OP`, `W-TYPE-NULL`, located inside the
expression). Every site with a destination schema: `check_assignable(src, site.dst_schema)`, where `src` is the
inferred type or, for synthetic sites, `site.src_schema` — `with:` -> target Input, `$exit` -> process outputs, entry
inputs, `finally` inputs, on_error Input (`ProcessError`) and handler exits. An error is `E-TYPE-ASSIGN`; a warning
caused only by nullability is `W-TYPE-NULL`, any other warning `W-TYPE-ASSIGN`.

Findings already made elsewhere are not repeated: an expression with a reference error (`E-REF-*`, e.g. an
unguarded read of a step that may not have run) is not type-checked, and name-level findings stay with the M1 binding
rules: the on_error Input is checked field by field over the fields
`ProcessError` has (E223 reports required fields it lacks), and a binding without a destination schema (an unknown
field, E212) is skipped.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

from wynd.spec.errors import format_loc
from wynd.spec.expr.analysis import check_references
from wynd.spec.expr.infer import check_assignable, infer_type
from wynd.spec.records import ProcessError
from wynd.spec.schemas import normalize_schema
from wynd.spec.yamlio import located

from .bindings import FINALLY_NAMES
from .exprcheck import locate

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic
    from wynd.spec.process_doc import ProcessDoc

    from ..loader import LoadedProcess
    from .exprcheck import TypedSite

PROCESS_ERROR = normalize_schema(ProcessError.model_json_schema())
NULL_ONLY = "may be null"


def check_types(lp: LoadedProcess, sites: Sequence[TypedSite]) -> list[Diagnostic]:
    doc = lp.doc
    out: list[Diagnostic] = []
    by_name: dict[int, int] = {}                          # finally index -> by-name sites seen so far
    for site in sites:
        src = site.src_schema
        if site.expr is not None:
            if any(d.severity == "error" for d in check_references(site.expr, site.env)):
                continue                                  # already reported; its types would only repeat it
            src, found = infer_type(site.expr, site.env)
            out += [locate(doc._source, site.loc, site.expr, d, lp.id) for d in found]
        k = None
        if site.target == "finally" and site.expr is None:
            k = by_name.get(site.loc[1], 0)
            by_name[site.loc[1]] = k + 1
        if site.dst_schema is None or src is None:
            continue
        if site.target == "on_error":
            out += _on_error(lp, site)
            continue
        subject = _subject(doc, site) if k is None else _finally_subject(lp, doc, site.loc[1], k)
        level, detail = check_assignable(src, site.dst_schema)
        if level != "ok":
            out.append(_finding(lp, site, level, detail, subject))
    return out


def _finding(lp: LoadedProcess, site: TypedSite, level: str, detail: str, subject: str) -> Diagnostic:
    if level == "error":
        severity, code = "error", "E-TYPE-ASSIGN"
    elif all(part.endswith(NULL_ONLY) for part in detail.split("; ")):
        severity, code = "warning", "W-TYPE-NULL"
    else:
        severity, code = "warning", "W-TYPE-ASSIGN"
    return located(lp.doc._source, severity, code, f"{subject}: {detail}", site.loc, process=lp.id)


def _subject(doc: ProcessDoc, site: TypedSite) -> str:
    """What the value is bound to, for the message."""
    loc = site.loc
    match site.target:
        case "with":
            field = format_loc(loc[5:])
            return f"input '{field}' of step '{doc.edges[loc[1]].to[loc[3]].step}'"
        case "exit":
            field = format_loc(loc[5:])
            return f"output '{field}' of {doc.edges[loc[1]].to[loc[3]].step}"
        case "entry":
            return f"process input '{loc[1]}' -> input '{loc[1]}' of entry step '{doc.entry}'"
        case "handler_exit":
            exit = site.dst_schema.get("properties", {}).get("exit", {}).get("const", "?")
            return f"on_error step '{doc.on_error}' exit '{exit}' -> process output '{exit}'"
        case "finally":
            field = format_loc(loc[3:])
            return f"input '{field}' of finally step '{doc.finally_[loc[1]].step}'"
    return format_loc(loc)


def _finally_subject(lp: LoadedProcess, doc: ProcessDoc, i: int, k: int) -> str:
    """The k-th by-name binding of `finally[i]`: the dataflow emits one site per Input property that is a process
    input or a finally name, in property order."""
    from .structure import step_ifaces

    step = doc.finally_[i].step
    iface = step_ifaces(lp).get(step)
    properties = (iface.input or {}).get("properties", {}) if iface is not None else {}
    names = [name for name in properties if name in doc.inputs or name in FINALLY_NAMES]
    field = names[k] if k < len(names) else "?"
    source = f"process input '{field}'" if field in doc.inputs else f"'{field}'"
    return f"{source} -> input '{field}' of finally step '{step}'"


def _on_error(lp: LoadedProcess, site: TypedSite) -> list[Diagnostic]:
    """The handler's Input against `ProcessError`, field by field over the fields both declare."""
    offered = PROCESS_ERROR["properties"]
    wanted = normalize_schema(site.dst_schema).get("properties", {})
    out = []
    for name, schema in wanted.items():
        if name not in offered:
            continue
        level, detail = check_assignable(offered[name], schema)
        if level != "ok":
            subject = f"on_error step '{lp.doc.on_error}' input '{name}' <- ProcessError.{name}"
            out.append(_finding(lp, site, level, detail, subject))
    return out
