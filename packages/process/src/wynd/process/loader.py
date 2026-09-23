"""Process loader output (PLAN §6.2; owner PROC-WS).

The dataclasses below are the W0-complete contract. `load_process` (backing `Workspace.load_process`) implements
resolution, phase and interface choice (`$DRAFTS/04 §3.6`) and reference cycles (`§3.7`).

Interface choice (first match): compiled & not stale & lock interface -> `lock`; proto declares inputs and outputs ->
`proto`; proto present -> `examples` (`precise=False`); compiled lock without interface -> `none` (`W129`). A
`process:<id>` reference uses the child's `ProcessDoc.interface()` (`process`).

Every `steps:` key gets a `ResolvedStep`. A step reference that does not resolve (E121, E122, E123 without a proto)
has `package=None`; a process reference always names its child, and the child is in `children` only when it loaded
(not for E124/E125). `process.yaml` YAML/schema errors raise `LoadError` (for children too); everything else is a
diagnostic on the process that holds the reference.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Literal, TypeVar

from pydantic import BaseModel

from wynd.spec.errors import Diagnostic, SpecError
from wynd.spec.hashing import proto_hash
from wynd.spec.interface import Interface
from wynd.spec.lockfiles import StepLock
from wynd.spec.process_doc import ProcessDoc
from wynd.spec.proto_step import ProtoStep, check_proto_step
from wynd.spec.workspace import (
    PROCESS_FILE,
    PROTO_DIR,
    STEP_LOCK_FILE,
    STEP_PROTO_FILE,
    STEPS_DIR,
    UseRef,
    local_step_id,
    root_step_id,
)
from wynd.spec.yamlio import SourceMap, parse_model

from .errors import LoadError, ProcessNotFound
from .workspace import PYPROJECT_FILE, diagnostic, join, read_text

if TYPE_CHECKING:
    from .workspace import ProcessEntry, Tree, Workspace

M = TypeVar("M", bound=BaseModel)


@dataclass(frozen=True)
class ResolvedInterface:
    interface: Interface | None           # spec Interface (outputs include the exit const); None when unknown
    source: Literal["lock", "proto", "examples", "process", "none"]
    precise: bool                         # False for "examples" (field-level findings become warnings)


@dataclass
class StepPackage:
    id: str                               # step id
    dir: str                              # workspace-relative package dir
    proto_path: str | None
    proto: ProtoStep | None
    proto_hash: str | None
    lock: StepLock | None
    phase: Literal["compiled", "design"]
    stale: bool
    interface: ResolvedInterface


@dataclass
class ResolvedStep:                       # one entry of process.yaml `steps:`
    name: str
    use: str
    ref_kind: Literal["local", "root", "process"]
    package: StepPackage | None           # None for process refs
    child: str | None                     # child process id for process refs


@dataclass
class LoadedProcess:
    id: str                               # process id
    dir: str                              # workspace-relative process dir
    path: str                             # workspace-relative process.yaml path
    doc: ProcessDoc
    source: SourceMap
    steps: dict[str, ResolvedStep]
    children: dict[str, LoadedProcess]    # memoised per Workspace (diamonds load once)
    diagnostics: list[Diagnostic]

    def closure_processes(self) -> dict[str, LoadedProcess]:
        """This process and every descendant, by id (this one first, then depth first in `steps:` order)."""
        out = {self.id: self}
        for child in self.children.values():
            for pid, lp in child.closure_processes().items():
                out.setdefault(pid, lp)
        return out

    def closure_packages(self) -> dict[str, StepPackage]:
        """Every resolved step package of the closure, by step id."""
        return {
            rs.package.id: rs.package
            for lp in self.closure_processes().values()
            for rs in lp.steps.values()
            if rs.package is not None
        }


def load_process(ws: Workspace, pid: str) -> LoadedProcess:
    """Load process `pid` and its children; memoised per Workspace. ProcessNotFound (E127) for an unknown id."""
    if pid not in ws.processes:
        raise ProcessNotFound(pid)
    return _load(ws, pid, ())


def _load(ws: Workspace, pid: str, stack: tuple[str, ...]) -> LoadedProcess:
    if pid in ws._loaded:
        return ws._loaded[pid]
    entry = ws.processes[pid]
    path = join(entry.dir, PROCESS_FILE)
    try:
        doc = parse_model(read_text(ws.tree, path), ProcessDoc, path)
    except SpecError as err:
        raise LoadError(_owned(err.diagnostics, pid)) from None
    source: SourceMap = doc._source
    diagnostics = [d for d in ws.diagnostics if d.process == pid]
    folder = pid.rpartition("/")[2]
    if doc.name != folder:
        diagnostics.append(diagnostic("E113", source=source, loc=("name",), process=pid, name=doc.name,
                                      folder=folder))

    stack = (*stack, pid)
    steps: dict[str, ResolvedStep] = {}
    children: dict[str, LoadedProcess] = {}
    for key, ref in doc.steps.items():
        use = ref.use_ref
        loc = ("steps", key, "use")
        if use.form == "process":
            steps[key] = ResolvedStep(key, ref.use, "process", None, use.target)
            child = _load_child(ws, key, use.target, stack, source, loc, diagnostics)
            if child is not None:
                children[use.target] = child
        else:
            package = _resolve_step(ws, entry, key, ref.use, use, source, loc, diagnostics)
            steps[key] = ResolvedStep(key, ref.use, use.form, package, None)

    lp = LoadedProcess(pid, entry.dir, path, doc, source, steps, children, diagnostics)
    ws._loaded[pid] = lp
    return lp


def _load_child(
    ws: Workspace,
    key: str,
    cid: str,
    stack: tuple[str, ...],
    source: SourceMap,
    loc: tuple,
    diagnostics: list[Diagnostic],
) -> LoadedProcess | None:
    pid = stack[-1]
    if cid not in ws.processes:
        diagnostics.append(diagnostic("E124", source=source, loc=loc, process=pid, alias=key, id=cid))
        return None
    if cid in stack:
        cycle = " -> ".join((*stack[stack.index(cid):], cid))
        diagnostics.append(diagnostic("E125", source=source, loc=loc, process=pid, cycle=cycle))
        return None
    return _load(ws, cid, stack)


def _resolve_step(
    ws: Workspace,
    entry: ProcessEntry,
    key: str,
    use_text: str,
    use: UseRef,
    source: SourceMap,
    loc: tuple,
    diagnostics: list[Diagnostic],
) -> StepPackage | None:
    pid = entry.id
    if use.form == "local":
        step_id = local_step_id(pid, use.target)
        pkg_dir = join(entry.dir, STEPS_DIR, use.target)
        proto_path = join(entry.dir, PROTO_DIR, f"{use.target}.yaml")
    else:
        step_root = ws.config.step_roots.get(use.alias)
        if step_root is None:
            configured = ", ".join(sorted(ws.config.step_roots)) or "none"
            diagnostics.append(diagnostic("E121", source=source, loc=loc, process=pid, alias=key, root=use.alias,
                                          aliases=configured))
            return None
        step_id = root_step_id(use.alias, use.target)
        pkg_dir = join(step_root.path, use.target)
        proto_path = join(pkg_dir, STEP_PROTO_FILE)
    expected_name = use.target.rpartition("/")[2]

    files = ws.tree.files()
    lock_path = join(pkg_dir, STEP_LOCK_FILE)
    has_py, has_lock, has_proto = (p in files for p in (join(pkg_dir, PYPROJECT_FILE), lock_path, proto_path))
    if has_py != has_lock:
        present, missing = (PYPROJECT_FILE, STEP_LOCK_FILE) if has_py else (STEP_LOCK_FILE, PYPROJECT_FILE)
        diagnostics.append(diagnostic("E123", file=join(pkg_dir, present), process=pid, dir=pkg_dir,
                                      present=present, missing=missing))
        if not has_proto:
            return None
    elif not has_py and not has_proto:
        diagnostics.append(diagnostic("E122", source=source, loc=loc, process=pid, alias=key, use=use_text,
                                      pkg=pkg_dir, proto=proto_path))
        return None

    proto = _parse(ws.tree, proto_path, ProtoStep, pid, diagnostics) if has_proto else None
    if proto is not None:
        if proto.name != expected_name:
            diagnostics.append(diagnostic("E119", source=proto._source, loc=("name",), process=pid, name=proto.name,
                                          expected=expected_name))
        diagnostics += _owned(_proto_findings(proto), pid)
    lock = _parse(ws.tree, lock_path, StepLock, pid, diagnostics) if has_py and has_lock else None
    current = proto_hash(proto) if proto is not None else None
    stale = lock is not None and proto is not None and lock.proto_hash != current
    return StepPackage(
        id=step_id,
        dir=pkg_dir,
        proto_path=proto_path if has_proto else None,
        proto=proto,
        proto_hash=current,
        lock=lock,
        phase="compiled" if lock is not None and not stale else "design",
        stale=stale,
        interface=_choose_interface(proto, lock, stale),
    )


def _choose_interface(proto: ProtoStep | None, lock: StepLock | None, stale: bool) -> ResolvedInterface:
    if lock is not None and not stale and lock.interface is not None:
        return ResolvedInterface(lock.interface, "lock", True)
    if proto is not None and _declares_schemas(proto):
        return ResolvedInterface(proto.interface(), "proto", True)
    if proto is not None:
        return ResolvedInterface(_examples_interface(proto), "examples", False)
    return ResolvedInterface(None, "none", False)


def _declares_schemas(proto: ProtoStep) -> bool:
    """The proto document itself has `inputs:` and `outputs:` keys (normalisation fills both when absent)."""
    return not _undeclared(proto)


def _undeclared(proto: ProtoStep) -> set[str]:
    return {key for key in ("inputs", "outputs") if (key,) not in proto._source.marks}


def _proto_findings(proto: ProtoStep) -> list[Diagnostic]:
    """`check_proto_step`, minus example-vs-schema findings for a section the proto does not declare: those schemas
    are inferred from the examples (SPEC §6.1), so an undeclared section is not an empty one."""
    undeclared = _undeclared(proto)
    return [
        d for d in check_proto_step(proto)
        if not (d.code == "E-EXAMPLE" and len(d.loc) > 2 and d.loc[0] == "examples" and d.loc[2] in undeclared)
    ]


def _examples_interface(proto: ProtoStep) -> Interface:
    """Field names from the examples (union of keys, per exit for outputs), every type `{}`; exits from the proto."""
    outputs = {
        exit: _object_schema(_keys(e.outputs for e in proto.examples if e.exit == exit), exit) for exit in proto.exits
    }
    return Interface(input=_object_schema(_keys(e.inputs for e in proto.examples)), outputs=outputs)


def _keys(mappings: Iterable[dict]) -> list[str]:
    names: list[str] = []
    for mapping in mappings:
        names += [name for name in mapping if name not in names]
    return names


def _object_schema(fields: list[str], exit: str | None = None) -> dict:
    properties: dict[str, dict] = {"exit": {"const": exit}} if exit is not None else {}
    properties.update({name: {} for name in fields})
    schema: dict = {"type": "object", "properties": properties}
    if exit is not None:
        schema["required"] = ["exit"]
    schema["additionalProperties"] = False
    return schema


def _parse(tree: Tree, path: str, model: type[M], pid: str, diagnostics: list[Diagnostic]) -> M | None:
    try:
        return parse_model(read_text(tree, path), model, path)
    except SpecError as err:
        diagnostics += _owned(err.diagnostics, pid)
        return None


def _owned(diagnostics: Iterable[Diagnostic], pid: str) -> list[Diagnostic]:
    return [replace(d, process=pid) for d in diagnostics]
