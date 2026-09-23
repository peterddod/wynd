"""Cassette sessions, wrappers, store and promotion (PLAN §3.16, §5.7 RT-CASSETTE; `$DRAFTS/03 §11.3–§11.5`).

Providers are the W0 scripted fakes; every replay uses a factory that fails if called, proving replay never builds
the real provider.
"""

import dataclasses
import json
import re
from pathlib import Path

import pytest

from wynd.runtime.cassettes import NO_RECORDING, CassetteError, CassetteMissError, CassetteSession, promote
from wynd.runtime.cassettes.key import (
    Normaliser,
    continue_request,
    generate_request,
    normalised_json,
    request_key,
    tool_request,
)
from wynd.runtime.cassettes.store import find, read_entry
from wynd.runtime.policy import CassetteConfig
from wynd.runtime.providers import ProviderInfo
from wynd.runtime.providers.scripted import ScriptedAgentProvider, ScriptedModelProvider
from wynd.runtime.providers.types import (
    AgentRequest,
    AgentResponse,
    Continuation,
    GenerateRequest,
    GenerateResponse,
    ToolCall,
    ToolHandle,
    ToolResult,
)
from wynd.runtime.usage import Usage
from wynd.spec.fragments import EnvFragment

USAGE = Usage(input_tokens=2120, output_tokens=120, cost_usd=0.00272, latency_ms=3144.0, calls=1)
PACKAGE = "extract_invoice_fields_9c0e4b1a22"
ENTRY_FIELDS = {
    "wynd_cassette", "kind", "key", "package", "step", "provider", "tier", "thinking", "model_id", "recorded_at",
    "request", "response",
}


def session(workspace: Path, mode: str, *, dir=None, record_dir=None, run_id="run-1", literals=None) -> CassetteSession:
    config = CassetteConfig(
        mode=mode,
        dir=str(dir) if dir else None,
        record_dir=str(record_dir) if record_dir else None,
        literals=literals or {},
    )
    return CassetteSession(
        config, run_id=run_id, workspace=workspace, provider="scripted", tier="cheap", thinking="low",
        package=PACKAGE, step="extract",
    )


def never_built():
    raise AssertionError("replay must never build the real provider")


def gen_req(workspace: Path, run_id: str, text: str = "INVOICE INV-1042", model_id: str = "scripted"):
    return GenerateRequest(
        model_id=model_id,
        thinking="low",
        system=f"Extract the fields. Workspace {workspace}, run {run_id}, started 2026-09-22T21:50:01Z.",
        messages=[{"role": "user", "content": [{"type": "text", "text": text}]}],
        tools=[],
        output_schema={"type": "object", "properties": {"exit": {"const": "done"}}},
    )


def gen_resp(**changes) -> GenerateResponse:
    resp = GenerateResponse(
        message={"role": "assistant", "content": [{"type": "text", "text": "done"}]},
        text="done",
        tool_calls=[],
        structured_output={"exit": "done", "total": 1200.5},
        stop="end",
        usage=USAGE,
        model_id="claude-haiku-4-5-20251001",
        cost_basis="api",
        raw={"id": "msg_1"},
    )
    return dataclasses.replace(resp, **changes)


def tool_use_resp(path: str) -> GenerateResponse:
    call = ToolCall("t1", "read_file", {"path": path})
    return gen_resp(
        message={"role": "assistant", "content": [{"type": "tool_use", "id": "t1", "name": "read_file",
                                                   "input": {"path": path}}]},
        text="",
        tool_calls=[call],
        structured_output=None,
        stop="tool_calls",
    )


def with_tool_result(req: GenerateRequest, assistant: dict) -> GenerateRequest:
    result = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "t1", "content": "text of the pdf",
                                           "is_error": False}]}
    return dataclasses.replace(req, messages=[*req.messages, assistant, result])


