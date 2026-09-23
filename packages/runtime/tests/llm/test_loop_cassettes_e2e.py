"""The loop, real tools and real cassettes end to end through `run_step` (SPEC §7; PLAN §3.9, §3.16, §5.5,
§5.7 RT-LOOP; `$DRAFTS/03 §16`): record in a temp workspace -> promote -> replay in another workspace -> an edited
input misses with the exact text; a missing tool-call recording is always `cassette_miss`; local tool side effects
happen in replay for both provider kinds; MCP is never contacted in replay."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Literal

import pytest
from pydantic import BaseModel

from wynd.runtime.cassettes import NO_RECORDING, CassetteMissError, promote
from wynd.runtime.cassettes.store import read_entry
from wynd.runtime.handle import StepCache
from wynd.runtime.mcp import McpServer, snapshot
from wynd.runtime.mcp.entry import McpServerEntry
from wynd.runtime.mcp.fake_server import serve_http
from wynd.runtime.mcp.snapshot import McpToolSpec
from wynd.runtime.middleware import run_chain
from wynd.runtime.policy import CassetteConfig, ExecPolicy
from wynd.runtime.providers import register_for_tests
from wynd.runtime.providers.scripted import ScriptedAgentProvider, ScriptedModelProvider
from wynd.runtime.providers.types import AgentResponse, GenerateResponse, ToolCall
from wynd.runtime.step import AgenticStep
from wynd.runtime.storage import registry_from_env
from wynd.runtime.testing import expect, run_step
from wynd.runtime.tools import tool, workspace_write
from wynd.runtime.usage import Usage
from wynd.runtime.worker.protocol import RunStepParams
from wynd.spec.lockfiles import RetryPolicy

LOOKUPS: list[str] = []


class Invoice(BaseModel):
    invoice_text: str


class Extracted(BaseModel):
    exit: Literal["done"] = "done"
    invoice_number: str
    total: float


class NotAnInvoice(BaseModel):
    exit: Literal["not_an_invoice"] = "not_an_invoice"


class ExtractInvoiceFields(AgenticStep):
    """Given the text of a supplier invoice, extract the invoice number and total.
    If the text is not a supplier invoice, take the not_an_invoice exit."""

    Input = Invoice
    Output = Extracted | NotAnInvoice

    def run(self, input: Invoice) -> Extracted | NotAnInvoice: ...


class Company(BaseModel):
    number: str


class Status(BaseModel):
    exit: Literal["done"] = "done"
    status: str


class CheckCompany(AgenticStep):
    """Look the company up in the public registry and report its status."""

    Input = Company
    Output = Status

    @tool(effects=["network"], idempotent=True)
    def registry_lookup(self, number: str) -> str:
        """Look up a company in the public registry."""
        LOOKUPS.append(number)
        if number == "down":
            raise ConnectionError("registry unavailable")
        return json.dumps({"number": number, "status": "active"})

    def run(self, input: Company) -> Status: ...


class Written(BaseModel):
    exit: Literal["done"] = "done"
    path: str


class WriteNote(AgenticStep):
    """Write a one-line summary of the text to notes/summary.txt in the workspace and report its path."""

    Input = Invoice
    Output = Written
    tools = [workspace_write]

    def run(self, input: Invoice) -> Written: ...


class Triage(AgenticStep):
    """Read the GitHub issue and report its state."""

    Input = Company
    Output = Status
    mcp = [McpServer("github", allow=["get_issue"])]

    def run(self, input: Company) -> Status: ...


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    LOOKUPS.clear()
    monkeypatch.setattr("wynd.runtime.agentic.loop.time.sleep", lambda s: None)
    monkeypatch.setattr("wynd.runtime.tools.toolset.time.sleep", lambda s: None)


@pytest.fixture
def dirs(tmp_path, monkeypatch) -> dict[str, Path]:
    paths = {name: tmp_path / name for name in ("staging", "cassettes", "record_ws", "replay_ws")}
    monkeypatch.setenv("WYND_CASSETTE_RECORD_DIR", str(paths["staging"]))
    return paths


@pytest.fixture
def use_provider(monkeypatch):
    """`use_provider(p)`: the process default provider becomes `p`, registered as "scripted"."""
    undos = []

    def use(provider: Any) -> None:
        undos.append(register_for_tests("scripted", provider))
        monkeypatch.setenv("WYND_DEFAULT_PROVIDER", "scripted")

    yield use
    for undo in reversed(undos):
        undo()


def usage() -> Usage:
    return Usage(input_tokens=50, output_tokens=5, cost_usd=0.0005, latency_ms=20.0, calls=1)


def generate(*, output: Any = None, tool_calls: tuple[ToolCall, ...] = ()) -> GenerateResponse:
    content = [{"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.arguments} for tc in tool_calls]
    return GenerateResponse(message={"role": "assistant", "content": content}, text="", tool_calls=list(tool_calls),
                            structured_output=output, stop="tool_calls" if tool_calls else "end", usage=usage(),
                            model_id="scripted-model")


def entries(directory: Path) -> list[Any]:
    return sorted((read_entry(p) for p in directory.glob("*.json")), key=lambda e: e.kind)


def events_file(monkeypatch, tmp_path: Path) -> Path:
    path = tmp_path / "events.jsonl"
    monkeypatch.setenv("WYND_EVENTS_FILE", str(path))
    return path


def read_events(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# --- record -> promote -> replay -> miss, with the fake provider ----------------------------------------------------

FAKE_SCRIPT = {"responses": [
    {"match": {"input": {"invoice_text": "Dear customer, your order has shipped"}},
     "output": {"exit": "not_an_invoice"}},
    {"match": {"input": {}}, "output": {"exit": "done", "invoice_number": "INV-1042", "total": 1200.5},
     "note": "scripted"},
]}
INVOICE = {"invoice_text": "INVOICE INV-1042\nTotal due: 1200.50 GBP"}


def test_record_promote_replay_then_an_edited_input_misses(dirs, monkeypatch):
    monkeypatch.setenv("WYND_DEFAULT_PROVIDER", "fake")
    monkeypatch.setenv("WYND_FAKE_PROVIDER_SCRIPT", json.dumps(FAKE_SCRIPT))

    recorded = run_step(ExtractInvoiceFields, INVOICE, mode="record", cassettes=dirs["cassettes"],
                        workspace=dirs["record_ws"])
    expect(recorded, exit="done", outputs={"invoice_number": "INV-1042", "total": 1200.5})
    [entry] = entries(dirs["staging"])
    assert (entry.kind, entry.provider, entry.tier, entry.model_id) == ("agent", "fake", "cheap", "fake")
    assert entry.request["workspace"] == "<workspace>"
    assert not dirs["cassettes"].exists()                       # nothing reaches the source before promotion

    assert promote(dirs["staging"], dirs["cassettes"]) == [dirs["cassettes"] / f"{entry.key[:32]}.json"]

    monkeypatch.delenv("WYND_FAKE_PROVIDER_SCRIPT")             # a live call would now fail: replay never makes one
    replayed = run_step(ExtractInvoiceFields, INVOICE, cassettes=dirs["cassettes"], workspace=dirs["replay_ws"])
    expect(replayed, exit="done", outputs={"invoice_number": "INV-1042", "total": 1200.5})
    assert replayed.summary.note == "scripted"
    [call] = [e for e in replayed.events if e["type"] == "model.call"]
    assert (call["cassette"], call["outcome"], call["request_hash"]) == ("replay", "valid", entry.key[:32])

    with pytest.raises(CassetteMissError) as err:
        run_step(ExtractInvoiceFields, {"invoice_text": "INVOICE INV-1043\nTotal due: 99.00 GBP"},
                 cassettes=dirs["cassettes"], workspace=dirs["replay_ws"])
    text = str(err.value)
    match = re.fullmatch(re.escape(NO_RECORDING) + r"\n  key: ([0-9a-f]{64})\n  cassettes: (.+)\n  request: (.+)",
                         text)
    assert match, text
    key, cassettes, dump = match.groups()
    assert cassettes == str(dirs["cassettes"])
    assert Path(dump) == dirs["replay_ws"] / ".wynd" / "cassettes" / "misses" / f"{key[:32]}.request.json"
    assert json.loads(Path(dump).read_text())["input"] == {"invoice_text": "INVOICE INV-1043\nTotal due: 99.00 GBP"}


# --- a missing tool-call recording is cassette_miss, never a tool failure ---------------------------------------------

def record_lookup(dirs, use_provider, number: str, final: dict[str, Any] | None) -> Any:
    script = [generate(tool_calls=(ToolCall("t1", "registry_lookup", {"number": number}),))]
    if final is not None:
        script.append(generate(output=final))
    use_provider(ScriptedModelProvider(script))
    result = run_step(CheckCompany, {"number": number}, mode="record", cassettes=dirs["cassettes"],
                      workspace=dirs["record_ws"])
    promote(dirs["staging"], dirs["cassettes"])
    use_provider(ScriptedModelProvider([]))         # replay must never reach it
    return result


def drop_tool_recording(directory: Path) -> None:
    [tool_entry] = [p for p in directory.glob("*.json") if read_entry(p).kind == "tool"]
    tool_entry.unlink()


def test_network_tool_calls_are_recorded_and_replayed(dirs, use_provider):
    record_lookup(dirs, use_provider, "123", {"exit": "done", "status": "active"})
    assert [e.kind for e in entries(dirs["cassettes"])] == ["generate", "generate", "tool"]
    assert LOOKUPS == ["123"]

    replayed = run_step(CheckCompany, {"number": "123"}, cassettes=dirs["cassettes"], workspace=dirs["replay_ws"])
    expect(replayed, exit="done", outputs={"status": "active"})
    assert LOOKUPS == ["123"]                                   # the network tool did not run again
    [tool_call] = [e for e in replayed.events if e["type"] == "tool.call"]
    assert (tool_call["replayed"], tool_call["ok"]) == (True, True)


def test_a_missing_tool_recording_is_a_cassette_miss_not_a_tool_error(dirs, use_provider, monkeypatch,
                                                                      tmp_path):
    record_lookup(dirs, use_provider, "123", {"exit": "done", "status": "active"})
    drop_tool_recording(dirs["cassettes"])
    log = events_file(monkeypatch, tmp_path)

    with pytest.raises(CassetteMissError) as err:              # run_step re-raises only for cause cassette_miss
        run_step(CheckCompany, {"number": "123"}, cassettes=dirs["cassettes"], workspace=dirs["replay_ws"])
    dump = Path(str(err.value).rsplit("\n  request: ", 1)[1])
    assert json.loads(dump.read_text()) == {"v": 1, "kind": "tool", "tool": "registry_lookup",
                                            "arguments": {"number": "123"}}
    assert LOOKUPS == ["123"]                                   # never executed live in replay
    events = read_events(log)
    assert [(e["type"], e.get("outcome")) for e in events] == [("model.call", "tool_calls")]   # no second model call


def test_the_miss_reaches_the_step_error_as_cause_cassette_miss(dirs, use_provider):
    record_lookup(dirs, use_provider, "123", {"exit": "done", "status": "active"})
    drop_tool_recording(dirs["cassettes"])
    params = RunStepParams(
        run_id="run-step", step_path="check", step_run=1, step_id="p#check", inputs={"number": "123"},
        workspace=str(dirs["replay_ws"]),
        policy=ExecPolicy(kind="agentic", retries=RetryPolicy(run=2, validation=2, tool=1), provider="scripted",
                          model_id="scripted", tier="cheap", thinking="low"),
        cassette=CassetteConfig(mode="replay", dir=str(dirs["cassettes"]),
                                literals={str(dirs["replay_ws"]): "<workspace>"}),
    )
    events: list[dict[str, Any]] = []
    result = run_chain(CheckCompany, params, emit=events.append, cache=StepCache())
    assert result.exit == "error"
    assert result.outputs["cause"] == "cassette_miss"
    assert result.outputs["message"].startswith(NO_RECORDING)
    assert result.usage is not None and result.usage.calls == 1
    assert not any(e["type"] == "tool.call" for e in events)


def test_an_error_example_whose_tool_recording_is_missing_still_fails(dirs, use_provider):
    recorded = record_lookup(dirs, use_provider, "down", None)       # the tool raised: recorded as a tool failure
    expect(recorded, exit="error")
    assert recorded.outputs["cause"] == "tool"

    def example() -> None:                                          # a generated test: `exit: error` example
        expect(run_step(CheckCompany, {"number": "down"}, cassettes=dirs["cassettes"],
                        workspace=dirs["replay_ws"]), exit="error")

    example()                                                       # replays the recorded tool failure: passes
    drop_tool_recording(dirs["cassettes"])
    with pytest.raises(CassetteMissError):
        example()                                                   # the miss is never mistaken for the error exit
    assert LOOKUPS == ["down", "down"]      # the record run's two tries (idempotent, transient); none in replay


# --- local tool side effects happen in replay for both provider kinds ------------------------------------------------

NOTE_ARGS = {"path": "notes/summary.txt", "content": "INV-1042: 1200.50 GBP"}


def model_script() -> list[Any]:
    return [generate(tool_calls=(ToolCall("w1", "workspace_write", NOTE_ARGS),)),
            generate(output={"exit": "done", "path": "notes/summary.txt"})]


def agent_script() -> list[Any]:
    transcript = [{"type": "tool_use", "id": "w1", "name": "workspace_write", "input": NOTE_ARGS},
                  {"type": "tool_result", "id": "w1", "is_error": False, "content": "notes/summary.txt"}]
    return [AgentResponse(structured_output={"exit": "done", "path": "notes/summary.txt"}, usage=usage(),
                          model_id="scripted-agent", transcript=transcript, session={}, tool_calls=1)]


@pytest.mark.parametrize(("provider_cls", "script", "kinds"), [
    (ScriptedModelProvider, model_script, ["generate", "generate"]),
    (ScriptedAgentProvider, agent_script, ["agent"]),
])
def test_workspace_writes_happen_again_in_replay(dirs, use_provider, provider_cls, script, kinds):
    use_provider(provider_cls(script()))
    recorded = run_step(WriteNote, INVOICE, mode="record", cassettes=dirs["cassettes"], workspace=dirs["record_ws"])
    expect(recorded, exit="done", outputs={"path": "notes/summary.txt"})
    assert (dirs["record_ws"] / "notes" / "summary.txt").read_text() == NOTE_ARGS["content"]
    assert [e.kind for e in entries(dirs["staging"])] == kinds     # the filesystem tool itself is never recorded
    promote(dirs["staging"], dirs["cassettes"])

    use_provider(provider_cls([]))
    replayed = run_step(WriteNote, INVOICE, cassettes=dirs["cassettes"], workspace=dirs["replay_ws"])
    expect(replayed, exit="done", outputs={"path": "notes/summary.txt"})
    assert (dirs["replay_ws"] / "notes" / "summary.txt").read_text() == NOTE_ARGS["content"]
    [write] = [e for e in replayed.events if e["type"] == "tool.call"]
    assert (write["tool"], write["ok"], write["replayed"]) == ("workspace_write", True, False)


# --- MCP: recorded live, replayed without a server --------------------------------------------------------------------

def test_mcp_calls_replay_without_connecting(dirs, use_provider, monkeypatch):
    schema = {"type": "object", "properties": {"number": {"type": "integer"}}, "required": ["number"]}
    snap = snapshot(McpServer("github", allow=["get_issue"]),
                    [McpToolSpec("get_issue", "Get a GitHub issue by number.", schema, {"readOnlyHint": True})])
    lock = {"name": "triage", "kind": "agentic", "entrypoint": "triage:Triage", "effects": ["network"],
            "mcp": [snap.model_dump(mode="json")]}
    script = [generate(tool_calls=(ToolCall("g1", "github__get_issue", {"number": 42}),)),
              generate(output={"exit": "done", "status": "open"})]
    monkeypatch.setenv("GITHUB_TOKEN", "gh-secret")
    with serve_http(token="gh-secret") as server:
        entry = McpServerEntry(name="github", transport="http", url=server.url,
                               headers={"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, auth_env=["GITHUB_TOKEN"])
        registry_from_env().put("mcp", "github", entry.model_dump(mode="json", exclude={"name"}))
        use_provider(ScriptedModelProvider(script))
        recorded = run_step(Triage, {"number": "42"}, mode="record", cassettes=dirs["cassettes"], lock=lock,
                            workspace=dirs["record_ws"])
    expect(recorded, exit="done", outputs={"status": "open"})
    assert [e.kind for e in entries(dirs["staging"])] == ["generate", "generate", "tool"]
    promote(dirs["staging"], dirs["cassettes"])

    monkeypatch.delenv("GITHUB_TOKEN")                              # the server is gone and its secret unset
    use_provider(ScriptedModelProvider([]))
    replayed = run_step(Triage, {"number": "42"}, cassettes=dirs["cassettes"], lock=lock, workspace=dirs["replay_ws"])
    expect(replayed, exit="done", outputs={"status": "open"})
    [call] = [e for e in replayed.events if e["type"] == "tool.call"]
    assert (call["tool"], call["source"], call["replayed"]) == ("github__get_issue", "mcp:github", True)
