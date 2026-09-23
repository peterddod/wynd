"""Per-dispatch execution policy and cassette configuration (PLAN §3.12 shapes; `build_policy` per §5.1).

`build_policy` runs executor-side: the worker receives a fully resolved `ExecPolicy`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ValidationError

from wynd.runtime.errors import StepFailure
from wynd.spec.base import DEFAULT_THINKING, DEFAULT_TIER
from wynd.spec.lockfiles import DEFAULT_RETRIES, RetryPolicy, StepKind, effective_retries

if TYPE_CHECKING:
    from wynd.runtime.storage.base import Registry
    from wynd.spec.lockfiles import McpSnapshot, StepLock
    from wynd.spec.process_doc import RetryOverride

DEFAULT_MAX_TURNS = 25


class CassetteConfig(BaseModel):
    mode: Literal["live", "record", "replay"] = "live"
    dir: str | None = None
    record_dir: str | None = None
    literals: dict[str, str] = {}           # extra Normaliser literals {absolute path: placeholder}, §3.16


class ExecPolicy(BaseModel):
    kind: StepKind
    retries: RetryPolicy
    provider: str | None = None
    model_id: str | None = None
    tier: str | None = None
    thinking: str | None = None
    builtin_tools: list[str] = []
    max_turns: int = 25
    effects: list[str] = []
    mcp: list[dict[str, Any]] = []          # McpSnapshot JSON + {"entry": McpServerEntry JSON from the user registry}


def build_policy(
    kind: StepKind,
    lock: StepLock | None,
    *,
    default_provider: str,
    registry: Registry,
    environ: Mapping[str, str],
    retry_override: RetryOverride | None = None,
) -> ExecPolicy:
    """Resolve provider/tier/model/MCP entries for one dispatch; `StepFailure("config")` on an unknown provider,
    tier or MCP entry. An http MCP entry's `url` is replaced by `environ["WYND_MCP_<NAME>_URL"]` when set."""
    retries = effective_retries((lock and lock.retries) or DEFAULT_RETRIES[kind], retry_override)
    effects = list(lock.effects) if lock else []
    if kind != "agentic":
        return ExecPolicy(kind=kind, retries=retries, effects=effects)

    from wynd.runtime.providers import resolve_model

    provider = (lock and lock.provider) or default_provider
    tier = (lock and lock.tier) or DEFAULT_TIER
    return ExecPolicy(
        kind=kind,
        retries=retries,
        provider=provider,
        model_id=resolve_model(provider, tier, registry),
        tier=tier,
        thinking=(lock and lock.thinking) or DEFAULT_THINKING,
        builtin_tools=list(lock.builtin_tools) if lock else [],
        max_turns=(lock and lock.max_turns) or DEFAULT_MAX_TURNS,
        effects=effects,
        mcp=[_mcp_block(snap, registry, environ) for snap in lock.mcp] if lock else [],
    )


def _mcp_block(snap: McpSnapshot, registry: Registry, environ: Mapping[str, str]) -> dict[str, Any]:
    from wynd.runtime.mcp.entry import McpServerEntry

    server = snap.server
    raw = registry.get("mcp", server)
    if raw is None:
        raise StepFailure(
            "config",
            f"MCP server {server!r} is not in the user registry ({registry.location()}); "
            f"add it with `wynd mcp add {server} --url <url>` (or --command)",
        )
    try:
        entry = McpServerEntry.model_validate({"name": server, **raw})
    except ValidationError as err:
        raise StepFailure("config", f"MCP server {server!r} has an invalid registry entry: {err}") from err
    data = entry.model_dump(mode="json")
    url = environ.get(f"WYND_MCP_{server.upper().replace('-', '_')}_URL")
    if entry.transport == "http" and url:
        data["url"] = url
    return {**snap.model_dump(mode="json"), "entry": data}
