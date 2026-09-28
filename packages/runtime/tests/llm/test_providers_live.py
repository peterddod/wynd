"""Live provider checks (`@live`, run with WYND_LIVE=1; small haiku calls, plus one tiny call per tier for the parity
check): the claude-code structured union, tool call + stateless continuation, MCP proxying through the in-process
server, the auth error, the tier parity rule (PLAN §3.15) and the anthropic Messages API (only with
ANTHROPIC_API_KEY). Auth is whatever the machine has (logged-in CLI, CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY).

`render_prompt` and the output-schema envelope belong to RT-LOOP (sub-wave 2b). While they are still stubs, these
checks run with stand-ins written from `$DRAFTS/03 §6.3–§6.4` (PLAN §0.3); once RT-LOOP lands, the real ones run.
"""

import inspect
import json
import os
import re
import threading
from dataclasses import replace
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Annotated, Literal

import claude_agent_sdk as sdk
import pytest
from pydantic import BaseModel, Field, TypeAdapter

import wynd.runtime.agentic.prompt as agentic_prompt
import wynd.runtime.agentic.schema as agentic_schema
from wynd.runtime.providers import anthropic as anthropic_provider
from wynd.runtime.providers import claude_code
from wynd.runtime.providers.anthropic import AnthropicProvider
from wynd.runtime.providers.claude_code import ClaudeCodeProvider
from wynd.runtime.providers.types import (
    AgentRequest,
    Continuation,
    GenerateRequest,
    ProviderError,
    ToolHandle,
    ToolResult,
    ToolSchema,
)

pytestmark = pytest.mark.live

INVOICE = "INVOICE INV-1042\nSupplier: Acme Ltd\nTotal due: GBP 1,200.50\nDue date: 1 October 2026\n"
RATE_SCHEMA = {"type": "object", "properties": {"currency": {"type": "string"}, "rate": {"type": "number"}},
               "required": ["currency", "rate"]}
MCP_TOKEN = "live-test-token-7f3a"
ISSUE = {"number": 42, "title": "Login page broken", "state": "open"}
ISSUE_SCHEMA = {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}


class Done(BaseModel):
    """The text is an invoice; fields extracted."""
    exit: Literal["done"] = "done"
    invoice_number: str
    total: float
    currency: str
    due_date: date


class NotAnInvoice(BaseModel):
    """The text is not an invoice."""
    exit: Literal["not_an_invoice"] = "not_an_invoice"


OUTPUT = TypeAdapter(Annotated[Done | NotAnInvoice, Field(discriminator="exit")])


DROP = {"discriminator", "title", "default", "examples", "minimum", "maximum", "exclusiveMinimum",
        "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern", "minItems", "maxItems",
        "uniqueItems", "minProperties", "maxProperties"}
FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}


def _json(value) -> str:
    return json.dumps(value, sort_keys=True, indent=2, ensure_ascii=False, default=str)


def render_prompt_standin(instruction, context, input, *, raw_prompt=None):
    if raw_prompt is not None:
        return instruction, raw_prompt
    parts = ["# Context\n```json\n" + _json(context) + "\n```"] if context else []
    parts.append("# Input\n```json\n" + _json(input) + "\n```")
    return agentic_prompt.SYSTEM_PREAMBLE + inspect.cleandoc(instruction), "\n\n".join(parts)


def _clean(node):
    match node:
        case dict():
            out = {}
            for key, value in node.items():
                if key in DROP or (key == "format" and value not in FORMATS):
                    continue
                if key == "properties":
                    out[key] = {name: _clean(sub) for name, sub in value.items()}
                else:
                    out["anyOf" if key == "oneOf" else key] = _clean(value)
            if "properties" in out:
                out["additionalProperties"] = False
                out["required"] = list(out["properties"])
            return out
        case list():
            return [_clean(item) for item in node]
        case _:
            return node


def wrap_output_schema_standin(schema: dict) -> dict:
    body = {key: value for key, value in schema.items() if key != "$defs"}
    wrapped = {"type": "object", "properties": {"output": _clean(body)}, "required": ["output"],
               "additionalProperties": False}
    if "$defs" in schema:
        wrapped["$defs"] = {name: _clean(sub) for name, sub in schema["$defs"].items()}
    return wrapped


def unwrap_output_standin(value):
    return value["output"] if isinstance(value, dict) and set(value) == {"output"} else value


def strict_compatible_standin(node) -> bool:
    match node:
        case dict():
            if node.get("type") == "object" and "properties" not in node:
                return False
            return all(strict_compatible_standin(sub) for sub in node.values())
        case list():
            return all(strict_compatible_standin(sub) for sub in node)
        case _:
            return True


def _is_stub(fn, *args) -> bool:
    try:
        fn(*args)
    except NotImplementedError:
        return True
    return False


