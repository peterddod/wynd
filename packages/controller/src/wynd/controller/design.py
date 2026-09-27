"""`DesignService`: design reads, saves and boundary commits (PLAN §8.1; `$DRAFTS/06 §5.7`, `$DRAFTS/07 §12.3`).

Design scope of a process = its `process.yaml`, every `proto/<name>.yaml` under its own directory (existing or being
created) and the `proto.yaml` of every step-root step its `steps:` reference (read from the working-tree document, or
from the `process.yaml` written by the same save). Compiled source and other processes' files are never in scope.

Revisions are `"sha256:<hex of file bytes>"` (None: the file does not exist). Documents are served as JSON with
`yaml_to_json` and written with `dump_yaml`; a proto's decimal `exit_codes` keys, which JSON can only carry as
strings, are written back as ints. Validation errors never block a save. `commit` stages exactly the dirty design-scope
paths with `process.git.commit_only`, so a commit never spans two processes. Turn locks are held in memory by this
service (one per controller).
"""

from __future__ import annotations

import hashlib
import os
import re
import threading
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal

from wynd.controller.errors import DesignLocked, Invalid, NotFound, OutOfScope, RevisionConflict, ValidationFailed
from wynd.spec.workspace import PROCESS_FILE, PROTO_DIR, STEP_LOCK_FILE, STEP_PROTO_FILE, STEPS_DIR

if TYPE_CHECKING:
    from wynd.controller.api.models_web import AvailableLocalStep, DesignDoc, SaveRequest, SaveResult
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import CommitInfo, ProcessInterface, StepInfo, StepPhase, ValidationReportDTO
    from wynd.process.workspace import Tree, Workspace
    from wynd.spec.errors import Diagnostic
    from wynd.spec.workspace import UseRef

PROTO_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")      # a `./steps/<name>` segment
PACKAGE_MARKERS = (STEP_LOCK_FILE, "pyproject.toml")


