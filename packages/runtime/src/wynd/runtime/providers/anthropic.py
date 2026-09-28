"""The `anthropic` ModelProvider over the Messages API with the runtime `HttpClient` (PLAN §3.15;
`$DRAFTS/03 §8`): strict structured output with prompt fallback, pricing table, no internal retries."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, ClassVar, Literal

from wynd.runtime.agentic.schema import strict_compatible, unwrap_output, wrap_output_schema
from wynd.runtime.http import HttpClient, HttpError
from wynd.runtime.providers.types import GenerateRequest, GenerateResponse, ProviderError, ToolCall
from wynd.runtime.usage import Usage
from wynd.spec.fragments import EnvFragment, EnvVar

if TYPE_CHECKING:
    from wynd.runtime.http import HttpResponse

DEFAULT_BASE_URL = "https://api.anthropic.com"
API_VERSION = "2023-06-01"
MAX_TOKENS = 16000
TIMEOUT_S = 600.0

# Model families with budget thinking and no effort parameter (matched by prefix); every other model gets adaptive
# thinking + `output_config.effort`. Never sent: `budget_tokens` to 4.6+ models, `{"type": "disabled"}`, `tool_choice`.
BUDGET_THINKING = ("claude-haiku-4-5",)
THINKING_BUDGETS = {"medium": 2048, "high": 8192}

JSON_PROMPT = "\n\nWhen you are finished, reply with only a JSON object matching this JSON Schema:\n"

# USD per 1M tokens (input, output), matched by model-id prefix, longest first. Cache reads cost 0.1x input and
# cache writes 1.25x input.
PRICING = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-4-6": (3.0, 15.0), "claude-sonnet-5": (2.0, 10.0),
           "claude-opus-4-6": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
           "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-fable-5": (10.0, 50.0)}

STOP_REASONS: dict[str, Literal["end", "tool_calls", "max_tokens", "refusal"]] = {
    "end_turn": "end", "stop_sequence": "end", "tool_use": "tool_calls", "max_tokens": "max_tokens",
    "refusal": "refusal"}


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
        """`http`: test seam, any object with `.post(url, json=, headers=, timeout=) -> HttpResponse`; `env`:
        defaults to `os.environ` at call time."""
        self._tiers = dict(tiers)
        self._http = http
        self._env = env

    def tiers(self) -> dict[str, str]:
        return dict(self._tiers)

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        env = os.environ if self._env is None else self._env
        key = env.get("ANTHROPIC_API_KEY")
        if not key:
            raise ProviderError("anthropic provider: set ANTHROPIC_API_KEY", kind="auth", retryable=False)
        base = (env.get("ANTHROPIC_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        headers = {"x-api-key": key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        http = self._http or HttpClient(timeout=TIMEOUT_S)
        t0 = time.monotonic()
        try:
            resp = http.post(f"{base}/v1/messages", json=request_body(req), headers=headers, timeout=TIMEOUT_S)
        except HttpError as e:
            if e.response is None:
                raise ProviderError(f"anthropic transport failure: {e}", kind="transport", retryable=True) from e
            resp = e.response
        elapsed_ms = (time.monotonic() - t0) * 1000
        if not 200 <= resp.status < 300:
            raise _status_error(resp)
        try:
            body = resp.json()
        except ValueError as e:
            raise ProviderError(f"anthropic API returned a body that is not JSON: {e}", kind="transport",
                                retryable=True, status=resp.status) from None
        return parse_response(req, body, elapsed_ms)


def thinking_params(model_id: str, thinking: str) -> dict[str, Any]:
    """`{"thinking": ...}` and/or `{"output_config": {"effort": ...}}` for a model family and thinking level."""
    if model_id.startswith(BUDGET_THINKING):
        budget = THINKING_BUDGETS.get(thinking)
        return {"thinking": {"type": "enabled", "budget_tokens": budget}} if budget else {}
    if thinking == "none":
        return {"output_config": {"effort": "low"}}
    return {"thinking": {"type": "adaptive"}, "output_config": {"effort": thinking}}


def request_body(req: GenerateRequest) -> dict[str, Any]:
    """The `POST /v1/messages` body. The Output schema is wrapped in the provider envelope; when it is not
    strict-compatible it goes into the system prompt instead and the JSON is parsed from the reply text."""
    params = thinking_params(req.model_id, req.thinking)
    output_config = dict(params.get("output_config", {}))
    system = req.system
    if req.output_schema is not None:
        wrapped = wrap_output_schema(req.output_schema)
        if strict_compatible(wrapped):
            output_config["format"] = {"type": "json_schema", "schema": wrapped}
        else:
            system += JSON_PROMPT + json.dumps(wrapped, sort_keys=True)
    body: dict[str, Any] = {"model": req.model_id, "max_tokens": MAX_TOKENS, "messages": req.messages}
    if system:
        body["system"] = system
    if req.tools:
        body["tools"] = [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                         for t in req.tools]
    if "thinking" in params:
        body["thinking"] = params["thinking"]
    if output_config:
        body["output_config"] = output_config
    return body


def parse_response(req: GenerateRequest, body: Mapping[str, Any], elapsed_ms: float) -> GenerateResponse:
    content = body["content"]
    tool_calls = [ToolCall(b["id"], b["name"], b["input"]) for b in content if b["type"] == "tool_use"]
    text = "".join(b["text"] for b in content if b["type"] == "text")
    stop = STOP_REASONS.get(body.get("stop_reason") or "", "end")
    structured = None
    if stop == "end" and req.output_schema is not None and not tool_calls:
        try:
            structured = unwrap_output(json.loads(_strip_fences(text)))
        except ValueError:
            structured = None
    u = body["usage"]     # input_tokens excludes cache tokens
    model = body.get("model") or req.model_id
    usage = Usage(input_tokens=u["input_tokens"], output_tokens=u["output_tokens"],
                  cache_read_tokens=u.get("cache_read_input_tokens") or 0,
                  cache_write_tokens=u.get("cache_creation_input_tokens") or 0,
                  cost_usd=cost(model, u), latency_ms=elapsed_ms, calls=1)
    # content is appended verbatim (thinking blocks keep their signature, as the API requires on continuation)
    return GenerateResponse(message={"role": "assistant", "content": content}, text=text, tool_calls=tool_calls,
                            structured_output=structured, stop=stop, usage=usage, model_id=model,
                            cost_basis="api", raw=dict(body))


def cost(model: str, u: Mapping[str, Any]) -> float | None:
    """USD for one response's usage; None for a model missing from `PRICING`."""
    for prefix in sorted(PRICING, key=len, reverse=True):
        if model.startswith(prefix):
            pi, po = PRICING[prefix]
            total = (u.get("input_tokens", 0) * pi + u.get("output_tokens", 0) * po
                     + (u.get("cache_read_input_tokens") or 0) * pi * 0.1
                     + (u.get("cache_creation_input_tokens") or 0) * pi * 1.25)
            return round(total / 1e6, 8)
    return None


def _strip_fences(text: str) -> str:
    t = text.strip()
    if not t.startswith("```"):
        return t
    t = t.split("\n", 1)[1] if "\n" in t else ""
    t = t.rstrip()
    return t[:-3].strip() if t.endswith("```") else t


def _status_error(resp: HttpResponse) -> ProviderError:
    status = resp.status
    message = f"anthropic API error {status}: {_error_text(resp)}"
    if status in (401, 403):
        return ProviderError(message, kind="auth", retryable=False, status=status)
    if status == 429 or status >= 500:
        return ProviderError(message, kind="transport", retryable=True, status=status)
    return ProviderError(message, kind="invalid_request", retryable=False, status=status)


def _error_text(resp: HttpResponse) -> str:
    """`<type>: <message>` from the API's `{"type": "error", "error": {...}}` body, else the raw body text."""
    try:
        err = resp.json()["error"]
        return f"{err['type']}: {err['message']}"
    except (ValueError, KeyError, TypeError):
        return resp.text()[:500]
