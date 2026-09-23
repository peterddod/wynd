"""`RunManager`: the run queue, per-run state and event buffers, retention and drain (PLAN §3.17;
`$DRAFTS/03 §13.2`)."""

from __future__ import annotations

from typing import Any


class RunManager:
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §3.17")
