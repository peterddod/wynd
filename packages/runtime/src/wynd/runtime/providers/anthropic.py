"""The `anthropic` ModelProvider over the Messages API with the runtime `HttpClient` (PLAN §3.15;
`$DRAFTS/03 §8`): strict structured output with prompt fallback, pricing table, no internal retries."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, ClassVar, Literal

from wynd.runtime.providers.types import GenerateRequest, GenerateResponse
from wynd.spec.fragments import EnvFragment, EnvVar

if TYPE_CHECKING:
    from wynd.runtime.http import HttpClient


class AnthropicProvider:
    name: ClassVar[str] = "anthropic"
    kind: ClassVar[Literal["model", "agent"]] = "model"
    default_tiers: ClassVar[dict[str, str]] = {
        "cheap": "claude-haiku-4-5",
        "standard": "claude-sonnet-5",
        "strong": "claude-opus-5",
    }
    env_fragment: ClassVar[EnvFragment] = EnvFragment(
        vars=[
            EnvVar(
                name="ANTHROPIC_API_KEY",
                description="Anthropic API key.",
                secret=True,
                required=True,
                used_by=["provider:anthropic"],
            ),
            EnvVar(
                name="ANTHROPIC_BASE_URL",
                description="Messages API base URL.",
                required=False,
                default="https://api.anthropic.com",
                used_by=["provider:anthropic"],
            ),
        ],
    )

    def __init__(
        self, tiers: Mapping[str, str], *, http: HttpClient | None = None, env: Mapping[str, str] | None = None
    ) -> None:
        raise NotImplementedError("PLAN §3.15")

    def tiers(self) -> dict[str, str]:
        raise NotImplementedError("PLAN §3.15")

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        raise NotImplementedError("PLAN §3.15")
