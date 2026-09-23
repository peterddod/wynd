"""`process.lock.yaml` construction (PLAN §3.11, §6.5; owner PROC-BUILD, M2; `$DRAFTS/04 §8.7`).

The lock is the spec `ProcessLock` with an image-mode `RunPlan` and no timestamps: the same commit yields a
byte-identical file.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.lockfiles import BaseChoice, LockedWheel, ProcessLock
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
    raise NotImplementedError("PLAN §6.5 make_process_lock")
