"""`RegistryService`: MCP servers, providers, image registries (PLAN §8.1, §3.14; `$DRAFTS/06 §5.13`).

Entries: runtime `McpServerEntry`/`ProviderEntry`, process `ImageRegistryEntry` (at most one default). A provider
entry stores only the tiers the user set; merged over the provider's `default_tiers` it must name exactly cheap,
standard and strong. `remove_*` removes the entry even when processes still reference it and reports them in
`referenced_by`. `oauth_start/oauth_complete/refresh_tokens` delegate to `wynd.controller.oauth`.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING
from urllib.parse import urlparse

from wynd.controller.errors import Invalid
from wynd.spec.base import TIERS

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import ProviderInfoDTO, RemoveResult
    from wynd.process.artefacts import ImageRegistryEntry
    from wynd.process.loader import LoadedProcess
    from wynd.runtime.mcp.entry import McpServerEntry
    from wynd.runtime.storage.base import Registry

MCP, PROVIDERS, REGISTRIES = "mcp", "providers", "registries"
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
ENV_REF = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")


class RegistryService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    @property
    def registry(self) -> Registry:
        return self.ctx.stores.registry

    # --- MCP servers ----------------------------------------------------------------------------------------------

    def list_mcp(self) -> list[McpServerEntry]:
        from wynd.runtime.mcp.entry import McpServerEntry

        return [McpServerEntry.model_validate(entry) for _, entry in sorted(self.registry.list(MCP).items())]

    def add_mcp(self, entry: McpServerEntry) -> McpServerEntry:
        """Upsert. An http url must be http(s); every `${env:NAME}` in `headers`/`env` joins `auth_env`."""
        if entry.transport == "http" and urlparse(entry.url or "").scheme not in ("http", "https"):
            raise Invalid(f"MCP server {entry.name!r}: url must be an http(s) URL, got {entry.url!r}")
        values = [*entry.headers.values(), *entry.env.values()]
        referenced = [name for value in values for name in ENV_REF.findall(value)]
        auth_env = list(dict.fromkeys([*entry.auth_env, *referenced]))
        bad = [name for name in auth_env if not ENV_NAME.match(name)]
        if bad:
            raise Invalid(f"MCP server {entry.name!r}: invalid env var name(s) {', '.join(bad)}")
        stored = entry.model_copy(update={"auth_env": auth_env})
        self.registry.put(MCP, stored.name, stored.model_dump(mode="json"))
        return stored

    def remove_mcp(self, name: str) -> RemoveResult:
        from wynd.controller.models import RemoveResult

        users = self._referencing(lambda lp: any(s.server == name for pkg in _packages(lp) for s in pkg.lock.mcp))
        return RemoveResult(removed=self.registry.remove(MCP, name), referenced_by=users)

    # --- providers ------------------------------------------------------------------------------------------------

    def list_providers(self) -> list[ProviderInfoDTO]:
        from wynd.runtime.providers import available_providers

        return [self._provider(name) for name in sorted(available_providers())]

    def add_provider(self, name: str, *, tiers: Mapping[str, str] | None = None) -> ProviderInfoDTO:
        """Overlay `tiers` on the stored entry (only the given tiers change)."""
        from wynd.runtime.providers import ProviderEntry, available_providers, provider_info

        available = available_providers()
        if name not in available:
            raise Invalid(f"unknown provider {name!r} (installed: {', '.join(sorted(available))})")
        tiers = dict(tiers or {})
        unknown = sorted(set(tiers) - set(TIERS))
        if unknown:
            raise Invalid(f"unknown tier(s) {', '.join(unknown)}; tiers are {', '.join(TIERS)}")
        empty = sorted(tier for tier, model in tiers.items() if not model.strip())
        if empty:
            raise Invalid(f"tier(s) {', '.join(empty)} need a model id")
        current = ProviderEntry.model_validate(self.registry.get(PROVIDERS, name) or {})
        entry = ProviderEntry(tiers={**current.tiers, **tiers})
        merged = {**provider_info(name).default_tiers, **entry.tiers}
        incomplete = [tier for tier in TIERS if not merged.get(tier)]
        if incomplete or set(merged) != set(TIERS):
            raise Invalid(f"provider {name!r} needs a model for every tier ({', '.join(TIERS)}); "
                          f"missing {', '.join(incomplete) or 'none'}")
        self.registry.put(PROVIDERS, name, entry.model_dump(mode="json"))
        return self._provider(name)

    def remove_provider(self, name: str) -> RemoveResult:
        from wynd.controller.models import RemoveResult

        users = self._referencing(lambda lp: lp.doc.provider == name
                                  or any(pkg.lock.provider == name for pkg in _packages(lp)))
        return RemoveResult(removed=self.registry.remove(PROVIDERS, name), referenced_by=users)

    def _provider(self, name: str) -> ProviderInfoDTO:
        from wynd.controller.models import ProviderInfoDTO
        from wynd.runtime.providers import check_provider, provider_info, provider_tiers

        ready, message = check_provider(name)
        return ProviderInfoDTO(name=name, kind=provider_info(name).kind, tiers=provider_tiers(name, self.registry),
                               ready=ready, message=message)

    # --- image registries -----------------------------------------------------------------------------------------

    def list_image_registries(self) -> list[ImageRegistryEntry]:
        from wynd.process.artefacts import ImageRegistryEntry

        return [ImageRegistryEntry.model_validate(e) for _, e in sorted(self.registry.list(REGISTRIES).items())]

    def add_image_registry(self, entry: ImageRegistryEntry) -> ImageRegistryEntry:
        """Upsert; a default entry clears the default flag of every other one."""
        if not entry.url.strip():
            raise Invalid(f"image registry {entry.name!r} needs a url")
        bad = [name for name in (entry.username_env, entry.password_env) if name and not ENV_NAME.match(name)]
        if bad:
            raise Invalid(f"image registry {entry.name!r}: invalid env var name(s) {', '.join(bad)}")
        if entry.default:
            for other in self.list_image_registries():
                if other.default and other.name != entry.name:
                    cleared = other.model_copy(update={"default": False})
                    self.registry.put(REGISTRIES, other.name, cleared.model_dump(mode="json"))
        self.registry.put(REGISTRIES, entry.name, entry.model_dump(mode="json"))
        return entry

    def remove_image_registry(self, name: str) -> RemoveResult:
        from wynd.controller.models import RemoveResult

        return RemoveResult(removed=self.registry.remove(REGISTRIES, name))

    def default_image_registry(self) -> ImageRegistryEntry | None:
        return next((entry for entry in self.list_image_registries() if entry.default), None)

    # --- MCP OAuth (CTL-OAUTH) ------------------------------------------------------------------------------------

    def oauth_start(self, name: str, *, redirect_uri: str, return_to: str | None = None) -> str:
        """-> the authorize URL."""
        from wynd.controller import oauth

        return oauth.oauth_start(self.registry, name, redirect_uri=redirect_uri, return_to=return_to)

    def oauth_complete(self, state: str, code: str) -> tuple[McpServerEntry, str | None]:
        """-> (the updated entry, return_to)."""
        from wynd.controller import oauth

        return oauth.oauth_complete(self.registry, state, code)

    def refresh_tokens(self) -> list[str]:
        """Names of the MCP entries whose tokens were refreshed; nothing to do unless an entry came from OAuth."""
        from wynd.controller import oauth

        if not any(entry.oauth for entry in self.list_mcp()):
            return []
        return oauth.refresh_tokens(self.registry)

    def _referencing(self, uses: Callable[[LoadedProcess], bool]) -> list[str]:
        """Ids of the processes (that load) for which `uses(lp)` holds."""
        from wynd.process.errors import WyndProcessError

        ws = self.ctx.workspace()
        users = []
        for pid in ws.process_ids():
            try:
                lp = ws.load_process(pid)
            except WyndProcessError:
                continue
            if uses(lp):
                users.append(pid)
        return users


def _packages(lp: LoadedProcess) -> list:
    """The process's own compiled step packages."""
    return [rs.package for rs in lp.steps.values() if rs.package is not None and rs.package.lock is not None]
