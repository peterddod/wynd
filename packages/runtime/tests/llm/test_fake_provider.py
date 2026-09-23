"""The `fake` AgentProvider: script sources, subset matching, miss error, zero usage (PLAN §3.15, §15 item 63;
`$DRAFTS/03 §14.5`)."""

import json
import threading
from pathlib import Path

import pytest

from wynd.runtime.providers import load_provider
from wynd.runtime.providers.fake import FakeProvider
from wynd.runtime.providers.types import AgentRequest, AgentResponse, ProviderError
from wynd.runtime.usage import Usage

SCRIPT = {"responses": [
    {"match": {"input": {"invoice_text": "Dear customer, your order has shipped"}},
     "output": {"exit": "not_an_invoice"}},
    {"match": {"input": {"record": {"key": "INV-7"}}}, "output": {"exit": "done", "fixed": True}, "note": "nested"},
    {"match": {"input": {}}, "output": {"exit": "done", "invoice_number": "INV-1042", "total": 1200.5,
                                         "currency": "GBP", "due_date": "2026-10-01"}, "note": "scripted"},
]}


def request(input: dict, *, cancel: threading.Event | None = None) -> AgentRequest:
    return AgentRequest(model_id="fake", thinking="low", instruction="Extract the fields.", context={}, input=input,
                        output_schema={"type": "object"}, tools=[], mcp_servers=[], workspace=Path("/tmp/ws"),
                        cancel=cancel)


@pytest.fixture
def provider(monkeypatch) -> FakeProvider:
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", json.dumps(SCRIPT))
    return FakeProvider(tiers={"cheap": "fake"})


def test_first_matching_response_wins(provider: FakeProvider):
    resp = provider.run(request({"invoice_text": "Dear customer, your order has shipped"}))
    assert resp.structured_output == {"exit": "not_an_invoice"}
    assert resp.note == ""
    resp = provider.run(request({"invoice_text": "INVOICE INV-1042 ..."}))
    assert resp.structured_output["invoice_number"] == "INV-1042"
    assert resp.note == "scripted"


def test_match_is_a_subset_of_the_input_including_nested_objects(provider: FakeProvider):
    resp = provider.run(request({"record": {"key": "INV-7", "total": 3}, "errors": [], "invoice_text": "x"}))
    assert resp.structured_output == {"exit": "done", "fixed": True}
    assert resp.note == "nested"
    other = provider.run(request({"record": {"key": "INV-8"}}))       # nested value differs -> falls through
    assert other.note == "scripted"


def test_response_shape_and_zero_usage(provider: FakeProvider):
    resp = provider.run(request({"invoice_text": "anything"}))
    assert isinstance(resp, AgentResponse)
    assert resp.model_id == "fake"
    assert resp.usage == Usage(cost_usd=0.0, calls=1)
    assert (resp.usage.input_tokens, resp.usage.output_tokens, resp.usage.latency_ms) == (0, 0, 0.0)
    assert resp.transcript == [] and resp.session == {} and resp.tool_calls == 0
    assert provider.tiers() == {"cheap": "fake"}


def test_a_miss_is_an_invalid_request(monkeypatch):
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", json.dumps({"responses": [
        {"match": {"input": {"invoice_text": "only this"}}, "output": {"exit": "done"}}]}))
    with pytest.raises(ProviderError) as err:
        FakeProvider(tiers={}).run(request({"invoice_text": "something else"}))
    assert err.value.kind == "invalid_request"
    assert err.value.retryable is False
    assert "no scripted response" in str(err.value) and "something else" in str(err.value)


def test_inline_list_script_with_leading_whitespace(monkeypatch):
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", '  \n [{"output": {"exit": "done", "n": 1}}]')
    assert FakeProvider(tiers={}).run(request({"a": 1})).structured_output == {"exit": "done", "n": 1}


def test_script_file_path(monkeypatch, tmp_path: Path):
    path = tmp_path / "fake_provider.json"
    path.write_text(json.dumps(SCRIPT))
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", str(path))
    resp = FakeProvider(tiers={}).run(request({"invoice_text": "Dear customer, your order has shipped"}))
    assert resp.structured_output == {"exit": "not_an_invoice"}


def test_script_is_read_on_every_call(monkeypatch):
    fake = FakeProvider(tiers={})
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", '[{"output": {"exit": "first"}}]')
    assert fake.run(request({})).structured_output == {"exit": "first"}
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", '[{"output": {"exit": "second"}}]')
    assert fake.run(request({})).structured_output == {"exit": "second"}


@pytest.mark.parametrize(
    ("value", "fragment"),
    [
        (None, "WYND_FAKE_PROVIDER_SCRIPT is not set"),
        ("   ", "WYND_FAKE_PROVIDER_SCRIPT is not set"),
        ("{not json", "is not valid JSON"),
        ('{"responses": {"a": 1}}', "must be"),
        ("[1, 2]", "must be"),
        ("/no/such/fake_provider.json", "cannot read"),
    ],
)
def test_script_problems_are_unavailable(monkeypatch, value, fragment):
    if value is None:
        monkeypatch.delenv("WYND_FAKE_PROVIDER_SCRIPT", raising=False)
    else:
        monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", value)
    with pytest.raises(ProviderError) as err:
        FakeProvider(tiers={}).run(request({}))
    assert err.value.kind == "unavailable"
    assert err.value.retryable is False
    assert fragment in str(err.value)


def test_cancel_is_honoured(provider: FakeProvider):
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ProviderError) as err:
        provider.run(request({}, cancel=cancel))
    assert (err.value.kind, err.value.retryable, str(err.value)) == ("transport", False, "cancelled")


def test_loaded_through_the_registry(provider: FakeProvider):
    fake = load_provider("fake")
    assert isinstance(fake, FakeProvider)
    assert fake.run(request({"x": 1})).note == "scripted"
