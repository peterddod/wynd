"""Expression reference checks at every typed site (PLAN §6.1, §6.2, §6.3 pass 6; owner PROC-VAL).

Per-site `TypeEnv` from the dataflow state -> `wynd.spec.expr.check_references`; field-level findings (`E-REF-FIELD`,
`E-REF-EXIT`) against `precise=False` interfaces become warnings with the suffix
` [provisional: interface inferred from examples]`; records `env_refs`. The process-independent findings
(`E-EXPR-*`, `E-REF-NAME`, `E-REF-SHAPE`) come from spec `check_process_doc` in pass 1 and are not repeated here.
`check_expr_at` serves the web editor (dataflow over the in-memory doc).
"""

from __future__ import annotations

import copy
import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any, Literal

from wynd.spec.errors import Diagnostic, format_loc
from wynd.spec.expr.analysis import check_expression, check_references, references
from wynd.spec.expr.errors import ExprSyntaxError
from wynd.spec.expr.evaluator import parse
from wynd.spec.lockfiles import branch_key
from wynd.spec.schemas import schema_at
from wynd.spec.workspace import PROCESS_FILE
from wynd.spec.yamlio import dump_yaml, source_loc

if TYPE_CHECKING:
    from wynd.spec.errors import Loc
    from wynd.spec.expr.analysis import TypeEnv
    from wynd.spec.process_doc import ProcessDoc
    from wynd.spec.yamlio import SourceMap

    from ..workspace import Tree, Workspace
    from .structure import StepIface

PROVISIONAL = " [provisional: interface inferred from examples]"
REFERENCE_CODES = frozenset({"E-REF-STEP", "E-REF-UNRUN", "E-REF-FIELD", "E-REF-EXIT", "E-REF-INPUT", "E-REF-EDGE"})
FIELD_CODES = frozenset({"E-REF-FIELD", "E-REF-EXIT"})


@dataclass(frozen=True)
class TypedSite:                          # produced by the dataflow walk
    process: str
    loc: Loc                              # document path of the value/expression
    expr: str | None                      # None for synthetic sites (entry, on_error, handler exits)
    env: TypeEnv                          # exit-aware scope at that site (WHEN/WITH/FINAL state)
    target: Literal["when", "with", "limit", "exit", "entry", "on_error", "handler_exit", "finally"]
    dst_schema: dict | None               # schema the value must be assignable to (None: condition/limit)
    src_schema: dict | None = None        # synthetic sites: the source schema instead of inferring expr


@dataclass(frozen=True)
class ExprCheckResult:
    errors: list[Diagnostic]              # with `span` inside the expression
    warnings: list[Diagnostic]
    scope: list[str]                      # references valid at `loc` (exit-aware)


# --- per-site checks ------------------------------------------------------------------------------------------------

def _referenced_step(site: TypedSite, doc: ProcessDoc, finding: Diagnostic) -> str | None:
    """The step whose outputs a field finding is about (`steps.<k>…` or `previous…` of the site's edge)."""
    for ref in references(site.expr):
        if (ref.line, ref.column) != (finding.line, finding.column):
            continue
        if ref.root == "steps" and ref.path and isinstance(ref.path[0], str):
            return ref.path[0]
        if ref.root == "previous" and site.loc[0] == "edges":
            return doc.edges[site.loc[1]].source_step
    return None


def site_findings(site: TypedSite, doc: ProcessDoc, ifaces: Mapping[str, StepIface],
                  codes: frozenset[str] | None = REFERENCE_CODES) -> list[Diagnostic]:
    """`check_references` at the site (only `codes`, all when None); expression-relative positions, provisional
    field findings downgraded."""
    out = []
    for found in check_references(site.expr, site.env):
        if codes is not None and found.code not in codes:
            continue
        if found.code in FIELD_CODES:
            step = _referenced_step(site, doc, found)
            if step in ifaces and not ifaces[step].precise:
                found = replace(found, severity="warning", message=found.message + PROVISIONAL)
        out.append(found)
    return out


