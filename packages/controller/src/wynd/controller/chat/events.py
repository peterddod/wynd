"""Per-chat in-memory event log (PLAN §8.1 chat row; `$DRAFTS/06 §7.2`, `$DRAFTS/07 §12.4`).

A ring buffer of the last 1000 `(id, event, data)` per chat; `id` only goes up; event types `item`, `delta`, `turn`,
`reset`. A reader whose cursor fell out of the buffer, or is ahead of it (the log restarted with the process), gets
`reset`: it refetches the snapshot and continues from the snapshot's cursor.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal


@dataclass(frozen=True)
class ChatEvent:
    id: int
    event: Literal["item", "delta", "turn", "reset"]
    data: dict[str, Any]


class ChatEventLog:
    def __init__(self, capacity: int = 1000) -> None:
        self._events: deque[ChatEvent] = deque(maxlen=capacity)
        self._last = 0
        self._lock = threading.Lock()

    @property
    def last_id(self) -> int:
        """The id of the newest event (0 before the first); a snapshot's cursor."""
        with self._lock:
            return self._last

    def append(self, event: str, data: dict[str, Any]) -> int:
        """-> the new event id."""
        with self._lock:
            self._last += 1
            self._events.append(ChatEvent(self._last, event, data))
            return self._last

    def since(self, cursor: int) -> tuple[list[ChatEvent], bool]:
        """-> (events after `cursor`, reset needed)."""
        with self._lock:
            oldest = self._events[0].id if self._events else self._last + 1
            if cursor > self._last or cursor < oldest - 1:
                return [], True
            return [e for e in self._events if e.id > cursor], False
