"""Image and release runs through the run API (PLAN §8.1 CTL-M2 row, §3.21 amendment 7; `$DRAFTS/06 §5.10`).
Stub; CTL-M2.

Every top-level `path`-typed input (per `ProcessService.interface(pid)`) whose value names an existing host file is
sent as run-API `files[<sha256(content)[:8]>-<basename>]` and replaced by `{"$file": <name>}`; other values pass
through unchanged. The run API client is injectable (`FakeRunApiClient` in tests).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from wynd.runtime.supervisor.client import RunApiClient

if TYPE_CHECKING:
    from wynd.controller.controller import Controller
    from wynd.controller.models import Run, RunTrigger


def path_inputs_to_files(ctl: Controller, pid: str, inputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict]]:
    """-> (inputs with `{"$file": name}` substitutions, run-API `files` {name: {"content_base64"}})."""
    raise NotImplementedError("PLAN §3.21")


def run_image(
    ctl: Controller,
    pid: str,
    inputs: dict[str, Any],
    *,
    commit: str | None = None,
    on_event: Callable[[dict], None] | None = None,
    trigger: RunTrigger = "api",
    run_api_client: Callable[[str], RunApiClient] = RunApiClient,
    run_id: str | None = None,
) -> Run:
    """Run the build of `pid` at `commit` (default: the process HEAD) in a warm or ephemeral container.

    `run_id` (default `new_id("run")`) is passed to `RunApiClient.submit(inputs, run_id=run_id, ...)`; `RunService.start`
    picks it up front so the id it returns is the id of the run.
    """
    raise NotImplementedError("PLAN §8.1")
