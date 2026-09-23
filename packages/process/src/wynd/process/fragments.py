"""Merge the env fragments of a process closure (PLAN §6.1, §6.4; owner PROC-ENV; `$DRAFTS/04 §6.1`).

Every compiled step in the closure contributes its fragment; agentic steps also get their effective provider's
fragment (`lock.provider or` the ROOT's provider `or DEFAULT_PROVIDER`; children's steps use the ROOT's provider).
Raises `DesignPhase` when any closure step is in the design phase.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo
    from wynd.spec.lockfiles import FragmentRecord

    from .loader import LoadedProcess


@dataclass(frozen=True)
class StepEnv:
    step_id: str
    requirements: tuple[str, ...]         # normalised, sorted, unique: lock deps ∪ provider deps (agentic only)
    provider: str | None                  # effective provider (agentic steps only)


@dataclass(frozen=True)
class MergedEnv:
    steps: dict[str, StepEnv]             # every compiled step in the closure (children included), by id
    system: tuple[str, ...]               # sorted union of `system:` over all fragments
    glibc_required_by: tuple[str, ...]    # sources whose fragment says `requires: glibc`
    providers: tuple[str, ...]            # effective providers in use (sorted)
    fragments: tuple[FragmentRecord, ...]


def merge_process_fragments(
    lp: LoadedProcess, providers: Callable[[str], ProviderInfo] | None = None
) -> MergedEnv:
    raise NotImplementedError("PLAN §6.1 merge_process_fragments")
