"""Chats (PLAN §8.1 chat row, §15 item 52; `$DRAFTS/06 §7`, §11.2 `test_chat.py`).

Turns run a scripted AgentProvider (`FakeProvider`) that behaves like a harness: it streams text/tool_use/tool_result
events and invokes the request's tool handles. `DesignService` is a double (`FakeDesign`, CTL-DESIGN is built in the
same sub-wave) that keeps the design contract the chat relies on: scope, lock, `sha256:` revisions, and one commit
of the dirty scope paths with the design message and trailers, made with real git.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
import uuid
from pathlib import Path

import pytest
import yaml

from wynd.controller.api.models_web import (
    SavedFile,
    SaveResult,
    SendMessageRequest,
)
from wynd.controller.chat.engine import _commit_summary
from wynd.controller.chat.events import ChatEventLog
from wynd.controller.chat.prompt import CHAT_REPLY_SCHEMA, SYSTEM_INSTRUCTION
from wynd.controller.chat.tools import READ_TOOLS, WRITE_TOOLS
from wynd.controller.errors import DesignLocked, Invalid, NotFound, OutOfScope, RevisionConflict, TurnInProgress
from wynd.controller.jobs import records
from wynd.controller.models import CommitInfo, Job, ProcessInterface, ValidationReportDTO
from wynd.process.git import commit_only
from wynd.process.jobs import new_job_record
from wynd.runtime.providers import register_for_tests
from wynd.runtime.providers.types import AgentResponse, ProviderError
from wynd.runtime.usage import Usage
from wynd.spec.fragments import EnvFragment
from wynd.spec.yamlio import dump_yaml

P1_FILE = "processes/p1/process.yaml"


# --- doubles ----------------------------------------------------------------------------------------------------------

class FakeProvider:
    """Scripted AgentProvider. Each `run` plays the next script: `("text", str)`, `("call", tool, args)`,
    `("signal", Event)`, `("wait", Event)`, `("raise", exc)`; then replies `{"reply": reply}`. A set `req.cancel` is
    honoured between steps unless `honour_cancel` is False."""

    name = "chat-fake"
    kind = "agent"
    default_tiers = {"cheap": "fake-cheap", "standard": "fake-standard", "strong": "fake-strong"}
    env_fragment = EnvFragment()

    def __init__(self) -> None:
        self.scripts: list[tuple[list[tuple], str]] = []
        self.requests = []
        self.results: list[tuple[str, object]] = []
        self.honour_cancel = True

    def __call__(self, tiers=None) -> FakeProvider:
        return self

    def tiers(self) -> dict[str, str]:
        return dict(self.default_tiers)

    def script(self, *steps: tuple, reply: str = "Done.") -> None:
        self.scripts.append((list(steps), reply))

    def run(self, req):
        self.requests.append(req)
        steps, reply = self.scripts.pop(0)
        handles = {h.name: h for h in req.tools}
        for n, step in enumerate(steps):
            if self.honour_cancel and req.cancel.is_set():
                raise ProviderError("cancelled", kind="transport", retryable=False)
            match step:
                case ("text", text):
                    req.on_event({"type": "text", "text": text})
                case ("call", name, args):
                    req.on_event({"type": "tool_use", "id": f"tu_{n}", "name": name, "input": args})
                    result = handles[name].invoke(args)
                    self.results.append((name, result))
                    req.on_event({"type": "tool_result", "id": f"tu_{n}", "is_error": result.is_error,
                                  "content": result.text})
                case ("signal", event):
                    event.set()
                case ("wait", event):
                    assert event.wait(10)
                case ("raise", error):
                    raise error
        return AgentResponse(structured_output={"reply": reply}, model_id=req.model_id, transcript=[], session={},
                             usage=Usage(input_tokens=100, output_tokens=20, cost_usd=0.01, calls=1))


class ModelOnly(FakeProvider):
    name = "chat-model"
    kind = "model"


def revision(path: Path) -> str | None:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}" if path.exists() else None


class FakeDesign:
    """`DesignService` double: scope = the process's `process.yaml` and `proto/*.yaml`."""

    def __init__(self, ctx) -> None:
        self.ctx = ctx
        self.locks: dict[str, tuple[str, str]] = {}
        self.saves: list[dict] = []
        self.commits: list[dict] = []

    def _dir(self, pid: str) -> str:
        return self.ctx.workspace().processes[pid].dir

    def lock(self, pid, chat_id, turn_id) -> None:
        held = self.locks.get(pid)
        if held is not None and held != (chat_id, turn_id):
            raise DesignLocked(f"{pid} is locked", details={"chat_id": held[0], "turn_id": held[1]})
        self.locks[pid] = (chat_id, turn_id)

    def unlock(self, pid, turn_id) -> None:
        if pid in self.locks and self.locks[pid][1] == turn_id:
            del self.locks[pid]

    def locked_by(self, pid):
        return self.locks.get(pid)

    def save(self, pid, req, *, origin="web", lock_owner=None) -> SaveResult:
        d = self._dir(pid)
        for w in req.writes:
            if w.path != f"{d}/process.yaml" and not (w.path.startswith(f"{d}/proto/") and ".." not in w.path):
                raise OutOfScope(f"{w.path} is not in the design scope of {pid}")
        if self.locks.get(pid) not in (None, lock_owner):
            raise DesignLocked(f"{pid} is locked")
        for w in req.writes:
            current = revision(self.ctx.root / w.path)
            if current != w.base_revision:
                raise RevisionConflict(f"{w.path} changed", details={"path": w.path, "current_revision": current})
        files = []
        for w in req.writes:
            path = self.ctx.root / w.path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(dump_yaml(w.doc))
            files.append(SavedFile(path=w.path, revision=revision(path), yaml=path.read_text()))
        self.saves.append({"pid": pid, "paths": [w.path for w in req.writes], "origin": origin,
                           "lock_owner": lock_owner})
        return SaveResult(files=files, validation=ValidationReportDTO(ok=True), interface=ProcessInterface(),
                          head="0" * 40)

    def commit(self, pid, *, reason, summary, origin="web", extra_trailers=None):
        self.commits.append({"pid": pid, "reason": reason, "summary": summary, "origin": origin,
                             "trailers": dict(extra_trailers or {})})
        trailers = {"Wynd-Origin": origin, "Wynd-Reason": reason, **(extra_trailers or {})}
        message = f"design({pid}): {summary or 'update'}\n\n" + "\n".join(f"{k}: {v}" for k, v in trailers.items())
        d = self._dir(pid)
        sha = commit_only(self.ctx.root, [f"{d}/process.yaml", f"{d}/proto"], message)
        if sha is None:
            return None
        return CommitInfo(sha=sha, short=sha[:7], subject=message.splitlines()[0], author="Wynd Test",
                          at=self.ctx.clock(), message=message)