def agent_req(workspace: Path, tools=(), **changes) -> AgentRequest:
    req = AgentRequest(
        model_id="scripted",
        thinking="low",
        instruction="Extract the fields.",
        context={"process.goal": "Pay supplier invoices"},
        input={"invoice_text": "INVOICE INV-1042"},
        output_schema={"type": "object"},
        tools=list(tools),
        mcp_servers=[],
        workspace=workspace,
    )
    return dataclasses.replace(req, **changes)


def agent_resp(**changes) -> AgentResponse:
    resp = AgentResponse(
        structured_output={"exit": "done", "total": 1200.5},
        usage=USAGE,
        model_id="claude-haiku-4-5-20251001",
        transcript=[{"type": "tool_use", "id": "t9", "name": "StructuredOutput", "input": {"exit": "done"}}],
        session={"session_id": "s1", "entries": [1, 2]},
        note="looked at the totals",
        tool_calls=0,
        startup_ms=612.0,
        cost_basis="list",
    )
    return dataclasses.replace(resp, **changes)


# --- ModelProvider ----------------------------------------------------------------------------------------------------

def test_generate_record_promote_replay_round_trip(tmp_path):
    ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
    staging, cassettes = tmp_path / "staging", tmp_path / "pkg" / "cassettes"
    first, second = tool_use_resp("invoice.pdf"), gen_resp()
    scripted = ScriptedModelProvider([first, second])
    built = []
    rec = session(ws1, "record", record_dir=staging).wrap(lambda: built.append(1) or scripted, "model")

    req1 = gen_req(ws1, "run-1")
    assert rec.generate(req1) is first                  # the live response reaches the loop untouched
    req2 = with_tool_result(req1, first.message)
    assert rec.generate(req2) is second
    assert built == [1]                                 # the inner provider is built once, lazily
    assert rec.last_key == request_key(generate_request(req2, provider="scripted", tier="cheap"),
                                       Normaliser.for_run("run-1", ws1))

    files = sorted(staging.glob("*.json"))
    assert len(files) == 2
    entry = json.loads((staging / f"{rec.last_key[:32]}.json").read_text())
    assert set(entry) == ENTRY_FIELDS
    assert entry["wynd_cassette"] == 1
    assert (entry["kind"], entry["key"]) == ("generate", rec.last_key)
    assert (entry["package"], entry["step"]) == (PACKAGE, "extract")
    assert (entry["provider"], entry["tier"], entry["thinking"]) == ("scripted", "cheap", "low")
    assert entry["model_id"] == "claude-haiku-4-5-20251001"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", entry["recorded_at"])
    assert entry["request"]["system"] == "Extract the fields. Workspace <workspace>, run <run.id>, started <datetime>."
    assert entry["request"]["model_id"] == "scripted"
    assert "raw" not in entry["response"] and "session" not in entry["response"]
    assert entry["response"]["usage"]["latency_ms"] == 3144.0

    assert promote(staging, cassettes) == [cassettes / f.name for f in files]

    rep = session(ws2, "replay", dir=cassettes, run_id="run-2").wrap(never_built, "model")
    got1 = rep.generate(gen_req(ws2, "run-2"))
    assert got1 == dataclasses.replace(first, raw=None)
    got2 = rep.generate(with_tool_result(gen_req(ws2, "run-2"), got1.message))
    assert got2 == dataclasses.replace(second, raw=None)
    assert got2.usage.latency_ms == 3144.0             # latency comes from the recording


def test_replay_of_an_edited_request_misses_with_the_exact_text(tmp_path):
    ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
    staging, cassettes = tmp_path / "staging", tmp_path / "cassettes"
    rec = session(ws1, "record", record_dir=staging).wrap(lambda: ScriptedModelProvider([gen_resp()]), "model")
    rec.generate(gen_req(ws1, "run-1"))
    promote(staging, cassettes)

    edited = gen_req(ws2, "run-2", text="INVOICE INV-1043")
    rep = session(ws2, "replay", dir=cassettes, run_id="run-2").wrap(never_built, "model")
    with pytest.raises(CassetteMissError) as err:
        rep.generate(edited)

    canonical = generate_request(edited, provider="scripted", tier="cheap")
    norm = Normaliser.for_run("run-2", ws2)
    key = request_key(canonical, norm)
    dump = ws2 / ".wynd" / "cassettes" / "misses" / f"{key[:32]}.request.json"
    assert NO_RECORDING == "no recording for this request — re-record with `wynd test --live`"
    assert str(err.value) == f"{NO_RECORDING}\n  key: {key}\n  cassettes: {cassettes}\n  request: {dump}"
    assert err.value.key == key
    assert json.loads(dump.read_text()) == json.loads(normalised_json(canonical, norm))
    assert "<workspace>" in dump.read_text()


