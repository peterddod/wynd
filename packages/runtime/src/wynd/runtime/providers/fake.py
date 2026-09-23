"""The `fake` AgentProvider (PLAN §3.15, §15 item 63; script format `$DRAFTS/03 §14.5`).

Test-only and inert unless selected: answers from `WYND_FAKE_PROVIDER_SCRIPT` (inline JSON when the value's first
non-space character is `{` or `[`, else a file path), zero usage, model id `fake`, no network I/O.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import ClassVar, Literal

from wynd.runtime.providers.types import AgentRequest, AgentResponse
from wynd.spec.fragments import EnvFragment, EnvVar


class FakeProvider:
    name: ClassVar[str] = "fake"
    kind: ClassVar[Literal["model", "agent"]] = "agent"
    default_tiers: ClassVar[dict[str, str]] = {"cheap": "fake", "standard": "fake", "strong": "fake"}
    env_fragment: ClassVar[EnvFragment] = EnvFragment(
        vars=[
            EnvVar(
                name="WYND_FAKE_PROVIDER_SCRIPT",
                description="Fake provider script: inline JSON (starting with { or [) or a file path.",
                required=False,
                used_by=["provider:fake"],
            ),
        ],
    )

    def __init__(self, tiers: Mapping[str, str]) -> None:
        raise NotImplementedError("PLAN §3.15")

    def tiers(self) -> dict[str, str]:
        raise NotImplementedError("PLAN §3.15")

    def run(self, req: AgentRequest) -> AgentResponse:
        raise NotImplementedError("PLAN §3.15")
