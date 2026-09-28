"""The `wynd-base` image family (PLAN §6.5, §15 item 50; owner PROC-BUILD, M2; `$DRAFTS/04 §9`).

One template (`templates/base.Dockerfile`), variants `slim` (`python:3.12-slim-trixie`) and `alpine`
(`python:3.12-alpine3.22`); refs `<WYND_BASE_REPO or "wynd-base">:<version>-<variant>`. `publish_base` reads the
target from the user registry section `registries` (validated with `artefacts.ImageRegistryEntry`) and pushes
`linux/amd64,linux/arm64`. `ensure_base` builds a missing local base on demand.
"""

from __future__ import annotations

import os
import shutil
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from .artefacts import ImageRegistryEntry
from .errors import WyndProcessError

if TYPE_CHECKING:
    from wynd.runtime.storage.base import Registry

    from .build.imagebuilder import ImageBuilder
    from .build.resolve import Resolver
    from .venvs import RuntimeSource

UV_VERSION = "0.10.7"
PYTHON_IMAGES = {"slim": "python:3.12-slim-trixie", "alpine": "python:3.12-alpine3.22"}
USER_SETUP = {
    "slim": "groupadd --system --gid 10001 wynd "
            "&& useradd --system --uid 10001 --gid 10001 --home-dir /home/wynd --create-home wynd",
    "alpine": "addgroup -S -g 10001 wynd && adduser -S -u 10001 -G wynd -h /home/wynd wynd",
}
TEMPLATE = Path(__file__).parent / "templates" / "base.Dockerfile"


def base_image_ref(version: str, variant: str, repo: str | None = None) -> str:
    repo = repo or os.environ.get("WYND_BASE_REPO") or "wynd-base"
    return f"{repo}:{version}-{variant}"


def render_base_dockerfile(version: str, variant: Literal["slim", "alpine"]) -> str:
    tokens = {"@VERSION@": version, "@VARIANT@": variant, "@UV_VERSION@": UV_VERSION,
              "@PYTHON_IMAGE@": PYTHON_IMAGES[variant], "@USER_SETUP@": USER_SETUP[variant]}
    text = TEMPLATE.read_text()
    for token, value in tokens.items():
        text = text.replace(token, value)
    return text


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
    """Build (and with `push`, push) `wynd-base:<version>-<variant>` for each variant; returns the refs.

    From the monorepo the context vendors freshly built spec/runtime wheels; otherwise `wynd-runtime==<version>`
    comes from the package index."""
    from .build.imagebuilder import ImageBuildRequest, open_image_builder
    from .build.resolve import UvResolver
    from .venvs import detect_runtime_source

    runtime = runtime or detect_runtime_source(os.environ)
    if runtime.editable is not None and runtime.version != version:
        raise WyndProcessError(f"source tree is wynd-runtime {runtime.version}; cannot build base {version}")
    unknown = sorted(set(variants) - set(PYTHON_IMAGES))
    if unknown:
        raise ValueError(f"unknown base variant(s) {', '.join(unknown)} (expected slim or alpine)")
    builder = image_builder or open_image_builder(os.environ)
    resolver = resolver or UvResolver()
    context = Path(tempfile.mkdtemp(prefix="wynd-base-"))
    try:
        wheels = context / "wheels"
        wheels.mkdir()
        if runtime.editable is not None:
            for source in runtime.editable:
                log(f"building wheel of {source}")
                resolver.build_wheel(source, wheels)
        pins = resolver.compile([f"wynd-runtime=={version}"], universal=True,
                                find_links=[wheels] if runtime.editable is not None else ())
        (context / "runtime-requirements.txt").write_text("".join(f"{pin}\n" for pin in pins))
        refs = []
        for variant in variants:
            ref = base_image_ref(version, variant, repo)
            dockerfile = context / f"{variant}.Dockerfile"
            dockerfile.write_text(render_base_dockerfile(version, variant))
            log(f"building {ref}")
            builder.build(ImageBuildRequest(context=context, dockerfile=dockerfile, tags=(ref,),
                                            platforms=tuple(platforms), push=push), log)
            refs.append(ref)
        return refs
    finally:
        shutil.rmtree(context, ignore_errors=True)


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
    """Build and push the variants as `<registry url>/wynd-base:<version>-<variant>` for `platforms`."""
    from .build.imagebuilder import open_image_builder

    entry = image_registry(registry, registry_name)
    builder = image_builder or open_image_builder(os.environ)
    registry_login(builder, entry, os.environ)
    return build_base(version, variants, log=log, image_builder=builder, repo=f"{entry.url}/wynd-base",
                      platforms=platforms, push=True)


def ensure_base(
    ref: str, version: str, variant: str, *, image_builder: ImageBuilder, log: Callable[[str], None]
) -> None:
    """Make `ref` available to the image build: present locally, or pulled by BuildKit when it names a registry, or
    built now from the monorepo's runtime source."""
    from .venvs import detect_runtime_source

    if image_builder.exists(ref):
        return
    repo = ref.rpartition(":")[0]
    if _has_registry_host(repo):
        return
    runtime = detect_runtime_source(os.environ)
    if runtime.editable is None:
        raise WyndProcessError(f"base image {ref} not found locally; run wynd base build {version} or set "
                               "WYND_BASE_REPO to a registry")
    log(f"base image {ref} not found locally; building it")
    build_base(version, [variant], log=log, image_builder=image_builder, runtime=runtime, repo=repo)


def image_registry(registry: Registry, name: str) -> ImageRegistryEntry:
    """The `registries` entry `name` of the user registry; `WyndProcessError` when there is none."""
    entry = registry.get("registries", name)
    if entry is None:
        raise WyndProcessError(f"unknown image registry '{name}'")
    return ImageRegistryEntry.model_validate({"name": name, **entry})


def registry_login(builder: ImageBuilder, entry: ImageRegistryEntry, environ: Mapping[str, str]) -> None:
    """Log in when the entry names both credential variables and they are set; else the docker credential store
    applies."""
    username = environ.get(entry.username_env or "", "")
    password = environ.get(entry.password_env or "", "")
    if username and password:
        builder.login(entry.url.split("/", 1)[0], username, password)


def _has_registry_host(repo: str) -> bool:
    """Docker's rule: the first path component is a registry host iff it has a `.` or `:` or is `localhost`."""
    head, sep, _ = repo.partition("/")
    return bool(sep) and ("." in head or ":" in head or head == "localhost")
