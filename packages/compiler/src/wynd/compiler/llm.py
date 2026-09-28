"""The compiler's own LLM use (`$DRAFTS/05 §13.2`, PLAN §7 item 5).

`ProviderLLM` sits on the runtime provider protocols: a ModelProvider gets a `GenerateRequest`; an AgentProvider runs
in raw mode (`AgentRequest(instruction=<system>, prompt=<user>, context={}, input={}, tools=[], mcp_servers=[],
builtin_tools=[], output_schema=<plain schema>, workspace=<scratch>/llm, ...)`). The compiler has no strictness
transform of its own: the provider's `wrap_output_schema` is the only one (PLAN §3.15).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Generic, Literal, Protocol, TypeVar

from wynd.runtime.providers.types import AgentRequest, GenerateRequest, ProviderError
from wynd.runtime.usage import Usage
from wynd.spec.hashing import canonical_json

if TYPE_CHECKING:
    from wynd.compiler.calls import CallKind
    from wynd.compiler.session import Memo
    from wynd.runtime.providers.types import AgentProvider, ModelProvider
    from wynd.runtime.storage.base import Registry

T = TypeVar("T")

REPAIR_TEXT = "Your previous answer did not match the schema:"


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


class CompilerLLMError(Exception):
    """A compiler call failed for good: provider error after its retries, timeout, or no valid answer after the
    schema-repair retry."""

    def __init__(self, kind: str, node: str, message: str):
        super().__init__(f"compiler call {kind} for {node!r} failed: {message}")
        self.kind = kind
        self.node = node


class CompilerLLM(Protocol):
    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]: ...


class ProviderLLM:
    """Validates `structured_output` with `response_model`; on a validation error re-asks once with the error
    appended; transport errors retry twice (backoff 5 s, 20 s). `timeout_s` bounds each AgentProvider request (via
    `AgentRequest.cancel`); ModelProvider requests rely on the provider's own HTTP timeout."""

    backoff_s: tuple[float, ...] = (5.0, 20.0)

    def __init__(self, provider: ModelProvider | AgentProvider, tiers: dict[str, str], workdir: Path,
                 timeout_s: int = 900):
        self.provider = provider
        self.tiers = tiers
        self.workdir = workdir
        self.timeout_s = timeout_s

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        model_id = self.tiers.get(Tier(tier).value)
        if not model_id:
            raise CompilerLLMError(kind, node, f"provider {self.provider.name!r} has no model for tier {tier!s}")
        schema = response_model.model_json_schema()
        usage = Usage()
        text = prompt
        for attempt in (1, 2):
            output, call_usage = self._request(kind, node, system, text, schema, model_id, thinking)
            usage = usage + call_usage
            try:
                if output is None:
                    raise ValueError("no structured output was returned")
                return LLMResult(response_model.model_validate(output), usage, memo_hit=False)
            except ValueError as err:          # pydantic's ValidationError is a ValueError
                if attempt == 2:
                    raise CompilerLLMError(kind, node, f"answer did not match the schema after a retry: {err}")
                text = f"{prompt}\n\n{REPAIR_TEXT}\n{err}"
        raise AssertionError("unreachable")

    def _request(self, kind: str, node: str, system: str, prompt: str, schema: dict[str, Any], model_id: str,
                 thinking: Thinking) -> tuple[Any, Usage]:
        """One provider request with transport retries; -> (structured output, usage)."""
        for retry in range(len(self.backoff_s) + 1):
            try:
                return self._send(system, prompt, schema, model_id, thinking)
            except ProviderError as err:
                if not err.retryable or retry == len(self.backoff_s):
                    raise CompilerLLMError(kind, node, f"{err.kind}: {err}") from err
                time.sleep(self.backoff_s[retry])
            except TimeoutError as err:
                raise CompilerLLMError(kind, node, str(err)) from err
        raise AssertionError("unreachable")

    def _send(self, system: str, prompt: str, schema: dict[str, Any], model_id: str,
              thinking: Thinking) -> tuple[Any, Usage]:
        if getattr(self.provider, "kind", None) == "model":
            response = self.provider.generate(GenerateRequest(
                model_id=model_id, thinking=thinking, system=system,
                messages=[{"role": "user", "content": [{"type": "text", "text": prompt}]}],
                tools=[], output_schema=schema))
            return response.structured_output, response.usage
        self.workdir.mkdir(parents=True, exist_ok=True)
        cancel = threading.Event()
        timer = threading.Timer(self.timeout_s, cancel.set)
        timer.daemon = True
        timer.start()
        try:
            response = self.provider.run(AgentRequest(
                model_id=model_id, thinking=thinking, instruction=system, context={}, input={},
                output_schema=schema, tools=[], mcp_servers=[], workspace=self.workdir, builtin_tools=[],
                prompt=prompt, cancel=cancel))
        except ProviderError:
            if cancel.is_set():
                raise TimeoutError(f"timed out after {self.timeout_s} s") from None
            raise
        finally:
            timer.cancel()
        return response.structured_output, response.usage


class MemoLLM:
    """Wraps any CompilerLLM with the session memo (`$DRAFTS/05 §6.6` call keys)."""

    def __init__(self, inner: CompilerLLM, memo: Memo, on_usage: Callable[[CallKind, str, Usage], None]):
        self.inner = inner
        self.memo = memo
        self.on_usage = on_usage

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        key = call_key(kind, tier=tier, thinking=thinking, system=system, prompt=prompt,
                       schema=response_model.model_json_schema())
        entry = self.memo.calls.get(key)
        if entry is not None:
            return LLMResult(response_model.model_validate(entry["response"]), Usage(), memo_hit=True)
        result = self.inner.call(kind, node=node, system=system, prompt=prompt, response_model=response_model,
                                 tier=tier, thinking=thinking)
        self.memo.calls[key] = {
            "kind": kind,
            "step": node,
            "response": result.value.model_dump(mode="json"),
            "usage": result.usage.model_dump(mode="json"),
        }
        self.on_usage(kind, node, result.usage)
        return LLMResult(result.value, result.usage, memo_hit=False)


def call_key(kind: str, *, tier: str, thinking: str, system: str, prompt: str, schema: dict[str, Any]) -> str:
    """`sha256(canonical_json({kind, tier, thinking, system, prompt, schema}))[:32]` (`$DRAFTS/05 §6.6`)."""
    payload = {"kind": kind, "tier": str(tier), "thinking": thinking, "system": system, "prompt": prompt,
               "schema": schema}
    return hashlib.sha256(canonical_json(payload)).hexdigest()[:32]


def default_llm(registry: Registry, workdir: Path) -> CompilerLLM:
    """`ProviderLLM` over `load_provider($WYND_COMPILER_PROVIDER or "claude-code", registry=registry)` with its
    `provider_tiers`."""
    from wynd.runtime.providers import load_provider, provider_tiers

    name = os.environ.get("WYND_COMPILER_PROVIDER") or "claude-code"
    return ProviderLLM(load_provider(name, registry=registry), provider_tiers(name, registry), workdir)