class DesignService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}               # per process: owner checks + revision checks + writes
        self._owners: dict[str, tuple[str, str]] = {}              # pid -> (chat_id, turn_id)

    def get(self, pid: str) -> DesignDoc:
        from wynd.controller.api.models_web import (
            DesignConventions,
            DesignDoc,
            FileDoc,
            LockOwner,
            ParseError,
            ProcessFileDoc,
        )

        root = self.ctx.root
        ws = self.ctx.workspace()
        pdir = _process_dir(ws, pid)
        process_path = f"{pdir}/{PROCESS_FILE}"
        data = _read(root, process_path) or b""
        doc, problem = _json_doc(data, process_path)
        process_file = ProcessFileDoc(
            path=process_path, revision=_revision(data), doc=doc, yaml=data.decode("utf-8", errors="replace"),
            parse_error=None if problem is None else ParseError(message=problem.message, line=problem.line,
                                                                column=problem.column),
        )
        protos = {}
        for path in _scope(ws, pdir, doc):
            proto = _read(root, path)
            if path == process_path or proto is None:
                continue
            protos[path] = FileDoc(path=path, revision=_revision(proto), doc=_json_doc(proto, path)[0],
                                   yaml=proto.decode("utf-8", errors="replace"))
        validation, steps, interface = self._derived(pid)
        owner = self.locked_by(pid)
        return DesignDoc(
            process_id=pid,
            head=_head(root),
            process_file=process_file,
            protos=protos,
            steps=steps,
            interface=interface,
            available_local=_available_local(ws, pdir, doc),
            conventions=DesignConventions(local_use=f"./{STEPS_DIR}/{{name}}",
                                          local_proto_path=f"{pdir}/{PROTO_DIR}/{{name}}.yaml"),
            validation=validation,
            locked_by=None if owner is None else LockOwner(chat_id=owner[0], turn_id=owner[1]),
        )

    def save(
        self,
        pid: str,
        req: SaveRequest,
        *,
        origin: Literal["web", "chat", "cli"] = "web",
        lock_owner: tuple[str, str] | None = None,
    ) -> SaveResult:
        """Checks run in the order Scope (`OutOfScope`), Lock (`DesignLocked`), Revision (`RevisionConflict`), Write
        (`Invalid`: a repeated path, a non-object doc, deleting `process.yaml`) and all precede any write; then
        every file is written (temp file + `os.replace`), validation/steps/interface are recomputed and, when
        `req.commit` is set, the scope is committed."""
        from wynd.controller.api.models_web import SavedCommit, SavedFile, SaveResult
        from wynd.spec.yamlio import dump_yaml

        root = self.ctx.root
        ws = self.ctx.workspace()
        pdir = _process_dir(ws, pid)
        process_path = f"{pdir}/{PROCESS_FILE}"
        paths = [PurePosixPath(write.path).as_posix() for write in req.writes]
        written = next((w.doc for p, w in zip(paths, req.writes, strict=True) if p == process_path and not w.delete),
                       None)
        roots = _root_protos(ws, _json_doc(_read(root, process_path) or b"", process_path)[0])
        roots |= _root_protos(ws, written)
        for path in paths:
            if not _in_scope(path, pdir, roots):
                raise OutOfScope(f"'{path}' is outside the design scope of process '{pid}'", details={"path": path},
                                 hint="a design save writes the process.yaml and proto-step YAML of this process only")

        with self._process_lock(pid):
            owner = self._owners.get(pid)
            if owner is not None and owner != lock_owner:
                raise DesignLocked(f"process '{pid}' is being edited by an assistant turn",
                                   details={"chat_id": owner[0], "turn_id": owner[1]})
            for path, write in zip(paths, req.writes, strict=True):
                current = _revision(_read(root, path))
                if write.base_revision != current:
                    raise RevisionConflict(f"'{path}' changed since it was read",
                                           details={"path": path, "current_revision": current})
            if len(set(paths)) != len(paths):
                raise Invalid("a save writes each path at most once")
            texts: list[str | None] = []                           # rendered before anything is written
            for path, write in zip(paths, req.writes, strict=True):
                if write.delete:
                    if path == process_path:
                        raise Invalid(f"'{path}' cannot be deleted by a design save")
                    texts.append(None)
                elif isinstance(write.doc, dict):
                    texts.append(dump_yaml(write.doc if path == process_path else _int_exit_codes(write.doc)))
                else:
                    raise Invalid(f"the document for '{path}' must be a JSON object")
            for path, text in zip(paths, texts, strict=True):
                _write(root / path, text)

        validation, steps, interface = self._derived(pid)
        info = None
        if req.commit is not None:
            info = self.commit(pid, reason=req.commit.reason, summary=req.commit.summary, origin=origin)
        files = [SavedFile(path=path, revision=None if text is None else _revision(text.encode()), yaml=text)
                 for path, text in zip(paths, texts, strict=True)]
        return SaveResult(
            files=files, validation=validation, steps=steps, interface=interface,
            commit=None if info is None else SavedCommit(sha=info.sha, message=info.subject), head=_head(root),
        )

    def commit(
        self,
        pid: str,
        *,
        reason: str,
        summary: str,
        origin: str = "web",
        extra_trailers: Mapping[str, str] | None = None,
    ) -> CommitInfo | None:
        """One commit of every dirty design-scope path (deletions included); None when none is dirty."""
        from wynd.controller.models import CommitInfo
        from wynd.process.git import commit_only, dirty_paths, git

        root = self.ctx.root
        ws = self.ctx.workspace()
        pdir = _process_dir(ws, pid)
        process_path = f"{pdir}/{PROCESS_FILE}"
        roots = _root_protos(ws, _json_doc(_read(root, process_path) or b"", process_path)[0])
        with self.ctx.git_lock:
            dirty = dirty_paths(root, [process_path, f"{pdir}/{PROTO_DIR}", *sorted(roots)])
            paths = [path for path in dirty if _in_scope(path, pdir, roots)]
            if not paths:
                return None
            summary = " ".join(summary.split())
            subject = f"design({pid}): " + (summary or "update " + ", ".join(PurePosixPath(p).name for p in paths[:3]))
            trailers = {"Wynd-Origin": origin, "Wynd-Reason": reason, **(extra_trailers or {})}
            message = subject + "\n\n" + "\n".join(f"{key}: {value}" for key, value in trailers.items())
            sha = commit_only(root, paths, message)
            if sha is None:
                return None
            short, author, at = git(root, "show", "-s", "--format=%h%x00%an%x00%aI", sha).strip().split("\0")
        return CommitInfo(sha=sha, short=short, subject=subject, author=author, at=datetime.fromisoformat(at),
                          message=message)

    def scope(self, pid: str) -> list[str]:
        """The design scope's existing local protos plus `process.yaml` and the referenced step-root protos (which
        may not exist yet); workspace-relative and sorted."""
        ws = self.ctx.workspace()
        pdir = _process_dir(ws, pid)
        process_path = f"{pdir}/{PROCESS_FILE}"
        return _scope(ws, pdir, _json_doc(_read(self.ctx.root, process_path) or b"", process_path)[0])

    def lock(self, pid: str, chat_id: str, turn_id: str) -> None:
        """Held for a chat turn acting on `pid`; saves by anyone but this owner fail with `DesignLocked`."""
        with self._process_lock(pid):
            owner = self._owners.get(pid)
            if owner is not None and owner != (chat_id, turn_id):
                raise DesignLocked(f"process '{pid}' is being edited by an assistant turn",
                                   details={"chat_id": owner[0], "turn_id": owner[1]})
            self._owners[pid] = (chat_id, turn_id)

    def unlock(self, pid: str, turn_id: str) -> None:
        """Releases the lock if `turn_id` holds it."""
        with self._process_lock(pid):
            owner = self._owners.get(pid)
            if owner is not None and owner[1] == turn_id:
                del self._owners[pid]

    def locked_by(self, pid: str) -> tuple[str, str] | None:
        return self._owners.get(pid)

    def _process_lock(self, pid: str) -> threading.Lock:
        with self._guard:
            return self._locks.setdefault(pid, threading.Lock())

    def _derived(self, pid: str) -> tuple[ValidationReportDTO, dict[str, StepInfo], ProcessInterface]:
        """Validation, steps and interface of the working tree; a process that does not load has only validation."""
        from wynd.controller.models import ProcessInterface

        validation = self.ctl.processes.validate(pid)
        try:
            return validation, self.ctl.processes.steps(pid), self.ctl.processes.interface(pid)
        except ValidationFailed:
            return validation, {}, ProcessInterface()


