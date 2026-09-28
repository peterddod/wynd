"""Base variant choice with the musl fallback (PLAN §6.5, §15 item 21; owner PROC-BUILD, M2; `$DRAFTS/04 §8.3`).

`alpine-python` falls back to `debian-slim-python` when a fragment declares `requires: glibc` or a binary-only
musllinux resolve fails; the reason is recorded in `process.lock.yaml`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from wynd.spec.lockfiles import BaseChoice

from ..errors import ResolutionError

if TYPE_CHECKING:
    from ..fragments import MergedEnv
    from ..venvs import VenvGroup
    from .resolve import Resolver

MUSL_ARCH = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}
FALLBACK = "alpine-python requested; fell back to debian-slim-python"


def choose_base(
    requested: Literal["debian-slim-python", "alpine-python"],
    env: MergedEnv,
    groups: Sequence[VenvGroup],
    *,
    resolver: Resolver,
    platform: str,
    version: str,
    find_links: Sequence[Path],
) -> BaseChoice:
    from ..base import base_image_ref

    def choice(variant: Literal["slim", "alpine"], reason: str | None = None) -> BaseChoice:
        return BaseChoice(requested=requested, variant=variant, version=version,
                          image=base_image_ref(version, variant), reason=reason)

    if requested == "debian-slim-python":
        return choice("slim")
    if env.glibc_required_by:
        return choice("slim", f"{FALLBACK}: requires: glibc declared by {', '.join(env.glibc_required_by)}")
    arch = MUSL_ARCH.get(platform)
    if arch is None:
        raise ValueError(f"unsupported platform {platform!r} (expected one of {', '.join(MUSL_ARCH)})")
    runtime = [f"wynd-spec=={version}", f"wynd-runtime=={version}"]
    for group in groups:
        try:
            resolver.compile([*group.requirements, *runtime], universal=False,
                             python_platform=f"{arch}-unknown-linux-musl", only_binary=True, find_links=find_links)
        except ResolutionError as err:
            first = next((line.strip() for line in str(err).splitlines() if line.strip()), "")
            steps = ", ".join(group.steps) or "none: an agentic edge's provider"
            return choice("slim", f"{FALLBACK}: venv {group.key} (steps {steps}) has no musl-compatible wheels: "
                                  f"{first}")
    return choice("alpine")
