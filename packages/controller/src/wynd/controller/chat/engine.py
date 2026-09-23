"""`ChatService` (PLAN §8.1 chat row; `$DRAFTS/06 §7`). Stub; CTL-CHAT.

A turn is one `AgentProvider.run` with provider `WYND_CHAT_PROVIDER` (default claude-code) at tier `WYND_CHAT_TIER`
(default standard), `builtin_tools=[]`, `cancel=turn.cancelled`; a turn that edited the acting-on process ends with a
design commit.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.api.models_web import ChatItem, ChatSnapshot, ChatSummary, SendMessageRequest
    from wynd.controller.chat.events import ChatEvent
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import Job


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

    def list(self, limit: int = 100) -> list[ChatSummary]:
        raise NotImplementedError("PLAN §8.1")

    def create(self, title: str | None = None, *, job_id: str | None = None) -> ChatSummary:
        raise NotImplementedError("PLAN §8.1")

    def snapshot(self, chat_id: str) -> ChatSnapshot:
        raise NotImplementedError("PLAN §8.1")

    def rename(self, chat_id: str, title: str) -> ChatSummary:
        raise NotImplementedError("PLAN §8.1")

    def delete(self, chat_id: str) -> None:
        raise NotImplementedError("PLAN §8.1")

    def send(self, chat_id: str, req: SendMessageRequest) -> tuple[str, ChatItem]:
        """-> (turn_id, user item); starts the turn thread."""
        raise NotImplementedError("PLAN §8.1")

    def cancel(self, chat_id: str) -> None:
        raise NotImplementedError("PLAN §8.1")

    def events(self, chat_id: str, since: int) -> tuple[list[ChatEvent], bool]:
        """-> (events, reset needed)."""
        raise NotImplementedError("PLAN §8.1")

    def run_turn(self, chat_id: str, turn_id: str, text: str, acting_on: str | None) -> None:
        raise NotImplementedError("PLAN §8.1")

    def add_job_item(self, chat_id: str, job: Job) -> None:
        raise NotImplementedError("PLAN §8.1")
