"""Step wheels (PLAN §3.1, §6.5; owner PROC-BUILD, M2).

Stage `<stage>/<module>/wynd_steps/<step_module_name>/` = the package's tracked `*.py` files except `test_*.py`, plus
`wynd-step.json` (`{schema:1, step, entrypoint, kind, proto_hash, step_hash, requirements, effects, tools, mcp}` —
no provider/tier/thinking) and a generated `pyproject.toml` (`name = wynd-step-<…>`, version `0.1.0`,
`packages = ["wynd_steps"]`, no dependencies); then `uv build --wheel`. Wheels install with `--no-deps`.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import TYPE_CHECKING

from wynd.spec.workspace import step_module_name

from ..errors import WyndProcessError
from ..hashing import step_hash

if TYPE_CHECKING:
    from wynd.spec.lockfiles import StepLock

    from ..loader import StepPackage
    from ..workspace import Tree
    from .resolve import Resolver

STEP_META_FILE = "wynd-step.json"
PYPROJECT = """\
[project]
name = "{dist}"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["wynd_steps"]
"""


def wheel_dist(step_id: str) -> str:
    """Distribution name of a step wheel (PLAN §3.1): `wynd-step-` + the module name with `_` -> `-`."""
    return "wynd-step-" + step_module_name(step_id).replace("_", "-")


def stage_step(tree: Tree, pkg: StepPackage, lock: StepLock, *, stage_root: Path) -> Path:
    """Write the wheel project of one compiled step under `stage_root/<module>/` (replacing an earlier stage) and
    return its directory. The entrypoint module must be one of the staged files."""
    module = step_module_name(pkg.id)
    project = Path(stage_root) / module
    if project.exists():
        shutil.rmtree(project)
    target = project / "wynd_steps" / module
    prefix = f"{pkg.dir}/"
    staged: set[str] = set()
    for path in sorted(tree.files()):
        if not path.startswith(prefix):
            continue
        rel = path[len(prefix):]
        if not rel.endswith(".py") or Path(rel).name.startswith("test_"):
            continue
        dest = target / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(tree.read_bytes(path))
        staged.add(rel)
    entry_module = lock.entrypoint.partition(":")[0].replace(".", "/")
    if f"{entry_module}.py" not in staged and f"{entry_module}/__init__.py" not in staged:
        raise WyndProcessError(f"step {pkg.id}: entrypoint {lock.entrypoint!r} names no module of the package "
                               f"{pkg.dir} (expected {entry_module}.py)")
    meta = {
        "schema": 1,
        "step": pkg.id,
        "entrypoint": lock.entrypoint,
        "kind": lock.kind,
        "proto_hash": lock.proto_hash,
        "step_hash": step_hash(tree, pkg),
        "requirements": list(lock.locked_deps or lock.fragment.deps),
        "effects": list(lock.effects),
        "tools": [tool.name for tool in lock.tools],
        "mcp": [snapshot.server for snapshot in lock.mcp],
    }
    (target / STEP_META_FILE).write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    (project / "pyproject.toml").write_text(PYPROJECT.format(dist=wheel_dist(pkg.id)))
    return project


def build_step_wheel(
    tree: Tree, pkg: StepPackage, lock: StepLock, *, out: Path, resolver: Resolver, stage_root: Path
) -> Path:
    """Stage the step and build its wheel into `out`; returns the wheel path."""
    project = stage_step(tree, pkg, lock, stage_root=stage_root)
    return resolver.build_wheel(project, Path(out))
