"""Image and release runs through the run API (PLAN §8.1 CTL-M2 row, §3.21 amendment 7; `$DRAFTS/06 §5.10`).

Every top-level `path`-typed input (per `ProcessService.interface(pid)`) whose value names an existing host file is
sent as run-API `files[<sha256(content)[:8]>-<basename>]` and replaced by `{"$file": <name>}`; other values pass
through unchanged. The run API client is injectable (`FakeRunApiClient` in tests).

A relative value names a file relative to the workspace root. Characters of the basename outside `[A-Za-z0-9_.-]`
become `_` (and it is cut to fit 128 characters), because the run API only accepts file names that are valid ids.
"""

from __future__ import annotations

import base64
import hashlib
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wynd.controller.errors import Conflict, Invalid, NotBuilt, Unavailable, WyndError, translated
from wynd.runtime.supervisor.client import RunApiClient, RunApiError

if TYPE_CHECKING:
    from wynd.controller.controller import Controller
    from wynd.controller.models import Run, RunTrigger

TOKEN_VAR = "WYND_RUN_API_TOKEN"
MAX_NAME = 128


def path_inputs_to_files(ctl: Controller, pid: str, inputs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict]]:
    """-> (inputs with `{"$file": name}` substitutions, run-API `files` {name: {"content_base64"}})."""
    schema = ctl.processes.interface(pid).inputs or {}
    properties = schema.get("properties") or {}
    substituted = dict(inputs)
    files: dict[str, dict] = {}
    for field, value in inputs.items():
        if not isinstance(value, str) or not value or not _is_path(properties.get(field)):
            continue
        path = Path(value) if Path(value).is_absolute() else ctl.ctx.root / value
        if not path.is_file():
            continue
        data = path.read_bytes()
        name = f"{hashlib.sha256(data).hexdigest()[:8]}-{_safe_name(path.name)}"
        files[name] = {"content_base64": base64.b64encode(data).decode()}
        substituted[field] = {"$file": name}
    return substituted, files


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
    from wynd.controller.models import Run
    from wynd.controller.runs.mirror import mirror_run
    from wynd.controller.status import process_head
    from wynd.runtime.ids import new_id

    ctx = ctl.ctx
    if commit is None:
        with translated():
            found = process_head(ctx, pid)
        commit = None if found is None else found[0]
    build = None if commit is None else ctx.artefacts.get_build(pid, commit)
    if build is None or not build.image:
        raise NotBuilt(f"process '{pid}' has no image build at {commit or 'its HEAD'}", hint=f"run `wynd build {pid}`")

    sent, files = path_inputs_to_files(ctl, pid, inputs)
    run_id = run_id or new_id("run")
    metadata = {"trigger": trigger, "release_id": None, "target": {"kind": "image", "commit": commit}}
    url, ephemeral = ctl.serve.acquire(build.image)
    try:
        client = run_api_client(url)
        client.token = ctl.env.resolve().get(TOKEN_VAR) or None
        try:
            client.submit(sent, run_id=run_id, files={name: base64.b64decode(f["content_base64"])
                                                      for name, f in files.items()}, metadata=metadata)
            record = mirror_run(ctx, client, run_id, metadata=metadata, on_event=on_event)
        except RunApiError as err:
            raise _translate(err, url) from None
    finally:
        if ephemeral:
            ctl.serve.stop(ctl.serve.ephemeral.get(url, url))
    return Run.from_record(record)


def _is_path(schema: dict | None) -> bool:
    """`format: path`, directly or as a member of the `anyOf` of an optional (`path?`) field."""
    if not schema:
        return False
    if schema.get("format") == "path":
        return True
    return any(isinstance(member, dict) and member.get("format") == "path" for member in schema.get("anyOf") or [])


def _safe_name(basename: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "_", basename)[: MAX_NAME - 9] or "file"


def _translate(err: RunApiError, url: str) -> WyndError:
    match err.status:
        case 0 | 429 | 503:
            return Unavailable(f"run API at {url}: {err}")
        case 400 | 413 | 422:
            return Invalid(str(err), details=err.body)
        case 409:
            return Conflict(str(err), details=err.body)
    return Unavailable(f"run API at {url}: {err}", details=err.body)
