"""MCP OAuth (PLAN §8.1 oauth row; `$DRAFTS/06 §5.13`). Stub; CTL-OAUTH.

RFC 9728 -> 8414 discovery, RFC 7591 dynamic registration, PKCE S256. Tokens are stored with `Registry.put_secret` as
`WYND_MCP_<NAME>_TOKEN` (`NAME = name.upper().replace("-", "_")`); the entry gets `auth_env`,
`headers {"Authorization": "Bearer ${env:WYND_MCP_<NAME>_TOKEN}"}` and `oauth`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.runtime.mcp.entry import McpServerEntry
    from wynd.runtime.storage.base import Registry


def oauth_start(registry: Registry, name: str, *, redirect_uri: str, return_to: str | None = None) -> str:
    """-> the authorize URL (pending state kept in memory for 10 minutes)."""
    raise NotImplementedError("PLAN §8.1")


def oauth_complete(registry: Registry, state: str, code: str) -> tuple[McpServerEntry, str | None]:
    """-> (the updated `McpServerEntry`, return_to)."""
    raise NotImplementedError("PLAN §8.1")


def refresh_tokens(registry: Registry) -> list[str]:
    """Refresh tokens expiring within 60 s; -> names refreshed. Failures are logged and skipped."""
    raise NotImplementedError("PLAN §8.1")
