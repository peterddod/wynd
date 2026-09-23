"""The `anthropic` ModelProvider with a fake HttpClient: request goldens per model family, structured output (strict
and prompt fallback), response mapping, error kinds and pricing (PLAN §3.15; `$DRAFTS/03 §8`, §16).

The output-schema envelope (`agentic.schema`) belongs to RT-LOOP; here it is a double, so these tests pin what the
provider does with the wrapped schema, not the envelope itself.
"""

import json as jsonlib
from dataclasses import dataclass

import pytest

from wynd.runtime.http import HttpError
from wynd.runtime.providers import anthropic
from wynd.runtime.providers.anthropic import AnthropicProvider, cost
from wynd.runtime.providers.types import GenerateRequest, ProviderError, ToolCall, ToolSchema
from wynd.runtime.usage import Usage

KEY = "sk-ant-test"
SCHEMA = {"type": "object", "properties": {"exit": {"const": "done"}, "total": {"type": "number"}}}
USER = {"role": "user", "content": [{"type": "text", "text": "# Input\n{}"}]}
TOOL = ToolSchema(name="lookup_rate", description="Look up a rate.",
                  input_schema={"type": "object", "properties": {"currency": {"type": "string"}}})


def wrap_double(schema: dict) -> dict:
    return {"type": "object", "properties": {"output": schema}, "required": ["output"], "additionalProperties": False}


def unwrap_double(value):
    return value["output"] if isinstance(value, dict) and set(value) == {"output"} else value


@pytest.fixture(autouse=True)
def envelope(monkeypatch):
    monkeypatch.setattr(anthropic, "wrap_output_schema", wrap_double)
    monkeypatch.setattr(anthropic, "unwrap_output", unwrap_double)
    monkeypatch.setattr(anthropic, "strict_compatible", lambda wrapped: True)


@dataclass
class FakeResponse:
    status: int
    body: bytes

    def json(self):
        return jsonlib.loads(self.body)

    def text(self) -> str:
        return self.body.decode()


class FakeHttp:
    """Records every post; answers with the next item: a dict (200 JSON body), a FakeResponse, or an exception."""

    def __init__(self, *items):
        self.items = list(items)
        self.calls: list[dict] = []

    def post(self, url, *, json=None, headers=None, timeout=None):
        self.calls.append({"url": url, "json": json, "headers": headers, "timeout": timeout})
        item = self.items.pop(0)
        if isinstance(item, BaseException):
            raise item
        if isinstance(item, dict):
            return FakeResponse(200, jsonlib.dumps(item).encode())
        return item


def api_body(content: list[dict], *, stop: str = "end_turn", model: str = "claude-sonnet-5-20260801", **usage) -> dict:
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": model, "content": content,
            "stop_reason": stop, "usage": {"input_tokens": 100, "output_tokens": 20, **usage}}


def gen(model: str = "claude-sonnet-5", thinking: str = "low", *, schema: dict | None = SCHEMA,
        tools: list[ToolSchema] | None = None, system: str = "You are one step.") -> GenerateRequest:
    return GenerateRequest(model_id=model, thinking=thinking, system=system, messages=[USER], tools=tools or [],
                           output_schema=schema)


def provider(http: FakeHttp, **env) -> AnthropicProvider:
    return AnthropicProvider(tiers=AnthropicProvider.default_tiers, http=http, env={"ANTHROPIC_API_KEY": KEY, **env})


def sent(req: GenerateRequest) -> dict:
    http = FakeHttp(api_body([{"type": "text", "text": '{"output": {"exit": "done"}}'}]))
    provider(http).generate(req)
    return http.calls[0]["json"]


# --- request mapping --------------------------------------------------------------------------------------------------


