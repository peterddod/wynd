"""Build artefact store and image-registry entries (PLAN §3.14, §6.1; owner PROC-GIT; `$DRAFTS/04 §11`).

`LocalArtefactStore` keeps builds at `<state_dir>/build/<pid>/<commit>/` (a build of the same commit replaces the
previous one). `open_artefact_store` selects the backend by `WYND_ARTEFACT_STORE` (default `local`) from the
`wynd.artefact_stores` entry points. `BuildInfo` and `ImageRegistryEntry` are the W0-complete contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from wynd.spec.env_manifest import EnvManifest


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
    def __init__(self, state_dir: Path) -> None:
        self.state_dir = Path(state_dir)

    def put_build(self, staged_dir: Path, info: BuildInfo) -> BuildInfo:
        raise NotImplementedError("PLAN §6.1 LocalArtefactStore.put_build")

    def get_build(self, pid: str, commit: str) -> BuildInfo | None:
        raise NotImplementedError("PLAN §6.1 LocalArtefactStore.get_build")

    def list_builds(self, pid: str) -> list[BuildInfo]:
        raise NotImplementedError("PLAN §6.1 LocalArtefactStore.list_builds")

    def local_dir(self, pid: str, commit: str) -> Path:
        raise NotImplementedError("PLAN §6.1 LocalArtefactStore.local_dir")


def open_artefact_store(state_dir: Path, environ: Mapping[str, str]) -> ArtefactStore:
    raise NotImplementedError("PLAN §6.1 open_artefact_store")
