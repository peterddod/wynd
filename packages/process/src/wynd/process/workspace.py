"""Workspace discovery and the git-backed `Tree` the loader reads through (PLAN §3.1, §3.19, §6.1; owner PROC-WS).

Discovery `$DRAFTS/04 §3.3`; root rules map to spec `E-ROOTS` (shape and overlap, raised while loading `wynd.yaml`)
and `E103` (invalid segment, missing root). `WorkingTree` lists files with `git ls-files -co --exclude-standard`
(`.wynd/` always excluded) and hashes blobs with one batched `git hash-object --stdin-paths` (clean filters applied);
`CommitTree` reads `git ls-tree -r` / `cat-file`.

Diagnostics that concern one process carry `process=<id>` and are copied into that process's
`LoadedProcess.diagnostics`; the others (roots, root step packages, stray markers) stay on `Workspace.diagnostics`.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from wynd.spec.errors import Diagnostic, Loc, SpecError
from wynd.spec.workspace import (
    PROCESS_FILE,
    STATE_DIR,
    STEP_LOCK_FILE,
    STEP_PROTO_FILE,
    STEPS_DIR,
    WORKSPACE_FILE,
    WorkspaceConfig,
    root_step_id,
)
from wynd.spec.workspace import find_workspace_root as find_marker
from wynd.spec.yamlio import SourceMap, located, parse_model, parse_yaml

from ._proc import require_tool
from .errors import CODES, GitError, LoadError, ProcessNotFound

if TYPE_CHECKING:
    from .loader import LoadedProcess

SEG = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")
PYPROJECT_FILE = "pyproject.toml"
PACKAGE_MARKERS = frozenset({PYPROJECT_FILE, STEP_LOCK_FILE, STEP_PROTO_FILE})


class Tree(Protocol):
    root: Path                                   # workspace root on disk (where git runs)

    def files(self) -> frozenset[str]: ...       # workspace-relative POSIX file paths

    def read_bytes(self, path: str) -> bytes: ...

    def blob_id(self, path: str) -> str: ...     # git blob sha1 of the content (as committed)

    def is_dir(self, path: str) -> bool: ...     # any file starts with path + "/"


class WorkingTree:
    """The checkout on disk, as git would commit it: tracked and untracked-but-not-ignored files that exist, never
    `.wynd/`. The file list and blob ids are read once, on first use; make a new tree to see later changes."""

    def __init__(self, ws_root: Path) -> None:
        self.root = Path(ws_root)
        self._files: frozenset[str] | None = None
        self._blobs: dict[str, str] | None = None

    def files(self) -> frozenset[str]:
        if self._files is None:
            out = _git_bytes(
                self.root, "ls-files", "-z", "--cached", "--others", "--exclude-standard", "--", ".",
                f":(exclude){STATE_DIR}",
            )
            listed = {path for path in out.decode().split("\0") if path and not path.endswith("/")}
            self._files = frozenset(path for path in listed if (self.root / path).is_file())
        return self._files

    def read_bytes(self, path: str) -> bytes:
        return (self.root / path).read_bytes()

    def blob_id(self, path: str) -> str:
        if self._blobs is None:
            paths = sorted(self.files())
            base = self.root.absolute()
            # Absolute paths: `--stdin-paths` resolves relative ones against the repository top level, not `-C`.
            stdin = "".join(f"{base / path}\n" for path in paths).encode()
            ids = _git_bytes(self.root, "hash-object", "--stdin-paths", input=stdin).decode().split()
            self._blobs = dict(zip(paths, ids, strict=True))
        try:
            return self._blobs[path]
        except KeyError:
            raise FileNotFoundError(path) from None

    def is_dir(self, path: str) -> bool:
        return _has_dir(self.files(), path)


class CommitTree:
    """A commit, read without checking it out. `sha` may be any commit-ish; it is resolved to the full sha once."""

    def __init__(self, ws_root: Path, sha: str) -> None:
        self.root = Path(ws_root)
        try:
            resolved = _git_bytes(
                self.root, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{sha}^{{commit}}"
            )
        except GitError:
            raise GitError(f"'{sha}' is not a commit in the repository of {self.root}") from None
        self.sha = resolved.decode().strip()
        self._blobs: dict[str, str] | None = None

    def _entries(self) -> dict[str, str]:
        if self._blobs is None:
            out = _git_bytes(self.root, "ls-tree", "-r", "-z", self.sha, "--", ".")
            blobs: dict[str, str] = {}
            for entry in out.decode().split("\0"):
                if not entry:
                    continue
                meta, path = entry.split("\t", 1)
                _mode, kind, blob = meta.split()
                if kind == "blob" and path != STATE_DIR and not path.startswith(f"{STATE_DIR}/"):
                    blobs[path] = blob
            self._blobs = blobs
        return self._blobs

    def files(self) -> frozenset[str]:
        return frozenset(self._entries())

    def read_bytes(self, path: str) -> bytes:
        return _git_bytes(self.root, "cat-file", "blob", self.blob_id(path))

    def blob_id(self, path: str) -> str:
        try:
            return self._entries()[path]
        except KeyError:
            raise FileNotFoundError(f"{path} (commit {self.sha})") from None

    def is_dir(self, path: str) -> bool:
        return _has_dir(self._entries(), path)


def _git_bytes(cwd: Path, *args: str, input: bytes | None = None) -> bytes:
    """`git -C <cwd> <args>` stdout; `GitError` with git's stderr on failure."""
    proc = subprocess.run([require_tool("git"), "-C", str(cwd), *args], input=input, capture_output=True)
    if proc.returncode != 0:
        detail = proc.stderr.decode(errors="replace").strip()
        raise GitError(f"git {' '.join(args)} failed in {cwd}" + (f": {detail}" if detail else ""))
    return proc.stdout


