"""Assemble a process's env manifest (PLAN §3.10, §6.1; owner PROC-ENV; sources `$DRAFTS/04 §10.2`).

Sources: step `fragment.vars`, MCP `auth_env`, provider fragments (vars + groups; `used_by` incl. agentic steps and
agentic branches), `env.*` references from edge expressions (`ValidationReport.env_refs`), `STORAGE_ENV`,
`SUPERVISOR_ENV`, `WYND_HOME` (optional), `WYND_REGISTRY_JSON` (iff `registry_snapshot` is non-empty) and
`WYND_MCP_<NAME>_URL` per http MCP server. Names, descriptions and defaults only — never values.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from wynd.runtime.storage import STORAGE_ENV
from wynd.runtime.supervisor.schema import SUPERVISOR_ENV
from wynd.spec.env_manifest import EnvManifest
from wynd.spec.fragments import EnvFragment, EnvGroup, EnvVar, merge_fragments
from wynd.spec.lockfiles import EdgesLock, branch_key
from wynd.spec.workspace import EDGES_LOCK_FILE
from wynd.spec.yamlio import parse_model

from .fragments import provider_fragment
from .workspace import join, read_text

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo
    from wynd.runtime.storage.base import Registry

    from .loader import LoadedProcess, StepPackage
    from .workspace import Workspace

WYND_HOME = EnvVar(
    name="WYND_HOME",
    description="User-level registries location (MCP servers, provider tiers).",
    required=False,
    default="~/.wynd",
    used_by=["runtime"],
)
REGISTRY_JSON = "WYND_REGISTRY_JSON"
REGISTRY_GROUP = "user-registry"
REGISTRY_GROUP_DESCRIPTION = (
    "The user-registry entries this process uses (MCP servers, provider tiers), supplied by the controller for image "
    "runs; locally the file registry under WYND_HOME is read instead."
)


def assemble_env_manifest(
    ws: Workspace,
    pid: str,
    registry: Registry | None = None,
    *,
    commit: str | None = None,
    providers: Callable[[str], ProviderInfo] | None = None,
) -> EnvManifest:
    """Every env var the closure may read, merged by name (`used_by` union, `secret`/`required` if any source says
    so, first non-empty description in source order: steps, providers, MCP, edge expressions, storage, runtime)."""
    from .validation import validate

    lp = ws.load_process(pid)
    report = validate(lp, providers=providers)
    closure = lp.closure_processes()
    packages = _compiled(lp)
    users = _provider_users(lp, {cid: _edges_lock(ws, proc) for cid, proc in closure.items()})
    fragments: list[EnvFragment] = []

    for sid, pkg in packages.items():
        lock = pkg.lock
        step_vars = [
            var.model_copy(update={"used_by": [*var.used_by, f"step:{sid}",
                                               *(f"tool:{sid}/{t.name}" for t in lock.tools if var.name in t.env)]})
            for var in lock.fragment.vars
        ]
        fragments.append(EnvFragment(vars=step_vars, groups=lock.fragment.groups))

    for name, used_by in sorted(users.items()):
        fragment = provider_fragment(name, providers)
        if fragment is not None:
            provider_vars = [var.model_copy(update={"used_by": [*var.used_by, *used_by]}) for var in fragment.vars]
            fragments.append(EnvFragment(vars=provider_vars, groups=fragment.groups))

    entries = _mcp_entries(packages, registry)
    for server, entry in entries.items():
        mcp_vars = [
            EnvVar(name=name, description=f"Referenced by MCP server {server}.", secret=True, used_by=[f"mcp:{server}"])
            for name in entry.get("auth_env") or []
        ]
        if entry.get("transport") == "http" and entry.get("url"):
            mcp_vars.append(EnvVar(
                name=f"WYND_MCP_{server.upper().replace('-', '_')}_URL",
                description=f"URL of MCP server {server}; overrides the user-registry url (e.g. an in-cluster "
                            "address).",
                required=False, default=entry["url"], used_by=[f"mcp:{server}"],
            ))
        fragments.append(EnvFragment(vars=mcp_vars))

    declared: dict[str, str] = {}
    for proc in closure.values():
        for name, description in proc.doc.env.vars.items():
            declared.setdefault(name, description)
    fragments.append(EnvFragment(vars=[
        EnvVar(name=name, description=declared.get(name) or "Referenced by edge expression.", used_by=refs)
        for name, refs in sorted(report.env_refs.items())
    ]))

    fragments += [EnvFragment(vars=STORAGE_ENV), EnvFragment(vars=SUPERVISOR_ENV), EnvFragment(vars=[WYND_HOME])]

    snapshot = _snapshot(packages, registry, users)
    if snapshot:
        used_by = [*(f"mcp:{name}" for name in snapshot.get("mcp", {})),
                   *(f"provider:{name}" for name in snapshot.get("providers", {}))]
        fragments.append(EnvFragment(
            vars=[EnvVar(name=REGISTRY_JSON, description=_describe_snapshot(snapshot), required=False,
                         one_of=REGISTRY_GROUP, used_by=used_by)],
            groups={REGISTRY_GROUP: EnvGroup(description=REGISTRY_GROUP_DESCRIPTION, modes=["image"])},
        ))

    merged = merge_fragments(fragments)
    return EnvManifest(process=pid, commit=commit, vars=merged.vars, groups=dict(sorted(merged.groups.items())))


def registry_snapshot(lp: LoadedProcess, registry: Registry) -> dict[str, Any]:
    """`{"mcp": {name: entry}, "providers": {name: entry}}` with exactly the user-registry entries the closure uses:
    every MCP server of a closure step's `mcp`, and the `providers` entry (when the registry has one) of every
    provider of an agentic step or agentic branch. Empty sections are left out; `{}` means nothing is used.

    A branch's provider here is the ROOT's default: `edges.lock.yaml` overrides are not visible from a
    `LoadedProcess` (`assemble_env_manifest`, which has the workspace, applies them)."""
    return _snapshot(_compiled(lp), registry, _provider_users(lp, {}))


def _compiled(lp: LoadedProcess) -> dict[str, StepPackage]:
    return {sid: pkg for sid, pkg in sorted(lp.closure_packages().items()) if pkg.lock is not None}


def _provider_users(lp: LoadedProcess, edges_locks: Mapping[str, EdgesLock]) -> dict[str, list[str]]:
    """Provider -> `used_by` refs of the agentic steps (`step:<id>`) and agentic branches (`edge:<pid>:<branch_key>`)
    that use it: a step's `lock.provider`, a branch's lock-entry provider, else the ROOT's default (SPEC §3.2)."""
    root = lp.doc.effective_provider
    users: dict[str, list[str]] = {}
    for sid, pkg in _compiled(lp).items():
        if pkg.lock.kind == "agentic":
            users.setdefault(pkg.lock.provider or root, []).append(f"step:{sid}")
    for cid, proc in lp.closure_processes().items():
        lock = edges_locks.get(cid, EdgesLock())
        for edge in proc.doc.edges:
            for index, branch in enumerate(edge.to):
                if branch.check is None:
                    continue
                key = branch_key(edge.from_, index, branch.name)
                entry = lock.edges.get(key)
                users.setdefault((entry.provider if entry is not None else None) or root, []).append(
                    f"edge:{cid}:{key}")
    return users


