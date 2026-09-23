"""Image builders behind one interface (PLAN §6.5, §15 item 46; owner PROC-BUILD, M2; `$DRAFTS/04 §8.6`).

Backends by `WYND_IMAGE_BUILDER` (default `buildx`) from the `wynd.image_builders` entry points. `BuildxImageBuilder`
runs `docker buildx build` (BuildKit, docker driver) so `FROM wynd-base:<ver>-<variant>` resolves from the local
image store; it writes `buildkit-metadata.json` into the context dir.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


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
        raise NotImplementedError("PLAN §6.5 BuildxImageBuilder.build")

    def exists(self, ref: str) -> bool:
        raise NotImplementedError("PLAN §6.5 BuildxImageBuilder.exists")

    def push(self, ref: str, log: Callable[[str], None]) -> str:
        raise NotImplementedError("PLAN §6.5 BuildxImageBuilder.push")

    def tag(self, src: str, dst: str) -> None:
        raise NotImplementedError("PLAN §6.5 BuildxImageBuilder.tag")

    def login(self, registry_host: str, username: str, password: str) -> None:
        raise NotImplementedError("PLAN §6.5 BuildxImageBuilder.login")

    def native_platform(self) -> str:
        raise NotImplementedError("PLAN §6.5 BuildxImageBuilder.native_platform")


def open_image_builder(environ: Mapping[str, str] = os.environ) -> ImageBuilder:
    raise NotImplementedError("PLAN §6.5 open_image_builder")
