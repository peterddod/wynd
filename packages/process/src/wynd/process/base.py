"""The `wynd-base` image family (PLAN §6.5, §15 item 50; owner PROC-BUILD, M2; `$DRAFTS/04 §9`).

One template (`templates/base.Dockerfile`), variants `slim` (`python:3.12-slim-trixie`) and `alpine`
(`python:3.12-alpine3.22`); refs `<WYND_BASE_REPO or "wynd-base">:<version>-<variant>`. `publish_base` reads the
target from the user registry section `registries` (validated with `artefacts.ImageRegistryEntry`) and pushes
`linux/amd64,linux/arm64`. `ensure_base` builds a missing local base on demand.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.runtime.storage.base import Registry

    from .build.imagebuilder import ImageBuilder
    from .build.resolve import Resolver
    from .venvs import RuntimeSource

UV_VERSION = "0.10.7"
PYTHON_IMAGES = {"slim": "python:3.12-slim-trixie", "alpine": "python:3.12-alpine3.22"}


def base_image_ref(version: str, variant: str, repo: str | None = None) -> str:
    raise NotImplementedError("PLAN §6.5 base_image_ref")


def render_base_dockerfile(version: str, variant: Literal["slim", "alpine"]) -> str:
    raise NotImplementedError("PLAN §6.5 render_base_dockerfile")


def build_base(
    version: str,
    variants: Sequence[str] = ("slim", "alpine"),
    *,
    log: Callable[[str], None],
    image_builder: ImageBuilder | None = None,
    runtime: RuntimeSource | None = None,
    resolver: Resolver | None = None,
    repo: str | None = None,
    platforms: Sequence[str] = (),
    push: bool = False,
) -> list[str]:
    raise NotImplementedError("PLAN §6.5 build_base")


def publish_base(
    version: str,
    variants: Sequence[str],
    registry_name: str,
    *,
    registry: Registry,
    log: Callable[[str], None],
    platforms: Sequence[str] = ("linux/amd64", "linux/arm64"),
    image_builder: ImageBuilder | None = None,
) -> list[str]:
    raise NotImplementedError("PLAN §6.5 publish_base")


def ensure_base(
    ref: str, version: str, variant: str, *, image_builder: ImageBuilder, log: Callable[[str], None]
) -> None:
    raise NotImplementedError("PLAN §6.5 ensure_base")
