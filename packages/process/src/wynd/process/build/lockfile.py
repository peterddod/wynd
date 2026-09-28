"""`process.lock.yaml` construction (PLAN §3.11, §6.5; owner PROC-BUILD, M2; `$DRAFTS/04 §8.7`).

The lock is the spec `ProcessLock` with an image-mode `RunPlan` and no timestamps: the same commit yields a
byte-identical file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

from wynd.spec.lockfiles import LockedVenv, ProcessLock

if TYPE_CHECKING:
    from wynd.spec.lockfiles import BaseChoice, LockedWheel
    from wynd.spec.plan import RunPlan

    from ..venvs import VenvGroup
    from .job import Prepared


def make_process_lock(
    prepared: Prepared,
    base: BaseChoice,
    platform: str,
    groups: Sequence[VenvGroup],
    pins: Mapping[str, list[str]],
    wheels: Mapping[str, LockedWheel],
    *,
    plan: RunPlan,
) -> ProcessLock:
    """`groups` are every venv of `plan` (edge venvs included) with their input requirements; `pins` their resolved
    requirement lists by group key; `wheels` the step wheels by step id."""
    if plan.mode != "image":
        raise ValueError("process.lock.yaml holds an image-mode plan")
    missing = sorted({venv.id for venv in plan.venvs} - {group.key for group in groups})
    if missing:
        raise ValueError(f"plan venvs without a resolved group: {', '.join(missing)}")
    return ProcessLock(
        process=prepared.process,
        commit=prepared.commit,
        source_sha=prepared.source_sha,
        process_hash=prepared.process_hash,
        runtime_version=base.version,
        platform=platform,
        base=base,
        system_packages=sorted(prepared.system_packages),
        fragments=list(prepared.fragments),
        wheels={sid: wheels[sid] for sid in sorted(wheels)},
        venvs=[LockedVenv(id=group.key, inputs=list(group.requirements), requirements=list(pins[group.key]))
               for group in sorted(groups, key=lambda g: g.key)],
        plan=plan.model_copy(update={"commit": prepared.commit}),
    )
