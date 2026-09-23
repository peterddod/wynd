"""The per-run tool set: invocation, retry policy (idempotent tools only), tracing, cassette replay of network/MCP
calls, MCP connect-and-verify (PLAN §3.9, §5.1, §5.5; `$DRAFTS/03 §9.2, §10.5`).

Outcomes of `invoke` (PLAN §3.9): bad arguments, `ToolInputError` and MCP `isError` are error results the model sees;
a tool that raises (after its retries) is `ToolFailure`; `CassetteMissError`, `MissingEnvVar`, `McpConfigError` and
`StepFailure` propagate unchanged so the step resolves with their own cause.
"""

from __future__ import annotations

import contextlib
import dataclasses
import functools
import json
import os
import time
import urllib.error
from collections.abc import Callable, Iterable, Mapping
from contextvars import ContextVar
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from pydantic_core import to_jsonable_python

from wynd.runtime.agentic.errors import McpConfigError, MissingEnvVar, ToolFailure, ToolInputError
from wynd.runtime.cassettes import CassetteMissError
from wynd.runtime.errors import StepFailure
from wynd.runtime.http import HttpError
from wynd.runtime.mcp.client import McpAuthError, McpError, tool_result_text
from wynd.runtime.mcp.entry import McpServerEntry, open_client
from wynd.runtime.mcp.snapshot import verify
from wynd.runtime.providers.types import McpServerRef, ToolHandle, ToolResult, ToolSchema
from wynd.runtime.tools.decorator import ToolSpec, step_tools, tool_spec
from wynd.spec.lockfiles import McpSnapshot

if TYPE_CHECKING:
    from wynd.runtime.cassettes import CassetteSession
    from wynd.runtime.handle import RuntimeHandle
    from wynd.runtime.mcp.client import McpClient
    from wynd.runtime.middleware import AgentCall

RESULT_LIMIT = 100_000          # characters of a tool result the model sees
TRACE_LIMIT = 4096              # characters of args/result in a tool.call event
PASS_THROUGH = (CassetteMissError, MissingEnvVar, McpConfigError, StepFailure)

_RUNTIME: ContextVar[RuntimeHandle | None] = ContextVar("wynd_tool_runtime", default=None)