@pytest.fixture(autouse=True)
def rt_loop_standins(monkeypatch):
    standins = {
        "render_prompt": (_is_stub(agentic_prompt.render_prompt, "", {}, {}), render_prompt_standin),
        "wrap_output_schema": (_is_stub(agentic_schema.wrap_output_schema, {"type": "string"}),
                               wrap_output_schema_standin),
        "unwrap_output": (_is_stub(agentic_schema.unwrap_output, {"output": 1}), unwrap_output_standin),
        "strict_compatible": (_is_stub(agentic_schema.strict_compatible, {"type": "object"}),
                              strict_compatible_standin),
    }
    for name, (stub, standin) in standins.items():
        for module in (claude_code, anthropic_provider):
            if stub and hasattr(module, name):
                monkeypatch.setattr(module, name, standin)


def provider(query_fn=None) -> ClaudeCodeProvider:
    return ClaudeCodeProvider(tiers=ClaudeCodeProvider.default_tiers, query_fn=query_fn)


def request(workspace: Path, **fields) -> AgentRequest:
    base = dict(model_id="haiku", thinking="low", instruction="", context={}, input={}, output_schema=None,
                tools=[], mcp_servers=[], workspace=workspace)
    return AgentRequest(**{**base, **fields})


def project_dir(workspace: Path) -> Path:
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    return projects / sdk.project_key_for_directory(str(workspace))


def test_claude_code_live_structured_union(tmp_path: Path):
    req = request(tmp_path, instruction=("Given the text of a supplier invoice, extract the invoice number, total, "
                                         "currency and due date. If the text is not a supplier invoice, take the "
                                         "not_an_invoice exit."),
                  context={"process.goal": "Pay supplier invoices on time"}, input={"invoice_text": INVOICE},
                  output_schema=OUTPUT.json_schema())
    resp = provider().run(req)
    out = OUTPUT.validate_python(resp.structured_output)
    assert isinstance(out, Done)
    assert (out.invoice_number, out.total, out.currency, out.due_date) == ("INV-1042", 1200.5, "GBP", date(2026, 10, 1))
    assert resp.model_id.startswith("claude-haiku")
    assert resp.usage.calls == 1 and resp.usage.output_tokens > 0
    assert resp.startup_ms is not None

    other = provider().run(replace(req, input={"invoice_text": "Dear customer, your order has shipped."}))
    assert isinstance(OUTPUT.validate_python(other.structured_output), NotAnInvoice)
    assert not project_dir(tmp_path).exists()


def test_claude_code_live_tool_and_continuation(tmp_path: Path):
    calls = []

    def lookup_rate(args: dict) -> ToolResult:
        calls.append(args)
        return ToolResult(json.dumps({"currency": args["currency"], "rate": 1.25}))

    tool = ToolHandle(name="lookup_rate", description="Look up the exchange rate to GBP for a currency code.",
                      input_schema={"type": "object", "properties": {"currency": {"type": "string"}},
                                    "required": ["currency"]},
                      invoke=lookup_rate)
    req = request(tmp_path, instruction=("Use the lookup_rate tool to find the exchange rate for the currency in the "
                                         "input, then report the currency code and the rate."),
                  input={"currency": "USD"}, output_schema=RATE_SCHEMA, tools=[tool])
    first = provider().run(req)
    assert calls == [{"currency": "USD"}]
    assert first.tool_calls == 1
    assert first.structured_output["rate"] == 1.25
    assert [(e["name"], e["input"]) for e in first.transcript if e["type"] == "tool_use"] == [
        ("lookup_rate", {"currency": "USD"})]

    retry = Continuation(session=first.session, message=(
        "Your structured output did not validate against the step's Output schema:\n"
        "- output.currency: must be written in lower case (for example \"usd\")\n"
        "Reply again with corrected structured output. Do not call tools again."))
    second = provider().run(replace(req, continuation=retry))
    assert calls == [{"currency": "USD"}]                    # no tool re-call: the conversation continued
    assert second.tool_calls == 0
    assert second.session["session_id"] == first.session["session_id"]
    assert second.structured_output == {"currency": "usd", "rate": 1.25}
    assert second.usage.calls == 1 and second.usage.output_tokens > 0
    assert not project_dir(tmp_path).exists()


class _McpHandler(BaseHTTPRequestHandler):
    """A minimal MCP server over streamable HTTP (JSON bodies) that requires a bearer token."""

    def log_message(self, *args):
        pass

    def _reply(self, status: int, body: dict | None = None) -> None:
        data = json.dumps(body).encode() if body is not None else b""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_POST(self):
        if self.headers.get("Authorization") != f"Bearer {MCP_TOKEN}":
            return self._reply(401)
        msg = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if "id" not in msg:
            return self._reply(202)
        match msg["method"]:
            case "initialize":
                result = {"protocolVersion": msg["params"]["protocolVersion"], "capabilities": {"tools": {}},
                          "serverInfo": {"name": "fake-github", "version": "0"}}
            case "tools/list":
                result = {"tools": [{"name": "get_issue", "description": "Get a GitHub issue by number.",
                                     "inputSchema": ISSUE_SCHEMA}]}
            case "tools/call":
                self.server.tool_calls.append(msg["params"])
                result = {"content": [{"type": "text", "text": json.dumps(ISSUE)}], "isError": False}
            case _:
                return self._reply(200, {"jsonrpc": "2.0", "id": msg["id"],
                                         "error": {"code": -32601, "message": "method not found"}})
        self._reply(200, {"jsonrpc": "2.0", "id": msg["id"], "result": result})

    def do_DELETE(self):
        self._reply(200)


