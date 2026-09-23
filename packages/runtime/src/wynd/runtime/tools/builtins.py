"""The builtin tool library (PLAN §5.1; `$DRAFTS/03 §9.3`): `http_get`, `web_search` (Brave, `BRAVE_API_KEY`),
`workspace_read`, `workspace_write`, `shell` (usable only as `shell.allow(<executables>)`), `now`."""

from __future__ import annotations

from typing import Any


def http_get(url: str, headers: dict[str, str] | None = None) -> Any:
    raise NotImplementedError("PLAN §5.1")


def web_search(query: str, count: int = 5) -> Any:
    raise NotImplementedError("PLAN §5.1")


def workspace_read(path: str) -> str:
    raise NotImplementedError("PLAN §5.1")


def workspace_write(path: str, content: str) -> str:
    raise NotImplementedError("PLAN §5.1")


def shell(command: list[str], timeout_s: int = 60) -> Any:
    raise NotImplementedError("PLAN §5.1")


def now() -> str:
    raise NotImplementedError("PLAN §5.1")


BUILTINS: list[Any] = []   # ToolSpec per builtin; filled by RT-TOOLS
