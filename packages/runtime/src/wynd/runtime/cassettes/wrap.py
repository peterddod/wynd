"""Cassette wrappers around providers (PLAN §3.16; `$DRAFTS/03 §11.4`). The real provider is built lazily, so replay
needs no credentials. `CassetteAgentProvider` replays a whole `run()` and then re-executes, in recorded order, every
`tool_use` of the recorded transcript whose handle is `local`.

One `CassetteSession` per step run (or edge check): it wraps the provider and serves the tool replay hooks
(`find_tool`, `write_tool`) the runtime-owned tool set uses for network and MCP tools.
"""

from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from wynd.runtime.cassettes import CassetteError, CassetteMissError
from wynd.runtime.cassettes.key import (
    Normaliser,
    agent_request,
    continue_request,
    generate_request,
    normalised_json,
    request_key,
    tool_request,
)
from wynd.runtime.cassettes.store import CassetteEntry, dump_miss, find, write_entry
from wynd.runtime.providers.types import (
    AgentRequest,
    AgentResponse,
    Continuation,
    GenerateRequest,
    GenerateResponse,
    ToolCall,
    ToolResult,
)
from wynd.runtime.usage import Usage

if TYPE_CHECKING:
    from wynd.runtime.policy import CassetteConfig

CASSETTE_KEY = "cassette_key"      # stamped into every AgentResponse.session; chains continuation keys


class CassetteSession:
    """Cassette state of one step run: mode, directories, the run's `Normaliser` and the recording metadata."""

    def __init__(
        self,
        config: CassetteConfig,
        *,
        run_id: str,
        workspace: Path,
        provider: str,
        tier: str | None = None,
        thinking: str | None = None,
        package: str | None = None,
        step: str | None = None,
    ) -> None:
        if config.mode == "record" and not config.record_dir:
            raise CassetteError("cassette mode 'record' needs CassetteConfig.record_dir")
        self.mode: Literal["live", "record", "replay"] = config.mode
        self.dir = Path(config.dir) if config.dir else None
        self.record_dir = Path(config.record_dir) if config.record_dir else None
        self.workspace = Path(workspace)
        self.normaliser = Normaliser.for_run(run_id, self.workspace, config.literals)
        self.provider = provider
        self.tier = tier
        self.thinking = thinking
        self.package = package
        self.step = step

    def wrap(
        self, factory: Callable[[], Any], kind: Literal["model", "agent"]
    ) -> CassetteModelProvider | CassetteAgentProvider:
        """Wrap the provider `factory()` builds; the factory is called at most once, and never in replay."""
        match kind:
            case "model":
                return CassetteModelProvider(self, factory)
            case "agent":
                return CassetteAgentProvider(self, factory)
        raise ValueError(f"unknown provider kind {kind!r}")

    def find_tool(self, name: str, arguments: Mapping[str, Any]) -> CassetteEntry:
        """The recorded call (`.result` or `.error`); `CassetteMissError` if there is none."""
        return self.lookup(tool_request(name, arguments))

    def write_tool(
        self, name: str, arguments: Mapping[str, Any], result: ToolResult | None, error: str | None = None
    ) -> None:
        """Record a tool call (record mode only; a no-op otherwise)."""
        if self.mode != "record":
            return
        if result is None:
            response: dict[str, Any] = {"error": error}
        else:
            response = {"result": {"text": result.text, "is_error": result.is_error}}
        self.record(tool_request(name, arguments), response, model_id=None)

    def lookup(self, canonical: dict[str, Any]) -> CassetteEntry:
        """Replay: the recording of `canonical`, its response restored into this run's terms; on a miss the
        normalised request is dumped and `CassetteMissError` raised."""
        key = request_key(canonical, self.normaliser)
        entry = find(self.dir, key)
        if entry is None:
            dump = dump_miss(self.workspace, key, self.normalised(canonical))
            raise CassetteMissError(key=key, dir=self.dir, dump=dump)
        return dataclasses.replace(entry, response=_map_strings(entry.response, self.normaliser.restore))

    def record(self, canonical: dict[str, Any], response: dict[str, Any], *, model_id: str | None) -> Path:
        """Record mode: write the entry into `record_dir` (the caller-supplied staging directory)."""
        key = request_key(canonical, self.normaliser)
        entry = CassetteEntry(
            kind=canonical["kind"],
            key=key,
            package=self.package,
            step=self.step,
            provider=self.provider,
            tier=self.tier,
            thinking=self.thinking,
            model_id=model_id,
            recorded_at=datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            request=self.normalised(canonical),
            response=_map_strings(response, self.normaliser.replace_literals),
        )
        return write_entry(self.record_dir, entry)

    def normalised(self, canonical: dict[str, Any]) -> dict[str, Any]:
        return json.loads(normalised_json(canonical, self.normaliser))


class CassetteModelProvider:
    """A ModelProvider whose `generate` is recorded (record) or served from the cassette directory (replay)."""

    def __init__(self, session: CassetteSession, factory: Callable[[], Any]) -> None:
        self.session = session
        self.name = session.provider
        self.last_key: str | None = None        # key of the latest request (the loop's `request_hash`)
        self._factory = factory
        self._provider: Any = None

    def generate(self, req: GenerateRequest) -> GenerateResponse:
        s = self.session
        canonical = generate_request(req, provider=s.provider, tier=s.tier)
        self.last_key = request_key(canonical, s.normaliser)
        if s.mode == "replay":
            return _generate_response(s.lookup(canonical).response)
        resp = self._inner().generate(req)
        if s.mode == "record":
            s.record(canonical, _generate_json(resp), model_id=resp.model_id)
        return resp

    def tiers(self) -> dict[str, str]:
        return _tiers(self.session, self._inner)

    def _inner(self) -> Any:
        if self._provider is None:
            self._provider = self._factory()
        return self._provider


