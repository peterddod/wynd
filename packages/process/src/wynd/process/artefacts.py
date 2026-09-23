"""Build artefact store and image-registry entries (PLAN §3.14, §6.1; owner PROC-GIT; `$DRAFTS/04 §11`).

`LocalArtefactStore` keeps builds at `<state_dir>/build/<pid>/<commit>/` (a build of the same commit replaces the
previous one). `open_artefact_store` selects the backend by `WYND_ARTEFACT_STORE` (default `local`) from the
`wynd.artefact_stores` entry points. `BuildInfo` and `ImageRegistryEntry` are the W0-complete contract.
"""

from __future__ import annotations

import shutil
from collections.abc import Mapping
from datetime import datetime
from importlib import metadata
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from wynd.spec.env_manifest import EnvManifest

BUILD_FILE = "build.json"
STORE_GROUP = "wynd.artefact_stores"


class BuildInfo(BaseModel):
    process: str
    commit: str
    source_sha: str
    job_id: str | None
    process_hash: str
    image: str | None                     # local tag, e.g. wynd/process_supplier_invoice:9f1c0a2b3c4d
    image_id: str | None
    image_digest: str | None
    pushed: list[str] = []                # ["<registry>/<repo>:<tag>@sha256:…"]
    base: dict                            # BaseChoice as dict
    manifest: EnvManifest
    bake: str | None = None               # path to the .pyz when the target was bake
    created_at: datetime
    dir: str                              # local path (or URI for remote stores)


class ImageRegistryEntry(BaseModel):      # user-registry section "registries"
    name: str
    url: str                              # e.g. "localhost:5001/wynd" or "ghcr.io/peterddod"
    username_env: str | None = None
    password_env: str | None = None
    insecure: bool = False
    default: bool = False


class ArtefactStore(Protocol):
    def put_build(self, staged_dir: Path, info: BuildInfo) -> BuildInfo: ...   # moves/uploads; writes build.json; returns info with dir

    def get_build(self, pid: str, commit: str) -> BuildInfo | None: ...

    def list_builds(self, pid: str) -> list[BuildInfo]: ...                    # newest first

    def local_dir(self, pid: str, commit: str) -> Path: ...                    # materialise locally


class LocalArtefactStore:
    """Builds at `<state_dir>/build/<pid>/<commit>/`, each described by its `build.json` (a `BuildInfo`)."""

    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)
        self.root = self.state_dir / "build"

    def put_build(self, staged_dir: Path, info: BuildInfo) -> BuildInfo:
        """Move `staged_dir` into place (replacing an earlier build of the same commit) and write `build.json`."""
        dest = self.root / info.process / info.commit
        if dest.exists():
            shutil.rmtree(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(staged_dir, dest)
        stored = info.model_copy(update={"dir": str(dest)})
        (dest / BUILD_FILE).write_text(stored.model_dump_json(indent=2) + "\n")
        return stored

    def get_build(self, pid: str, commit: str) -> BuildInfo | None:
        path = self.root / pid / commit / BUILD_FILE
        if not path.is_file():
            return None
        return BuildInfo.model_validate_json(path.read_text())

    def list_builds(self, pid: str) -> list[BuildInfo]:
        """Every build of `pid`, newest first."""
        infos = [BuildInfo.model_validate_json(p.read_text()) for p in (self.root / pid).glob(f"*/{BUILD_FILE}")]
        return sorted(infos, key=lambda info: info.created_at, reverse=True)

    def local_dir(self, pid: str, commit: str) -> Path:
        path = self.root / pid / commit
        if not (path / BUILD_FILE).is_file():
            raise FileNotFoundError(f"no build of process {pid} at {commit} in {self.root}")
        return path


def open_artefact_store(state_dir: Path, environ: Mapping[str, str]) -> ArtefactStore:
    """The backend `WYND_ARTEFACT_STORE` names (default `local`) from the `wynd.artefact_stores` entry points,
    constructed with `state_dir`."""
    name = environ.get("WYND_ARTEFACT_STORE") or "local"
    found = list(metadata.entry_points(group=STORE_GROUP, name=name))
    if not found:
        raise ValueError(f"unknown WYND_ARTEFACT_STORE {name!r} (no {STORE_GROUP} entry point {name!r} is installed)")
    return found[0].load()(Path(state_dir))