def test_request_golden_sonnet_with_schema_and_tools():
    assert sent(gen("claude-sonnet-5", "medium", tools=[TOOL])) == {
        "model": "claude-sonnet-5",
        "max_tokens": 16000,
        "system": "You are one step.",
        "messages": [USER],
        "tools": [{"name": "lookup_rate", "description": "Look up a rate.",
                   "input_schema": {"type": "object", "properties": {"currency": {"type": "string"}}}}],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": "medium", "format": {"type": "json_schema", "schema": wrap_double(SCHEMA)}},
    }


@pytest.mark.parametrize(
    ("thinking", "expected_thinking", "expected_config"),
    [
        ("none", None, {"effort": "low"}),
        ("low", {"type": "adaptive"}, {"effort": "low"}),
        ("medium", {"type": "adaptive"}, {"effort": "medium"}),
        ("high", {"type": "adaptive"}, {"effort": "high"}),
    ],
)
@pytest.mark.parametrize("model", ["claude-sonnet-5", "claude-opus-5", "claude-opus-5-5", "claude-sonnet-4-6"])
def test_adaptive_models_get_effort_and_never_a_budget(model, thinking, expected_thinking, expected_config):
    body = sent(gen(model, thinking, schema=None))
    assert body.get("thinking") == expected_thinking
    assert body["output_config"] == expected_config
    assert "tool_choice" not in body
    assert "budget_tokens" not in jsonlib.dumps(body)
    assert '"disabled"' not in jsonlib.dumps(body)


@pytest.mark.parametrize(
    ("thinking", "expected"),
    [
        ("none", None),
        ("low", None),
        ("medium", {"type": "enabled", "budget_tokens": 2048}),
        ("high", {"type": "enabled", "budget_tokens": 8192}),
    ],
)
def test_haiku_uses_budget_thinking_and_no_effort(thinking, expected):
    body = sent(gen("claude-haiku-4-5-20251001", thinking, schema=None))
    assert body.get("thinking") == expected
    assert "output_config" not in body
    assert body["max_tokens"] > 8192


def test_haiku_format_without_effort():
    body = sent(gen("claude-haiku-4-5", "low"))
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": wrap_double(SCHEMA)}}


def test_non_strict_schema_goes_into_the_system_prompt(monkeypatch):
    seen = []
    monkeypatch.setattr(anthropic, "strict_compatible", lambda wrapped: seen.append(wrapped) or False)
    body = sent(gen("claude-sonnet-5", "low"))
    assert seen == [wrap_double(SCHEMA)]
    assert body["output_config"] == {"effort": "low"}           # no format
    assert body["system"] == ("You are one step.\n\nWhen you are finished, reply with only a JSON object matching "
                              "this JSON Schema:\n" + jsonlib.dumps(wrap_double(SCHEMA), sort_keys=True))


def test_free_text_request_has_no_format_and_empty_system_is_omitted():
    body = sent(gen("claude-sonnet-5", "low", schema=None, system=""))
    assert "system" not in body and "tools" not in body
    assert body["output_config"] == {"effort": "low"}


