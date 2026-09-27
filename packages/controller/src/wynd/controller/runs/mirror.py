"""Mirror a run-API run into the controller's stores (PLAN §8.1; `$DRAFTS/06 §5.10`).

Streams `GET /v1/runs/{id}/events` into `stores.traces.write(ev)` and `on_event` (events unchanged, PLAN §3.13), then
creates or updates the local `RunRecord` from `GET /v1/runs/{id}` with `meta = dict(metadata)` (PLAN §3.14).

A dropped stream is resumed after the last event seen (at most `MAX_RECONNECTS` times in a row). The record's
`inputs` come from the `run.start` event (the run API's `Run` does not carry them) and `workspace` from `run.end`;
`workspace_bytes` stays unknown (the workspace lives in the container), `trace_bytes` is the local trace file's size.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import unquote, urlparse

from wynd.runtime.supervisor.client import RunApiError

if TYPE_CHECKING:
    from wynd.controller.controller import ControllerContext
    from wynd.runtime.supervisor.client import RunApiClient

MAX_RECONNECTS = 3


def mirror_run(
    ctx: ControllerContext,
    client: RunApiClient,
    run_id: str,
    *,
    metadata: Mapping[str, Any],
    on_event: Callable[[dict], None] | None = None,
) -> dict:
    """-> the stored `RunRecord` dict."""
    from wynd.runtime.storage.models import RunRecord

    traces = ctx.stores.traces
    seen: dict[str, dict] = {}
    try:
        _stream(client, run_id, lambda event: _deliver(traces, seen, event, on_event))
    finally:
        traces.close(run_id)
    run = client.get(run_id)
    now = datetime.now(UTC)
    record = RunRecord(
        id=run_id, process=run.process, status=run.status, created_at=run.created_at, updated_at=now,
        started_at=run.started_at, finished_at=run.finished_at, ref=run.commit, mode="image",
        inputs=seen.get("run.start", {}).get("inputs") or {}, exit=run.exit, outputs=run.outputs, error=run.error,
        trace=traces.uri(run_id), workspace=seen.get("run.end", {}).get("workspace"), duration_ms=run.duration_ms,
        usage=None if run.usage is None else run.usage.model_dump(), trace_bytes=_file_size(traces.uri(run_id)),
        meta=dict(metadata),
    ).model_dump(mode="json")
    if ctx.stores.runs.get(run_id) is None:
        ctx.stores.runs.create(record)
        return record
    patch = {key: value for key, value in record.items() if key not in ("created_at", "updated_at")}
    return ctx.stores.runs.update(run_id, patch)


def _stream(client: RunApiClient, run_id: str, deliver: Callable[[dict], None]) -> None:
    """Deliver every `trace` event until `end`, resuming a dropped stream after the last event id seen."""
    last: int | None = None
    failures = 0
    while True:
        ended = False
        try:
            for index, event, data in client.events(run_id, after=last):
                if last is not None and index <= last:
                    continue                            # already seen (a server that ignored `after`)
                last, failures = index, 0
                match event:
                    case "trace":
                        deliver(data)
                    case "end":
                        ended = True
        except (RunApiError, OSError) as err:
            if isinstance(err, RunApiError) and err.status != 0:
                raise
        if ended:
            return
        failures += 1
        if failures > MAX_RECONNECTS:
            raise RunApiError(f"lost the event stream of run {run_id}", status=0, code="stream_lost")


def _deliver(traces: Any, seen: dict[str, dict], event: dict, on_event: Callable[[dict], None] | None) -> None:
    traces.write(event)
    if event.get("type") in ("run.start", "run.end"):
        seen[event["type"]] = event
    if on_event is not None:
        on_event(event)


def _file_size(uri: str) -> int | None:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    path = Path(unquote(parsed.path))
    return path.stat().st_size if path.is_file() else None
