"""Per-chat in-memory event log (PLAN §8.1 chat row; `$DRAFTS/06 §7.2`, `$DRAFTS/07 §12.4`). Stub; CTL-CHAT.

A ring buffer of the last 1000 `(id, event, data)` per chat; `id` only goes up; event types `item`, `delta`, `turn`,
`reset`. A reader whose cursor fell out of the buffer gets `reset`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ChatEvent:
    id: int
    event: Literal["item", "delta", "turn", "reset"]
    data: dict[str, Any]


class ChatEventLog:
    def __init__(self, capacity: int = 1000) -> None:
        raise NotImplementedError("PLAN §8.1")

    def append(self, event: str, data: dict[str, Any]) -> int:
        """-> the new event id."""
        raise NotImplementedError("PLAN §8.1")

    def since(self, cursor: int) -> tuple[list[ChatEvent], bool]:
        """-> (events after `cursor`, reset needed)."""
        raise NotImplementedError("PLAN §8.1")
