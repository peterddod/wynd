"""Venv groups and local step venvs (PLAN §6.1, §6.4; owner PROC-ENV; `$DRAFTS/04 §6.2–§6.4`).

Steps with an identical normalised requirement set share one venv. Local venvs live at
`<ws>/.wynd/venvs/<local_venv_id>/`: `uv venv --relocatable --python 3.12 --no-project` + `uv pip install
<runtime.install_args()> <requirements> "pytest>=8,<10"` into a temp dir, marker `wynd-venv.json` written last, atomic
rename. `RuntimeSource` is editable `packages/spec` + `packages/runtime` from this monorepo (`direct_url.json` or
`WYND_RUNTIME_SOURCE=<dir>`), else pinned `wynd-spec==V wynd-runtime==V`.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .fragments import MergedEnv
    from .workspace import Workspace


@dataclass(frozen=True)
class RuntimeSource:
    version: str                                   # wynd-runtime version == wynd-base version
    editable: tuple[Path, Path] | None             # (packages/spec, packages/runtime) when running from the monorepo

    def install_args(self) -> list[str]:
        raise NotImplementedError("PLAN §6.4 RuntimeSource.install_args")

    def descriptor(self) -> dict[str, Any]:
        raise NotImplementedError("PLAN §6.4 RuntimeSource.descriptor")


def detect_runtime_source(environ: Mapping[str, str]) -> RuntimeSource:
    raise NotImplementedError("PLAN §6.4 detect_runtime_source")


@dataclass(frozen=True)
class VenvGroup:
    key: str                                       # dependency_set_hash of the requirements
    requirements: tuple[str, ...]                  # the input set (not yet resolved)
    steps: tuple[str, ...]                         # sorted step ids


def venv_groups(env: MergedEnv) -> list[VenvGroup]:
    raise NotImplementedError("PLAN §6.4 venv_groups")


def local_venv_id(group: VenvGroup, runtime: RuntimeSource) -> str:
    raise NotImplementedError("PLAN §6.4 local_venv_id")


def ensure_local_venv(
    group: VenvGroup, *, venv_root: Path, runtime: RuntimeSource, log: Callable[[str], None] | None
) -> str:
    raise NotImplementedError("PLAN §6.4 ensure_local_venv")


def step_python(
    requirements: Sequence[str],
    *,
    venv_root: Path,
    runtime: RuntimeSource | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    raise NotImplementedError("PLAN §6.4 step_python")


def sync_interfaces(
    ws: Workspace, pid: str, *, venv_root: Path, log: Callable[[str], None] | None = None
) -> list[str]:
    raise NotImplementedError("PLAN §6.4 sync_interfaces")