def _has_dir(files: Any, path: str) -> bool:
    prefix = f"{path.rstrip('/')}/" if path else ""
    return any(file.startswith(prefix) for file in files)


@dataclass(frozen=True)
class ProcessEntry:
    id: str                                      # path relative to its process root
    root: str                                    # workspace-relative process root
    dir: str                                     # workspace-relative process dir


@dataclass(frozen=True)
class StepEntry:
    id: str                                      # "<alias>:<path>"
    alias: str
    path: str                                    # relative to the step root
    dir: str                                     # workspace-relative package dir
    proto_path: str | None                       # "<dir>/proto.yaml" when present


@dataclass
class Workspace:
    root: Path
    tree: Tree
    config: WorkspaceConfig
    processes: dict[str, ProcessEntry]
    root_steps: dict[str, StepEntry]
    diagnostics: list[Diagnostic]
    _loaded: dict[str, LoadedProcess] = field(default_factory=dict, init=False, repr=False, compare=False)

    def process_ids(self) -> list[str]:
        return sorted(self.processes)

    def process_dir(self, pid: str) -> Path:
        return self.root / self._entry(pid).dir

    def process_root_of(self, pid: str) -> str:
        return self._entry(pid).root

    def load_process(self, pid: str) -> LoadedProcess:
        # Implemented in loader.py (PLAN §6.1): resolution, phases, interfaces, cycles; memoised per Workspace.
        from .loader import load_process

        return load_process(self, pid)

    def _entry(self, pid: str) -> ProcessEntry:
        try:
            return self.processes[pid]
        except KeyError:
            raise ProcessNotFound(pid) from None


def load_workspace(root: Path, tree: Tree | None = None) -> Workspace:
    """Discover processes and root step packages under `root` (a git working tree unless `tree` is given).

    Raises `LoadError` for a missing `wynd.yaml` (E100), a root outside git (E101) or an invalid `wynd.yaml` (spec
    codes); every other finding is a diagnostic."""
    root = Path(root).absolute()
    if tree is None:
        if not (root / WORKSPACE_FILE).is_file():
            raise LoadError([diagnostic("E100", file=WORKSPACE_FILE, start=root)])
        if not _inside_git(root):
            raise LoadError([diagnostic("E101", root=root)])
        tree = WorkingTree(root)
    files = tree.files()
    if WORKSPACE_FILE not in files:
        raise LoadError([diagnostic("E100", file=WORKSPACE_FILE, start=root)])
    try:
        config = _load_config(read_text(tree, WORKSPACE_FILE))
    except SpecError as err:
        raise LoadError(err.diagnostics) from None

    process_roots = list(config.process_roots)
    step_roots = {alias: step_root.path for alias, step_root in config.step_roots.items()}
    source: SourceMap | None = config._source
    diagnostics: list[Diagnostic] = []
    for i, path in enumerate(process_roots):
        diagnostics += _root_problems(tree, "process", path, ("process_roots", i), source)
    for alias, path in step_roots.items():
        diagnostics += _root_problems(tree, "step", path, ("step_roots", alias), source)

    processes = _discover_processes(files, process_roots, step_roots, diagnostics)
    root_steps = _discover_root_steps(files, step_roots, diagnostics)
    _check_local_packages(files, process_roots, processes, diagnostics)
    return Workspace(root, tree, config, processes, root_steps, diagnostics)


