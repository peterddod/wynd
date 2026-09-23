"""`RegistryService`: MCP servers, providers, image registries (PLAN §8.1, §3.14; `$DRAFTS/06 §5.13`). Stub; CTL-CORE.

Entries: runtime `McpServerEntry`/`ProviderEntry`, process `ImageRegistryEntry` (one default). Providers merge to
exactly cheap/standard/strong; the DTO is `ProviderInfoDTO`. `remove_*` reports `referenced_by`.
`oauth_start/oauth_complete/refresh_tokens` delegate to `wynd.controller.oauth`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import ProviderInfoDTO, RemoveResult
    from wynd.process.artefacts import ImageRegistryEntry
    from wynd.runtime.mcp.entry import McpServerEntry


class RegistryService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def list_mcp(self) -> list[McpServerEntry]:
        raise NotImplementedError("PLAN §8.1")

    def add_mcp(self, entry: McpServerEntry) -> McpServerEntry:
        """Upsert."""
        raise NotImplementedError("PLAN §8.1")

    def remove_mcp(self, name: str) -> RemoveResult:
        raise NotImplementedError("PLAN §8.1")

    def list_providers(self) -> list[ProviderInfoDTO]:
        raise NotImplementedError("PLAN §8.1")

    def add_provider(self, name: str, *, tiers: Mapping[str, str] | None = None) -> ProviderInfoDTO:
        raise NotImplementedError("PLAN §8.1")

    def remove_provider(self, name: str) -> RemoveResult:
        raise NotImplementedError("PLAN §8.1")

    def list_image_registries(self) -> list[ImageRegistryEntry]:
        raise NotImplementedError("PLAN §8.1")

    def add_image_registry(self, entry: ImageRegistryEntry) -> ImageRegistryEntry:
        raise NotImplementedError("PLAN §8.1")

    def remove_image_registry(self, name: str) -> RemoveResult:
        raise NotImplementedError("PLAN §8.1")

    def default_image_registry(self) -> ImageRegistryEntry | None:
        raise NotImplementedError("PLAN §8.1")

    def oauth_start(self, name: str, *, redirect_uri: str, return_to: str | None = None) -> str:
        """-> the authorize URL."""
        raise NotImplementedError("PLAN §8.1")

    def oauth_complete(self, state: str, code: str) -> tuple[McpServerEntry, str | None]:
        """-> (the updated entry, return_to)."""
        raise NotImplementedError("PLAN §8.1")

    def refresh_tokens(self) -> list[str]:
        """Names of the MCP entries whose tokens were refreshed."""
        raise NotImplementedError("PLAN §8.1")