class RecordingRunner:
    """JobRunner double: `submit` stores a queued `JobRecord`, `status` reads it back."""

    name = "recording"

    def __init__(self) -> None:
        self.runs = None
        self.root = None
        self.submitted: list[str] = []

    def submit(self, kind, ref, inputs) -> str:
        record = new_job_record(kind, ref, inputs, ws_root=self.root, runner=self.name, handler="tests:none")
        records.create(self.runs, record)
        self.submitted.append(record.id)
        return record.id

    def status(self, job_id):
        return records.load(self.runs, job_id)


# --- fixtures and helpers ---------------------------------------------------------------------------------------------

@pytest.fixture
def provider():
    fake = FakeProvider()
    undo = register_for_tests(fake.name, fake)
    yield fake
    undo()


@pytest.fixture
def chat_controller(workspace, make_controller, provider):
    def make(**env: str):
        runner = RecordingRunner()
        ctl = make_controller(workspace, env={"WYND_CHAT_PROVIDER": provider.name, **env}, runner=runner)
        runner.runs, runner.root = ctl.ctx.stores.runs, workspace
        ctl.design = FakeDesign(ctl.ctx)
        made.append(ctl)
        return ctl

    made = []
    yield make
    for ctl in made:
        ctl.chats.close()


@pytest.fixture
def ctl(chat_controller):
    return chat_controller()