def test_tier_remap_misses(tmp_path):
    ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
    staging, cassettes = tmp_path / "staging", tmp_path / "cassettes"
    default = resolve_model_double(ScriptedModelProvider, "cheap", MemRegistry())
    rec = session(ws1, "record", record_dir=staging).wrap(lambda: ScriptedModelProvider([gen_resp()]), "model")
    rec.generate(gen_req(ws1, "run-1", model_id=default))
    promote(staging, cassettes)

    unchanged = resolve_model_double(ScriptedModelProvider, "cheap", MemRegistry())
    rep = session(ws2, "replay", dir=cassettes, run_id="run-2").wrap(never_built, "model")
    assert rep.generate(gen_req(ws2, "run-2", model_id=unchanged)).structured_output == gen_resp().structured_output

    remapped = resolve_model_double(
        ScriptedModelProvider, "cheap", MemRegistry({"providers": {"scripted": {"tiers": {"cheap": "scripted-large"}}}})
    )
    assert remapped == "scripted-large"
    with pytest.raises(CassetteMissError) as err:
        rep.generate(gen_req(ws2, "run-2", model_id=remapped))
    assert str(err.value).startswith(NO_RECORDING + "\n  key: ")


def test_literals_keep_a_tmp_path_key_stable_across_temp_dirs(tmp_path):
    ws1, ws2, t1, t2 = tmp_path / "ws1", tmp_path / "ws2", tmp_path / "tmp1", tmp_path / "tmp2"
    staging, cassettes = tmp_path / "staging", tmp_path / "cassettes"
    scripted = ScriptedModelProvider([tool_use_resp(f"{t1}/invoice.pdf"), gen_resp()])
    rec = session(ws1, "record", record_dir=staging, literals={str(t1): "<tmp>"}).wrap(lambda: scripted, "model")
    req1 = gen_req(ws1, "run-1", text=f"read {t1}/invoice.pdf")
    first = rec.generate(req1)
    rec.generate(with_tool_result(req1, first.message))
    promote(staging, cassettes)

    rep = session(ws2, "replay", dir=cassettes, run_id="run-2", literals={str(t2): "<tmp>"}).wrap(never_built, "model")
    req1 = gen_req(ws2, "run-2", text=f"read {t2}/invoice.pdf")
    got = rep.generate(req1)
    assert got.tool_calls == [ToolCall("t1", "read_file", {"path": f"{t2}/invoice.pdf"})]   # restored to this run
    assert rep.generate(with_tool_result(req1, got.message)).structured_output == {"exit": "done", "total": 1200.5}

    without = session(ws2, "replay", dir=cassettes, run_id="run-2").wrap(never_built, "model")
    with pytest.raises(CassetteMissError):
        without.generate(req1)


def test_live_mode_calls_the_provider_and_writes_nothing(tmp_path):
    ws, staging, cassettes = tmp_path / "ws", tmp_path / "staging", tmp_path / "cassettes"
    resp = gen_resp()
    live = session(ws, "live", dir=cassettes, record_dir=staging)
    model = live.wrap(lambda: ScriptedModelProvider([resp]), "model")
    req = gen_req(ws, "run-1")
    assert model.generate(req) is resp
    assert model.last_key == request_key(generate_request(req, provider="scripted", tier="cheap"),
                                         Normaliser.for_run("run-1", ws))
    agent = live.wrap(lambda: ScriptedAgentProvider([agent_resp()]), "agent")
    assert agent.run(agent_req(ws)).session == {"session_id": "s1", "entries": [1, 2], "cassette_key": agent.last_key}
    live.write_tool("fetch", {"url": "https://x.test"}, ToolResult("ok"))
    assert not staging.exists() and not cassettes.exists() and not ws.exists()