def _mcp_entries(packages: Mapping[str, StepPackage], registry: Registry | None) -> dict[str, dict[str, Any]]:
    servers = sorted({snapshot.server for pkg in packages.values() for snapshot in pkg.lock.mcp})
    if registry is None:
        return {}
    return {name: dict(entry) for name in servers if (entry := registry.get("mcp", name)) is not None}


def _snapshot(
    packages: Mapping[str, StepPackage], registry: Registry | None, users: Mapping[str, list[str]]
) -> dict[str, Any]:
    if registry is None:
        return {}
    snapshot: dict[str, Any] = {}
    mcp = _mcp_entries(packages, registry)
    if mcp:
        snapshot["mcp"] = mcp
    providers = {name: dict(entry) for name in sorted(users) if (entry := registry.get("providers", name)) is not None}
    if providers:
        snapshot["providers"] = providers
    return snapshot


def _describe_snapshot(snapshot: Mapping[str, Any]) -> str:
    """E.g. `User-registry snapshot. MCP: github (http https://api.githubcopilot.com/mcp/). Tiers: claude-code
    cheap=sonnet.`"""
    parts = ["User-registry snapshot."]
    servers = [
        f"{name} ({entry.get('transport')} {entry.get('url') or ' '.join(entry.get('command') or [])})"
        for name, entry in snapshot.get("mcp", {}).items()
    ]
    if servers:
        parts.append(f"MCP: {', '.join(servers)}.")
    tiers = [
        f"{name} {' '.join(f'{tier}={model}' for tier, model in (entry.get('tiers') or {}).items())}".strip()
        for name, entry in snapshot.get("providers", {}).items()
    ]
    if tiers:
        parts.append(f"Tiers: {', '.join(tiers)}.")
    return " ".join(parts)


def _edges_lock(ws: Workspace, proc: LoadedProcess) -> EdgesLock:
    path = join(proc.dir, EDGES_LOCK_FILE)
    if path not in ws.tree.files():
        return EdgesLock()
    return parse_model(read_text(ws.tree, path), EdgesLock, path)
