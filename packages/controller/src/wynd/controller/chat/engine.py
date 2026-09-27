"""`ChatService` (PLAN §8.1 chat row, §15 item 52; `$DRAFTS/06 §7`).

A chat is a record with no process; every user message carries its own `acting_on` pointer (the open process or
null). A turn is one `AgentProvider.run`: provider `WYND_CHAT_PROVIDER` (default claude-code) at tier `WYND_CHAT_TIER`
(default standard), `builtin_tools=[]`, `cancel=turn.cancelled`, raw prompt mode (`chat/prompt.py`), with the
persisted history (the last 30 user/assistant items, compacted) in the context: Claude Code sessions are never
resumed. Provider events stream into the chat (`text` -> `delta` on the assistant item, `tool_use`/`tool_result` ->
tool items). The design lock on `acting_on` is held for the whole turn, and a turn that edited it ends with one design
commit (`Wynd-Origin: chat`, `Wynd-Chat: <chat id>`), even when the provider failed after writing.

Chat records live in the controller `DocStore` (`chat/store.py`); this process keeps the live copy of every chat it
touched, and the per-chat event logs, in memory. One lock guards both, so a snapshot's items and cursor agree.
"""

from __future__ import annotations

import functools
import json
import threading
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from wynd.controller.api.models_web import (
    AssistantItem,
    ChatJob,
    ChatSnapshot,
    ChatSummary,
    CommitItem,
    JobItem,
    NoticeItem,
    ToolItem,
    UserItem,
)
from wynd.controller.chat import store
from wynd.controller.chat.events import ChatEventLog
from wynd.controller.errors import Invalid, NotFound, TurnInProgress, Unavailable, WyndError
from wynd.runtime.usage import Usage

if TYPE_CHECKING:
    from collections.abc import Callable

    from pydantic import BaseModel

    from wynd.controller.api.models_web import ChatItem, SendMessageRequest
    from wynd.controller.chat.events import ChatEvent
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import Job
    from wynd.runtime.providers.types import AgentResponse

DEFAULT_CHAT_TIER = "standard"
HISTORY_ITEMS = 30
HISTORY_TEXT_LIMIT = 2000
TITLE_LIMIT = 60
TOOL_SUMMARY_LIMIT = 200
COMMIT_SUMMARY_LIMIT = 60
CLOSE_TIMEOUT_S = 30.0


@dataclass
class TurnState:
    chat_id: str
    turn_id: str
    edited: set[str] = field(default_factory=set)
    cancelled: threading.Event = field(default_factory=threading.Event)