def test_record_mode_needs_a_record_dir(tmp_path):
    with pytest.raises(CassetteError, match="record_dir"):
        session(tmp_path, "record")


def test_unknown_provider_kind_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="unknown provider kind"):
        session(tmp_path, "live").wrap(never_built, "chat")


def test_tiers_come_from_the_provider_and_never_build_it_in_replay(tmp_path, monkeypatch):
    scripted = ScriptedModelProvider(tiers={"cheap": "small"})
    assert session(tmp_path, "live").wrap(lambda: scripted, "model").tiers()["cheap"] == "small"

    info = ProviderInfo("scripted", "model", {"cheap": "default-small"}, EnvFragment())
    monkeypatch.setattr("wynd.runtime.providers.provider_info", lambda name: info)   # RT-PROVIDERS double
    replay = session(tmp_path, "replay", dir=tmp_path / "cassettes").wrap(never_built, "model")
    assert replay.tiers() == {"cheap": "default-small"}


# --- AgentProvider ----------------------------------------------------------------------------------------------------

def test_agent_record_replay_and_continuation_chain(tmp_path):
    ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
    staging, cassettes = tmp_path / "staging", tmp_path / "cassettes"
    invalid = agent_resp(structured_output={"exit": "done"})
    valid = agent_resp(session={"session_id": "s1", "entries": [1, 2, 3]}, note="")
    scripted = ScriptedAgentProvider([invalid, valid])
    rec = session(ws1, "record", record_dir=staging).wrap(lambda: scripted, "agent")

    r1 = rec.run(agent_req(ws1))
    k1 = rec.last_key
    assert r1.session == {"session_id": "s1", "entries": [1, 2], "cassette_key": k1}
    r2 = rec.run(agent_req(ws1, continuation=Continuation(r1.session, "total is missing")))
    assert r2.structured_output == valid.structured_output
    assert scripted.requests[1].continuation.session == {"session_id": "s1", "entries": [1, 2]}   # opaque again
    assert rec.last_key == request_key(continue_request(k1, "total is missing"), Normaliser.for_run("run-1", ws1))
    entry = json.loads((staging / f"{rec.last_key[:32]}.json").read_text())
    assert entry["kind"] == "agent_continue"
    assert "session" not in entry["response"]
    promote(staging, cassettes)

    rep = session(ws2, "replay", dir=cassettes, run_id="run-2").wrap(never_built, "agent")
    p1 = rep.run(agent_req(ws2))
    assert p1 == dataclasses.replace(invalid, session={"cassette_key": k1})
    p2 = rep.run(agent_req(ws2, continuation=Continuation(p1.session, "total is missing")))
    assert p2.structured_output == valid.structured_output
    with pytest.raises(CassetteMissError):
        rep.run(agent_req(ws2, continuation=Continuation(p1.session, "due date is missing")))