def _process_dir(ws: Workspace, pid: str) -> str:
    if pid not in ws.processes:
        raise NotFound(f"unknown process '{pid}'")
    return ws.processes[pid].dir


def _scope(ws: Workspace, pdir: str, doc: Any) -> list[str]:
    local = [path for path in ws.tree.files() if _is_local_proto(path, pdir)]
    return sorted({f"{pdir}/{PROCESS_FILE}", *local, *_root_protos(ws, doc)})


def _in_scope(path: str, pdir: str, roots: set[str]) -> bool:
    return path == f"{pdir}/{PROCESS_FILE}" or _is_local_proto(path, pdir) or path in roots


def _is_local_proto(path: str, pdir: str) -> bool:
    rel = PurePosixPath(path)
    return rel.parent.as_posix() == f"{pdir}/{PROTO_DIR}" and rel.suffix == ".yaml" and bool(PROTO_NAME.match(rel.stem))


def _uses(doc: Any) -> list[UseRef]:
    """The parseable `use:` references of a raw (possibly mid-edit) process document."""
    from wynd.spec.workspace import parse_use

    steps = doc.get("steps") if isinstance(doc, dict) else None
    refs = []
    for ref in steps.values() if isinstance(steps, dict) else ():
        try:
            refs.append(parse_use(ref.get("use") if isinstance(ref, dict) else None))
        except ValueError:
            continue
    return refs