@pytest.fixture
def mcp_url():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _McpHandler)
    server.tool_calls = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}/mcp", server
    server.shutdown()
    server.server_close()


def test_claude_code_live_mcp_proxy(tmp_path: Path, mcp_url):
    from wynd.runtime.mcp.client import HttpTransport, McpClient

    url, server = mcp_url
    client = McpClient(HttpTransport(url, {"Authorization": f"Bearer {MCP_TOKEN}"}, 10.0))
    client.initialize()

    def get_issue(args: dict) -> ToolResult:
        r = client.call_tool("get_issue", args)
        text = "".join(c.get("text", "") for c in r["content"] if c.get("type") == "text")
        return ToolResult(text, is_error=bool(r.get("isError")))

    seen = {}

    def capture(*, prompt, options):
        seen["options"] = options
        return sdk.query(prompt=prompt, options=options)

    tool = ToolHandle(name="github__get_issue", description="Get a GitHub issue by number.",
                      input_schema=ISSUE_SCHEMA, invoke=get_issue)
    schema = {"type": "object", "properties": {"title": {"type": "string"}, "state": {"type": "string"}},
              "required": ["title", "state"]}
    try:
        resp = provider(capture).run(request(
            tmp_path, instruction="Fetch the GitHub issue whose number is in the input and report its title and state.",
            input={"issue": 42}, output_schema=schema, tools=[tool]))
    finally:
        client.close()
    assert server.tool_calls == [{"name": "get_issue", "arguments": {"number": 42}}]
    assert resp.structured_output == {"title": "Login page broken", "state": "open"}
    options = seen["options"]
    assert set(options.mcp_servers) == {"wynd"} and options.mcp_servers["wynd"]["type"] == "sdk"
    assert options.allowed_tools == ["mcp__wynd__github__get_issue"]
    assert MCP_TOKEN not in repr(options)                     # auth stays in this process, never on the CLI's argv
    assert not project_dir(tmp_path).exists()


def test_claude_code_live_auth_error(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "invalid")
    with pytest.raises(ProviderError) as err:
        provider().run(request(tmp_path, instruction="Reply with one word.", prompt="Say ok.", max_turns=1))
    assert (err.value.kind, err.value.retryable) == ("auth", False)


@pytest.mark.parametrize("tier", ["cheap", "standard", "strong"])
def test_tier_parity_live(tmp_path: Path, tier: str):
    """SPEC §3.9 comparable strength: the model Claude Code runs for the claude-code alias of a tier is the same family
    and version as `AnthropicProvider.default_tiers[tier]`. On failure, update the anthropic defaults."""
    results = []

    async def capture(*, prompt, options):
        async for m in sdk.query(prompt=prompt, options=options):
            if isinstance(m, sdk.ResultMessage):
                results.append(m)
            yield m

    alias = ClaudeCodeProvider.default_tiers[tier]
    provider(capture).run(request(tmp_path, model_id=alias, thinking="none", instruction="Reply with one word.",
                                  prompt="Say ok.", max_turns=1))
    served = {re.sub(r"-\d{8}$", "", model) for model in results[-1].model_usage or {}}
    expected = AnthropicProvider.default_tiers[tier]
    assert expected in served, (f"claude-code {tier} ({alias}) ran {sorted(served)}, "
                                f"but AnthropicProvider.default_tiers[{tier!r}] is {expected!r}")


@pytest.mark.skipif(not os.environ.get("ANTHROPIC_API_KEY"), reason="needs ANTHROPIC_API_KEY")
def test_anthropic_live_tools_and_format():
    p = AnthropicProvider(tiers=AnthropicProvider.default_tiers)
    tool = ToolSchema(name="lookup_rate", description="Look up the exchange rate to GBP for a currency code.",
                      input_schema={"type": "object", "properties": {"currency": {"type": "string"}},
                                    "required": ["currency"]})
    user = {"role": "user", "content": [{"type": "text", "text": "Find the GBP exchange rate for USD with the tool, "
                                                                 "then report the currency code and the rate."}]}
    req = GenerateRequest(model_id=AnthropicProvider.default_tiers["cheap"], thinking="low",
                          system="You are one step in an automated process.", messages=[user], tools=[tool],
                          output_schema=RATE_SCHEMA)
    first = p.generate(req)
    assert first.stop == "tool_calls"
    [call] = first.tool_calls
    assert (call.name, call.arguments) == ("lookup_rate", {"currency": "USD"})
    result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call.id,
                                           "content": json.dumps({"currency": "USD", "rate": 1.25})}]}
    second = p.generate(replace(req, messages=[user, first.message, result]))
    assert second.stop == "end"
    assert second.structured_output == {"currency": "USD", "rate": 1.25}
    assert second.usage.cost_usd is not None and second.cost_basis == "api"
