"""Mirror a run-API run into the controller's stores (PLAN §8.1; `$DRAFTS/06 §5.10`). Stub; CTL-M2.

Streams `GET /v1/runs/{id}/events` into `stores.traces.write(ev)` and `on_event` (events unchanged, PLAN §3.13), then
creates or updates the local `RunRecord` from `GET /v1/runs/{id}` with `meta = dict(metadata)` (PLAN §3.14).
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.controller.controller import ControllerContext
    from wynd.runtime.supervisor.client import RunApiClient


def mirror_run(
    ctx: ControllerContext,
    client: RunApiClient,
    run_id: str,
    *,
    metadata: Mapping[str, Any],
    on_event: Callable[[dict], None] | None = None,
) -> dict:
    """-> the stored `RunRecord` dict."""
    raise NotImplementedError("PLAN §8.1")