def send(ctl, chat_id: str, text: str, acting_on: str | None = None, client_id: str | None = None):
    req = SendMessageRequest(text=text, acting_on=acting_on, client_id=client_id or uuid.uuid4().hex)
    return ctl.chats.send(chat_id, req)


def wait_turn(ctl, chat_id: str, timeout: float = 10.0):
    deadline = time.monotonic() + timeout
    while ctl.chats.snapshot(chat_id).chat.running_turn is not None:
        assert time.monotonic() < deadline, "the turn did not finish"
        time.sleep(0.01)
    return ctl.chats.snapshot(chat_id)


def events(ctl, chat_id: str, since: int = 0) -> list:
    found, reset = ctl.chats.events(chat_id, since)
    assert not reset
    return found


def p1_doc(workspace: Path, **changes) -> dict:
    return {**yaml.safe_load((workspace / P1_FILE).read_text()), **changes}


def git_log(git, ws: Path, base: str) -> list[str]:
    return git(ws, "log", "--format=%H", f"{base}..HEAD").split()


# --- event log --------------------------------------------------------------------------------------------------------

def test_event_log_resumes_inside_the_buffer_and_resets_outside_it():
    log = ChatEventLog(capacity=3)
    assert log.since(0) == ([], False)
    assert [log.append("item", {"n": n}) for n in range(1, 6)] == [1, 2, 3, 4, 5]
    assert log.last_id == 5
    found, reset = log.since(2)
    assert ([e.id for e in found], reset) == ([3, 4, 5], False)
    assert found[0].event == "item" and found[0].data == {"n": 3}
    assert log.since(5) == ([], False)
    assert log.since(1) == ([], True)            # event 2 was evicted
    assert log.since(6) == ([], True)            # ahead of the log (e.g. it restarted with the process)
    assert log.since(-1) == ([], True)


def test_a_cursor_the_chat_log_never_reached_needs_a_reset(ctl):
    chat = ctl.chats.create()
    assert ctl.chats.events(chat.id, 0) == ([], False)
    assert ctl.chats.events(chat.id, 7) == ([], True)


# --- records ----------------------------------------------------------------------------------------------------------

def test_create_list_rename_delete_and_persistence(ctl, chat_controller):
    first = ctl.chats.create()
    ctl.ctx.clock.advance(60)
    second = ctl.chats.create("  Compile p1  ")
    assert (first.title, second.title) == ("New chat", "Compile p1")
    assert (first.running_turn, first.job, first.usage_totals) == (None, None, None)
    assert [c.id for c in ctl.chats.list()] == [second.id, first.id]
    ctl.ctx.clock.advance(60)
    renamed = ctl.chats.rename(first.id, "Invoices")
    assert renamed.title == "Invoices" and renamed.updated_at > second.updated_at
    assert [c.id for c in ctl.chats.list()] == [first.id, second.id]
    assert [c.id for c in ctl.chats.list(limit=1)] == [first.id]
    with pytest.raises(Invalid):
        ctl.chats.rename(first.id, "  ")

    other = chat_controller()                    # a fresh service reads the documents back
    assert [(c.id, c.title) for c in other.chats.list()] == [(first.id, "Invoices"), (second.id, "Compile p1")]
    assert other.chats.snapshot(first.id).items == []

    ctl.chats.delete(first.id)
    assert [c.id for c in ctl.chats.list()] == [second.id]
    for call in (lambda: ctl.chats.snapshot(first.id), lambda: ctl.chats.cancel("chat_nope"),
                 lambda: ctl.chats.events("../x", 0)):
        with pytest.raises(NotFound):
            call()


def test_items_a_crashed_turn_left_open_are_closed_on_load(ctl, chat_controller):
    chat = ctl.chats.create()
    doc = ctl.ctx.docs.get("chats", chat.id)
    doc["items"] = [
        {"id": "it_1", "seq": 1, "type": "assistant", "created_at": doc["created_at"], "turn_id": "turn_x",
         "text": "half", "status": "streaming", "error": None, "usage": None},
        {"id": "it_2", "seq": 2, "type": "tool", "created_at": doc["created_at"], "turn_id": "turn_x",
         "tool": "read_design", "args": {}, "write": False, "acting_on": None, "status": "running",
         "summary": None, "duration_ms": None},
    ]
    ctl.ctx.docs.put("chats", chat.id, doc)
    items = chat_controller().chats.snapshot(chat.id).items
    assert [(i.type, i.status) for i in items] == [("assistant", "error"), ("tool", "error")]