def _root_protos(ws: Workspace, doc: Any) -> set[str]:
    """`<step root>/<path>/proto.yaml` of every step-root step the document references through a configured alias."""
    paths = set()
    for use in _uses(doc):
        step_root = ws.config.step_roots.get(use.alias) if use.form == "root" else None
        if step_root is not None and step_root.path is not None:
            paths.add(f"{step_root.path}/{use.target}/{STEP_PROTO_FILE}")
    return paths


def _available_local(ws: Workspace, pdir: str, doc: Any) -> list[AvailableLocalStep]:
    """Local protos and packages of the process that its `steps:` do not reference."""
    from wynd.controller.api.models_web import AvailableLocalStep

    files = ws.tree.files()
    protos = {PurePosixPath(path).stem: path for path in files if _is_local_proto(path, pdir)}
    steps_dir = f"{pdir}/{STEPS_DIR}/"
    packages = set()
    for path in files:
        if not path.startswith(steps_dir):
            continue
        name, _, rest = path[len(steps_dir):].partition("/")
        if rest in PACKAGE_MARKERS:
            packages.add(name)
    referenced = {use.target for use in _uses(doc) if use.form == "local"}
    available = []
    for name in sorted((protos.keys() | packages) - referenced):
        lock_path = f"{steps_dir}{name}/{STEP_LOCK_FILE}"
        has_lock = lock_path in files
        available.append(AvailableLocalStep(
            use=f"./{STEPS_DIR}/{name}", proto_path=protos.get(name),
            source_path=f"{steps_dir}{name}" if has_lock else None,
            phase=_phase(ws.tree, protos.get(name), lock_path if has_lock else None),
        ))
    return available


def _phase(tree: Tree, proto_path: str | None, lock_path: str | None) -> StepPhase:
    """As `ProcessService` reports a step: design without a lock or with a stale one, handwritten without a proto."""
    from wynd.process.workspace import read_text
    from wynd.spec.errors import SpecError
    from wynd.spec.hashing import proto_hash
    from wynd.spec.lockfiles import StepLock
    from wynd.spec.proto_step import ProtoStep
    from wynd.spec.yamlio import parse_model

    if lock_path is None:
        return "missing" if proto_path is None else "design"
    try:
        lock = parse_model(read_text(tree, lock_path), StepLock, lock_path)
        proto = None if proto_path is None else parse_model(read_text(tree, proto_path), ProtoStep, proto_path)
    except SpecError:
        return "design"
    if proto is None:
        return "handwritten"
    return "compiled" if lock.proto_hash == proto_hash(proto) else "design"


def _json_doc(data: bytes, path: str) -> tuple[dict | None, Diagnostic | None]:
    """(JSON-safe document, None) or (None, the YAML diagnostic)."""
    from wynd.spec.yamlio import yaml_to_json

    return yaml_to_json(data.decode("utf-8", errors="replace"), path)


def _int_exit_codes(doc: dict) -> dict:
    """A proto's `exit_codes` with decimal keys as ints, where the key keeps its position."""
    codes = doc.get("exit_codes")
    if not isinstance(codes, dict):
        return doc
    fixed = {int(k) if isinstance(k, str) and k.isascii() and k.isdigit() else k: v for k, v in codes.items()}
    return {**doc, "exit_codes": fixed}


def _read(root: Path, path: str) -> bytes | None:
    file = root / path
    return file.read_bytes() if file.is_file() else None


def _revision(data: bytes | None) -> str | None:
    return None if data is None else f"sha256:{hashlib.sha256(data).hexdigest()}"


def _write(path: Path, text: str | None) -> None:
    if text is None:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_bytes(text.encode())
    os.replace(tmp, path)


def _head(root: Path) -> str:
    """The workspace HEAD; "" before the first commit."""
    from wynd.process.errors import GitError
    from wynd.process.git import rev_parse

    try:
        return rev_parse(root, "HEAD")
    except GitError:
        return ""
