"""Host-side dependency resolution (PLAN §6.5; owner PROC-BUILD, M2; `$DRAFTS/04 §8.2`).

`UvResolver` runs `uv pip compile` (`--universal` for venv pins; `--python-platform … --only-binary :all:` for the
musl check) and `uv build --wheel` (`WYND_UV` overrides the executable). Failures raise `errors.ResolutionError`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Protocol


class Resolver(Protocol):
    def compile(
        self,
        requirements: Sequence[str],
        *,
        universal: bool,
        python_version: str = "3.12",
        python_platform: str | None = None,
        only_binary: bool = False,
        find_links: Sequence[Path] = (),
    ) -> list[str]: ...                                  # full pinned set, one requirement per entry

    def build_wheel(self, src: Path, out_dir: Path) -> Path: ...


class UvResolver:
    def compile(
        self,
        requirements: Sequence[str],
        *,
        universal: bool,
        python_version: str = "3.12",
        python_platform: str | None = None,
        only_binary: bool = False,
        find_links: Sequence[Path] = (),
    ) -> list[str]:
        raise NotImplementedError("PLAN §6.5 UvResolver.compile")

    def build_wheel(self, src: Path, out_dir: Path) -> Path:
        raise NotImplementedError("PLAN §6.5 UvResolver.build_wheel")