# --- a turn -----------------------------------------------------------------------------------------------------------

def test_a_turn_without_a_process_streams_items_deltas_and_turn_events(ctl, provider):
    provider.script(("text", "Let me look."), ("call", "read_design", {"process": "p2"}), reply="p2 tags names.")
    chat = ctl.chats.create()
    turn_id, user = send(ctl, chat.id, "What does p2 do?")
    assert (user.type, user.text, user.acting_on, user.seq) == ("user", "What does p2 do?", None, 1)
    snapshot = wait_turn(ctl, chat.id)

    found = events(ctl, chat.id)
    assert [e.event for e in found] == ["item", "turn", "item", "delta", "item", "item", "item", "turn"]
    assert [e.id for e in found] == list(range(1, 9))
    assert found[0].data["item"]["id"] == user.id
    assert found[1].data == {"turn_id": turn_id, "status": "running", "acting_on": None, "edited": [], "error": None}
    assert found[2].data["item"] | {"created_at": None} == {
        "id": "it_2", "seq": 2, "type": "assistant", "created_at": None, "turn_id": turn_id, "text": "",
        "status": "streaming", "error": None, "usage": None}
    assert found[3].data == {"item_id": "it_2", "text": "Let me look."}
    running, done = found[4].data["item"], found[5].data["item"]
    assert (running["id"], running["tool"], running["args"], running["status"]) == (
        "it_3", "read_design", {"process": "p2"}, "running")
    assert (running["write"], running["acting_on"]) == (False, None)
    assert (done["id"], done["status"]) == ("it_3", "ok") and done["duration_ms"] >= 0
    assert json.loads(provider.results[0][1].text)["path"] == "processes/p2/process.yaml"
    assert done["summary"] == provider.results[0][1].text[:200]
    final = found[6].data["item"]
    assert (final["id"], final["text"], final["status"]) == ("it_2", "p2 tags names.", "done")
    assert final["usage"]["input_tokens"] == 100
    assert found[7].data == {"turn_id": turn_id, "status": "done", "acting_on": None, "edited": [], "error": None}

    assert snapshot.cursor == 8 and events(ctl, chat.id, snapshot.cursor) == []
    assert [(i.type, i.seq) for i in snapshot.items] == [("user", 1), ("assistant", 2), ("tool", 3)]
    assert snapshot.chat.title == "What does p2 do?"
    assert snapshot.chat.usage_totals.input_tokens == 100

    req = provider.requests[0]
    assert sorted(h.name for h in req.tools) == sorted(READ_TOOLS)          # no write tools without a process
    assert (req.model_id, req.thinking, req.builtin_tools, req.mcp_servers) == ("fake-standard", "low", [], [])
    assert (req.instruction, req.output_schema, req.input) == (SYSTEM_INSTRUCTION, CHAT_REPLY_SCHEMA,
                                                               {"message": "What does p2 do?"})
    assert req.context == {"acting_on": None, "acting_on_design": None, "bound_job": None, "history": []}
    assert req.prompt.startswith("# Context\n") and req.prompt.endswith("# Message\nWhat does p2 do?")
    assert req.workspace == ctl.ctx.state_dir / "chats" / chat.id and req.workspace.is_dir()
    assert isinstance(req.cancel, threading.Event)


def test_the_chat_tier_and_the_persisted_history_reach_the_provider(chat_controller, provider):
    ctl = chat_controller(WYND_CHAT_TIER="strong")
    provider.script(("call", "validate_process", {"process": "p1"}), reply="p1 is valid.")
    provider.script(reply="Yes.")
    chat = ctl.chats.create()
    send(ctl, chat.id, "Is p1 valid?", acting_on="p1")
    wait_turn(ctl, chat.id)
    send(ctl, chat.id, "Sure?")
    snapshot = wait_turn(ctl, chat.id)
    first, second = provider.requests
    assert first.model_id == "fake-strong"
    assert first.context["acting_on_design"] == (ctl.ctx.root / P1_FILE).read_text()
    assert first.context["history"] == []
    assert second.context["history"] == [
        {"role": "user", "text": "Is p1 valid?", "acting_on": "p1", "tools": []},
        {"role": "assistant", "text": "p1 is valid.", "acting_on": None, "tools": ["validate_process"]},
    ]
    assert json.loads(provider.results[0][1].text)["ok"] is True
    assert snapshot.chat.title == "Is p1 valid?"                           # only the first message titles
    assert snapshot.chat.usage_totals.input_tokens == 200
    assert snapshot.chat.usage_totals.cost_usd == pytest.approx(0.02)


