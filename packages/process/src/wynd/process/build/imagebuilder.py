"""Image builders behind one interface (PLAN §6.5, §15 item 46; owner PROC-BUILD, M2; `$DRAFTS/04 §8.6`).

Backends by `WYND_IMAGE_BUILDER` (default `buildx`) from the `wynd.image_builders` entry points. `BuildxImageBuilder`
runs `docker buildx build` (BuildKit, docker driver) so `FROM wynd-base:<ver>-<variant>` resolves from the local
image store; it writes `buildkit-metadata.json` into the context dir.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path
from typing import Protocol

from .. import _proc
from ..errors import WyndProcessError

BUILDER_GROUP = "wynd.image_builders"
METADATA_FILE = "buildkit-metadata.json"
DIGEST = re.compile(r"digest: (sha256:[0-9a-f]{64})")


@dataclass(frozen=True)
class ImageBuildRequest:
    context: Path
    dockerfile: Path
    tags: tuple[str, ...]
    labels: Mapping[str, str] = field(default_factory=dict)
    platforms: tuple[str, ...] = ()               # () = builder native
    build_args: Mapping[str, str] = field(default_factory=dict)
    push: bool = False                            # push all tags as part of the build (multi-platform publish)


@dataclass(frozen=True)
class ImageBuildResult:
    tags: tuple[str, ...]
    image_id: str | None
    digest: str | None
    size_bytes: int | None = None                 # size of the loaded image; None when pushed or unknown


class ImageBuilder(Protocol):
    name: str

    def build(self, req: ImageBuildRequest, log: Callable[[str], None]) -> ImageBuildResult: ...

    def exists(self, ref: str) -> bool: ...

    def push(self, ref: str, log: Callable[[str], None]) -> str: ...        # returns the repo digest "sha256:…"

    def tag(self, src: str, dst: str) -> None: ...

    def login(self, registry_host: str, username: str, password: str) -> None: ...

    def native_platform(self) -> str: ...                                    # e.g. "linux/arm64"


class BuildxImageBuilder:
    name = "buildx"

    def build(self, req: ImageBuildRequest, log: Callable[[str], None]) -> ImageBuildResult:
        meta_path = Path(req.context) / METADATA_FILE
        args = ["buildx", "build", "--progress=plain", "--file", str(req.dockerfile)]
        for tag in req.tags:
            args += ["--tag", tag]
        for key, value in req.labels.items():
            args += ["--label", f"{key}={value}"]
        for key, value in req.build_args.items():
            args += ["--build-arg", f"{key}={value}"]
        if req.platforms:
            args += ["--platform", ",".join(req.platforms)]
        args += ["--push" if req.push else "--load", "--metadata-file", str(meta_path), str(req.context)]
        self._docker(args, log=log)
        meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
        size = None if req.push or not req.tags else self._size(req.tags[0])
        return ImageBuildResult(tags=tuple(req.tags), image_id=meta.get("containerimage.config.digest"),
                                digest=meta.get("containerimage.digest"), size_bytes=size)

    def exists(self, ref: str) -> bool:
        proc = subprocess.run([_proc.require_tool("docker"), "image", "inspect", ref], capture_output=True)
        return proc.returncode == 0

    def push(self, ref: str, log: Callable[[str], None]) -> str:
        out = self._docker(["push", ref], log=log)
        match = DIGEST.search(out)
        if match is None:
            raise WyndProcessError(f"docker push {ref} reported no digest")
        return match[1]

    def tag(self, src: str, dst: str) -> None:
        self._docker(["tag", src, dst])

    def login(self, registry_host: str, username: str, password: str) -> None:
        self._docker(["login", registry_host, "-u", username, "--password-stdin"], input=password)

    def native_platform(self) -> str:
        return self._docker(["version", "--format", "{{.Server.Os}}/{{.Server.Arch}}"]).strip()

    def _size(self, ref: str) -> int | None:
        out = self._docker(["image", "inspect", "--format", "{{.Size}}", ref]).strip()
        return int(out) if out.isdigit() else None

    def _docker(self, args: list[str], *, log: Callable[[str], None] | None = None, input: str | None = None) -> str:
        try:
            return _proc.run([_proc.require_tool("docker"), *args], cwd=tempfile.gettempdir(), log=log, input=input)
        except subprocess.CalledProcessError as err:
            lines = (err.stderr or "").strip().splitlines()
            detail = lines[-1] if lines else f"exit {err.returncode}"
            raise WyndProcessError(f"docker {' '.join(args[:2])} failed: {detail}") from None


def open_image_builder(environ: Mapping[str, str] = os.environ) -> ImageBuilder:
    """The backend `WYND_IMAGE_BUILDER` names (default `buildx`) from the `wynd.image_builders` entry points."""
    name = environ.get("WYND_IMAGE_BUILDER") or "buildx"
    found = list(metadata.entry_points(group=BUILDER_GROUP, name=name))
    if not found:
        raise ValueError(f"unknown WYND_IMAGE_BUILDER {name!r} (no {BUILDER_GROUP} entry point {name!r} is installed)")
    return found[0].load()()
