"""Assemble a process's env manifest (PLAN §3.10, §6.1; owner PROC-ENV; sources `$DRAFTS/04 §10.2`).

Sources: step `fragment.vars`, MCP `auth_env`, provider fragments (vars + groups; `used_by` incl. agentic steps and
agentic branches), `env.*` references from edge expressions (`ValidationReport.env_refs`), `STORAGE_ENV`,
`SUPERVISOR_ENV`, `WYND_HOME` (optional), `WYND_REGISTRY_JSON` (iff `registry_snapshot` is non-empty) and
`WYND_MCP_<NAME>_URL` per http MCP server. Names, descriptions and defaults only — never values.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo
    from wynd.runtime.storage.base import Registry
    from wynd.spec.env_manifest import EnvManifest

    from .loader import LoadedProcess
    from .workspace import Workspace


def assemble_env_manifest(
    ws: Workspace,
    pid: str,
    registry: Registry | None = None,
    *,
    commit: str | None = None,
    providers: Callable[[str], ProviderInfo] | None = None,
) -> EnvManifest:
    raise NotImplementedError("PLAN §3.10 assemble_env_manifest")


def registry_snapshot(lp: LoadedProcess, registry: Registry) -> dict[str, Any]:
    raise NotImplementedError("PLAN §3.10 registry_snapshot")