def test_a_wynd_error_is_an_error_result_the_model_can_recover_from(ctl, provider):
    provider.script(("call", "read_design", {"process": "nope"}), ("call", "read_trace", {"run_id": "run_x"}),
                    ("call", "list_runs", {"process": "p1"}), reply="Not found.")
    chat = ctl.chats.create()
    send(ctl, chat.id, "Read nope")
    snapshot = wait_turn(ctl, chat.id)
    (_, design), (_, trace), (_, runs) = provider.results
    assert design.is_error and json.loads(design.text)["error"]["code"] == "not_found"
    assert trace.is_error and json.loads(trace.text)["error"]["code"] == "not_found"
    assert not runs.is_error and json.loads(runs.text) == {"runs": []}
    tools = [i for i in snapshot.items if i.type == "tool"]
    assert [t.status for t in tools] == ["error", "error", "ok"]
    assert tools[0].summary.startswith('{"error": {"code": "not_found"')
    assert snapshot.items[1].status == "done"


def test_write_tools_act_on_the_open_process_only(ctl, provider):
    provider.script(reply="ok")
    chat = ctl.chats.create()
    send(ctl, chat.id, "hi", acting_on="p1")
    wait_turn(ctl, chat.id)
    handles = {h.name: h for h in provider.requests[0].tools}
    assert set(handles) == set(READ_TOOLS) | set(WRITE_TOOLS)
    for name in WRITE_TOOLS:
        assert "process" not in handles[name].input_schema.get("properties", {})
        assert handles[name].description.startswith("Acts on p1")


def test_edit_design_writes_the_open_process_and_the_turn_ends_in_one_commit(ctl, provider, workspace, git):
    base = git(workspace, "rev-parse", "HEAD").strip()
    doc = p1_doc(workspace, goal="Upper-case a text and count its words.")
    provider.script(("call", "read_design", {"process": "p2"}), ("call", "edit_design", {"doc": doc}),
                    ("call", "validate_process", {"process": "p1"}),
                    reply="**Shortened** the goal of p1.\n\nNothing else changed.")
    chat = ctl.chats.create()
    turn_id, _ = send(ctl, chat.id, "Shorten the goal", acting_on="p1")
    snapshot = wait_turn(ctl, chat.id)

    assert [r.is_error for _, r in provider.results] == [False, False, False]
    edit = json.loads(provider.results[1][1].text)
    assert edit["path"] == P1_FILE and edit["revision"] == revision(workspace / P1_FILE)
    assert edit["validation"]["ok"] is True
    assert yaml.safe_load((workspace / P1_FILE).read_text())["goal"] == "Upper-case a text and count its words."
    assert ctl.design.saves == [{"pid": "p1", "paths": [P1_FILE], "origin": "chat", "lock_owner": (chat.id, turn_id)}]
    assert ctl.design.commits == [{"pid": "p1", "reason": "chat_turn", "summary": "Shortened the goal of p1.",
                                   "origin": "chat", "trailers": {"Wynd-Chat": chat.id}}]

    commits = git_log(git, workspace, base)
    assert len(commits) == 1
    assert git(workspace, "show", "--name-only", "--format=", commits[0]).split() == [P1_FILE]
    message = git(workspace, "log", "-1", "--format=%B", commits[0])
    assert message.splitlines()[0] == "design(p1): Shortened the goal of p1."
    assert {"Wynd-Origin: chat", "Wynd-Reason: chat_turn", f"Wynd-Chat: {chat.id}"} <= set(message.splitlines())
    assert git(workspace, "status", "--porcelain").strip() == ""

    tools = [i for i in snapshot.items if i.type == "tool"]
    assert [(t.tool, t.write, t.acting_on) for t in tools] == [
        ("read_design", False, None), ("edit_design", True, "p1"), ("validate_process", False, None)]
    commit = snapshot.items[-1]
    assert (commit.type, commit.process_id, commit.sha, commit.turn_id) == ("commit", "p1", commits[0], turn_id)
    assert commit.message.startswith("design(p1): Shortened the goal of p1.")
    last = events(ctl, chat.id)[-1]
    assert (last.event, last.data["status"], last.data["edited"]) == ("turn", "done", ["p1"])


