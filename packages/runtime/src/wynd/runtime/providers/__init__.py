"""Provider registry: entry-point group `wynd.providers` (`claude-code`, `anthropic`, `fake`), tier resolution and
readiness checks (SPEC §3.9, PLAN §3.15; `$DRAFTS/03 §5.2`).

Provider classes carry class attributes `name, kind, default_tiers, env_fragment`, readable without importing any SDK.
"""

from __future__ import annotations

import importlib.metadata
import os
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, ConfigDict, ValidationError

from wynd.runtime.errors import StepFailure
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

ENTRY_POINT_GROUP = "wynd.providers"

_TEST_OVERRIDES: dict[str, Any] = {}


class ProviderEntry(BaseModel):            # user registry section "providers"
    model_config = ConfigDict(extra="forbid")
    tiers: dict[Literal["cheap", "standard", "strong"], str] = {}      # overlays the provider's default_tiers


@dataclass(frozen=True)
class ProviderInfo:
    name: str
    kind: Literal["model", "agent"]
    default_tiers: dict[str, str]
    env_fragment: EnvFragment


def _not_installed(name: str) -> str:
    return f"provider {name!r} is not installed (entry point group {ENTRY_POINT_GROUP})"


def _provider_class(name: str) -> Any:
    """The registered class (or test stand-in) for `name`; KeyError if unknown. Loading a class never imports an
    SDK."""
    if name in _TEST_OVERRIDES:
        return _TEST_OVERRIDES[name]
    eps = importlib.metadata.entry_points(group=ENTRY_POINT_GROUP, name=name)
    if not eps:
        raise KeyError(name)
    return next(iter(eps)).load()


def available_providers() -> dict[str, Literal["model", "agent"]]:
    kinds = {ep.name: ep.load().kind for ep in importlib.metadata.entry_points(group=ENTRY_POINT_GROUP)}
    kinds.update({name: cls.kind for name, cls in _TEST_OVERRIDES.items()})
    return kinds


def provider_info(name: str) -> ProviderInfo:
    """KeyError if unknown."""
    cls = _provider_class(name)
    return ProviderInfo(name=name, kind=cls.kind, default_tiers=dict(cls.default_tiers), env_fragment=cls.env_fragment)


def provider_tiers(name: str, registry: Registry | None) -> dict[str, str]:
    """`{**default_tiers, **ProviderEntry(registry["providers"][name]).tiers}`; KeyError if the provider is unknown,
    pydantic `ValidationError` if the registry entry is malformed."""
    tiers = dict(_provider_class(name).default_tiers)
    if registry is None:
        return tiers
    entry = ProviderEntry.model_validate(registry.get("providers", name) or {})
    return {**tiers, **entry.tiers}


def resolve_model(provider: str, tier: str, registry: Registry | None) -> str:
    """Model id for a tier, falling back to the class's `default_tiers`; `StepFailure("config")` on an unknown
    provider or tier."""
    try:
        tiers = provider_tiers(provider, registry)
    except KeyError:
        raise StepFailure("config", _not_installed(provider)) from None
    except ValidationError as e:
        raise StepFailure("config", f"user registry entry providers.{provider} is invalid: {e}") from None
    model = tiers.get(tier)
    if not model:
        raise StepFailure(
            "config",
            f"provider {provider!r} has no model for tier {tier!r}; "
            f"run `wynd provider add {provider} --tier {tier}=<model>`",
        )
    return model


def load_provider(name: str, *, registry: Registry | None = None) -> ModelProvider | AgentProvider:
    """A fresh provider instance with its effective tiers (cheap, stateless; created per step run). KeyError if
    unknown."""
    return _provider_class(name)(tiers=provider_tiers(name, registry))


def register_for_tests(name: str, cls: type) -> Callable[[], None]:
    """Test seam: resolve `name` to `cls` until the returned undo function is called."""
    missing = object()
    previous = _TEST_OVERRIDES.get(name, missing)
    _TEST_OVERRIDES[name] = cls

    def undo() -> None:
        if previous is missing:
            _TEST_OVERRIDES.pop(name, None)
        else:
            _TEST_OVERRIDES[name] = previous

    return undo


def check_provider(name: str) -> tuple[bool, str]:
    """Readiness without a model call: claude-code = SDK importable and bundled CLI found; anthropic =
    ANTHROPIC_API_KEY set; fake = True."""
    try:
        _provider_class(name)
    except KeyError:
        return False, _not_installed(name)
    match name:
        case "claude-code":
            from wynd.runtime.providers.claude_code import check_ready

            return check_ready()
        case "anthropic":
            if os.environ.get("ANTHROPIC_API_KEY"):
                return True, "ANTHROPIC_API_KEY is set"
            return False, "ANTHROPIC_API_KEY is not set"
        case _:
            return True, f"provider {name!r} needs no setup"