def test_headers_url_and_timeout():
    http = FakeHttp(api_body([{"type": "text", "text": "{}"}]))
    provider(http).generate(gen())
    call = http.calls[0]
    assert call["url"] == "https://api.anthropic.com/v1/messages"
    assert call["headers"] == {"x-api-key": KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    assert call["timeout"] == 600.0


def test_base_url_override():
    http = FakeHttp(api_body([{"type": "text", "text": "{}"}]))
    provider(http, ANTHROPIC_BASE_URL="http://127.0.0.1:9999/").generate(gen())
    assert http.calls[0]["url"] == "http://127.0.0.1:9999/v1/messages"


def test_env_defaults_to_os_environ(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-from-environ")
    http = FakeHttp(api_body([{"type": "text", "text": "{}"}]))
    AnthropicProvider(tiers={}, http=http).generate(gen())
    assert http.calls[0]["headers"]["x-api-key"] == "sk-from-environ"


def test_missing_api_key_is_auth_without_a_request(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    http = FakeHttp()
    with pytest.raises(ProviderError) as err:
        AnthropicProvider(tiers={}, http=http).generate(gen())
    assert (err.value.kind, err.value.retryable) == ("auth", False)
    assert "set ANTHROPIC_API_KEY" in str(err.value)
    assert http.calls == []


# --- response mapping -------------------------------------------------------------------------------------------------


def test_structured_output_from_json_text():
    http = FakeHttp(api_body([{"type": "text", "text": '{"output": {"exit": "done", "total": 12.5}}'}]))
    resp = provider(http).generate(gen())
    assert resp.structured_output == {"exit": "done", "total": 12.5}
    assert resp.stop == "end"
    assert resp.tool_calls == []
    assert resp.text == '{"output": {"exit": "done", "total": 12.5}}'
    assert resp.model_id == "claude-sonnet-5-20260801"
    assert resp.cost_basis == "api"
    assert resp.raw == api_body([{"type": "text", "text": '{"output": {"exit": "done", "total": 12.5}}'}])


def test_fenced_json_is_parsed():
    text = '```json\n{"output": {"exit": "done"}}\n```'
    http = FakeHttp(api_body([{"type": "text", "text": text}]))
    assert provider(http).generate(gen()).structured_output == {"exit": "done"}


def test_unparseable_text_gives_no_structured_output():
    http = FakeHttp(api_body([{"type": "text", "text": "I could not find a total."}]))
    resp = provider(http).generate(gen())
    assert resp.structured_output is None
    assert resp.text == "I could not find a total."


def test_free_text_request_never_parses_json():
    http = FakeHttp(api_body([{"type": "text", "text": '{"output": 1}'}]))
    assert provider(http).generate(gen(schema=None)).structured_output is None


def test_tool_use_response():
    content = [{"type": "text", "text": "Let me look that up."},
               {"type": "tool_use", "id": "toolu_1", "name": "lookup_rate", "input": {"currency": "USD"}}]
    http = FakeHttp(api_body(content, stop="tool_use"))
    resp = provider(http).generate(gen(tools=[TOOL]))
    assert resp.stop == "tool_calls"
    assert resp.tool_calls == [ToolCall("toolu_1", "lookup_rate", {"currency": "USD"})]
    assert resp.structured_output is None
    assert resp.message == {"role": "assistant", "content": content}


def test_thinking_blocks_are_kept_verbatim_in_the_message():
    content = [{"type": "thinking", "thinking": "Adding up...", "signature": "sig-abc=="},
               {"type": "redacted_thinking", "data": "opaque"},
               {"type": "text", "text": '{"output": {"exit": "done"}}'}]
    resp = provider(FakeHttp(api_body(content))).generate(gen())
    assert resp.message == {"role": "assistant", "content": content}
    assert resp.text == '{"output": {"exit": "done"}}'
    assert resp.structured_output == {"exit": "done"}


@pytest.mark.parametrize(("stop_reason", "stop"), [("max_tokens", "max_tokens"), ("refusal", "refusal"),
                                                   ("stop_sequence", "end"), ("pause_turn", "end")])
def test_stop_reasons(stop_reason, stop):
    http = FakeHttp(api_body([{"type": "text", "text": '{"output": {"exit": "done"'}], stop=stop_reason))
    resp = provider(http).generate(gen())
    assert resp.stop == stop
    if stop != "end":
        assert resp.structured_output is None


def test_usage_and_cost():
    body = api_body([{"type": "text", "text": "{}"}], model="claude-haiku-4-5-20251001",
                    cache_read_input_tokens=1000, cache_creation_input_tokens=400)
    body["usage"].update(input_tokens=2000, output_tokens=300)
    resp = provider(FakeHttp(body)).generate(gen("claude-haiku-4-5"))
    assert resp.usage.model_dump(exclude={"latency_ms"}) == Usage(
        input_tokens=2000, output_tokens=300, cache_read_tokens=1000, cache_write_tokens=400,
        cost_usd=round((2000 * 1.0 + 300 * 5.0 + 1000 * 0.1 + 400 * 1.25) / 1e6, 8), calls=1,
    ).model_dump(exclude={"latency_ms"})
    assert resp.usage.latency_ms >= 0.0


def test_usage_null_cache_fields():
    body = api_body([{"type": "text", "text": "{}"}], cache_read_input_tokens=None, cache_creation_input_tokens=None)
    resp = provider(FakeHttp(body)).generate(gen())
    assert (resp.usage.cache_read_tokens, resp.usage.cache_write_tokens) == (0, 0)


# --- errors -----------------------------------------------------------------------------------------------------------


def error_response(status: int, message: str = "boom", type_: str = "api_error") -> FakeResponse:
    body = {"type": "error", "error": {"type": type_, "message": message}}
    return FakeResponse(status, jsonlib.dumps(body).encode())


@pytest.mark.parametrize(
    ("status", "kind", "retryable"),
    [
        (401, "auth", False), (403, "auth", False),
        (400, "invalid_request", False), (404, "invalid_request", False), (413, "invalid_request", False),
        (422, "invalid_request", False),
        (429, "transport", True), (500, "transport", True), (502, "transport", True), (503, "transport", True),
        (504, "transport", True), (529, "transport", True),
    ],
)
def test_status_to_kind(status, kind, retryable):
    http = FakeHttp(error_response(status, "messages.0: bad block", "invalid_request_error"))
    with pytest.raises(ProviderError) as err:
        provider(http).generate(gen())
    assert (err.value.kind, err.value.retryable, err.value.status) == (kind, retryable, status)
    assert str(err.value) == f"anthropic API error {status}: invalid_request_error: messages.0: bad block"
    assert len(http.calls) == 1                    # no internal retries


def test_non_json_error_body_is_quoted():
    http = FakeHttp(FakeResponse(502, b"<html>Bad Gateway</html>"))
    with pytest.raises(ProviderError) as err:
        provider(http).generate(gen())
    assert str(err.value) == "anthropic API error 502: <html>Bad Gateway</html>"


def test_a_success_body_that_is_not_json_is_retryable_transport():
    http = FakeHttp(FakeResponse(200, b"<html>proxy page</html>"))
    with pytest.raises(ProviderError) as err:
        provider(http).generate(gen())
    assert (err.value.kind, err.value.retryable, err.value.status) == ("transport", True, 200)


def test_network_failure_is_retryable_transport():
    http = FakeHttp(HttpError("connection refused"))
    with pytest.raises(ProviderError) as err:
        provider(http).generate(gen())
    assert (err.value.kind, err.value.retryable, err.value.status) == ("transport", True, None)
    assert "connection refused" in str(err.value)


def test_http_error_carrying_a_response_maps_its_status():
    http = FakeHttp(HttpError("HTTP 401", response=error_response(401, "invalid x-api-key", "authentication_error")))
    with pytest.raises(ProviderError) as err:
        provider(http).generate(gen())
    assert err.value.kind == "auth"


# --- pricing and tiers ------------------------------------------------------------------------------------------------


def test_cost_known_unknown_and_longest_prefix():
    u = {"input_tokens": 1_000_000, "output_tokens": 1_000_000}
    assert cost("claude-opus-5-5-20260901", u) == 24.0          # claude-opus-5-5, not claude-opus-5
    assert cost("claude-opus-5-20260501", u) == 30.0
    assert cost("claude-sonnet-5", {"input_tokens": 10, "output_tokens": 0}) == 0.00002
    assert cost("gpt-9", u) is None
    resp = provider(FakeHttp(api_body([{"type": "text", "text": "{}"}], model="mystery-model"))).generate(gen())
    assert resp.usage.cost_usd is None


def test_tiers():
    p = AnthropicProvider(tiers={"cheap": "claude-haiku-4-5", "strong": "claude-opus-5-5"})
    assert p.tiers() == {"cheap": "claude-haiku-4-5", "strong": "claude-opus-5-5"}
    assert p.name == "anthropic" and p.kind == "model"
