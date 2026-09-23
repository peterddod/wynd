"""Build the run plan for local and image mode (PLAN §3.11, §6.1, §6.4; owner PROC-ENV).

Local: `PlanVenv(id=<local id>)`, absolute `package_dir`/`dir`, `venv_root=<ws>/.wynd/venvs`. Image: venv id = group
key, `package_dir=None`, `dir=None`, `venv_root=/opt/wynd/venvs`. `assign_edge_venvs` applies the §6.4 edge venv rule
(first venv whose requirements include the provider's fragment deps, else a new venv) and runs inside `plan_local`
and `prepare_build_job` before venvs are created or resolved.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from wynd.spec.lockfiles import EdgesLock, branch_key, load_edges_lock
from wynd.spec.plan import PlanNode, PlanProcess, PlanStep, PlanVenv, RunPlan
from wynd.spec.workspace import EDGES_LOCK_FILE, STATE_DIR

from . import venvs as venvs_mod
from .errors import DesignPhase, ValidationFailed
from .fragments import merge_process_fragments, provider_fragment, requirement_set, step_env
from .venvs import RuntimeSource, VenvGroup

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo

    from .loader import LoadedProcess
    from .validation import ValidationReport
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
    """The plan of `lp`'s closure over the given venvs (each step runs in the venv whose `steps` lists it).

    `mode` decides the paths: local plans hold absolute package and process dirs (so `ws_root` is required), image
    plans none. `ws_root`, when given in either mode, is also where each process's `edges.lock.yaml` is read from."""
    if mode == "local" and ws_root is None:
        raise ValueError("a local plan needs ws_root for its absolute package and process dirs")
    local = mode == "local"
    venv_of = {sid: venv.id for venv in venvs for sid in venv.steps}
    packages = sorted(lp.closure_packages().items())
    design = [sid for sid, pkg in packages if pkg.phase == "design"]
    if design:
        raise DesignPhase(design)
    steps: dict[str, PlanStep] = {}
    for sid, pkg in packages:
        if sid not in venv_of:
            raise ValueError(f"step {sid} is not served by any venv")
        steps[sid] = PlanStep(
            id=sid, kind=pkg.lock.kind, entrypoint=pkg.lock.entrypoint, venv=venv_of[sid],
            package_dir=str(ws_root / pkg.dir) if local else None, lock=pkg.lock,
        )
    processes: dict[str, PlanProcess] = {}
    for pid, proc in lp.closure_processes().items():
        nodes = {
            key: PlanNode(process=rs.child) if rs.ref_kind == "process" else PlanNode(step=rs.package.id)
            for key, rs in proc.steps.items()
        }
        processes[pid] = PlanProcess(
            id=pid,
            dir=str(ws_root / proc.dir) if local else None,
            definition=report.normalized[pid],
            nodes=nodes,
            edges_lock=load_edges_lock(ws_root / proc.dir / EDGES_LOCK_FILE) if ws_root is not None else EdgesLock(),
        )
    return RunPlan(
        mode=mode, root=lp.id, provider=lp.doc.effective_provider, venv_root=venv_root, venvs=list(venvs),
        steps=steps, processes=processes,
    )


def plan_local(
    ws: Workspace,
    pid: str,
    *,
    venv_root: Path | None = None,
    runtime: RuntimeSource | None = None,
    providers: Callable[[str], ProviderInfo] | None = None,
    log: Callable[[str], None] | None = None,
) -> RunPlan:
    """Validate (`ValidationFailed`), merge fragments (`DesignPhase`), group, assign edge venvs, create the local
    venvs that are missing, and return the local plan."""
    from .validation import validate

    lp = ws.load_process(pid)
    report = validate(lp, providers=providers)
    if not report.ok:
        raise ValidationFailed(report)
    groups = venvs_mod.venv_groups(merge_process_fragments(lp, providers))
    root = Path(venv_root) if venv_root is not None else ws.root / STATE_DIR / "venvs"
    keyed = [PlanVenv(id=group.key, steps=list(group.steps)) for group in groups]
    draft = build_plan(lp, report, mode="local", venv_root=str(root), venvs=keyed, ws_root=ws.root)
    edge_venvs, extra = _assign(draft, providers)
    runtime = runtime or venvs_mod.detect_runtime_source(os.environ)
    ids = {
        group.key: venvs_mod.ensure_local_venv(group, venv_root=root, runtime=runtime, log=log)
        for group in [*groups, *extra]
    }
    local = [PlanVenv(id=ids[group.key], steps=list(group.steps)) for group in [*groups, *extra]]
    plan = build_plan(lp, report, mode="local", venv_root=str(root), venvs=local, ws_root=ws.root)
    return plan.model_copy(update={"edge_venvs": {ref: ids[key] for ref, key in edge_venvs.items()}})


def assign_edge_venvs(plan: RunPlan) -> tuple[dict[str, str], list[PlanVenv]]:
    """PLAN §6.4: every branch with a `check` runs in the first venv (plan order) whose requirements include its
    provider's fragment deps (provider = `EdgeLockEntry.provider or plan.provider`); when none does, a venv with
    exactly those deps is added (id = the group key of that set, serving no steps). Returns
    `{"<pid>:<branch_key>": venv id}` and the added venvs."""
    edge_venvs, extra = _assign(plan)
    return edge_venvs, [PlanVenv(id=group.key, steps=[]) for group in extra]


def _assign(
    plan: RunPlan, providers: Callable[[str], ProviderInfo] | None = None
) -> tuple[dict[str, str], list[VenvGroup]]:
    requirements = {venv.id: _venv_requirements(plan, venv, providers) for venv in plan.venvs}
    extra: list[VenvGroup] = []
    edge_venvs: dict[str, str] = {}
    for pid, process in plan.processes.items():
        for edge in process.definition.edges:
            for index, branch in enumerate(edge.to):
                if branch.check is None:
                    continue
                key = branch_key(edge.from_, index, branch.name)
                entry = process.edges_lock.edges.get(key)
                provider = (entry.provider if entry is not None else None) or plan.provider
                fragment = provider_fragment(provider, providers)
                needed = requirement_set(fragment.deps if fragment is not None else [])
                venv = next((vid for vid, reqs in requirements.items() if set(needed) <= set(reqs)), None)
                if venv is None:
                    venv = venvs_mod.group_key(needed)
                    if venv not in requirements:                    # an edge venv added by an earlier call
                        requirements[venv] = needed
                        extra.append(VenvGroup(venv, needed, ()))
                edge_venvs[f"{pid}:{key}"] = venv
    return edge_venvs, extra


def _venv_requirements(
    plan: RunPlan, venv: PlanVenv, providers: Callable[[str], ProviderInfo] | None
) -> tuple[str, ...]:
    """Requirements of a plan venv: those of the steps it serves (identical by construction); none without steps."""
    if not venv.steps:
        return ()
    return step_env(venv.steps[0], plan.steps[venv.steps[0]].lock, plan.provider, providers).requirements