def test_agent_replay_reexecutes_local_tool_uses_in_recorded_order(tmp_path):
    ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
    staging, cassettes = tmp_path / "staging", tmp_path / "cassettes"
    calls = []

    def handles():
        def invoke(name):
            return lambda args: calls.append((name, args)) or ToolResult("ok")
        return [
            ToolHandle("write_note", "Write a note.", {"type": "object"}, invoke("write_note"), local=True),
            ToolHandle("fetch", "Fetch a URL.", {"type": "object"}, invoke("fetch"), local=False),
        ]

    transcript = [
        {"type": "text", "text": "working"},
        {"type": "tool_use", "id": "t1", "name": "write_note", "input": {"path": f"{ws1}/notes/a.txt", "text": "a"}},
        {"type": "tool_result", "id": "t1", "is_error": False, "content": "ok"},
        {"type": "tool_use", "id": "t2", "name": "fetch", "input": {"url": "https://x.test"}},
        {"type": "tool_use", "id": "t3", "name": "Read", "input": {"file_path": "notes/a.txt"}},
        {"type": "tool_use", "id": "t4", "name": "write_note", "input": {"path": "notes/b.txt", "text": "b"}},
        {"type": "tool_use", "id": "t5", "name": "StructuredOutput", "input": {"exit": "done"}},
    ]
    scripted = ScriptedAgentProvider([agent_resp(transcript=transcript, tool_calls=4)])
    rec = session(ws1, "record", record_dir=staging).wrap(lambda: scripted, "agent")
    rec.run(agent_req(ws1, tools=handles()))
    assert [name for name, _ in calls] == ["write_note", "fetch", "write_note"]   # the harness ran them live
    promote(staging, cassettes)

    calls.clear()
    events = []
    rep = session(ws2, "replay", dir=cassettes, run_id="run-2").wrap(never_built, "agent")
    resp = rep.run(agent_req(ws2, tools=handles(), on_event=events.append))
    assert calls == [
        ("write_note", {"path": f"{ws2}/notes/a.txt", "text": "a"}),
        ("write_note", {"path": "notes/b.txt", "text": "b"}),
    ]
    assert events == resp.transcript
    assert [e.get("id") for e in events] == [None, "t1", "t1", "t2", "t3", "t4", "t5"]
    assert resp.tool_calls == 4 and resp.structured_output == {"exit": "done", "total": 1200.5}


# --- tool calls -------------------------------------------------------------------------------------------------------

def test_tool_calls_record_promote_replay_and_miss(tmp_path):
    ws1, ws2 = tmp_path / "ws1", tmp_path / "ws2"
    staging, cassettes = tmp_path / "staging", tmp_path / "cassettes"
    rec = session(ws1, "record", record_dir=staging)
    rec.write_tool("fetch", {"url": "https://x.test"}, ToolResult('{"status": 200}'))
    rec.write_tool("fetch", {"url": "https://down.test"}, None, error="tool fetch failed: ConnectionError: reset")
    rec.write_tool("github__get_issue", {"number": 42}, ToolResult("no such issue", is_error=True))
    assert len(promote(staging, cassettes)) == 3

    rep = session(ws2, "replay", dir=cassettes, run_id="run-2")
    ok = rep.find_tool("fetch", {"url": "https://x.test"})
    assert (ok.kind, ok.result, ok.error) == ("tool", ToolResult('{"status": 200}'), None)
    failed = rep.find_tool("fetch", {"url": "https://down.test"})
    assert (failed.result, failed.error) == (None, "tool fetch failed: ConnectionError: reset")
    assert rep.find_tool("github__get_issue", {"number": 42}).result == ToolResult("no such issue", is_error=True)

    with pytest.raises(CassetteMissError) as err:
        rep.find_tool("fetch", {"url": "https://y.test"})
    key = request_key(tool_request("fetch", {"url": "https://y.test"}), Normaliser.for_run("run-2", ws2))
    dump = ws2 / ".wynd" / "cassettes" / "misses" / f"{key[:32]}.request.json"
    assert str(err.value) == f"{NO_RECORDING}\n  key: {key}\n  cassettes: {cassettes}\n  request: {dump}"


def test_write_tool_writes_only_in_record_mode(tmp_path):
    staging = tmp_path / "staging"
    session(tmp_path / "ws", "replay", dir=tmp_path / "cassettes", record_dir=staging).write_tool(
        "fetch", {"url": "https://x.test"}, ToolResult("ok")
    )
    assert not staging.exists()


# --- store ------------------------------------------------------------------------------------------------------------

def test_lfs_pointer_in_the_cassette_dir_raises_cassette_error(tmp_path):
    ws, cassettes = tmp_path / "ws", tmp_path / "cassettes"
    req = gen_req(ws, "run-1")
    key = request_key(generate_request(req, provider="scripted", tier="cheap"), Normaliser.for_run("run-1", ws))
    cassettes.mkdir()
    pointer = cassettes / f"{key[:32]}.json"
    pointer.write_text("version https://git-lfs.github.com/spec/v1\noid sha256:" + "0" * 64 + "\nsize 2048\n")
    with pytest.raises(CassetteError) as err:
        session(ws, "replay", dir=cassettes).wrap(never_built, "model").generate(req)
    assert str(err.value) == (
        f"cassette {pointer} is a git-lfs pointer, not a recording: install git-lfs (https://git-lfs.com) and run "
        "'git lfs pull'"
    )


