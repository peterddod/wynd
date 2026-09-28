"""The builtin tool library (PLAN §5.1; `$DRAFTS/03 §9.3`): `http_get`, `web_search` (Brave, `BRAVE_API_KEY`),
`workspace_read`, `workspace_write`, `shell` (usable only as `shell.allow(<executables>)`), `now`.

Builtins run inside a step run and reach it through `current_runtime()` (workspace, HTTP client). Bad arguments
(a path outside the workspace, an executable outside the allowlist, a timeout) are `ToolInputError`s, which the model
sees as error results.
"""

from __future__ import annotations

import fnmatch
import functools
import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field

from wynd.runtime.agentic.errors import ToolInputError
from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.http import HttpError
from wynd.runtime.tools.decorator import ToolDecl, ToolSpec, env, tool, tool_spec
from wynd.runtime.tools.toolset import current_runtime

HTTP_GET_LIMIT = 200_000            # bytes of body text returned by http_get
READ_LIMIT = 1_000_000              # bytes returned by workspace_read
SHELL_OUTPUT_LIMIT = 20_000         # characters of stdout and of stderr returned by shell
BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
REFUSED_EXECUTABLES = ("python*", "sh", "bash", "zsh", "dash", "fish", "node", "deno", "perl", "ruby", "php", "env",
                       "xargs")


class HttpGetResult(BaseModel):
    status: int
    content_type: str
    text: str
    truncated: bool


class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str


class ShellToolResult(BaseModel):
    exit_code: int
    stdout: str
    stderr: str


@tool(effects=["network"], idempotent=True)
def http_get(url: str, headers: dict[str, str] | None = None) -> HttpGetResult:
    """Fetch a URL with HTTP GET and return the status, content type and body text (first 200 kB, UTF-8 with
    replacement)."""
    resp = current_runtime().http.get(url, headers=headers, timeout=30)
    content_type = next((v for k, v in resp.headers.items() if k.lower() == "content-type"), "")
    return HttpGetResult(
        status=resp.status,
        content_type=content_type,
        text=resp.body[:HTTP_GET_LIMIT].decode("utf-8", errors="replace"),
        truncated=len(resp.body) > HTTP_GET_LIMIT,
    )


@tool(effects=["network"], idempotent=True, env=["BRAVE_API_KEY"])
def web_search(query: str, count: int = 5) -> list[SearchResult]:
    """Search the web and return the top results (title, url, snippet)."""
    resp = current_runtime().http.get(
        BRAVE_SEARCH_URL,
        params={"q": query, "count": str(min(max(count, 1), 20))},
        headers={"Accept": "application/json", "X-Subscription-Token": env("BRAVE_API_KEY")},
        timeout=30,
    )
    if resp.status >= 400:
        raise HttpError(f"Brave Search returned HTTP {resp.status}", response=resp)
    results = (json.loads(resp.body).get("web") or {}).get("results") or []
    return [SearchResult(title=r.get("title", ""), url=r.get("url", ""), snippet=r.get("description", ""))
            for r in results]


@tool(effects=["filesystem"], idempotent=True)
def workspace_read(path: str) -> str:
    """Read a UTF-8 text file from this run's workspace. `path` is relative to the workspace root."""
    target = _in_workspace(path)
    if not target.is_file():
        raise ToolInputError(f"no such file: {path}")
    data = target.read_bytes()
    truncated = len(data) > READ_LIMIT
    head = data[:READ_LIMIT]
    if b"\x00" in head:
        raise ToolInputError(f"{path} is a binary file, not UTF-8 text")
    try:
        text = head.decode("utf-8")
    except UnicodeDecodeError as e:
        if not truncated or e.start < len(head) - 3:     # only a character cut by the limit is forgiven
            raise ToolInputError(f"{path} is not UTF-8 text") from e
        text = head[:e.start].decode("utf-8")
    if truncated:
        text += f"\n…[truncated {len(data) - READ_LIMIT} bytes]"
    return text