class CassetteAgentProvider:
    """An AgentProvider whose `run` is recorded (record) or replayed as a whole (replay). Every returned session
    carries `cassette_key`, so a continuation's key is chained on the call it continues."""

    def __init__(self, session: CassetteSession, factory: Callable[[], Any]) -> None:
        self.session = session
        self.name = session.provider
        self.last_key: str | None = None
        self._factory = factory
        self._provider: Any = None

    def run(self, req: AgentRequest) -> AgentResponse:
        s = self.session
        if req.continuation is None:
            canonical = agent_request(req, provider=s.provider, tier=s.tier)
        else:
            canonical = continue_request(req.continuation.session.get(CASSETTE_KEY), req.continuation.message)
        key = request_key(canonical, s.normaliser)
        self.last_key = key
        if s.mode == "replay":
            entry = s.lookup(canonical)
            _reexecute_local_tools(req, entry.response.get("transcript", []))
            return _agent_response(entry.response, key)
        resp = self._inner().run(_without_cassette_key(req))
        if s.mode == "record":
            s.record(canonical, _agent_json(resp), model_id=resp.model_id)
        return dataclasses.replace(resp, session={**resp.session, CASSETTE_KEY: key})

    def tiers(self) -> dict[str, str]:
        return _tiers(self.session, self._inner)

    def _inner(self) -> Any:
        if self._provider is None:
            self._provider = self._factory()
        return self._provider


def _tiers(session: CassetteSession, inner: Callable[[], Any]) -> dict[str, str]:
    """Replay never builds the provider: it reports the class's default tiers."""
    if session.mode == "replay":
        from wynd.runtime.providers import provider_info

        return dict(provider_info(session.provider).default_tiers)
    return inner().tiers()


def _map_strings(value: Any, fn: Callable[[str], str]) -> Any:
    match value:
        case str():
            return fn(value)
        case dict():
            return {fn(k) if isinstance(k, str) else k: _map_strings(v, fn) for k, v in value.items()}
        case list():
            return [_map_strings(v, fn) for v in value]
    return value


def _without_cassette_key(req: AgentRequest) -> AgentRequest:
    if req.continuation is None or CASSETTE_KEY not in req.continuation.session:
        return req
    session = {k: v for k, v in req.continuation.session.items() if k != CASSETTE_KEY}
    return dataclasses.replace(req, continuation=Continuation(session=session, message=req.continuation.message))


def _reexecute_local_tools(req: AgentRequest, transcript: list[dict[str, Any]]) -> None:
    """Replay the harness's stream: every entry goes to `on_event`; recorded `tool_use`s of `local` tools run again
    (results discarded, the recording stands) so workspace side effects match the runtime-owned loop."""
    local = {h.name: h for h in req.tools if h.local}
    for item in transcript:
        if req.on_event is not None:
            req.on_event(item)
        if item.get("type") == "tool_use" and item.get("name") in local:
            local[item["name"]].invoke(item.get("input") or {})


def _generate_json(resp: GenerateResponse) -> dict[str, Any]:
    return {
        "message": resp.message,
        "text": resp.text,
        "tool_calls": [dataclasses.asdict(tc) for tc in resp.tool_calls],
        "structured_output": resp.structured_output,
        "stop": resp.stop,
        "usage": resp.usage.model_dump(mode="json"),
        "model_id": resp.model_id,
        "cost_basis": resp.cost_basis,
    }


def _generate_response(doc: dict[str, Any]) -> GenerateResponse:
    return GenerateResponse(
        message=doc["message"],
        text=doc["text"],
        tool_calls=[ToolCall(id=tc["id"], name=tc["name"], arguments=tc["arguments"]) for tc in doc["tool_calls"]],
        structured_output=doc["structured_output"],
        stop=doc["stop"],
        usage=Usage.model_validate(doc["usage"]),
        model_id=doc["model_id"],
        cost_basis=doc.get("cost_basis"),
    )


def _agent_json(resp: AgentResponse) -> dict[str, Any]:
    return {
        "structured_output": resp.structured_output,
        "note": resp.note,
        "tool_calls": resp.tool_calls,
        "model_id": resp.model_id,
        "usage": resp.usage.model_dump(mode="json"),
        "transcript": resp.transcript,
        "startup_ms": resp.startup_ms,
        "cost_basis": resp.cost_basis,
    }


def _agent_response(doc: dict[str, Any], key: str) -> AgentResponse:
    return AgentResponse(
        structured_output=doc["structured_output"],
        usage=Usage.model_validate(doc["usage"]),
        model_id=doc["model_id"],
        transcript=doc["transcript"],
        session={CASSETTE_KEY: key},
        note=doc.get("note", ""),
        tool_calls=doc.get("tool_calls", 0),
        startup_ms=doc.get("startup_ms"),
        cost_basis=doc.get("cost_basis"),
    )
