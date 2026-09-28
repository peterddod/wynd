"""`RunService` (PLAN §8.1; `$DRAFTS/06 §5.10`).

Env gate: before any local or image run, `wynd env check` of the process (local: `ctl.env.check(pid, mode="local")`;
image: the build's manifest against `resolve_image`, mode "image"); errors raise `EnvMissing` and no `RunRecord` is
created. Local target = `wynd.process.local.run_local(...)` with metadata `{"trigger", "release_id", "target"}`;
image targets call `wynd.controller.runs.image.run_image`, release targets `ctl.releases.trigger` (which gates on the
release's own env bindings). `Run` is the `RunRecord` projection. A run started in the background that fails before
the executor records it (design phase, a validation error, a missing venv tool) is recorded as `failed` with
`error.cause = "internal"`.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from wynd.controller.errors import EnvMissing, Invalid, NotBuilt, NotFound, Unavailable, translated

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import CreateRunRequest, EnvCheckDTO, Run, RunTarget, RunTrigger

TERMINAL = ("succeeded", "failed")
EVERY_RECORD = 1_000_000                          # the release filter reads `meta`, so it lists every run first


class RunService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl
        self._pending: dict[str, Run] = {}         # started in the background, not yet recorded by the executor
        self._lock = threading.Lock()

    def run(
        self,
        pid: str,
        inputs: dict[str, Any],
        *,
        target: RunTarget | None = None,
        on_event: Callable[[dict], None] | None = None,
        trigger: RunTrigger = "api",
    ) -> Run:
        """Blocking; `target=None` is the local target."""
        from wynd.controller.models import ImageTarget, LocalTarget, ReleaseTarget

        match target:
            case None | LocalTarget():
                self._gate_local(pid)
                return self._run_local(pid, inputs, run_id=None, on_event=on_event, trigger=trigger)
            case ImageTarget(commit=commit):
                from wynd.controller.runs.image import run_image

                self._gate_image(pid, commit)
                return run_image(self.ctl, pid, inputs, commit=commit, on_event=on_event, trigger=trigger)
            case ReleaseTarget(release_id=release_id):
                source = "manual" if trigger == "api" else trigger
                return self.ctl.releases.trigger(release_id, inputs, source=source)
        raise Invalid(f"unknown run target {target!r}")

    def start(self, req: CreateRunRequest, *, trigger: RunTrigger = "api") -> Run:
        """Background thread; returns the run in state `running`. The env gate and the input check happen here, so
        their errors reach the caller."""
        from wynd.controller.models import LocalTarget, Run
        from wynd.runtime.ids import new_id

        pid, target = req.process_id, req.target
        self._check_inputs(pid, req.inputs)
        local = isinstance(target, LocalTarget)
        if local:
            self._gate_local(pid)
        else:
            self._gate_image(pid, target.commit)
        run_id = new_id("run")
        run = Run(id=run_id, process_id=pid, commit=None if local else target.commit,
                  mode="local" if local else "image", target=target, trigger=trigger, status="running",
                  inputs=req.inputs, started_at=self.ctx.clock())
        with self._lock:
            self._pending[run_id] = run
        thread = threading.Thread(target=self._background, args=(run, req.inputs), name=f"wynd-{run_id}", daemon=True)
        thread.start()
        return run

    def get(self, run_id: str) -> Run:
        from wynd.controller.models import Run

        record = self.ctx.stores.runs.get(run_id)
        if record is not None and record.get("kind") == "run":
            return Run.from_record(record)
        with self._lock:
            pending = self._pending.get(run_id)
        if pending is None:
            raise NotFound(f"unknown run '{run_id}'")
        return pending

    def list(self, *, process_id: str | None = None, release_id: str | None = None, limit: int = 50) -> list[Run]:
        """Newest first."""
        from wynd.controller.models import Run

        fetch = limit if release_id is None else EVERY_RECORD
        records = self.ctx.stores.runs.list(kind="run", process=process_id, limit=fetch)
        runs = [Run.from_record(record) for record in records]
        with self._lock:
            pending = [run for run_id, run in self._pending.items() if all(r.id != run_id for r in runs)]
        runs = sorted(pending, key=lambda run: run.id, reverse=True) + runs
        if process_id is not None:
            runs = [run for run in runs if run.process_id == process_id]
        if release_id is not None:
            runs = [run for run in runs if run.release_id == release_id]
        return runs[:limit]

    def events(self, run_id: str, since: int = 0) -> list[dict]:
        """PLAN §3.13 events with `seq > since`, passed through unchanged."""
        self.get(run_id)
        return [event for event in self.ctx.stores.traces.read(run_id) if event["seq"] > since]

    def follow(
        self,
        run_id: str,
        *,
        since: int = 0,
        on_event: Callable[[dict], None],
        poll: float = 0.2,
        timeout: float | None = None,
    ) -> Run:
        """Deliver every event after `since` until the run ends; `Unavailable` when `timeout` passes first (the run
        keeps going)."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            run = self.get(run_id)
            for event in self.events(run_id, since):
                on_event(event)
                since = event["seq"]
            if run.status in TERMINAL:
                return run
            if deadline is not None and time.monotonic() >= deadline:
                raise Unavailable(f"run {run_id} is still {run.status} after {timeout}s")
            time.sleep(poll)

    # --- internals ------------------------------------------------------------------------------------------------

    def _run_local(
        self, pid: str, inputs: dict[str, Any], *, run_id: str | None, on_event: Callable[[dict], None] | None,
        trigger: RunTrigger,
    ) -> Run:
        from wynd.controller.models import Run
        from wynd.process.local import run_local

        ws = self.ctx.workspace()
        if pid not in ws.processes:
            raise NotFound(f"unknown process '{pid}'")
        metadata = {"trigger": trigger, "release_id": None, "target": {"kind": "local"}}
        with translated():
            result = run_local(ws, pid, inputs, env=self.ctl.env.resolve(), stores=self.ctx.stores, run_id=run_id,
                               on_event=on_event, metadata=metadata, venv_root=self.ctx.state_dir / "venvs")
        return Run.from_record(self.ctx.stores.runs.get(result.run_id))

    def _background(self, run: Run, inputs: dict[str, Any]) -> None:
        from wynd.controller.runs.image import run_image

        try:
            match run.mode:
                case "local":
                    self._run_local(run.process_id, inputs, run_id=run.id, on_event=None, trigger=run.trigger)
                case "image":
                    run_image(self.ctl, run.process_id, inputs, commit=run.commit, trigger=run.trigger, run_id=run.id)
        except Exception as err:  # noqa: BLE001 — recorded on the run; nobody is waiting on this thread
            if self.ctx.stores.runs.get(run.id) is None:
                self._record_failure(run, err)
        finally:
            with self._lock:
                self._pending.pop(run.id, None)

    def _record_failure(self, run: Run, err: Exception) -> None:
        from wynd.runtime.storage.models import RunRecord
        from wynd.spec.records import ProcessError

        message = getattr(err, "message", None) or str(err) or type(err).__name__
        error = ProcessError(run_id=run.id, process=run.process_id, step=None, cause="internal",
                             message=f"run failed to start: {message}", inputs=run.inputs)
        now = datetime.now(UTC)
        target = run.target.model_dump(mode="json")
        record = RunRecord(
            id=run.id, process=run.process_id, status="failed", created_at=now, updated_at=now,
            started_at=run.started_at, finished_at=now, mode=run.mode, inputs=run.inputs, exit="error",
            outputs={"error": error.model_dump(mode="json")}, error=error.model_dump(mode="json"),
            meta={"trigger": run.trigger, "release_id": None, "target": target},
        )
        self.ctx.stores.runs.create(record.model_dump(mode="json"))

    def _check_inputs(self, pid: str, inputs: dict[str, Any]) -> None:
        """The executor's boundary check, done before a background run starts (`Invalid`, HTTP 422)."""
        from pydantic import ValidationError

        from wynd.runtime.errors import InvalidProcessInputs

        ws = self.ctx.workspace()
        if pid not in ws.processes:
            raise NotFound(f"unknown process '{pid}'")
        with translated():
            model = ws.load_process(pid).doc.models().input
            try:
                model.model_validate(inputs)
            except ValidationError as err:
                raise InvalidProcessInputs(json.loads(err.json(include_url=False))) from None

    def _gate_local(self, pid: str) -> None:
        if pid not in self.ctx.workspace().processes:
            raise NotFound(f"unknown process '{pid}'")
        _refuse(pid, self.ctl.env.check(pid, mode="local"))

    def _gate_image(self, pid: str, commit: str | None) -> None:
        from wynd.controller.envcheck import check_manifest
        from wynd.controller.status import process_head

        if commit is None:
            with translated():
                found = process_head(self.ctx, pid)
            commit = None if found is None else found[0]
        if commit is None or self.ctx.artefacts.get_build(pid, commit) is None:
            raise NotBuilt(f"process '{pid}' has no build at {commit or 'its HEAD'}", hint=f"run `wynd build {pid}`")
        manifest = self.ctl.env.manifest(pid, commit)
        _refuse(pid, check_manifest(manifest, self.ctl.env.resolve_image(pid), mode="image"))


def _refuse(pid: str, check: EnvCheckDTO) -> None:
    if check.ok:
        return
    raise EnvMissing(
        f"process '{pid}' needs env var(s) that are not set: {', '.join(check.missing)}",
        details={"missing": check.missing, "issues": [issue.model_dump(mode="json") for issue in check.issues]},
        hint=f"set them in the environment or in the workspace .env file (see `wynd env check {pid}`)",
    )
