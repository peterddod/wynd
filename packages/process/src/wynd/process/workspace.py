"""Workspace discovery and the git-backed `Tree` the loader reads through (PLAN §3.1, §3.19, §6.1; owner PROC-WS).

Discovery `$DRAFTS/04 §3.3`; root rules map to spec `E-ROOTS` and `E103`–`E106`. `WorkingTree` lists files with
`git ls-files -co --exclude-standard` (`.wynd/` always excluded) and hashes blobs with one batched
`git hash-object --stdin-paths` (clean filters applied); `CommitTree` reads `git ls-tree -r` / `cat-file`.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic
    from wynd.spec.workspace import WorkspaceConfig

    from .loader import LoadedProcess


class Tree(Protocol):
    root: Path                                   # workspace root on disk (where git runs)

    def files(self) -> frozenset[str]: ...       # workspace-relative POSIX file paths

    def read_bytes(self, path: str) -> bytes: ...

    def blob_id(self, path: str) -> str: ...     # git blob sha1 of the content (as committed)

    def is_dir(self, path: str) -> bool: ...     # any file starts with path + "/"


class WorkingTree:
    """The checkout on disk, as git would commit it."""

    def __init__(self, ws_root: Path) -> None:
        self.root = Path(ws_root)

    def files(self) -> frozenset[str]:
        raise NotImplementedError("PLAN §3.19 WorkingTree.files")

    def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError("PLAN §3.19 WorkingTree.read_bytes")

    def blob_id(self, path: str) -> str:
        raise NotImplementedError("PLAN §3.19 WorkingTree.blob_id")

    def is_dir(self, path: str) -> bool:
        raise NotImplementedError("PLAN §3.19 WorkingTree.is_dir")


class CommitTree:
    """A commit, read without checking it out."""

    def __init__(self, ws_root: Path, sha: str) -> None:
        self.root = Path(ws_root)
        self.sha = sha

    def files(self) -> frozenset[str]:
        raise NotImplementedError("PLAN §3.19 CommitTree.files")

    def read_bytes(self, path: str) -> bytes:
        raise NotImplementedError("PLAN §3.19 CommitTree.read_bytes")

    def blob_id(self, path: str) -> str:
        raise NotImplementedError("PLAN §3.19 CommitTree.blob_id")

    def is_dir(self, path: str) -> bool:
        raise NotImplementedError("PLAN §3.19 CommitTree.is_dir")


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

    def process_ids(self) -> list[str]:
        raise NotImplementedError("PLAN §6.1 Workspace.process_ids")

    def process_dir(self, pid: str) -> Path:
        raise NotImplementedError("PLAN §6.1 Workspace.process_dir")

    def process_root_of(self, pid: str) -> str:
        raise NotImplementedError("PLAN §6.1 Workspace.process_root_of")

    def load_process(self, pid: str) -> LoadedProcess:
        # Implemented in loader.py (PLAN §6.1): resolution, phases, interfaces, cycles; memoised per Workspace.
        from .loader import load_process

        return load_process(self, pid)


def load_workspace(root: Path, tree: Tree | None = None) -> Workspace:
    raise NotImplementedError("PLAN §6.1 load_workspace")


def find_workspace_root(start: Path | None = None) -> Path | None:
    raise NotImplementedError("PLAN §6.1 find_workspace_root")
