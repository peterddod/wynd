"""Merge the env fragments of a process closure (PLAN §6.1, §6.4; owner PROC-ENV; `$DRAFTS/04 §6.1`).

Every compiled step in the closure contributes its fragment; agentic steps also get their effective provider's
fragment (`lock.provider or` the ROOT's provider `or DEFAULT_PROVIDER`; children's steps use the ROOT's provider).
Raises `DesignPhase` when any closure step is in the design phase. Providers of agentic branches are not merged
here: `plan.assign_edge_venvs` gives them venvs and `envmanifest` lists their vars.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from wynd.spec.hashing import normalize_requirement
from wynd.spec.lockfiles import FragmentRecord

from .errors import DesignPhase

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo
    from wynd.spec.fragments import EnvFragment
    from wynd.spec.lockfiles import StepLock

    from .loader import LoadedProcess

ProviderLookup = Callable[[str], "ProviderInfo"]


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


def merge_process_fragments(lp: LoadedProcess, providers: ProviderLookup | None = None) -> MergedEnv:
    packages = lp.closure_packages()
    design = sorted(sid for sid, pkg in packages.items() if pkg.phase == "design")
    if design:
        raise DesignPhase(design)
    root_provider = lp.doc.effective_provider
    steps: dict[str, StepEnv] = {}
    sources: list[tuple[str, EnvFragment]] = []
    for sid in sorted(packages):
        lock = packages[sid].lock
        steps[sid] = step_env(sid, lock, root_provider, providers)
        sources.append((f"step:{sid}", lock.fragment))
    used = sorted({env.provider for env in steps.values() if env.provider is not None})
    for name in used:
        fragment = provider_fragment(name, providers)
        if fragment is not None:
            sources.append((f"provider:{name}", fragment))
    return MergedEnv(
        steps=steps,
        system=tuple(sorted({pkg for _, frag in sources for pkg in frag.system})),
        glibc_required_by=tuple(source for source, frag in sources if frag.requires == "glibc"),
        providers=tuple(used),
        fragments=tuple(
            FragmentRecord(source=source, deps=list(frag.deps), system=list(frag.system), requires=frag.requires)
            for source, frag in sources
        ),
    )


def step_env(step_id: str, lock: StepLock, root_provider: str, providers: ProviderLookup | None = None) -> StepEnv:
    """Requirements of one compiled step: `locked_deps or fragment.deps`, plus the provider's deps if agentic."""
    deps = list(lock.locked_deps or lock.fragment.deps)
    provider = None
    if lock.kind == "agentic":
        provider = lock.provider or root_provider
        fragment = provider_fragment(provider, providers)
        if fragment is not None:
            deps += fragment.deps
    return StepEnv(step_id, requirement_set(deps), provider)


def provider_fragment(name: str, providers: ProviderLookup | None = None) -> EnvFragment | None:
    """The provider's env fragment; None when it is not installed (the validator warns W207 and a run of the step
    fails with cause `config`, so planning goes on without its dependencies)."""
    if providers is None:
        from wynd.runtime.providers import provider_info as providers
    try:
        return providers(name).env_fragment
    except KeyError:
        return None


def requirement_set(requirements: Iterable[str]) -> tuple[str, ...]:
    """Normalised, sorted, unique requirement strings: the identity of a venv's dependency set."""
    return tuple(sorted({normalize_requirement(req) for req in requirements}))