def locate(source: SourceMap | None, loc: Loc, text: str, found: Diagnostic, pid: str) -> Diagnostic:
    """An expression-relative finding placed in the document: the exact column when the scalar allows it, else the
    scalar start with "(expression L:C)" and a caret snippet."""
    if source is None or found.line is None:
        return replace(found, loc=tuple(loc), process=pid, file=source.file if source else None)
    line, column, exact = source.expr_position(source_loc(source, loc), found.line, found.column or 1)
    message, snippet = found.message, found.snippet
    if not exact:
        message = f"{message} (expression {found.line}:{found.column})"
        lines = text.splitlines() or [""]
        text_line = lines[found.line - 1] if 0 < found.line <= len(lines) else lines[-1]
        snippet = f"{text_line}\n{' ' * ((found.column or 1) - 1)}^"
    return replace(found, message=message, file=source.file, line=line, column=column, loc=tuple(loc),
                   process=pid, snippet=snippet)


def _usage(pid: str, doc: ProcessDoc, loc: Loc) -> str:
    """The `used_by` reference of an expression site: `edge:<pid>:<branch_key>.<field>` (field = the with-path,
    `when` or `limits.<key>`); `finally:<pid>:<step>.<field>` for `finally[].with`."""
    if loc[0] == "finally":
        return f"finally:{pid}:{doc.finally_[loc[1]].step}.{format_loc(loc[3:])}"
    edge = doc.edges[loc[1]]
    branch = edge.to[loc[3]]
    rest = loc[4:]
    field = format_loc(rest[1:]) if rest[0] == "with" else format_loc(rest)
    return f"edge:{pid}:{branch_key(edge.from_, loc[3], branch.name)}.{field}"


def check_sites(pid: str, doc: ProcessDoc, sites: list[TypedSite],
                ifaces: Mapping[str, StepIface]) -> tuple[list[Diagnostic], dict[str, list[str]]]:
    """Reference findings located in `doc`'s file, and `env_refs` (env var -> usages) of every site."""
    out: list[Diagnostic] = []
    env_refs: dict[str, list[str]] = {}
    for site in sites:
        if site.expr is None:
            continue
        for found in site_findings(site, doc, ifaces):
            out.append(locate(doc._source, site.loc, site.expr, found, pid))
        for ref in references(site.expr):
            if ref.root == "env" and len(ref.path) == 1 and isinstance(ref.path[0], str):
                usages = env_refs.setdefault(ref.path[0], [])
                usage = _usage(pid, doc, site.loc)
                if usage not in usages:
                    usages.append(usage)
    return out, env_refs


# --- the web editor's check -----------------------------------------------------------------------------------------

class _Overlay:
    """A Tree whose files `texts` replace or add to `base` (in-memory design documents)."""

    def __init__(self, base: Tree, texts: Mapping[str, str]) -> None:
        self.root = base.root
        self._base = base
        self._data = {path: text.encode() for path, text in texts.items()}
        self._files = base.files() | frozenset(self._data)

    def files(self) -> frozenset[str]:
        return self._files

    def read_bytes(self, path: str) -> bytes:
        return self._data[path] if path in self._data else self._base.read_bytes(path)

    def blob_id(self, path: str) -> str:
        if path not in self._data:
            return self._base.blob_id(path)
        data = self._data[path]
        return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

    def is_dir(self, path: str) -> bool:
        prefix = f"{path.rstrip('/')}/" if path else ""
        return any(file.startswith(prefix) for file in self._files)


def model_loc(raw: dict, loc: Loc) -> Loc:
    """The normalised-model location of a raw-document location: a shorthand edge (`to: x`) keeps its branch's
    `with`/`limits`/`when` on the edge."""
    loc = tuple(loc)
    if len(loc) >= 3 and loc[0] == "edges" and loc[2] != "to":
        edges = raw.get("edges")
        i = loc[1]
        if isinstance(edges, list) and isinstance(i, int) and 0 <= i < len(edges):
            edge = edges[i]
            if isinstance(edge, dict) and not isinstance(edge.get("to"), list):
                return ("edges", i, "to", 0, *loc[2:])
    return loc


def _raw_loc(raw: dict, loc: Loc) -> Loc:
    """The inverse of `model_loc` for a shorthand edge."""
    if len(loc) >= 5 and loc[0] == "edges" and loc[2:4] == ("to", 0):
        edges = raw.get("edges")
        i = loc[1]
        if isinstance(edges, list) and isinstance(i, int) and 0 <= i < len(edges):
            edge = edges[i]
            if isinstance(edge, dict) and not isinstance(edge.get("to"), list):
                return ("edges", i, *loc[4:])
    return loc


