"""Subprocess helpers for external executables (PLAN §6.1; owner PROC-WS).

`run` streams output lines to `log`; `require_tool` resolves `git`, `uv` (`WYND_UV`) or `docker` and raises
`ToolMissing` with an install hint.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from pathlib import Path


def run(
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    log: Callable[[str], None] | None = None,
    check: bool = True,
    input: str | bytes | None = None,
) -> str:
    raise NotImplementedError("PLAN §6.1 _proc.run")


def require_tool(name: str) -> str:
    raise NotImplementedError("PLAN §6.1 _proc.require_tool")
