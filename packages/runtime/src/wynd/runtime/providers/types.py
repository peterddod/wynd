"""Provider-neutral request/response types, the provider protocols and `ProviderError` (PLAN §3.15;
`$DRAFTS/03 §5.1`). `ProviderError` is defined only here.

Messages are Anthropic-shaped: `{"role": "user"|"assistant", "content": [block, ...]}`. Output schemas are passed
UNWRAPPED; providers wrap them with `wynd.runtime.agentic.schema.wrap_output_schema`.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from wynd.runtime.usage import Usage
from wynd.spec.lockfiles import Thinking

Message = dict[str, Any]

ProviderErrorKind = Literal["transport", "auth", "unavailable", "invalid_request", "refusal", "max_turns"]


@dataclass(frozen=True)
class ToolSchema:
    name: str
    description: str
    input_schema: dict[str, Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ToolResult:
    text: str
    is_error: bool = False


@dataclass(frozen=True)
class ToolHandle:
    name: str
    description: str
    input_schema: dict[str, Any]
    invoke: Callable[[dict[str, Any]], ToolResult]
    local: bool = False     # True iff the tool has no `network` effect and is not MCP (re-executed on AgentProvider replay)


@dataclass(frozen=True)
class McpServerRef:
    name: str
    allow: tuple[str, ...]


@dataclass(frozen=True)
class GenerateRequest:
    model_id: str
    thinking: Thinking
    system: str
    messages: list[Message]
    tools: list[ToolSchema]
    output_schema: dict[str, Any] | None


@dataclass(frozen=True)
class GenerateResponse:
    message: Message
    text: str
    tool_calls: list[ToolCall]
    structured_output: Any | None
    stop: Literal["end", "tool_calls", "max_tokens", "refusal"]
    usage: Usage
    model_id: str
    cost_basis: Literal["api", "list"] | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class Continuation:
    session: dict[str, Any]
    message: str


@dataclass(frozen=True)
class AgentRequest:
    model_id: str
    thinking: Thinking
    instruction: str
    context: dict[str, Any]
    input: dict[str, Any]
    output_schema: dict[str, Any] | None
    tools: list[ToolHandle]
    mcp_servers: list[McpServerRef]
    workspace: Path
    max_turns: int = 25
    builtin_tools: list[str] = field(default_factory=list)   # never None; [] = no harness built-ins
    continuation: Continuation | None = None
    prompt: str | None = None                 # raw mode: instruction = system prompt verbatim, prompt = user message
    on_event: Callable[[dict[str, Any]], None] | None = None   # {"type":"text"|"tool_use"|"tool_result", ...}; not keyed
    cancel: threading.Event | None = None     # checked between SDK messages; set -> ProviderError "cancelled"


@dataclass(frozen=True)
class AgentResponse:
    structured_output: Any | None
    usage: Usage
    model_id: str
    transcript: list[dict[str, Any]]
    session: dict[str, Any]
    note: str = ""
    tool_calls: int = 0
    startup_ms: float | None = None
    cost_basis: Literal["api", "list"] | None = None


class ModelProvider(Protocol):
    name: str

    def generate(self, req: GenerateRequest) -> GenerateResponse: ...

    def tiers(self) -> dict[str, str]: ...


class AgentProvider(Protocol):
    name: str

    def run(self, req: AgentRequest) -> AgentResponse: ...

    def tiers(self) -> dict[str, str]: ...


class ProviderError(Exception):
    """A provider call failed; the loop maps `kind` to a step error cause (PLAN §3.9)."""

    def __init__(
        self,
        message: str,
        *,
        kind: ProviderErrorKind,
        retryable: bool,
        tool_called: bool = False,
        status: int | None = None,
    ) -> None:
        super().__init__(message)
        self.kind = kind
        self.retryable = retryable
        self.tool_called = tool_called
        self.status = status
