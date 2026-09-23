"""`ProcessService` (PLAN §8.1; `$DRAFTS/06 §5.4`).

StepInfo/ProcessInterface DTOs are built from `LoadedProcess` (source mapping lock->"compiled", proto->"declared",
examples->"inferred", process->"process", none->null; `kind: TraceStepKind`). Step phase: "missing" when the
reference does not resolve, "design" without a lock or with a stale one, "handwritten" for a compiled package without
a proto, else "compiled"; a `process:` step is "design" when any step of the child's closure is. Every call reloads
the workspace from disk (the web autosaves).
"""

from __future__ import annotations

import hashlib
import re
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from wynd.controller.errors import Conflict, Invalid, NotFound, translated
from wynd.spec.workspace import PROCESS_FILE, RESERVED_PROCESS_SEGMENTS, STEP_LOCK_FILE

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import (
        Build,
        CommitInfo,
        ExprCheck,
        ExprCheckRequest,
        FileContent,
        Interface,
        ProcessInterface,
        ProcessStatus,
        ProcessSummary,
        StatusFlag,
        StepCatalogEntry,
        StepInfo,
        StepPhase,
        StepSource,
        ValidationReportDTO,
    )
    from wynd.process.loader import LoadedProcess, ResolvedInterface, ResolvedStep
    from wynd.process.testing import TestReport
    from wynd.process.workspace import StepEntry, Workspace
    from wynd.spec.lockfiles import StepLock
    from wynd.spec.proto_step import ProtoStep

PROCESS_ID = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*(/[A-Za-z0-9_][A-Za-z0-9_-]*)*$")
PROCESS_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
SOURCES = {"lock": "compiled", "proto": "declared", "examples": "inferred", "process": "process", "none": None}
MAX_FILE_BYTES = 256 * 1024
CASSETTES = "cassettes"