def find_workspace_root(start: Path | None = None) -> Path | None:
    """`WYND_WORKSPACE` when set, else the nearest ancestor of `start` (default: cwd) containing `wynd.yaml`."""
    env = os.environ.get("WYND_WORKSPACE")
    if env:
        return Path(env).absolute()
    return find_marker(Path(start) if start is not None else Path.cwd())


# --- discovery ------------------------------------------------------------------------------------------------------

def _discover_processes(
    files: frozenset[str], process_roots: list[str], step_roots: dict[str, str], diagnostics: list[Diagnostic]
) -> dict[str, ProcessEntry]:
    processes: dict[str, ProcessEntry] = {}
    for d in sorted(_parent(f) for f in files if _name(f) == PROCESS_FILE):
        file = join(d, PROCESS_FILE)
        process_root = _root_of(d, process_roots)
        if process_root is None:
            alias = next((a for a, r in step_roots.items() if _root_of(d, [r])), None)
            if alias is not None:
                diagnostics.append(diagnostic("E114", file=file, path=file, alias=alias))
            continue  # a process.yaml outside every root is not a process (fixtures, docs)
        pid = d[len(process_root) + 1:]
        bad = _bad_segment(pid)
        if bad is not None:
            diagnostics.append(diagnostic("E116", file=file, path=d, seg=bad))
        elif pid in processes:
            diagnostics.append(diagnostic("E112", file=file, process=pid, id=pid, a=processes[pid].root,
                                          b=process_root))
        else:
            processes[pid] = ProcessEntry(pid, process_root, d)
    dirs = {entry.dir: pid for pid, entry in processes.items()}
    for pid, entry in sorted(processes.items()):
        outer = next((dirs[a] for a in _ancestors(entry.dir) if a in dirs), None)
        if outer is not None:
            diagnostics.append(diagnostic("E110", file=join(entry.dir, PROCESS_FILE), process=pid, inner=pid,
                                          outer=outer))
    return processes


def _discover_root_steps(
    files: frozenset[str], step_roots: dict[str, str], diagnostics: list[Diagnostic]
) -> dict[str, StepEntry]:
    root_steps: dict[str, StepEntry] = {}
    for alias, step_root in step_roots.items():
        markers: dict[str, set[str]] = {}
        for f in files:
            if _name(f) in PACKAGE_MARKERS and _root_of(_parent(f), [step_root]):
                markers.setdefault(_parent(f), set()).add(_name(f))
        for d, found in sorted(markers.items()):
            has_py, has_lock, has_proto = (m in found for m in (PYPROJECT_FILE, STEP_LOCK_FILE, STEP_PROTO_FILE))
            if not (has_py and has_lock) and not has_proto:
                present, missing = (PYPROJECT_FILE, STEP_LOCK_FILE) if has_py else (STEP_LOCK_FILE, PYPROJECT_FILE)
                diagnostics.append(diagnostic("E123", file=join(d, present), dir=d, present=present, missing=missing))
                continue
            path = d[len(step_root) + 1:]
            bad = _bad_segment(path)
            if bad is not None:
                diagnostics.append(diagnostic("E116", file=join(d, STEP_PROTO_FILE if has_proto else STEP_LOCK_FILE),
                                              path=d, seg=bad))
                continue
            sid = root_step_id(alias, path)
            root_steps[sid] = StepEntry(sid, alias, path, d, join(d, STEP_PROTO_FILE) if has_proto else None)
    dirs = {entry.dir for entry in root_steps.values()}
    for entry in sorted(root_steps.values(), key=lambda e: e.dir):
        outer = next((a for a in _ancestors(entry.dir) if a in dirs), None)
        if outer is not None:
            diagnostics.append(diagnostic("E111", file=entry.proto_path or join(entry.dir, STEP_LOCK_FILE),
                                          inner=entry.dir, outer=outer))
    return root_steps