def test_edit_proto_writes_protos_in_scope_and_refuses_a_child_process(ctl, provider, workspace):
    proto = {"kind": "proto_step", "name": "shout", "instruction": "Shout the text.", "inputs": {"text": "string"},
             "outputs": {"text": "string"}}
    upper = yaml.safe_load((workspace / "processes/p1/proto/upper.yaml").read_text())
    provider.script(("call", "edit_proto", {"step": "shout", "doc": proto}),
                    ("call", "edit_proto", {"step": "upper", "doc": {**upper, "instruction": "Upper-case it."}}),
                    ("call", "edit_proto", {"step": "../p2/proto/tag", "doc": proto}), reply="ok")
    chat = ctl.chats.create()
    send(ctl, chat.id, "Add shout", acting_on="p1")
    wait_turn(ctl, chat.id)
    new, upper_result, bad = (r for _, r in provider.results)
    assert not new.is_error and json.loads(new.text)["path"] == "processes/p1/proto/shout.yaml"
    assert yaml.safe_load((workspace / "processes/p1/proto/shout.yaml").read_text()) == proto
    assert not upper_result.is_error and json.loads(upper_result.text)["path"] == "processes/p1/proto/upper.yaml"
    assert bad.is_error and json.loads(bad.text)["error"]["code"] == "invalid"

    provider.script(("call", "edit_proto", {"step": "child", "doc": proto}), reply="no")
    chat = ctl.chats.create()
    send(ctl, chat.id, "Edit the child", acting_on="parent")
    snapshot = wait_turn(ctl, chat.id)
    out_of_scope = provider.results[-1][1]
    assert out_of_scope.is_error and json.loads(out_of_scope.text)["error"]["code"] == "out_of_scope"
    assert [s["pid"] for s in ctl.design.saves] == ["p1", "p1"]
    assert [c["pid"] for c in ctl.design.commits] == ["p1"]                  # nothing edited in `parent`
    assert snapshot.items[-1].type == "tool" and snapshot.items[-1].status == "error"


def test_the_design_lock_is_held_for_the_turn_and_released_after(ctl, provider):
    started, gate = threading.Event(), threading.Event()
    provider.script(("signal", started), ("wait", gate), reply="done")
    chat = ctl.chats.create()
    turn_id, _ = send(ctl, chat.id, "wait", acting_on="p1", client_id="c-1")
    assert started.wait(10)
    assert ctl.design.locked_by("p1") == (chat.id, turn_id)
    assert ctl.chats.snapshot(chat.id).chat.running_turn == turn_id
    assert [c.running_turn for c in ctl.chats.list()] == [turn_id]
    assert send(ctl, chat.id, "wait", acting_on="p1", client_id="c-1")[0] == turn_id      # idempotent resend
    with pytest.raises(TurnInProgress):
        send(ctl, chat.id, "again")
    with pytest.raises(TurnInProgress):
        ctl.chats.delete(chat.id)
    other = ctl.chats.create()
    with pytest.raises(DesignLocked):
        send(ctl, other.id, "edit p1 too", acting_on="p1")
    assert ctl.chats.snapshot(other.id).items == [] and ctl.chats.snapshot(other.id).chat.running_turn is None
    gate.set()
    wait_turn(ctl, chat.id)
    assert ctl.design.locked_by("p1") is None
    assert len(provider.requests) == 1