class ChatService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl
        self._lock = threading.RLock()
        self._chats: dict[str, dict[str, Any]] = {}          # live records of the chats this process touched
        self._logs: dict[str, ChatEventLog] = {}
        self._turns: dict[str, TurnState] = {}               # running turn per chat
        self._threads: dict[str, threading.Thread] = {}

    def list(self, limit: int = 100) -> list[ChatSummary]:
        """Newest `updated_at` first."""
        with self._lock:
            records = {record["id"]: record for record in store.list_records(self.ctx.docs)}
            records.update(self._chats)
            ordered = sorted(records.values(), key=lambda r: (store.parse_time(r["updated_at"]), r["id"]),
                             reverse=True)
            return [self._summary(record) for record in ordered[:limit]]

    def create(self, title: str | None = None, *, job_id: str | None = None) -> ChatSummary:
        from wynd.runtime.ids import new_id

        title = (title or "").strip() or store.DEFAULT_TITLE
        record = store.new_record(new_id("chat"), title, job_id, self.ctx.clock())
        with self._lock:
            self._chats[record["id"]] = record
            store.save(self.ctx.docs, record)
            return self._summary(record)

    def snapshot(self, chat_id: str) -> ChatSnapshot:
        with self._lock:
            record = self._record(chat_id)
            items = [store.to_item(item) for item in record["items"]]
            return ChatSnapshot(chat=self._summary(record), items=items, cursor=self._log(chat_id).last_id)

    def rename(self, chat_id: str, title: str) -> ChatSummary:
        title = title.strip()
        if not title:
            raise Invalid("a chat title cannot be empty")
        with self._lock:
            record = self._record(chat_id)
            record["title"] = title
            record["updated_at"] = store.timestamp(self.ctx.clock())
            store.save(self.ctx.docs, record)
            return self._summary(record)

    def delete(self, chat_id: str) -> None:
        with self._lock:
            self._record(chat_id)
            turn = self._turns.get(chat_id)
            if turn is not None:
                raise TurnInProgress(f"chat {chat_id} is running turn {turn.turn_id}; cancel it first",
                                     details={"turn_id": turn.turn_id})
            store.delete(self.ctx.docs, chat_id)
            self._chats.pop(chat_id, None)
            self._logs.pop(chat_id, None)

    def send(self, chat_id: str, req: SendMessageRequest) -> tuple[str, ChatItem]:
        """-> (turn_id, user item); starts the turn thread. A repeated `client_id` returns the existing turn."""
        from wynd.runtime.ids import new_id

        if req.acting_on is not None and req.acting_on not in self.ctx.workspace().processes:
            raise NotFound(f"unknown process '{req.acting_on}'")
        with self._lock:
            record = self._record(chat_id)
            seen = record["client_ids"].get(req.client_id)
            if seen is not None:
                return seen["turn_id"], store.to_item(store.find_item(record, seen["item_id"]))
            if not req.text.strip():
                raise Invalid("the message is empty")
            running = self._turns.get(chat_id)
            if running is not None:
                raise TurnInProgress(f"chat {chat_id} is already running turn {running.turn_id}",
                                     details={"turn_id": running.turn_id})
            turn = TurnState(chat_id, new_id("turn"))
            self._turns[chat_id] = turn
        try:
            if req.acting_on is not None:
                self.ctl.design.lock(req.acting_on, chat_id, turn.turn_id)
        except BaseException:
            with self._lock:
                self._turns.pop(chat_id, None)
            raise
        with self._lock:
            first = not any(item["type"] == "user" for item in record["items"])
            item = store.append_item(record, UserItem, self.ctx.clock(), text=req.text, acting_on=req.acting_on)
            record["client_ids"][req.client_id] = {"turn_id": turn.turn_id, "item_id": item["id"]}
            if first and record["title"] == store.DEFAULT_TITLE:
                record["title"] = " ".join(req.text.split())[:TITLE_LIMIT]
            store.save(self.ctx.docs, record)
            self._emit(chat_id, "item", {"item": dict(item)})
            self._emit_turn(chat_id, turn, "running", req.acting_on, None)
            thread = threading.Thread(target=self.run_turn, args=(chat_id, turn.turn_id, req.text, req.acting_on),
                                      name=f"wynd-{turn.turn_id}", daemon=True)
            self._threads[chat_id] = thread
        thread.start()
        return turn.turn_id, store.to_item(item)

    def cancel(self, chat_id: str) -> None:
        """Stops the running turn, if any: every later tool call is refused and later deltas are dropped; the turn
        ends `cancelled` and still commits the edits made before the cancel."""
        with self._lock:
            self._record(chat_id)
            turn = self._turns.get(chat_id)
        if turn is not None:
            turn.cancelled.set()

    def events(self, chat_id: str, since: int) -> tuple[list[ChatEvent], bool]:
        """-> (events after `since`, reset needed)."""
        with self._lock:
            self._record(chat_id)
            log = self._log(chat_id)
        return log.since(since)

    def run_turn(self, chat_id: str, turn_id: str, text: str, acting_on: str | None) -> None:
        """The blocking core of a turn. `send` has registered the turn, taken the design lock and appended the user
        item (the chat's last user item is this turn's message); the design lock is released here."""
        with self._lock:
            record = self._record(chat_id)
            turn = self._turns.setdefault(chat_id, TurnState(chat_id, turn_id))
            if turn.turn_id != turn_id:
                raise TurnInProgress(f"chat {chat_id} is running turn {turn.turn_id}", details={"turn_id": turn_id})
            history = _history(record["items"])
            assistant = store.append_item(record, AssistantItem, self.ctx.clock(), turn_id=turn_id, status="streaming")
            self._emit(chat_id, "item", {"item": dict(assistant)})
        tools: dict[str, tuple[str, float]] = {}              # tool_use id -> (tool item id, monotonic start)
        status, error = "done", None
        try:
            reply, usage = None, None
            try:
                on_event = functools.partial(self._on_agent_event, chat_id, turn, acting_on, assistant["id"], tools)
                response = self._run_provider(chat_id, turn, text, acting_on, history, on_event)
                usage = response.usage
                if turn.cancelled.is_set():
                    status = "cancelled"                       # the provider did not stop: its result is discarded
                else:
                    reply = _reply(response)
            except Exception as err:
                status, error = ("cancelled", None) if turn.cancelled.is_set() else ("error", _message(err))
            final = self._finish(chat_id, assistant["id"], tools, status, error, reply, usage)
            if acting_on is not None and acting_on in turn.edited:
                self._commit(chat_id, turn, acting_on, final)
        finally:
            try:
                if acting_on is not None:
                    self.ctl.design.unlock(acting_on, turn_id)
            finally:
                self._end(chat_id, turn, status, error, acting_on)

    def add_job_item(self, chat_id: str, job: Job) -> None:
        with self._lock:
            self._add(chat_id, JobItem, job_id=job.id, kind=job.kind, process_id=job.process_id)
            store.save(self.ctx.docs, self._record(chat_id))

    def close(self) -> None:
        """Cancel running turns and wait for their threads (`Controller.close`)."""
        with self._lock:
            for turn in self._turns.values():
                turn.cancelled.set()
            threads = list(self._threads.values())
        for thread in threads:
            thread.join(timeout=CLOSE_TIMEOUT_S)

    # --- a turn --------------------------------------------------------------------------------------------------

    def _run_provider(self, chat_id: str, turn: TurnState, text: str, acting_on: str | None,
                      history: list[dict[str, Any]], on_event: Callable[[dict[str, Any]], None]) -> AgentResponse:
        from wynd.controller.chat.prompt import CHAT_REPLY_SCHEMA, SYSTEM_INSTRUCTION, render_user_prompt
        from wynd.controller.chat.tools import build_tools, process_file
        from wynd.runtime.errors import StepFailure
        from wynd.runtime.providers import provider_info, resolve_model
        from wynd.runtime.providers.types import AgentRequest
        from wynd.spec.base import DEFAULT_PROVIDER, DEFAULT_THINKING

        name = self.ctx.env.get("WYND_CHAT_PROVIDER") or DEFAULT_PROVIDER
        tier = self.ctx.env.get("WYND_CHAT_TIER") or DEFAULT_CHAT_TIER
        try:
            kind = provider_info(name).kind
        except KeyError:
            raise Unavailable(f"chat provider '{name}' is not installed (WYND_CHAT_PROVIDER)") from None
        if kind != "agent":
            raise Unavailable("chat requires an AgentProvider", details={"provider": name, "kind": kind})
        try:
            model_id = resolve_model(name, tier, self.ctx.stores.registry)
        except StepFailure as err:
            raise Unavailable(str(err)) from None
        context = {
            "acting_on": acting_on,
            "acting_on_design": process_file(self.ctx, acting_on)[1] if acting_on is not None else None,
            "bound_job": self._bound_job(chat_id),
            "history": history,
        }
        workspace = self.ctx.state_dir / "chats" / chat_id    # scratch cwd, never the repository
        workspace.mkdir(parents=True, exist_ok=True)
        request = AgentRequest(
            model_id=model_id,
            thinking=DEFAULT_THINKING,
            instruction=SYSTEM_INSTRUCTION,
            context=context,
            input={"message": text},
            output_schema=CHAT_REPLY_SCHEMA,
            tools=build_tools(self.ctl, acting_on, turn),
            mcp_servers=[],
            workspace=workspace,
            builtin_tools=[],
            prompt=render_user_prompt(context, text),
            on_event=on_event,
            cancel=turn.cancelled,
        )
        return self.ctx.load_provider(name).run(request)

    def _on_agent_event(self, chat_id: str, turn: TurnState, acting_on: str | None, assistant_id: str,
                        tools: dict[str, tuple[str, float]], event: dict[str, Any]) -> None:
        from wynd.controller.chat.tools import WRITE_TOOLS

        if turn.cancelled.is_set():
            return                                             # later deltas and tool activity are dropped
        with self._lock:
            record = self._record(chat_id)
            match event.get("type"):
                case "text":
                    item = store.find_item(record, assistant_id)
                    delta = ("\n\n" if item["text"] else "") + str(event.get("text") or "")
                    item["text"] += delta
                    self._emit(chat_id, "delta", {"item_id": assistant_id, "text": delta})
                case "tool_use":
                    name = str(event.get("name") or "")
                    write = name in WRITE_TOOLS
                    item = store.append_item(record, ToolItem, self.ctx.clock(), turn_id=turn.turn_id, tool=name,
                                             args=event.get("input"), write=write,
                                             acting_on=acting_on if write else None, status="running")
                    tools[str(event.get("id"))] = (item["id"], time.monotonic())
                    self._emit(chat_id, "item", {"item": dict(item)})
                case "tool_result":
                    started = tools.pop(str(event.get("id")), None)
                    if started is None:
                        return
                    item = store.find_item(record, started[0])
                    item.update(status="error" if event.get("is_error") else "ok",
                                summary=_text(event.get("content"))[:TOOL_SUMMARY_LIMIT],
                                duration_ms=round((time.monotonic() - started[1]) * 1000, 3))
                    self._emit(chat_id, "item", {"item": dict(item)})

    def _finish(self, chat_id: str, assistant_id: str, tools: dict[str, tuple[str, float]], status: str,
                error: str | None, reply: str | None, usage: Usage | None) -> str:
        """Close the turn's items; -> the assistant's final text."""
        with self._lock:
            record = self._record(chat_id)
            for item_id, _ in tools.values():                  # calls that never reported a result
                item = store.find_item(record, item_id)
                item.update(status="error", summary="no result: the turn ended first")
                self._emit(chat_id, "item", {"item": dict(item)})
            item = store.find_item(record, assistant_id)
            item.update(text=item["text"] if reply is None else reply, status=status, error=error,
                        usage=None if usage is None else usage.model_dump(mode="json"))
            if usage is not None:
                totals = record["usage_totals"]
                total = usage if totals is None else Usage.model_validate(totals) + usage
                record["usage_totals"] = total.model_dump(mode="json")
            record["updated_at"] = store.timestamp(self.ctx.clock())
            self._emit(chat_id, "item", {"item": dict(item)})
            return item["text"]

    def _commit(self, chat_id: str, turn: TurnState, pid: str, text: str) -> None:
        """The commit boundary of a turn that edited `pid`."""
        try:
            info = self.ctl.design.commit(pid, reason="chat_turn", summary=_commit_summary(text), origin="chat",
                                          extra_trailers={"Wynd-Chat": chat_id})
        except Exception as err:
            with self._lock:
                self._add(chat_id, NoticeItem, level="error",
                          text=f"The edits to {pid} are saved but were not committed: {_message(err)}")
            return
        if info is not None:
            with self._lock:
                self._add(chat_id, CommitItem, turn_id=turn.turn_id, process_id=pid, sha=info.sha,
                          message=info.message or info.subject)

    def _end(self, chat_id: str, turn: TurnState, status: str, error: str | None, acting_on: str | None) -> None:
        with self._lock:
            try:
                self._emit_turn(chat_id, turn, status, acting_on, error)
                store.save(self.ctx.docs, self._record(chat_id))
            finally:
                self._turns.pop(chat_id, None)
                self._threads.pop(chat_id, None)

    def _bound_job(self, chat_id: str) -> dict[str, Any] | None:
        with self._lock:
            job_id = self._record(chat_id).get("job_id")
        if job_id is None:
            return None
        try:
            return self.ctl.jobs.get(job_id).model_dump(mode="json")
        except NotFound:
            return None

    # --- records and events (callers hold self._lock) ------------------------------------------------------------

    def _record(self, chat_id: str) -> dict[str, Any]:
        record = self._chats.get(chat_id)
        if record is None:
            record = _settle(store.load(self.ctx.docs, chat_id))
            self._chats[chat_id] = record
        return record

    def _log(self, chat_id: str) -> ChatEventLog:
        return self._logs.setdefault(chat_id, ChatEventLog())

    def _add(self, chat_id: str, model: type[BaseModel], **fields: Any) -> None:
        item = store.append_item(self._record(chat_id), model, self.ctx.clock(), **fields)
        self._emit(chat_id, "item", {"item": dict(item)})

    def _emit(self, chat_id: str, event: str, data: dict[str, Any]) -> None:
        self._log(chat_id).append(event, data)

    def _emit_turn(self, chat_id: str, turn: TurnState, status: str, acting_on: str | None,
                   error: str | None) -> None:
        self._emit(chat_id, "turn", {"turn_id": turn.turn_id, "status": status, "acting_on": acting_on,
                                     "edited": sorted(turn.edited), "error": error})

    def _summary(self, record: dict[str, Any]) -> ChatSummary:
        turn = self._turns.get(record["id"])
        return ChatSummary(
            id=record["id"],
            title=record["title"],
            created_at=record["created_at"],
            updated_at=record["updated_at"],
            running_turn=None if turn is None else turn.turn_id,
            job=self._chat_job(record.get("job_id")),
            usage_totals=record.get("usage_totals"),
        )

    def _chat_job(self, job_id: str | None) -> ChatJob | None:
        if job_id is None:
            return None
        try:
            job = self.ctl.jobs.get(job_id)
        except NotFound:
            return None
        pending = sum(1 for q in (job.session or {}).get("questions", []) if q.get("status") == "pending")
        return ChatJob(id=job.id, kind=job.kind, process_id=job.process_id, status=job.status,
                       pending_questions=pending)