def _check_local_packages(
    files: frozenset[str], process_roots: list[str], processes: dict[str, ProcessEntry], diagnostics: list[Diagnostic]
) -> None:
    """Every compiled package under a process root sits at exactly `<process dir>/steps/<name>` (E115) and packages
    do not nest (E111)."""
    process_dirs = {entry.dir: pid for pid, entry in processes.items()}
    lock_dirs = sorted(_parent(f) for f in files if _name(f) == STEP_LOCK_FILE and _root_of(_parent(f), process_roots))
    lock_set = set(lock_dirs)
    for d in lock_dirs:
        file = join(d, STEP_LOCK_FILE)
        owner = next((a for a in _ancestors(d) if a in process_dirs), None)
        pid = process_dirs.get(owner)
        parts = d[len(owner) + 1:].split("/") if owner is not None else []
        if len(parts) != 2 or parts[0] != STEPS_DIR or not SEG.match(parts[1]):
            diagnostics.append(diagnostic("E115", file=file, process=pid, path=d))
        outer = next((a for a in _ancestors(d) if a in lock_set), None)
        if outer is not None:
            diagnostics.append(diagnostic("E111", file=file, process=pid, inner=d, outer=outer))


def _root_problems(tree: Tree, kind: str, path: str, loc: Loc, source: SourceMap | None) -> list[Diagnostic]:
    bad = _bad_segment(path)
    if bad is not None:
        return [diagnostic("E103", source=source, loc=loc, kind=kind, path=path, reason=f"invalid segment '{bad}'")]
    if tree.is_dir(path):
        return []
    # An empty root on disk is fine (wynd init), but only the checkout itself may consult the disk: any other tree
    # (a commit) holds a root exactly when it holds a file under it.
    if isinstance(tree, WorkingTree) and (tree.root / path).is_dir():
        return []
    return [diagnostic("E103", source=source, loc=loc, kind=kind, path=path, reason="does not exist")]


# --- helpers shared with loader.py ----------------------------------------------------------------------------------

def diagnostic(
    code: str,
    *,
    source: SourceMap | None = None,
    loc: Loc = (),
    file: str | None = None,
    process: str | None = None,
    **fields: Any,
) -> Diagnostic:
    """A diagnostic from the `CODES` template; located through `source` when the document was parsed."""
    severity, template = CODES[code]
    message = template.format(**fields)
    if source is not None:
        return located(source, severity, code, message, loc, process=process)
    return Diagnostic(severity, code, message, file=file, loc=tuple(loc), process=process)


def _load_config(text: str) -> WorkspaceConfig:
    """`wynd.yaml` text; an empty document (a bare marker file) means all defaults. Raises SpecError."""
    data, _ = parse_yaml(text, WORKSPACE_FILE)
    if data is None:
        return WorkspaceConfig()
    return parse_model(text, WorkspaceConfig, WORKSPACE_FILE)


def read_text(tree: Tree, path: str) -> str:
    """UTF-8 text of a tree file; SpecError (E-YAML) when it is not UTF-8."""
    try:
        return tree.read_bytes(path).decode("utf-8")
    except UnicodeDecodeError as err:
        raise SpecError([Diagnostic("error", "E-YAML", f"not valid UTF-8 (byte {err.start})", file=path)]) from None


def _inside_git(path: Path) -> bool:
    try:
        _git_bytes(path, "rev-parse", "--show-prefix")
    except GitError:
        return False
    return True


def _bad_segment(rel: str) -> str | None:
    """The first segment of a relative id path that is not a valid id segment ("" for an empty path)."""
    return next((seg for seg in rel.split("/") if not SEG.match(seg)), None)


def _root_of(d: str, roots: list[str]) -> str | None:
    return next((r for r in roots if d == r or d.startswith(f"{r}/")), None)


def _ancestors(d: str) -> list[str]:
    """Proper ancestors of a relative dir, nearest first."""
    parts = d.split("/")
    return ["/".join(parts[:i]) for i in range(len(parts) - 1, 0, -1)]


def _parent(path: str) -> str:
    return path.rpartition("/")[0]


def _name(path: str) -> str:
    return path.rpartition("/")[2]


def join(*parts: str) -> str:
    return "/".join(part for part in parts if part)