def test_a_provider_error_ends_the_turn_releases_the_lock_and_still_commits_edits(ctl, provider, workspace, git):
    base = git(workspace, "rev-parse", "HEAD").strip()
    doc = p1_doc(workspace, goal="Edited before the failure.")
    provider.script(("call", "edit_design", {"doc": doc}), ("text", "Now validating"),
                    ("raise", ProviderError("claude-code transport failure: boom", kind="transport",
                                            retryable=True)))
    chat = ctl.chats.create()
    turn_id, _ = send(ctl, chat.id, "Edit then fail", acting_on="p1")
    snapshot = wait_turn(ctl, chat.id)
    assistant = snapshot.items[1]
    assert (assistant.status, assistant.error, assistant.text) == (
        "error", "claude-code transport failure: boom", "Now validating")
    assert ctl.design.locked_by("p1") is None
    assert len(git_log(git, workspace, base)) == 1
    assert snapshot.items[-1].type == "commit"
    assert ctl.design.commits[0]["summary"] == "Now validating"
    assert events(ctl, chat.id)[-1].data == {"turn_id": turn_id, "status": "error", "acting_on": "p1",
                                             "edited": ["p1"], "error": "claude-code transport failure: boom"}


def test_cancel_refuses_further_tool_writes_and_keeps_earlier_edits(ctl, provider, workspace, git):
    base = git(workspace, "rev-parse", "HEAD").strip()
    reached, gate = threading.Event(), threading.Event()
    provider.honour_cancel = False                       # the harness keeps going: the tool guard must refuse
    provider.script(("call", "edit_design", {"doc": p1_doc(workspace, goal="First edit.")}), ("signal", reached),
                    ("wait", gate), ("text", "dropped"),
                    ("call", "edit_design", {"doc": p1_doc(workspace, goal="Second edit.")}), reply="discarded")
    chat = ctl.chats.create()
    turn_id, _ = send(ctl, chat.id, "Edit twice", acting_on="p1")
    assert reached.wait(10)
    ctl.chats.cancel(chat.id)
    gate.set()
    snapshot = wait_turn(ctl, chat.id)

    refused = provider.results[-1][1]
    assert refused.is_error and json.loads(refused.text)["error"]["code"] == "cancelled"
    assert yaml.safe_load((workspace / P1_FILE).read_text())["goal"] == "First edit."
    assert len(ctl.design.saves) == 1
    assert len(git_log(git, workspace, base)) == 1                          # the edit before the cancel
    assistant = snapshot.items[1]
    assert (assistant.status, assistant.text, assistant.error) == ("cancelled", "", None)
    assert [i.type for i in snapshot.items] == ["user", "assistant", "tool", "commit"]
    found = events(ctl, chat.id)
    assert "delta" not in [e.event for e in found]
    assert found[-1].data == {"turn_id": turn_id, "status": "cancelled", "acting_on": "p1", "edited": ["p1"],
                              "error": None}


def test_a_provider_that_honours_cancel_stops_the_turn(ctl, provider):
    reached, gate = threading.Event(), threading.Event()
    provider.script(("signal", reached), ("wait", gate), ("text", "never"), reply="never")
    chat = ctl.chats.create()
    send(ctl, chat.id, "long")
    assert reached.wait(10)
    ctl.chats.cancel(chat.id)
    gate.set()
    snapshot = wait_turn(ctl, chat.id)
    assert (snapshot.items[1].status, snapshot.items[1].text) == ("cancelled", "")
    ctl.chats.cancel(chat.id)                                                # no running turn: nothing to do


def test_start_compile_commits_first_then_submits_a_job_bound_to_the_chat(ctl, provider, workspace, git):
    base = git(workspace, "rev-parse", "HEAD").strip()
    provider.script(("call", "edit_design", {"doc": p1_doc(workspace, goal="Compile me.")}),
                    ("call", "start_compile", {}), reply="Started a compile.")
    chat = ctl.chats.create()
    send(ctl, chat.id, "Edit and compile", acting_on="p1")
    snapshot = wait_turn(ctl, chat.id)

    started = provider.results[-1][1]
    assert not started.is_error, started.text
    job = json.loads(started.text)["job"]
    assert ctl.ctx.runner.submitted == [job["id"]]
    record = records.load(ctl.ctx.stores.runs, job["id"])
    assert (record.job_kind, record.process, record.chat_id) == ("compile", "p1", chat.id)
    assert (record.inputs["accept_proposals"], record.ref) == (False, git(workspace, "rev-parse", "HEAD").strip())
    assert (job["chat_id"], job["kind"]) == (chat.id, "compile")
    assert [(c["reason"], c["origin"]) for c in ctl.design.commits] == [("before_job", "chat"), ("chat_turn", "chat")]
    assert len(git_log(git, workspace, base)) == 1                          # the turn-end commit found nothing
    job_items = [i for i in snapshot.items if i.type == "job"]
    assert [(i.job_id, i.kind, i.process_id) for i in job_items] == [(job["id"], "compile", "p1")]
    assert [i.type for i in snapshot.items] == ["user", "assistant", "tool", "tool", "job"]    # no commit item


