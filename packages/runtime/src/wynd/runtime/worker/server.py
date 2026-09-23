"""The worker side of the venv protocol: stdio claim, per-run output capture, `init` snapshot verification and
method dispatch incl. `edge.check` (PLAN §3.12, §3.6; `$DRAFTS/02 §5.1`)."""

from __future__ import annotations

from typing import Any, TextIO


class WorkerServer:
    """Handles one JSON-RPC request at a time on the main thread."""

    def __init__(self, out: TextIO) -> None:
        raise NotImplementedError("PLAN §3.12")

    def handle_line(self, line: str) -> dict[str, Any] | None:
        raise NotImplementedError("PLAN §3.12")

    def write(self, obj: dict[str, Any]) -> None:
        raise NotImplementedError("PLAN §3.12")


def main() -> None:
    """Entry point of `python -m wynd.runtime.worker`: claim stdio, serve requests until EOF or `shutdown`."""
    raise NotImplementedError("PLAN §3.12")
