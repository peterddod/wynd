"""The `claude-code` AgentProvider over claude-agent-sdk (PLAN §3.15, §1.4; `$DRAFTS/03 §7`).

`claude_agent_sdk` is imported only inside function bodies: the runtime never depends on it; the provider's env
fragment declares it for the step venvs that need it.

Step tools reach the harness as one in-process SDK MCP server (`wynd`); built-ins are off unless the lock lists
`builtin_tools`; settings, CLAUDE.md, hooks and MCP configs of the host are ignored. A validation retry resumes the
same session statelessly from the transcript captured in `AgentResponse.session`; the local session file is deleted
after every call. Auth is the CLI's own precedence (`ANTHROPIC_API_KEY` > `CLAUDE_CODE_OAUTH_TOKEN` > login).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from wynd.runtime import __version__
from wynd.runtime.agentic.prompt import render_prompt
from wynd.runtime.agentic.schema import unwrap_output, wrap_output_schema
from wynd.runtime.providers.types import AgentRequest, AgentResponse, ProviderError, ToolHandle
from wynd.runtime.usage import Usage
from wynd.spec.fragments import EnvFragment, EnvGroup, EnvVar

log = logging.getLogger("wynd.provider.claude_code")

SERVER = "wynd"
EFFORT = {"none": "low", "low": "low", "medium": "medium", "high": "high"}
BUILTIN_EFFECTS = {"Read": ["filesystem"], "Glob": ["filesystem"], "Grep": ["filesystem"], "Edit": ["filesystem"],
                   "Write": ["filesystem"], "NotebookEdit": ["filesystem"], "Bash": ["shell"],
                   "WebFetch": ["network"], "WebSearch": ["network"]}
AUTH_HINT = ("claude-code is not authenticated: log in with `claude` (local) or set CLAUDE_CODE_OAUTH_TOKEN "
             "(`claude setup-token`) or ANTHROPIC_API_KEY. ")
TRANSCRIPT_TEXT_LIMIT = 4000
NOTE_LIMIT = 500


def _sdk() -> Any:
    try:
        import claude_agent_sdk
    except ImportError as e:
        raise ProviderError("claude-code provider needs claude-agent-sdk in this venv; it is installed from the "
                            "provider's env fragment (is the step's venv built from the effective fragment?)",
                            kind="unavailable", retryable=False) from e
    return claude_agent_sdk


def _find_cli(sdk: Any) -> str | None:
    """The CLI the SDK will run: its bundled binary, else `claude` on PATH."""
    bundled = Path(sdk.__file__).parent / "_bundled"
    for name in ("claude", "claude.exe"):
        if (bundled / name).is_file():
            return str(bundled / name)
    return shutil.which("claude")


def check_ready() -> tuple[bool, str]:
    """`check_provider("claude-code")`: the SDK imports and a Claude Code CLI is found. No model call."""
    try:
        sdk = _sdk()
    except ProviderError as e:
        return False, str(e)
    cli = _find_cli(sdk)
    if cli is None:
        return False, ("claude-agent-sdk is installed but no Claude Code CLI was found "
                       "(no bundled binary, no `claude` on PATH)")
    return True, f"claude-agent-sdk {sdk.__version__}, Claude Code CLI {cli}"


class _TranscriptStore:
    """Minimal duck-typed SessionStore (append + load only): captures the main transcript for stateless resume."""

    def __init__(self, entries: list[dict[str, Any]] | None = None) -> None:
        self.entries: list[dict[str, Any]] = list(entries or [])

    async def append(self, key: Mapping[str, Any], entries: list[dict[str, Any]]) -> None:
        if key.get("subpath") is None:
            self.entries.extend(entries)

    async def load(self, key: Mapping[str, Any]) -> list[dict[str, Any]] | None:
        return list(self.entries) if key.get("subpath") is None and self.entries else None


@dataclass
class _State:
    calls: int = 0                        # bridged tool invocations in this call
    failure: Exception | None = None      # set by the bridge when a tool invocation raised: the harness aborts


class ClaudeCodeProvider:
    name: ClassVar[str] = "claude-code"
    kind: ClassVar[Literal["model", "agent"]] = "agent"
    default_tiers: ClassVar[dict[str, str]] = {"cheap": "haiku", "standard": "sonnet", "strong": "opus"}
    env_fragment: ClassVar[EnvFragment] = EnvFragment(
        deps=["claude-agent-sdk>=0.2.157,<0.3"],
        requires="glibc",
        vars=[
            EnvVar(
                name="CLAUDE_CODE_OAUTH_TOKEN",
                description="Claude Code subscription token (dev key, `claude setup-token`). Locally the logged-in "
                "`claude` CLI is used when neither this nor ANTHROPIC_API_KEY is set.",
                secret=True,
                required=False,
                one_of="claude-code-auth",
                used_by=["provider:claude-code"],
            ),
            EnvVar(
                name="ANTHROPIC_API_KEY",
                description="Anthropic API key; alternative to CLAUDE_CODE_OAUTH_TOKEN (takes precedence if both are "
                "set).",
                secret=True,
                required=False,
                one_of="claude-code-auth",
                used_by=["provider:claude-code"],
            ),
        ],
        groups={
            "claude-code-auth": EnvGroup(
                description="Claude Code authentication: one of CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY "
                "(locally the logged-in `claude` CLI suffices).",
                modes=["image"],
            )
        },
    )

    def __init__(self, tiers: Mapping[str, str], *, query_fn: Callable[..., Any] | None = None) -> None:
        self._tiers = dict(tiers)
        self._query_fn = query_fn        # test seam: replaces claude_agent_sdk.query (an async iterator of messages)

    def tiers(self) -> dict[str, str]:
        return dict(self._tiers)

    def run(self, req: AgentRequest) -> AgentResponse:
        return asyncio.run(self._run(req))        # worker threads have no running loop; one loop per call

    async def _run(self, req: AgentRequest) -> AgentResponse:
        sdk = _sdk()
        query = self._query_fn or sdk.query
        state = _State()
        prev = req.continuation.session if req.continuation else None
        store = _TranscriptStore(prev["entries"] if prev else None)
        system, user = render_prompt(req.instruction, req.context, req.input, raw_prompt=req.prompt)
        builtins = list(req.builtin_tools)
        opts = sdk.ClaudeAgentOptions(
            model=req.model_id,
            system_prompt=({"type": "preset", "preset": "claude_code", "append": system} if builtins else system),
            tools=builtins,
            mcp_servers=({SERVER: sdk.create_sdk_mcp_server(SERVER, tools=[_bridge(sdk, h, state) for h in req.tools])}
                         if req.tools else {}),
            allowed_tools=[f"mcp__{SERVER}__{h.name}" for h in req.tools] + builtins,
            strict_mcp_config=True,
            setting_sources=[],
            permission_mode="dontAsk",
            output_format=({"type": "json_schema", "schema": wrap_output_schema(req.output_schema)}
                           if req.output_schema is not None else None),
            cwd=str(req.workspace),
            max_turns=req.max_turns,
            effort=EFFORT[req.thinking],                 # explicit: never inherit CLAUDE_EFFORT from a parent
            resume=prev["session_id"] if prev else None,
            session_store=store,
            env={"CLAUDE_AGENT_SDK_CLIENT_APP": f"wynd/{__version__}",
                 "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},
            stderr=lambda line: log.debug("claude: %s", line),
        )
        t0 = time.monotonic()
        startup_ms: float | None = None
        session_id: str | None = None
        result: Any = None
        model_id, note, builtin_calls = req.model_id, "", 0
        transcript: list[dict[str, Any]] = []
        try:
            _check_cancel(req, called=False)
            prompt = req.continuation.message if req.continuation else user
            async with contextlib.aclosing(query(prompt=prompt, options=opts)) as messages:
                async for m in messages:
                    if state.failure is not None:
                        break                            # a tool raised: abort the harness (closes the CLI)
                    _check_cancel(req, called=state.calls + builtin_calls > 0)
                    if isinstance(m, sdk.SystemMessage) and m.subtype == "init":
                        startup_ms = (time.monotonic() - t0) * 1000
                        session_id = m.data.get("session_id") or session_id
                        model_id = m.data.get("model") or model_id
                    elif isinstance(m, sdk.AssistantMessage):
                        for b in m.content:
                            if isinstance(b, sdk.TextBlock) and b.text.strip():
                                note = b.text.strip()[:NOTE_LIMIT]
                                _push(transcript, req.on_event, {"type": "text", "text": b.text})
                            elif isinstance(b, sdk.ToolUseBlock) and b.name != "StructuredOutput":
                                if not b.name.startswith(f"mcp__{SERVER}__"):
                                    builtin_calls += 1
                                _push(transcript, req.on_event, {"type": "tool_use", "id": b.id,
                                      "name": b.name.removeprefix(f"mcp__{SERVER}__"), "input": b.input})
                    elif isinstance(m, sdk.UserMessage):
                        for b in m.content if isinstance(m.content, list) else []:
                            if isinstance(b, sdk.ToolResultBlock):
                                _push(transcript, req.on_event, {"type": "tool_result", "id": b.tool_use_id,
                                      "is_error": bool(b.is_error),
                                      "content": _text(b.content)[:TRANSCRIPT_TEXT_LIMIT]})
                    elif isinstance(m, sdk.RateLimitEvent) and m.rate_limit_info.status != "allowed":
                        log.warning("claude-code rate limit %s (%s)", m.rate_limit_info.status,
                                    m.rate_limit_info.rate_limit_type)
                    elif isinstance(m, sdk.ResultMessage):
                        result = m
                        session_id = session_id or m.session_id
        except sdk.ClaudeSDKError as e:
            if state.failure is not None:
                raise state.failure from e               # the harness died after a tool raised: the tool's error wins
            raise _sdk_error(sdk, e, tool_called=state.calls + builtin_calls > 0) from e
        finally:
            if session_id:
                _cleanup_local_session(sdk, session_id, req.workspace)
        called = state.calls + builtin_calls > 0
        if state.failure is not None:
            raise state.failure
        if result is None:
            raise ProviderError("claude-code ended without a result", kind="transport", retryable=True,
                                tool_called=called)
        if result.is_error:
            raise _classify(result, tool_called=called)
        if result.stop_reason == "refusal":
            raise ProviderError(result.result or "the model refused", kind="refusal", retryable=False,
                                tool_called=called)
        usage, cum, basis = _usage_delta(result, prev, latency_ms=(time.monotonic() - t0) * 1000)
        out = result.structured_output
        return AgentResponse(
            structured_output=unwrap_output(out) if out is not None else None,
            usage=usage,
            model_id=model_id,
            transcript=transcript,
            session={"session_id": result.session_id, "entries": store.entries, "cum": cum},
            note=note if out is not None else (result.result or note)[:NOTE_LIMIT],
            tool_calls=state.calls + builtin_calls,
            startup_ms=startup_ms,
            cost_basis=basis,
        )


def _check_cancel(req: AgentRequest, *, called: bool) -> None:
    if req.cancel is not None and req.cancel.is_set():
        raise ProviderError("cancelled", kind="transport", retryable=False, tool_called=called)


def _push(transcript: list[dict[str, Any]], on_event: Callable[[dict[str, Any]], None] | None,
          item: dict[str, Any]) -> None:
    transcript.append(item)
    if on_event is not None:
        on_event(item)


def _text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "\n".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")


def _bridge(sdk: Any, h: ToolHandle, state: _State) -> Any:
    """Expose a runtime ToolHandle over the in-process `wynd` MCP server. The sync `invoke` (validation, retries,
    trace, replay) runs in a thread. Any exception it raises (`ToolFailure`, `CassetteMissError`, ...) aborts the
    harness and is re-raised by `run` — it never becomes an error result the model could work around."""

    @sdk.tool(h.name, h.description, h.input_schema)
    async def handler(args: dict[str, Any]) -> dict[str, Any]:
        if state.failure is not None:
            return {"content": [{"type": "text", "text": "step aborted"}], "is_error": True}
        state.calls += 1
        try:
            r = await asyncio.to_thread(h.invoke, args)
        except Exception as e:
            state.failure = e
            return {"content": [{"type": "text", "text": f"tool failed: {e}; the step is aborting"}], "is_error": True}
        return {"content": [{"type": "text", "text": r.text}], "is_error": r.is_error}

    return handler


def _sdk_error(sdk: Any, e: Exception, *, tool_called: bool) -> ProviderError:
    if isinstance(e, sdk.CLINotFoundError):
        return ProviderError(f"Claude Code CLI not found: {e}", kind="unavailable", retryable=False)
    if isinstance(e, sdk.ResultError):
        return _classify(e, tool_called=tool_called)
    return ProviderError(f"claude-code transport failure: {e}", kind="transport", retryable=True,
                         tool_called=tool_called)


def _classify(e: Any, *, tool_called: bool) -> ProviderError:
    """Map an SDK `ResultError` or an error `ResultMessage` (same attribute names) to a ProviderError kind."""
    status = e.api_error_status
    text = e.result or "; ".join(e.errors or []) or (str(e) if isinstance(e, BaseException)
                                                      else f"claude-code error result ({e.subtype})")
    if e.subtype == "error_max_turns":
        return ProviderError(text, kind="max_turns", retryable=False, tool_called=tool_called, status=status)
    if status in (401, 403) or "Not logged in" in text:
        return ProviderError(AUTH_HINT + text, kind="auth", retryable=False, tool_called=tool_called, status=status)
    if status in (400, 404, 413, 422):
        return ProviderError(text, kind="invalid_request", retryable=False, tool_called=tool_called, status=status)
    return ProviderError(text, kind="transport", retryable=True, tool_called=tool_called, status=status)


def _usage_delta(result: Any, prev: Mapping[str, Any] | None, *,
                 latency_ms: float) -> tuple[Usage, dict[str, Any], Literal["api", "list"] | None]:
    """Per-call usage. `model_usage`/`total_cost_usd` are cumulative over a resumed session, so the previous call's
    totals (`session["cum"]`) are subtracted."""
    mu = result.model_usage or {}
    tot = {"in": sum(v.get("inputTokens", 0) for v in mu.values()),
           "out": sum(v.get("outputTokens", 0) for v in mu.values()),
           "cr": sum(v.get("cacheReadInputTokens", 0) for v in mu.values()),
           "cw": sum(v.get("cacheCreationInputTokens", 0) for v in mu.values()),
           "cost": result.total_cost_usd}
    base = prev["cum"] if prev else {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0}
    cost = None if tot["cost"] is None else round(tot["cost"] - (base["cost"] or 0.0), 8)
    basis = next((v.get("costBasis") for v in mu.values() if v.get("costBasis")), None)
    usage = Usage(input_tokens=tot["in"] - base["in"], output_tokens=tot["out"] - base["out"],
                  cache_read_tokens=tot["cr"] - base["cr"], cache_write_tokens=tot["cw"] - base["cw"],
                  cost_usd=cost, latency_ms=latency_ms, calls=1)
    return usage, tot, ("list" if basis == "list" else ("api" if basis else None))


def _cleanup_local_session(sdk: Any, session_id: str, cwd: Path) -> None:
    """Delete the session file the CLI wrote for this call and its project dir when empty."""
    with contextlib.suppress(FileNotFoundError, ValueError):
        sdk.delete_session(session_id, directory=str(cwd))
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    with contextlib.suppress(OSError):
        (projects / sdk.project_key_for_directory(str(cwd))).rmdir()