def test_a_job_bound_chat_shows_its_job_and_passes_it_as_context(ctl, provider, workspace, git, monkeypatch):
    head = git(workspace, "rev-parse", "HEAD").strip()
    job = Job(id="job_20260922T120000000_aaaaaa", kind="compile", process_id="p1", ref=head,
              status="awaiting_input", created_at="2026-09-22T12:00:00Z",
              session={"state": "awaiting_input", "questions": [{"id": "q1", "status": "pending"},
                                                                {"id": "q2", "status": "pending"},
                                                                {"id": "q3", "status": "answered"}]})

    def get(job_id: str) -> Job:
        if job_id != job.id:
            raise NotFound(f"no job '{job_id}'")
        return job

    monkeypatch.setattr(ctl.jobs, "get", get)
    chat = ctl.chats.create("Compile p1", job_id=job.id)
    ctl.chats.add_job_item(chat.id, job)
    summary = ctl.chats.snapshot(chat.id).chat
    assert summary.job.model_dump() == {"id": job.id, "kind": "compile", "process_id": "p1",
                                        "status": "awaiting_input", "pending_questions": 2}
    provider.script(reply="Two questions are waiting.")
    send(ctl, chat.id, "What is it waiting for?")
    snapshot = wait_turn(ctl, chat.id)
    assert provider.requests[0].context["bound_job"] == job.model_dump(mode="json")
    assert (snapshot.chat.title, snapshot.items[0].type, snapshot.items[0].job_id) == ("Compile p1", "job", job.id)
    assert ctl.chats.create(job_id="job_gone").job is None


def test_send_validates_the_chat_the_text_and_the_process(ctl, provider):
    chat = ctl.chats.create()
    with pytest.raises(NotFound):
        send(ctl, "chat_missing", "hi")
    with pytest.raises(Invalid):
        send(ctl, chat.id, "   ")
    with pytest.raises(NotFound):
        send(ctl, chat.id, "hi", acting_on="nope")
    assert ctl.chats.snapshot(chat.id).items == [] and provider.requests == []


@pytest.mark.parametrize(("name", "message"), [
    ("chat-model", "chat requires an AgentProvider"),
    ("not-a-provider", "chat provider 'not-a-provider' is not installed (WYND_CHAT_PROVIDER)"),
])
def test_the_chat_needs_an_installed_agent_provider(chat_controller, name, message):
    undo = register_for_tests("chat-model", ModelOnly())
    try:
        ctl = chat_controller(WYND_CHAT_PROVIDER=name)
        chat = ctl.chats.create()
        send(ctl, chat.id, "hi", acting_on="p1")
        snapshot = wait_turn(ctl, chat.id)
    finally:
        undo()
    assert (snapshot.items[1].status, snapshot.items[1].error) == ("error", message)
    assert ctl.design.locked_by("p1") is None and ctl.design.commits == []


@pytest.mark.parametrize(("reply", "summary"), [
    ("**Raise** the `fix` loop limit to 5\n\nOnly `process.yaml` changed.", "Raise the fix loop limit to 5"),
    ("\n\n## Done\nmore", "Done"),
    ("Validation passes (only the expected loop-related info notices for the cycle)",
     "Validation passes (only the expected loop-related info"),
    ("x" * 70, "x" * 60),
    ("", ""),
])
def test_the_commit_summary_is_the_first_line_of_the_reply(reply, summary):
    assert _commit_summary(reply) == summary