def _set_at(raw: Any, loc: Loc, value: str) -> None:
    """Put `value` at `loc` when its parent container exists."""
    if not loc:
        return
    parent = raw
    for part in loc[:-1]:
        try:
            parent = parent[part]
        except (KeyError, IndexError, TypeError):
            return
    last = loc[-1]
    if isinstance(parent, dict) or (isinstance(parent, list) and isinstance(last, int) and last < len(parent)):
        parent[last] = value


def _scope(doc: ProcessDoc, env: TypeEnv) -> list[str]:
    """References valid at a site: process inputs, run id, previous, per step `exit`/`runs` and the output fields
    every possible exit declares (only when the step has run on every path), branch counters, declared env vars."""
    out = [f"process.inputs.{name}" for name in doc.inputs] + ["run.id"]
    if env.previous is not None:
        out += ["previous.outputs", "previous.summary"]
    for key, view in env.steps.items():
        out += [f"steps.{key}.exit", f"steps.{key}.runs"]
        if view.may_be_unrun or not view.exits:
            continue
        fields = [_properties(schema) for schema in view.exits.values()]
        common = [name for name in fields[0] if name != "exit" and all(name in f for f in fields[1:])]
        out += [f"steps.{key}.outputs.{name}" for name in common]
    for edge, names in env.edges.items():
        for i, name in enumerate(names):
            out.append(f'edges["{edge}"][{i}].taken')
            if name is not None:
                out.append(f'edges["{edge}"].{name}.taken')
    out += [f"env.{name}" for name in doc.env.vars]
    return out


def _properties(schema: dict) -> dict:
    """The properties of an object schema, through a root `$ref` (recursive models such as StepError)."""
    resolved = schema_at(schema, ())
    return resolved.get("properties", {}) if isinstance(resolved, dict) else {}


def _syntax_only(expr: str, loc: Loc, pid: str) -> ExprCheckResult:
    """No process context: the process-independent findings only."""
    errors = [replace(d, loc=tuple(loc), process=pid, line=None, column=None) for d in check_expression(expr)]
    return ExprCheckResult(errors=errors, warnings=[], scope=[])


def check_expr_at(
    ws: Workspace, pid: str, doc: dict[str, Any], protos: dict[str, Any], loc: Loc, expr: str
) -> ExprCheckResult:
    """Check `expr` as the value at `loc` of the in-memory process document `doc` (raw JSON form; `protos` are
    in-memory proto documents by workspace-relative path). Findings carry `span` inside `expr`. When the document
    does not load, only the process-independent findings are returned (empty scope)."""
    from ..errors import LoadError, ProcessNotFound
    from ..workspace import load_workspace
    from .dataflow import analyse, typed_sites
    from .structure import normalise, step_ifaces

    raw = copy.deepcopy(doc)
    nloc = model_loc(raw, loc)
    try:
        parse(expr)
        placeholder = expr
    except ExprSyntaxError:
        placeholder = "true" if nloc[-1:] == ("when",) else "null"
    _set_at(raw, _raw_loc(raw, nloc), placeholder)

    if pid not in ws.processes:
        return _syntax_only(expr, nloc, pid)
    texts = {f"{ws.processes[pid].dir}/{PROCESS_FILE}": dump_yaml(raw)}
    texts.update({path: dump_yaml(proto) for path, proto in protos.items() if isinstance(proto, dict)})
    try:
        lp = load_workspace(ws.root, _Overlay(ws.tree, texts)).load_process(pid)
    except (LoadError, ProcessNotFound):
        return _syntax_only(expr, nloc, pid)

    ifaces = step_ifaces(lp)
    norm = normalise(lp.doc)
    site = next((s for s in typed_sites(pid, norm, analyse(norm, ifaces), ifaces) if s.loc == nloc), None)
    if site is None:
        return _syntax_only(expr, nloc, pid)
    site = replace(site, expr=expr)
    errors: list[Diagnostic] = []
    warnings: list[Diagnostic] = []
    for found in site_findings(site, norm, ifaces, codes=None):
        found = replace(found, loc=nloc, process=pid, line=None, column=None)
        if found.severity == "error":
            errors.append(found)
        else:
            warnings.append(found)
    return ExprCheckResult(errors=errors, warnings=warnings, scope=_scope(norm, site.env))
