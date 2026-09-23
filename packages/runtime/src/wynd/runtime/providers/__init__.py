"""Provider registry: entry-point group `wynd.providers` (`claude-code`, `anthropic`, `fake`), tier resolution and
readiness checks (SPEC §3.9, PLAN §3.15; `$DRAFTS/03 §5.2`).

Provider classes carry class attributes `name, kind, default_tiers, env_fragment`, readable without importing any SDK.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from pydantic import BaseModel, ConfigDict

from wynd.spec.base import DEFAULT_PROVIDER
from wynd.spec.fragments import EnvFragment

if TYPE_CHECKING:
    from wynd.runtime.providers.types import AgentProvider, ModelProvider
    from wynd.runtime.storage.base import Registry

__all__ = [
    "DEFAULT_PROVIDER",
    "ProviderEntry",
    "ProviderInfo",
    "available_providers",
    "check_provider",
    "load_provider",
    "provider_info",
    "provider_tiers",
    "register_for_tests",
    "resolve_model",
]


class ProviderEntry(BaseModel):            # user registry section "providers"
    model_config = ConfigDict(extra="forbid")
    tiers: dict[Literal["cheap", "standard", "strong"], str] = {}      # overlays the provider's default_tiers


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    kind: Literal["model", "agent"]
    default_tiers: dict[str, str]
    env_fragment: EnvFragment


def available_providers() -> dict[str, Literal["model", "agent"]]:
    raise NotImplementedError("PLAN §3.15")


def provider_info(name: str) -> ProviderInfo:
    """KeyError if unknown."""
    raise NotImplementedError("PLAN §3.15")


def provider_tiers(name: str, registry: Registry | None) -> dict[str, str]:
    """`{**default_tiers, **ProviderEntry(registry["providers"][name]).tiers}`."""
    raise NotImplementedError("PLAN §3.15")


def resolve_model(provider: str, tier: str, registry: Registry | None) -> str:
    """Model id for a tier, falling back to the class's `default_tiers`; `StepFailure("config")` on an unknown
    provider or tier."""
    raise NotImplementedError("PLAN §3.15")


def load_provider(name: str, *, registry: Registry | None = None) -> ModelProvider | AgentProvider:
    raise NotImplementedError("PLAN §3.15")


def register_for_tests(name: str, cls: type) -> Callable[[], None]:
    """Test seam: resolve `name` to `cls` until the returned undo function is called."""
    raise NotImplementedError("PLAN §3.15")


def check_provider(name: str) -> tuple[bool, str]:
    """Readiness without a model call: claude-code = SDK importable and bundled CLI found; anthropic =
    ANTHROPIC_API_KEY set; fake = True."""
    raise NotImplementedError("PLAN §3.15")