def _settle(record: dict[str, Any]) -> dict[str, Any]:
    """A record read from the store has no running turn in this process: items a crashed turn left streaming or
    running are closed as errors."""
    for item in record["items"]:
        match item:
            case {"type": "assistant", "status": "streaming"}:
                item.update(status="error", error="interrupted: the controller stopped during this turn")
            case {"type": "tool", "status": "running"}:
                item.update(status="error", summary="interrupted: the controller stopped during this turn")
    return record


def _history(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The last 30 user/assistant items before the chat's last user item (this turn's message), compacted to
    `{role, text, acting_on, tools}`."""
    last_user = max((i for i, item in enumerate(items) if item["type"] == "user"), default=len(items))
    tools: dict[str, list[str]] = {}
    for item in items:
        if item["type"] == "tool":
            tools.setdefault(item["turn_id"], []).append(item["tool"])
    prior = [item for item in items[:last_user] if item["type"] in ("user", "assistant")][-HISTORY_ITEMS:]
    history = []
    for item in prior:
        text = item["text"]
        if item["type"] == "assistant" and item["status"] != "done":
            text += f" [{item['status']}" + (f": {item['error']}]" if item.get("error") else "]")
        history.append({
            "role": item["type"],
            "text": text[:HISTORY_TEXT_LIMIT],
            "acting_on": item.get("acting_on"),
            "tools": tools.get(item.get("turn_id") or "", []) if item["type"] == "assistant" else [],
        })
    return history


def _reply(response: AgentResponse) -> str | None:
    output = response.structured_output
    reply = output.get("reply") if isinstance(output, dict) else None
    return reply if isinstance(reply, str) else None


def _message(err: Exception) -> str:
    if isinstance(err, WyndError):
        return err.message
    return str(err) or type(err).__name__


def _text(content: Any) -> str:
    if content is None:
        return ""
    return content if isinstance(content, str) else json.dumps(content, ensure_ascii=False, default=str)


def _commit_summary(text: str) -> str:
    """The commit summary of a chat turn: the reply's first non-empty line without Markdown markup, cut to 60
    characters at a word boundary (the system instruction asks for a short first line)."""
    line = next((s for s in (_plain(line) for line in text.splitlines()) if s), "")
    if len(line) <= COMMIT_SUMMARY_LIMIT:
        return line
    cut = line[:COMMIT_SUMMARY_LIMIT]
    return cut.rsplit(" ", 1)[0] if " " in cut else cut


def _plain(line: str) -> str:
    return line.strip().lstrip("#>-* ").replace("**", "").replace("`", "").strip()