class ProcessService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def list(self, q: str = "", flags: Sequence[StatusFlag] = ()) -> list[ProcessSummary]:
        """Every process sorted by id (one that fails to load carries `error`); with `q` or `flags`, the search
        hits in search order."""
        ws = self.ctx.workspace()
        summaries = [self._summary(ws, pid) for pid in ws.process_ids()]
        if not q.strip() and not flags:
            return summaries
        from wynd.controller.search import search

        by_id = {summary.id: summary for summary in summaries}
        docs = [_search_doc(ws.load_process(s.id)) for s in summaries if s.error is None]
        statuses = {s.id: s.status for s in summaries if s.status is not None}
        return [by_id[pid].model_copy(update={"matches": matches})
                for pid, _score, matches in search(docs, statuses, q, flags)]

    def get(self, pid: str) -> ProcessSummary:
        ws = self.ctx.workspace()
        _require(ws, pid)
        return self._summary(ws, pid)

    def new(self, pid: str, *, goal: str | None = None, root: str | None = None, commit: bool = True) -> ProcessSummary:
        from wynd.controller.workspace import new_process_files
        from wynd.process.git import commit_only

        _check_new_id(pid)
        ws = self.ctx.workspace()
        roots = list(ws.config.process_roots)
        root = root or roots[0]
        if root not in roots:
            raise Invalid(f"'{root}' is not a process root (process roots: {', '.join(roots)})")
        rel = f"{root}/{pid}"
        if pid in ws.processes or (self.ctx.root / rel).exists():
            raise Conflict(f"process '{pid}' already exists ({rel})")
        parts = pid.split("/")
        for depth in range(1, len(parts)):
            outer = "/".join(parts[:depth])
            if (self.ctx.root / root / outer / PROCESS_FILE).exists():
                raise Conflict(f"'{pid}' would be nested inside process '{outer}'; processes are leaves")
        for name, text in new_process_files(pid, goal=goal).items():
            path = self.ctx.root / rel / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        if commit:
            with self.ctx.git_lock:
                commit_only(self.ctx.root, [rel], f"design({pid}): new process")
        return self.get(pid)

    def status(self, pid: str) -> ProcessStatus:
        from wynd.controller.status import derive_status

        ws = self.ctx.workspace()
        _require(ws, pid)
        with translated():
            return derive_status(self.ctx, ws, pid)

    def validate(self, pid: str) -> ValidationReportDTO:
        """The validator's findings; a process that does not load reports the loader's diagnostics."""
        from wynd.controller.models import Issue, ValidationReportDTO
        from wynd.process.errors import LoadError
        from wynd.process.validation import validate_process

        ws = self.ctx.workspace()
        _require(ws, pid)
        try:
            report = validate_process(ws, pid)
        except LoadError as err:
            return ValidationReportDTO(ok=False, issues=[Issue.from_diagnostic(d) for d in err.diagnostics])
        return ValidationReportDTO.from_report(report)

    def validate_all(self) -> dict[str, ValidationReportDTO]:
        return {pid: self.validate(pid) for pid in self.ctx.workspace().process_ids()}

    def steps(self, pid: str) -> dict[str, StepInfo]:
        ws = self.ctx.workspace()
        _require(ws, pid)
        with translated():
            lp = ws.load_process(pid)
        users = _users(ws)
        return {key: _step_info(lp, rs, users) for key, rs in lp.steps.items()}

    def step_catalog(self) -> list[StepCatalogEntry]:
        """Every step-root step, whether or not a process references it."""
        from wynd.controller.models import StepCatalogEntry

        ws = self.ctx.workspace()
        users = _users(ws)
        entries = []
        for sid, entry in sorted(ws.root_steps.items()):
            proto, lock, stale, broken = _read_package(ws, entry)
            entries.append(StepCatalogEntry(
                use=sid, root=entry.alias, path=entry.path,
                kind=None if lock is None else lock.kind,
                phase="missing" if broken else _phase(proto, lock, stale),
                instruction=None if proto is None else proto.instruction,
                used_by=users.get(sid, []),
            ))
        return entries

    def interface(self, pid: str, commit: str | None = None) -> ProcessInterface:
        """The working tree's process interface, or the one at `commit`."""
        from wynd.controller.models import InterfaceExample, ProcessInterface
        from wynd.process.errors import GitError
        from wynd.process.workspace import CommitTree

        tree = None
        if commit is not None:
            try:
                tree = CommitTree(self.ctx.root, commit)
            except GitError:
                raise NotFound(f"'{commit}' is not a commit of this repository") from None
        ws = self.ctx.workspace(tree)
        _require(ws, pid)
        with translated():
            doc = ws.load_process(pid).doc
        iface = doc.interface()
        return ProcessInterface(
            commit=None if tree is None else tree.sha,
            inputs=iface.input,
            outputs=dict(iface.outputs),
            examples=[InterfaceExample(inputs=e.inputs, outputs=e.outputs, exit=e.exit) for e in doc.examples],
        )

    def builds(self, pid: str) -> list[Build]:
        """Builds of the process, newest first, against the process HEAD."""
        from wynd.controller.models import Build
        from wynd.controller.status import process_head
        from wynd.process.git import count_touching

        _require(self.ctx.workspace(), pid)
        with translated():
            found = process_head(self.ctx, pid)
        builds = []
        for info in self.ctx.artefacts.list_builds(pid):
            behind = 0 if found is None else count_touching(self.ctx.root, info.commit, found[0], found[1])
            builds.append(Build(
                process_id=pid, commit=info.commit, short=info.commit[:7], image=info.image or info.bake or "",
                built_at=info.created_at, job_id=info.job_id, at_head=found is not None and info.commit == found[0],
                behind=behind, env=info.manifest.vars,
            ))
        return builds

    def read_file(self, pid: str, path: str) -> FileContent:
        """A file of the process's reference closure as git would see it (never ignored files or `.wynd/`)."""
        from wynd.process.git import reference_closure

        ws = self.ctx.workspace()
        _require(ws, pid)
        rel = PurePosixPath(path)
        norm = rel.as_posix()
        if rel.is_absolute() or ".." in rel.parts or norm == ".":
            raise NotFound(f"'{path}' is not a workspace-relative path")
        with translated():
            closure = reference_closure(ws, pid)
        inside = any(norm == c or norm.startswith(f"{c}/") for c in closure)
        if not inside or norm not in ws.tree.files():
            raise NotFound(f"'{norm}' is not a file of process '{pid}' (its reference closure)")
        return _file_content(norm, ws.tree.read_bytes(norm))

    def step_source(self, pid: str, step: str) -> StepSource:
        """The compiled package of step `step`: its files (except cassettes and the lock) and its cassette list."""
        from wynd.controller.models import StepSource

        ws = self.ctx.workspace()
        _require(ws, pid)
        with translated():
            lp = ws.load_process(pid)
        rs = lp.steps.get(step)
        if rs is None:
            raise NotFound(f"process '{pid}' has no step '{step}'")
        pkg = rs.package
        if pkg is None or pkg.lock is None:
            raise NotFound(f"step '{step}' of process '{pid}' has no compiled package")
        files = sorted(f for f in ws.tree.files() if f.startswith(f"{pkg.dir}/"))
        lock_path = f"{pkg.dir}/{STEP_LOCK_FILE}"
        cassette_dir = f"{pkg.dir}/{CASSETTES}/"
        return StepSource(
            step=step,
            package_dir=pkg.dir,
            lock_yaml=ws.tree.read_bytes(lock_path).decode(),
            files=[_file_content(f, ws.tree.read_bytes(f)) for f in files
                   if f != lock_path and not f.startswith(cassette_dir)],
            cassettes=[{"path": f, "size": (self.ctx.root / f).stat().st_size} for f in files
                       if f.startswith(cassette_dir)],
        )

    def history(self, pid: str, limit: int = 50) -> list[CommitInfo]:
        """The newest commits touching the process's reference closure."""
        from wynd.controller.models import CommitInfo
        from wynd.controller.status import working_closure
        from wynd.process.git import log_paths

        ws = self.ctx.workspace()
        _require(ws, pid)
        return [CommitInfo(**commit) for commit in log_paths(self.ctx.root, working_closure(ws, pid), limit)]

    def test(self, pid: str, *, log: Callable[[str], None] | None = None) -> TestReport:
        """Replay tests, not a job; results are recorded only when the closure is clean (PLAN §3.20)."""
        from wynd.process.git import closure_head, dirty_paths, reference_closure
        from wynd.process.testing import run_tests

        ws = self.ctx.workspace()
        _require(ws, pid)
        scratch_root = self.ctx.state_dir / "tmp"
        scratch_root.mkdir(parents=True, exist_ok=True)
        with translated(), tempfile.TemporaryDirectory(prefix="test-", dir=scratch_root) as scratch:
            closure = reference_closure(ws, pid)
            commit = None if dirty_paths(self.ctx.root, closure) else closure_head(self.ctx.root, closure)
            return run_tests(ws, pid, mode="replay", commit=commit, runs=self.ctx.stores.runs,
                             venv_root=self.ctx.state_dir / "venvs", scratch=Path(scratch),
                             env=self.ctl.env.resolve(), log=log)

    def sync_interfaces(self, pid: str) -> list[str]:
        """`wynd validate --sync-interfaces`: refresh lock `interface`/`context`/`shell.exit_codes` snapshots; -> the
        lock files changed."""
        from wynd.process.venvs import sync_interfaces

        ws = self.ctx.workspace()
        _require(ws, pid)
        with translated():
            return sync_interfaces(ws, pid, venv_root=self.ctx.state_dir / "venvs")

    def check_expr(self, req: ExprCheckRequest) -> ExprCheck:
        """Check one expression of the in-flight (unsaved) process document the web sends."""
        from wynd.controller.models import ExprCheck, ExprIssue
        from wynd.process.validation.exprcheck import check_expr_at

        if not isinstance(req.process, dict):
            raise Invalid("process must be the process document (a JSON object)")
        with translated():
            result = check_expr_at(self.ctx.workspace(), req.process_id, req.process, req.protos, tuple(req.loc),
                                   req.expr)

        def issue(d: Any) -> ExprIssue:
            start, end = d.span or (0, len(req.expr))
            return ExprIssue(message=d.message, start=start, end=end)

        return ExprCheck(ok=not result.errors, errors=[issue(d) for d in result.errors],
                         warnings=[issue(d) for d in result.warnings], scope=result.scope if req.scope else None)

    def _summary(self, ws: Workspace, pid: str) -> ProcessSummary:
        from wynd.controller.errors import WyndError
        from wynd.controller.models import ProcessSummary
        from wynd.controller.status import derive_status

        entry = ws.processes[pid]
        try:
            with translated():
                doc = ws.load_process(pid).doc
                status = derive_status(self.ctx, ws, pid)
        except WyndError as err:
            return ProcessSummary(id=pid, name=pid.rpartition("/")[2], path=entry.dir, error=err.message)
        return ProcessSummary(id=pid, name=doc.name, goal=doc.goal, path=entry.dir, status=status)


