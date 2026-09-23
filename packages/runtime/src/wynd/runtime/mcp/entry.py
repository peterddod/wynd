"""User-registry MCP server entries (section `mcp`, PLAN §3.14) and client construction (`$DRAFTS/03 §10.2`).
Header and env values may reference `${env:NAME}`, resolved at run time; never literal secrets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from wynd.runtime.mcp.client import McpClient

ENV_REF = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


class McpServerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str                              # ^[a-z0-9][a-z0-9_-]*$
    transport: Literal["http", "stdio"]
    url: str | None = None                 # http
    headers: dict[str, str] = {}           # values may contain ${env:NAME}; never literal secrets
    command: list[str] | None = None       # stdio argv
    env: dict[str, str] = {}               # stdio child env overlay; ${env:NAME} allowed
    auth_env: list[str] = []               # every env var referenced (manifest)
    timeout_s: float = 30.0
    description: str = ""
    oauth: dict[str, Any] | None = None    # {client_id, token_endpoint, expires_at, refresh_env} (controller OAuth, M4)


def resolve_env_refs(value: str, env: Mapping[str, str], *, server: str) -> str:
    """Substitute `${env:NAME}`; an unset NAME -> `McpConfigError`."""
    raise NotImplementedError("PLAN §5.1")


def open_client(entry: McpServerEntry, env: Mapping[str, str]) -> McpClient:
    """Resolve env references, connect and initialise."""
    raise NotImplementedError("PLAN §5.1")