def test_read_entry_rejects_files_that_are_not_recordings(tmp_path):
    path = tmp_path / "x.json"
    path.write_text('{"kind": "generate"}')
    with pytest.raises(CassetteError, match="not a wynd cassette entry"):
        read_entry(path)
    path.write_text("{not json")
    with pytest.raises(CassetteError, match="not valid JSON"):
        read_entry(path)


def test_a_key_prefix_collision_is_a_miss(tmp_path):
    key = "a" * 64
    other = "a" * 32 + "b" * 32
    (tmp_path / f"{key[:32]}.json").write_text(json.dumps({
        "wynd_cassette": 1, "kind": "tool", "key": other, "request": {}, "response": {"result": {"text": "x"}},
    }))
    assert find(tmp_path, other).key == other
    assert find(tmp_path, key) is None
    assert find(None, key) is None


# --- promote ----------------------------------------------------------------------------------------------------------

def _entry(path: Path, key: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"wynd_cassette": 1, "kind": "tool", "key": key, "request": {}, "response": {}}))


def test_promote_replaces_only_json_and_mirrors_subdirectories(tmp_path):
    staging, dest = tmp_path / "staging", tmp_path / "dest"
    _entry(staging / "a.json", "a")
    _entry(staging / "step_x" / "b.json", "b")
    _entry(staging / "edges" / "c.json", "c")
    (staging / "misses").mkdir()
    (staging / "misses" / "k.request.json").write_text('{"v": 1, "kind": "tool"}')    # a miss dump, never promoted
    (staging / "notes.txt").write_text("not a cassette")
    _entry(dest / "stale.json", "stale")
    _entry(dest / "step_x" / "stale.json", "stale")
    (dest / "a.json").write_text("version https://git-lfs.github.com/spec/v1\n")    # an old pointer is replaced too
    (dest / ".gitattributes").write_text("*.json filter=lfs\n")
    (dest / "README.md").write_text("recordings\n")

    written = promote(staging, dest)

    assert written == [dest / "a.json", dest / "edges" / "c.json", dest / "step_x" / "b.json"]
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*.json")) == [
        "a.json", "edges/c.json", "step_x/b.json",
    ]
    assert (dest / "a.json").read_bytes() == (staging / "a.json").read_bytes()
    assert (dest / ".gitattributes").exists() and (dest / "README.md").exists()
    assert not (dest / "notes.txt").exists()


def test_promote_without_recordings_clears_the_old_ones(tmp_path):
    dest = tmp_path / "dest"
    _entry(dest / "old.json", "old")
    assert promote(tmp_path / "never-recorded", dest) == []
    assert list(dest.rglob("*.json")) == []
    assert promote(tmp_path / "never-recorded", tmp_path / "absent") == []
    assert not (tmp_path / "absent").exists()


# --- doubles for RT-PROVIDERS (same sub-wave, PLAN §0 rule 3) ---------------------------------------------------------

class MemRegistry:
    """In-memory user registry: only the read side a tier lookup needs."""

    def __init__(self, sections: dict | None = None) -> None:
        self.sections = sections or {}

    def get(self, section: str, name: str) -> dict | None:
        return self.sections.get(section, {}).get(name)

    def list(self, section: str) -> dict:
        return dict(self.sections.get(section, {}))


def resolve_model_double(provider_cls, tier: str, registry: MemRegistry) -> str:
    """`resolve_model` per PLAN §3.15: the class's default tiers overlaid with the registry's `providers` entry."""
    entry = registry.get("providers", provider_cls.name) or {}
    return {**provider_cls.default_tiers, **entry.get("tiers", {})}[tier]
