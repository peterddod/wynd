"""Scripted providers for offline unit tests of the loop, cassettes and their callers (PLAN §5.1).

Each call takes the next script item: a response is returned, an exception instance is raised. Every request is kept
in `requests`. Running past the end of the script raises `AssertionError` so a test fails instead of reaching a real
provider.

An instance can stand in for a provider class: `register_for_tests("scripted", provider)` makes
`load_provider("scripted", ...)` call `provider(tiers=...)`, which returns the same instance (now reporting those
tiers), and `provider_info` reads its `name`, `kind`, `default_tiers` and `env_fragment`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import ClassVar, Literal, TypeVar

from wynd.runtime.providers.types import (
    AgentRequest,
    AgentResponse,
    GenerateRequest,
    GenerateResponse,
    ProviderError,
)
from wynd.spec.fragments import EnvFragment

T = TypeVar("T")

SCRIPTED_TIERS = {"cheap": "scripted", "standard": "scripted", "strong": "scripted"}


def _next_item(script: list[T | BaseException], call: int, method: str) -> T:
    if call > len(script):
        raise AssertionError(f"scripted provider: no script item for {method} call {call} (script has {len(script)})")
    item = script[call - 1]
    if isinstance(item, BaseException):
        raise item
    return item


class ScriptedModelProvider:
    """ModelProvider fake: `generate` returns the next scripted `GenerateResponse` (or raises the next exception)."""

    name: str = "scripted"
    kind: ClassVar[Literal["model", "agent"]] = "model"
    default_tiers: ClassVar[dict[str, str]] = SCRIPTED_TIERS
    env_fragment: ClassVar[EnvFragment] = EnvFragment()

    def __init__(
        self,
        script: Iterable[GenerateResponse | BaseException] = (),
        *,
        name: str = "scripted",
        tiers: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.script = list(script)
        self.requests: list[GenerateRequest] = []
        self._tiers = {**SCRIPTED_TIERS, **(tiers or {})}

    def __call__(self, tiers: Mapping[str, str] | None = None) -> ScriptedModelProvider:
        if tiers is not None:
            self._tiers = {**SCRIPTED_TIERS, **tiers}
        return self

    def tiers(self) -> dict[str, str]:
        return dict(self._tiers)

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        self.requests.append(req)
        return _next_item(self.script, len(self.requests), "generate")


class ScriptedAgentProvider:
    """AgentProvider fake: `run` returns the next scripted `AgentResponse` (or raises the next exception).

    Like a real harness it streams the response's transcript and runs the tools it says it called: before returning,
    every transcript entry is passed to `req.on_event` (when set) in order, and every `{"type": "tool_use", "name",
    "input"}` entry whose name is one of `req.tools` is then invoked through its `ToolHandle`. Other entries (harness
    built-ins, `StructuredOutput`) are not executed. An exception raised by a tool (e.g. `ToolFailure`,
    `CassetteMissError`) propagates, as a harness aborts on it. A set `req.cancel` raises
    `ProviderError(kind="transport")` "cancelled" without recording the request or consuming a script item.
    """

    name: str = "scripted"
    kind: ClassVar[Literal["model", "agent"]] = "agent"
    default_tiers: ClassVar[dict[str, str]] = SCRIPTED_TIERS
    env_fragment: ClassVar[EnvFragment] = EnvFragment()

    def __init__(
        self,
        script: Iterable[AgentResponse | BaseException] = (),
        *,
        name: str = "scripted",
        tiers: Mapping[str, str] | None = None,
    ) -> None:
        self.name = name
        self.script = list(script)
        self.requests: list[AgentRequest] = []
        self._tiers = {**SCRIPTED_TIERS, **(tiers or {})}

    def __call__(self, tiers: Mapping[str, str] | None = None) -> ScriptedAgentProvider:
        if tiers is not None:
            self._tiers = {**SCRIPTED_TIERS, **tiers}
        return self

    def tiers(self) -> dict[str, str]:
        return dict(self._tiers)

    def run(self, req: AgentRequest) -> AgentResponse:
        if req.cancel is not None and req.cancel.is_set():
            raise ProviderError("cancelled", kind="transport", retryable=False)
        self.requests.append(req)
        response = _next_item(self.script, len(self.requests), "run")
        handles = {h.name: h for h in req.tools}
        for entry in response.transcript:
            if req.on_event is not None:
                req.on_event(entry)
            if entry.get("type") == "tool_use" and entry.get("name") in handles:
                handles[entry["name"]].invoke(entry.get("input") or {})
        return response
