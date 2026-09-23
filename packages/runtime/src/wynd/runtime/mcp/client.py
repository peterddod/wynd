"""Stdlib MCP client over streamable HTTP (JSON and SSE bodies) and stdio, protocol `2025-11-25` (PLAN §1.5;
`$DRAFTS/03 §10.3`; spike `docs/design/spikes/mcp_client_stdlib.py`)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

PROTOCOL_VERSION = "2025-11-25"


class McpError(Exception):
    def __init__(self, message: str, *, transient: bool = False) -> None:
        super().__init__(message)
        self.transient = transient


class McpAuthError(McpError):
    """HTTP 401/403 from the server."""


class HttpTransport:
    def __init__(self, url: str, headers: dict[str, str], timeout: float) -> None:
        raise NotImplementedError("PLAN §5.1")

    def send(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError("PLAN §5.1")

    def close(self) -> None:
        raise NotImplementedError("PLAN §5.1")


class StdioTransport:
    def __init__(self, command: list[str], env: Mapping[str, str]) -> None:
        raise NotImplementedError("PLAN §5.1")

    def send(self, msg: dict[str, Any]) -> dict[str, Any] | None:
        raise NotImplementedError("PLAN §5.1")

    def close(self) -> None:
        raise NotImplementedError("PLAN §5.1")


class McpClient:
    def __init__(self, transport: HttpTransport | StdioTransport) -> None:
        raise NotImplementedError("PLAN §5.1")

    def initialize(self) -> dict[str, Any]:
        raise NotImplementedError("PLAN §5.1")

    def list_tools(self) -> list[dict[str, Any]]:
        raise NotImplementedError("PLAN §5.1")

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("PLAN §5.1")

    def close(self) -> None:
        raise NotImplementedError("PLAN §5.1")