def _require(ws: Workspace, pid: str) -> None:
    if pid not in ws.processes:
        raise NotFound(f"unknown process '{pid}'")


def _check_new_id(pid: str) -> None:
    if not PROCESS_ID.match(pid):
        raise Invalid(f"'{pid}' is not a process id: '/'-separated segments of [A-Za-z0-9_][A-Za-z0-9_-]*")
    name = pid.rpartition("/")[2]
    if name in RESERVED_PROCESS_SEGMENTS:
        raise Invalid(f"process id '{pid}' ends in the reserved segment '{name}' "
                      f"(reserved: {', '.join(sorted(RESERVED_PROCESS_SEGMENTS))})")
    if not PROCESS_NAME.match(name):
        raise Invalid(f"the last segment of '{pid}' is the process name and must match {PROCESS_NAME.pattern}")


def _users(ws: Workspace) -> dict[str, list[str]]:
    """Step id (or `process:<id>`) -> the ids of the processes whose `steps:` reference it; unloadable processes are
    skipped."""
    from wynd.process.errors import WyndProcessError

    users: dict[str, list[str]] = {}
    for pid in ws.process_ids():
        try:
            lp = ws.load_process(pid)
        except WyndProcessError:
            continue
        for rs in lp.steps.values():
            key = f"process:{rs.child}" if rs.ref_kind == "process" else rs.package.id if rs.package else None
            if key is not None and pid not in users.setdefault(key, []):
                users[key].append(pid)
    return users