@tool(effects=["filesystem"], idempotent=True)
def workspace_write(path: str, content: str) -> str:
    """Write UTF-8 text to a file in this run's workspace, creating directories; returns the relative path."""
    target = _in_workspace(path)
    if target.is_dir():
        raise ToolInputError(f"{path} is a directory")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return target.relative_to(Path(current_runtime().workspace).resolve()).as_posix()


@tool(effects=["shell"])
def shell(
    command: Annotated[list[str], Field(description="argv: the executable, then its arguments (no shell syntax)")],
    timeout_s: Annotated[int, Field(ge=1, le=600)] = 60,
) -> ShellToolResult:
    """Run a command (argv list, no shell) in this run's workspace; stdout and stderr are truncated to 20 kB each."""
    return _run_command(command, timeout_s, ())


def _allow(*executables: str) -> ToolDecl:
    """`shell.allow("pdftotext", ...)`: the shell tool restricted to these executables (matched against the basename
    of argv[0]). Interpreters and shells are refused, so the model can never run code it wrote."""
    if not executables:
        raise StepDefinitionError("shell.allow(...) needs at least one executable")
    for exe in executables:
        if not isinstance(exe, str) or not exe or "/" in exe or os.sep in exe:
            raise StepDefinitionError(f"shell.allow: {exe!r} must be an executable name, not a path")
        if any(fnmatch.fnmatchcase(exe, pattern) for pattern in REFUSED_EXECUTABLES):
            raise StepDefinitionError(
                f"shell.allow: {exe!r} is an interpreter or shell; the shell tool may only run fixed executables, "
                "never model-written code (SPEC §3.8)"
            )
    allow = tuple(executables)

    @functools.wraps(shell)
    def run(command: list[str], timeout_s: int = 60) -> ShellToolResult:
        return _run_command(command, timeout_s, allow)

    return ToolDecl(name="shell", effects=("shell",), idempotent=False, env=(), allow=allow, fn=run)


shell.allow = _allow


@tool
def now() -> str:
    """Return the current UTC time as an ISO-8601 timestamp."""
    return datetime.now(UTC).isoformat(timespec="seconds")


BUILTINS: list[ToolSpec] = [
    tool_spec(f, "builtin") for f in (http_get, web_search, workspace_read, workspace_write, shell, now)
]


def _in_workspace(path: str) -> Path:
    workspace = Path(current_runtime().workspace).resolve()
    target = (workspace / path).resolve()
    if not target.is_relative_to(workspace):
        raise ToolInputError(f"path escapes the workspace: {path}")
    return target


def _run_command(command: list[str], timeout_s: int, allow: tuple[str, ...]) -> ShellToolResult:
    if not command:
        raise ToolInputError("command must be a non-empty argv list")
    exe = os.path.basename(command[0])
    if exe not in allow:
        permitted = ", ".join(allow) if allow else "nothing (declare the tool as shell.allow(...))"
        raise ToolInputError(f"{exe!r} is not allowed; this step's shell tool may run: {permitted}")
    workspace = Path(current_runtime().workspace)
    if os.sep in command[0] and (workspace / command[0]).resolve().is_relative_to(workspace.resolve()):
        raise ToolInputError("the shell tool runs installed executables, not files in the workspace")
    try:
        proc = subprocess.run(command, cwd=workspace, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", stdin=subprocess.DEVNULL, timeout=timeout_s, shell=False)
    except subprocess.TimeoutExpired as e:
        raise ToolInputError(f"timed out after {timeout_s}s") from e
    except FileNotFoundError as e:
        raise ToolInputError(f"command not found: {command[0]}") from e
    return ShellToolResult(exit_code=proc.returncode, stdout=_head(proc.stdout), stderr=_head(proc.stderr))


def _head(text: str) -> str:
    if len(text) <= SHELL_OUTPUT_LIMIT:
        return text
    return text[:SHELL_OUTPUT_LIMIT] + f"…[truncated {len(text) - SHELL_OUTPUT_LIMIT} chars]"
