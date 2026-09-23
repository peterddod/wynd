"""Base variant choice with the musl fallback (PLAN §6.5, §15 item 21; owner PROC-BUILD, M2; `$DRAFTS/04 §8.3`).

`alpine-python` falls back to `debian-slim-python` when a fragment declares `requires: glibc` or a binary-only
musllinux resolve fails; the reason is recorded in `process.lock.yaml`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.spec.lockfiles import BaseChoice

    from ..fragments import MergedEnv
    from ..venvs import VenvGroup
    from .resolve import Resolver


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
    raise NotImplementedError("PLAN §6.5 choose_base")
