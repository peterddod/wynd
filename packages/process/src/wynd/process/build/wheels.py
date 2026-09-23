"""Step wheels (PLAN §3.1, §6.5; owner PROC-BUILD, M2).

Stage `<stage>/<module>/wynd_steps/<step_module_name>/` = the package's tracked `*.py` files except `test_*.py`, plus
`wynd-step.json` (`{schema:1, step, entrypoint, kind, proto_hash, step_hash, requirements, effects, tools, mcp}` —
no provider/tier/thinking) and a generated `pyproject.toml` (`name = wynd-step-<…>`, version `0.1.0`,
`packages = ["wynd_steps"]`, no dependencies); then `uv build --wheel`. Wheels install with `--no-deps`.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.lockfiles import StepLock

    from ..loader import StepPackage
    from ..workspace import Tree
    from .resolve import Resolver


def stage_step(tree: Tree, pkg: StepPackage, lock: StepLock, *, stage_root: Path) -> Path:
    raise NotImplementedError("PLAN §6.5 stage_step")


def build_step_wheel(
    tree: Tree, pkg: StepPackage, lock: StepLock, *, out: Path, resolver: Resolver, stage_root: Path
) -> Path:
    raise NotImplementedError("PLAN §6.5 build_step_wheel")
