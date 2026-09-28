"""User-registry MCP server entries (section `mcp`, PLAN §3.14) and client construction (`$DRAFTS/03 §10.2`).
Header and env values may reference `${env:NAME}`, resolved at run time; never literal secrets."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from wynd.runtime.agentic.errors import McpConfigError
from wynd.runtime.mcp.client import HttpTransport, McpClient, StdioTransport

ENV_REF = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
SERVER_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*$")


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

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        if not SERVER_NAME.match(value):
            raise ValueError(f"MCP server name {value!r} must match {SERVER_NAME.pattern}")
        return value

    @model_validator(mode="after")
    def _endpoint(self) -> McpServerEntry:
        if self.transport == "http" and not self.url:
            raise ValueError(f"MCP server {self.name!r}: transport http needs a url")
        if self.transport == "stdio" and not self.command:
            raise ValueError(f"MCP server {self.name!r}: transport stdio needs a command")
        return self


def resolve_env_refs(value: str, env: Mapping[str, str], *, server: str) -> str:
    """Substitute `${env:NAME}`; an unset (or empty) NAME -> `McpConfigError`."""

    def substitute(match: re.Match[str]) -> str:
        name = match.group(1)
        found = env.get(name)
        if not found:
            raise McpConfigError(f"MCP server {server!r} needs env var {name} (listed in process.env.yaml)")
        return found

    return ENV_REF.sub(substitute, value)


def open_client(entry: McpServerEntry, env: Mapping[str, str]) -> McpClient:
    """Resolve env references, connect and initialise. A stdio server runs with `env` plus its resolved overlay."""
    match entry.transport:
        case "http":
            headers = {k: resolve_env_refs(v, env, server=entry.name) for k, v in entry.headers.items()}
            transport: HttpTransport | StdioTransport = HttpTransport(entry.url or "", headers, entry.timeout_s)
        case "stdio":
            overlay = {k: resolve_env_refs(v, env, server=entry.name) for k, v in entry.env.items()}
            transport = StdioTransport(entry.command or [], {**env, **overlay})
    client = McpClient(transport)
    try:
        client.initialize()
    except BaseException:
        client.close()
        raise
    return client
