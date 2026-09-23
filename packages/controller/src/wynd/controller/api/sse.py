"""Server-sent events (PLAN §3.21; `$DRAFTS/06 §8.2`). Stub; CTL-API.

Headers `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`; first line
`retry: 2000`; frames `id: <n>\\nevent: <type>\\ndata: <single-line json>\\n\\n`; `: ping` every 15 s. Every stream polls
its store (no pub/sub).
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Iterable
from typing import Any


def frames(items: Iterable[tuple[int, str, dict]]) -> str:
    """Render `(id, event, data)` items as SSE frames."""
    raise NotImplementedError("PLAN §3.21")


def poll_stream(
    request: Any,
    fetch: Callable[[int], tuple[list[tuple[int, str, dict]], bool]],
    since: int,
) -> AsyncIterator[str]:
    """Poll `fetch(cursor)` (in the threadpool) until it reports done or the client disconnects."""
    raise NotImplementedError("PLAN §3.21")
