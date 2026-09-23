"""Per-dispatch execution policy and cassette configuration (PLAN §3.12 shapes; `build_policy` per §5.1).

`build_policy` runs executor-side: the worker receives a fully resolved `ExecPolicy`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from wynd.spec.lockfiles import RetryPolicy, StepKind

if TYPE_CHECKING:
    from wynd.runtime.storage.base import Registry
    from wynd.spec.lockfiles import StepLock
    from wynd.spec.process_doc import RetryOverride


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
    raise NotImplementedError("PLAN §5.1")
