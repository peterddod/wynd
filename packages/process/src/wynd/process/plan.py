"""Build the run plan for local and image mode (PLAN §3.11, §6.1, §6.4; owner PROC-ENV).

Local: `PlanVenv(id=<local id>)`, absolute `package_dir`/`dir`, `venv_root=<ws>/.wynd/venvs`. Image: venv id = group
key, `package_dir=None`, `dir=None`, `venv_root=/opt/wynd/venvs`. `assign_edge_venvs` applies the §6.4 edge venv rule
(first venv whose requirements include the provider's fragment deps, else a new venv) and runs inside `plan_local`
and `prepare_build_job` before venvs are created or resolved.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo
    from wynd.spec.plan import PlanVenv, RunPlan

    from .loader import LoadedProcess
    from .validation import ValidationReport
    from .venvs import RuntimeSource
    from .workspace import Workspace


def build_plan(
    lp: LoadedProcess,
    report: ValidationReport,
    *,
    mode: Literal["local", "image"],
    venv_root: str,
    venvs: list[PlanVenv],
    ws_root: Path | None,
) -> RunPlan:
    raise NotImplementedError("PLAN §6.4 build_plan")


def plan_local(
    ws: Workspace,
    pid: str,
    *,
    venv_root: Path | None = None,
    runtime: RuntimeSource | None = None,
    providers: Callable[[str], ProviderInfo] | None = None,
    log: Callable[[str], None] | None = None,
) -> RunPlan:
    raise NotImplementedError("PLAN §6.4 plan_local")


def assign_edge_venvs(plan: RunPlan) -> tuple[dict[str, str], list[PlanVenv]]:
    raise NotImplementedError("PLAN §6.4 assign_edge_venvs")
