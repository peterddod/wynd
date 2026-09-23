"""The compiler's own LLM use (`$DRAFTS/05 §13.2`, PLAN §7 item 5).

`ProviderLLM` sits on the runtime provider protocols: a ModelProvider gets a `GenerateRequest`; an AgentProvider runs
in raw mode (`AgentRequest(instruction=<system>, prompt=<user>, context={}, input={}, tools=[], mcp_servers=[],
builtin_tools=[], output_schema=<plain schema>, workspace=<scratch>/llm, ...)`). The compiler has no strictness
transform of its own: the provider's `wrap_output_schema` is the only one (PLAN §3.15).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Generic, Literal, Protocol, TypeVar

if TYPE_CHECKING:
    from wynd.compiler.calls import CallKind
    from wynd.compiler.session import Memo
    from wynd.runtime.providers.types import AgentProvider, ModelProvider
    from wynd.runtime.storage.base import Registry
    from wynd.runtime.usage import Usage

T = TypeVar("T")


class Tier(StrEnum):
    CHEAP = "cheap"
    STANDARD = "standard"
    STRONG = "strong"


Thinking = Literal["low", "medium", "high"]


@dataclass
class LLMResult(Generic[T]):
    value: T
    usage: Usage
    memo_hit: bool


class CompilerLLM(Protocol):
    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]: ...


class ProviderLLM:
    """Validates `structured_output` with `response_model`; on a validation error re-asks once with the error
    appended; transport errors retry twice (backoff 5 s, 20 s)."""

    def __init__(self, provider: ModelProvider | AgentProvider, tiers: dict[str, str], workdir: Path,
                 timeout_s: int = 900):
        self.provider = provider
        self.tiers = tiers
        self.workdir = workdir
        self.timeout_s = timeout_s

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        raise NotImplementedError("PLAN §7")


class MemoLLM:
    """Wraps any CompilerLLM with the session memo (`$DRAFTS/05 §6.6` call keys)."""

    def __init__(self, inner: CompilerLLM, memo: Memo, on_usage: Callable[[CallKind, str, Usage], None]):
        self.inner = inner
        self.memo = memo
        self.on_usage = on_usage

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        raise NotImplementedError("PLAN §7")


def default_llm(registry: Registry, workdir: Path) -> CompilerLLM:
    """`ProviderLLM` over `load_provider($WYND_COMPILER_PROVIDER or "claude-code", registry=registry)` with its
    `provider_tiers`."""
    raise NotImplementedError("PLAN §7")