def _step_info(lp: LoadedProcess, rs: ResolvedStep, users: dict[str, list[str]]) -> StepInfo:
    from wynd.controller.models import Interface, StepInfo, StepLockInfo
    from wynd.process.loader import ResolvedInterface

    base: dict[str, Any] = {"name": rs.name, "use": rs.use, "ref_kind": rs.ref_kind}
    if rs.ref_kind == "process":
        child = lp.children.get(rs.child)
        used_by = users.get(f"process:{rs.child}", [lp.id])
        if child is None:
            return StepInfo(**base, resolved=False, kind="process", phase="missing", used_by=used_by,
                            interface=Interface())
        design = any(step.package is None or step.package.phase == "design"
                     for proc in child.closure_processes().values() for step in proc.steps.values()
                     if step.ref_kind != "process")
        iface = ResolvedInterface(child.doc.interface(), "process", True)
        return StepInfo(**base, resolved=True, source_path=child.dir, kind="process",
                        phase="design" if design else "compiled", used_by=used_by, instruction=child.doc.goal,
                        interface=_interface(iface))
    pkg = rs.package
    if pkg is None:
        return StepInfo(**base, resolved=False, phase="missing", used_by=[lp.id], interface=Interface())
    lock = pkg.lock
    lock_info = None if lock is None else StepLockInfo(
        provider=lock.provider, tier=lock.tier, thinking=lock.thinking, effects=list(lock.effects),
        env=[var.name for var in lock.fragment.vars],
    )
    return StepInfo(
        **base, resolved=True, proto_path=pkg.proto_path, source_path=None if lock is None else pkg.dir,
        kind=None if lock is None else lock.kind, phase=_phase(pkg.proto, lock, pkg.stale), lock=lock_info,
        used_by=users.get(pkg.id, [lp.id]), instruction=None if pkg.proto is None else pkg.proto.instruction,
        interface=_interface(pkg.interface),
    )


def _phase(proto: ProtoStep | None, lock: StepLock | None, stale: bool) -> StepPhase:
    if lock is None or stale:
        return "design"
    return "handwritten" if proto is None else "compiled"


def _interface(resolved: ResolvedInterface) -> Interface:
    from wynd.controller.models import ExitSchema, Interface
    from wynd.spec.base import RESERVED_EXIT

    source = SOURCES[resolved.source]
    if resolved.interface is None:
        return Interface(source=source)
    exits = [ExitSchema(name=name, schema=schema) for name, schema in resolved.interface.outputs.items()
             if name != RESERVED_EXIT]
    return Interface(inputs=resolved.interface.input, exits=exits, source=source)


def _read_package(ws: Workspace, entry: StepEntry) -> tuple[ProtoStep | None, StepLock | None, bool, bool]:
    """(proto, lock, stale, broken) of a step-root package, read through the workspace tree."""
    from wynd.process.workspace import read_text
    from wynd.spec.errors import SpecError
    from wynd.spec.hashing import proto_hash
    from wynd.spec.lockfiles import StepLock
    from wynd.spec.proto_step import ProtoStep
    from wynd.spec.yamlio import parse_model

    files = ws.tree.files()
    lock_path = f"{entry.dir}/{STEP_LOCK_FILE}"
    try:
        proto = None
        if entry.proto_path is not None:
            proto = parse_model(read_text(ws.tree, entry.proto_path), ProtoStep, entry.proto_path)
        lock = parse_model(read_text(ws.tree, lock_path), StepLock, lock_path) if lock_path in files else None
    except SpecError:
        return None, None, False, True
    stale = lock is not None and proto is not None and lock.proto_hash != proto_hash(proto)
    return proto, lock, stale, False


def _search_doc(lp: LoadedProcess) -> dict[str, Any]:
    steps = [(key, rs.package.proto.instruction if rs.package and rs.package.proto else "")
             for key, rs in lp.steps.items()]
    return {"id": lp.id, "name": lp.doc.name, "goal": lp.doc.goal, "steps": steps}


def _file_content(path: str, data: bytes) -> FileContent:
    from wynd.controller.models import FileContent

    return FileContent(
        path=path, revision=f"sha256:{hashlib.sha256(data).hexdigest()}",
        content=data[:MAX_FILE_BYTES].decode("utf-8", errors="replace"), size=len(data),
        truncated=len(data) > MAX_FILE_BYTES,
    )