class ToolSet:
    """`for_step(step, call, cassettes)`, `schemas()`, `handles()`, `mcp_refs()`, `connect_mcp()`, `invoke(name,
    arguments)`, `trace_harness_calls(transcript)`, `close()`; `calls` counts every requested invocation of a known
    tool (replayed and argument-rejected ones included)."""

    def __init__(
        self,
        specs: Iterable[ToolSpec],
        *,
        runtime: RuntimeHandle | None = None,
        cassettes: CassetteSession | None = None,
        tool_retries: int = 0,
        mcp: Iterable[Mapping[str, Any]] = (),
    ) -> None:
        self.calls = 0
        self._runtime = runtime
        self._cassettes = cassettes
        self._mode = cassettes.mode if cassettes is not None else "live"
        self._tool_retries = tool_retries
        self._mcp = [(_snapshot_of(block), block.get("entry")) for block in mcp]
        self._clients: dict[str, McpClient] = {}
        self._specs: dict[str, ToolSpec] = {}
        for spec in [*specs, *(s for snap, entry in self._mcp for s in _mcp_specs(snap, entry))]:
            if spec.name in self._specs:
                raise ValueError(f"duplicate tool name {spec.name!r}")
            self._specs[spec.name] = spec

    @classmethod
    def for_step(cls, step: Any, call: AgentCall, cassettes: CassetteSession | None) -> ToolSet:
        """The step's `@tool` methods (bound to `step`), its `tools` and its MCP snapshots (`call.policy.mcp`)."""
        specs = [
            dataclasses.replace(s, fn=s.fn.__get__(step, type(step))) if s.source == "method" else s
            for s in step_tools(type(step))
        ]
        return cls(specs, runtime=call.runtime, cassettes=cassettes, tool_retries=call.policy.retries.tool,
                   mcp=call.policy.mcp)

    def schemas(self) -> list[ToolSchema]:
        return [ToolSchema(s.name, s.description, s.input_schema) for s in self._specs.values()]

    def handles(self) -> list[ToolHandle]:
        return [
            ToolHandle(s.name, s.description, s.input_schema, functools.partial(self.invoke, s.name), local=_local(s))
            for s in self._specs.values()
        ]

    def mcp_refs(self) -> list[McpServerRef]:
        return [McpServerRef(snap.server, tuple(snap.allow)) for snap, _ in self._mcp]

    def connect_mcp(self) -> None:
        """Connect to every MCP server (env references resolved from `os.environ` now) and verify its snapshot.
        Raises `McpSnapshotMismatch`, or `McpConfigError` when a server has no registry entry or cannot be reached."""
        for snap, entry in self._mcp:
            if snap.server in self._clients:
                continue
            if not entry:
                raise McpConfigError(
                    f"MCP server {snap.server!r} is not in the user registry; add it with `wynd mcp add {snap.server}`"
                )
            try:
                server = McpServerEntry.model_validate(entry)
            except ValidationError as e:
                raise McpConfigError(f"MCP server {snap.server!r}: invalid user-registry entry: {e}") from e
            try:
                client = open_client(server, os.environ)
                self._clients[snap.server] = client
                verify(snap, client)
            except McpError as e:
                hint = ""
                if isinstance(e, McpAuthError) and server.auth_env:
                    hint = f" (check {', '.join(server.auth_env)})"
                raise McpConfigError(f"MCP server {server.name!r}: {e}{hint}") from e

    def invoke(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        spec = self._specs.get(name)
        if spec is None:
            return ToolResult(f"unknown tool {name!r}", is_error=True)
        self.calls += 1
        started = time.perf_counter()
        args = None
        if spec.args_model is not None:
            try:
                args = spec.args_model.model_validate(arguments)
            except ValidationError as e:
                result = ToolResult("invalid arguments:\n" + _errors(e), is_error=True)
                self._emit(spec, arguments, result, started, tries=0)
                return result
        recorded = not _local(spec)
        if recorded and self._mode == "replay":
            return self._replay(spec, arguments, started)
        tries = 1 + (self._tool_retries if spec.idempotent else 0)
        for attempt in range(1, tries + 1):
            try:
                result = self._execute(spec, args, arguments)
                break
            except ToolInputError as e:
                result = ToolResult(str(e), is_error=True)
                break
            except PASS_THROUGH:
                raise
            except Exception as e:
                if is_transient(e) and attempt < tries:
                    time.sleep(0.5 * 2 ** (attempt - 1))
                    continue
                message = f"tool {name} failed: {type(e).__name__}: {e}"
                self._emit(spec, arguments, None, started, tries=attempt, error=message)
                if recorded and self._mode == "record":
                    self._cassettes.write_tool(name, arguments, None, error=message)
                raise ToolFailure(message) from e
        if recorded and self._mode == "record":
            self._cassettes.write_tool(name, arguments, result)
        self._emit(spec, arguments, result, started, tries=attempt)
        return result

    def trace_harness_calls(self, transcript: list[dict[str, Any]]) -> None:
        """`tool.call` events for an AgentProvider run: harness built-ins (source "harness") and, in replay, the
        recorded network/MCP calls that were not re-executed. Calls that went through `invoke` are already traced."""
        results = {item.get("id"): item for item in transcript if item.get("type") == "tool_result"}
        replayed = self._mode == "replay"
        for item in transcript:
            name = item.get("name")
            if item.get("type") != "tool_use" or name == "StructuredOutput":
                continue
            spec = self._specs.get(name)
            if spec is not None and not (replayed and not _local(spec)):
                continue
            outcome = results.get(item.get("id")) or {}
            self._trace(
                tool=name,
                source=_source(spec) if spec else "harness",
                effects=list(spec.effects) if spec else [],
                idempotent=spec.idempotent if spec else False,
                args=_clip_args(item.get("input") or {}),
                result=_clip(outcome.get("content")),
                ok=True,
                is_error=bool(outcome.get("is_error")),
                error=None,
                tries=1,
                latency_ms=None,
                replayed=replayed,
            )

    def close(self) -> None:
        """Close every MCP client (stdio: terminate the child)."""
        for client in self._clients.values():
            with contextlib.suppress(Exception):
                client.close()
        self._clients.clear()

    def _execute(self, spec: ToolSpec, args: Any, arguments: dict[str, Any]) -> ToolResult:
        if spec.source == "mcp":
            client = self._clients.get(spec.server or "")
            if client is None:
                raise McpConfigError(f"MCP server {spec.server!r} is not connected (connect_mcp was not called)")
            raw = client.call_tool(spec.name.removeprefix(f"{spec.server}__"), arguments)
            return ToolResult(_cap(tool_result_text(raw)), is_error=bool(raw.get("isError")))
        token = _RUNTIME.set(self._runtime)
        try:
            value = spec.fn(**{k: getattr(args, k) for k in type(args).model_fields})
        finally:
            _RUNTIME.reset(token)
        return ToolResult(_cap(_serialise(spec, value)))

    def _replay(self, spec: ToolSpec, arguments: dict[str, Any], started: float) -> ToolResult:
        entry = self._cassettes.find_tool(spec.name, arguments)
        if entry.error is not None:
            self._emit(spec, arguments, None, started, tries=1, error=entry.error, replayed=True)
            raise ToolFailure(entry.error)
        self._emit(spec, arguments, entry.result, started, tries=1, replayed=True)
        return entry.result

    def _emit(self, spec: ToolSpec, arguments: Any, result: ToolResult | None, started: float, *, tries: int,
              error: str | None = None, replayed: bool = False) -> None:
        self._trace(
            tool=spec.name,
            source=_source(spec),
            effects=list(spec.effects),
            idempotent=spec.idempotent,
            args=_clip_args(arguments),
            result=_clip(result.text) if result is not None else None,
            ok=error is None,
            is_error=result.is_error if result is not None else False,
            error=error,
            tries=tries,
            latency_ms=round((time.perf_counter() - started) * 1000, 3),
            replayed=replayed,
        )

    def _trace(self, **fields: Any) -> None:
        if self._runtime is not None:
            self._runtime.trace.emit("tool.call", **fields)


def is_transient(exc: BaseException) -> bool:
    """Worth one more try of an idempotent tool: timeouts, connection failures, HTTP 429/502/503/504, transient
    MCP errors."""
    match exc:
        case McpError():
            return exc.transient
        case HttpError():
            return exc.response is None or exc.response.status in (429, 502, 503, 504)
        case urllib.error.HTTPError():
            return False
        case urllib.error.URLError() | TimeoutError() | ConnectionError():
            return True
    return False


def current_runtime() -> RuntimeHandle:
    """The handle of the step run a builtin/method tool is executing in; outside a run -> RuntimeError."""
    runtime = _RUNTIME.get()
    if runtime is None:
        raise RuntimeError("current_runtime() is only available while a tool runs inside a step run")
    return runtime


def handles_for(fns: Iterable[Callable[..., Any]]) -> list[ToolHandle]:
    """ToolHandles for `@tool` functions outside a step run (e.g. the controller chat): arguments are validated and
    results serialised as in a step, with no retries, tracing, cassettes or runtime handle."""
    return ToolSet([tool_spec(fn, "builtin") for fn in fns]).handles()


def _local(spec: ToolSpec) -> bool:
    """Executed in every mode (never recorded): no `network` effect and not MCP."""
    return spec.source != "mcp" and "network" not in spec.effects


def _source(spec: ToolSpec) -> str:
    return f"mcp:{spec.server}" if spec.source == "mcp" else spec.source


def _snapshot_of(block: Mapping[str, Any]) -> McpSnapshot:
    """`ExecPolicy.mcp` items are McpSnapshot JSON plus `"entry"` (the user-registry McpServerEntry JSON)."""
    return McpSnapshot.model_validate({k: v for k, v in block.items() if k != "entry"})


def _mcp_specs(snap: McpSnapshot, entry: Mapping[str, Any] | None) -> list[ToolSpec]:
    """One tool per snapshot tool, named `<server>__<tool>`; the model sees the snapshot's description and schema."""
    auth_env = tuple((entry or {}).get("auth_env", ()))
    return [
        ToolSpec(
            name=f"{snap.server}__{t.name}",
            description=t.description,
            effects=("network",),
            idempotent=t.idempotent,
            env=auth_env,
            source="mcp",
            server=snap.server,
            input_schema=t.input_schema,
            args_model=None,
            returns=None,
        )
        for t in snap.tools
    ]


def _serialise(spec: ToolSpec, value: Any) -> str:
    if isinstance(value, str):
        return value
    data = spec.returns.dump_python(value, mode="json") if spec.returns is not None else to_jsonable_python(value)
    return json.dumps(data, ensure_ascii=False, sort_keys=True)


def _cap(text: str, limit: int = RESULT_LIMIT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"…[truncated {len(text) - limit} chars]"


def _clip(value: Any) -> str | None:
    if value is None:
        return None
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return _cap(text, TRACE_LIMIT)


def _clip_args(arguments: Any) -> Any:
    text = json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
    return arguments if len(text) <= TRACE_LIMIT else _cap(text, TRACE_LIMIT)


def _errors(e: ValidationError) -> str:
    return "\n".join(
        f"- {'.'.join(str(p) for p in err['loc']) or '<arguments>'}: {err['msg']}" for err in e.errors()
    )
