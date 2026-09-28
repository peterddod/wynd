"""`ReleaseService` (PLAN §8.1 releases row; `$DRAFTS/06 §5.14`).

Only built processes can be released. A release is a stored document (`releases/store.py`): the build's image, a
trigger (manual, schedule or webhook; every release can also be run manually) and an env binding per manifest var
(`{value}` literal or `{from_env}` read from the controller's resolved env; secrets are `from_env` only). `create`
and `update` validate against the build's manifest (`Invalid`, `EnvUnbound`); an enabled release is started warm in a
background thread, and `warm_all()` does that for every enabled release (serve-api startup).

`trigger` resolves the bindings (`EnvMissing` when a `from_env` source is unset, or when the container env fails the
image env check), ensures the serving container (`ServingBackend.ensure(release, manifest, env)`, env built with
`wynd.controller.serving.container_env` from the bindings plus the controller-supplied `WYND_RUN_API_TOKEN` and
`WYND_REGISTRY_JSON`), turns path inputs into run-API files (`runs/image.path_inputs_to_files`), submits through the
injectable run-API client, records the run as `running` and mirrors it in a daemon thread. Every fire is recorded,
failed ones with `ok: false` and the error. `WYND_REGISTRY_JSON` is the user-registry snapshot of the process at the
release's commit, so a release never depends on the working tree.

Serving calls for one release (`ensure`, `remove`) are serialised, so disabling or deleting a release that is still
starting removes the container once it is up. The release document's `state`/`state_detail` record the last known
serving outcome for `status.py`; the `Release` DTO reads the backend's live status and falls back to them only while
the backend reports no container for an enabled release (starting, or failed to start).
"""

from __future__ import annotations

import base64
import hmac
import json
import logging
import re
import threading
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, tzinfo
from typing import TYPE_CHECKING, Any, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from wynd.controller.errors import (
    Conflict,
    EnvMissing,
    EnvUnbound,
    Invalid,
    NotBuilt,
    NotFound,
    Unauthorized,
    Unavailable,
    WyndError,
    translated,
)
from wynd.runtime.supervisor.client import RunApiClient, RunApiError

if TYPE_CHECKING:
    from wynd.controller.api.models_web import CreateReleaseRequest, EnvBinding, Release, ReleasePatch, Trigger
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import EnvCheckDTO, Run, TriggerFire
    from wynd.process.artefacts import BuildInfo
    from wynd.spec.env_manifest import EnvManifest

TOKEN_VAR = "WYND_RUN_API_TOKEN"
REGISTRY_JSON = "WYND_REGISTRY_JSON"
API_TOKEN_VAR = "WYND_API_TOKEN"
ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
log = logging.getLogger(__name__)


class ReleaseService:
    def __init__(
        self,
        ctx: ControllerContext,
        ctl: Controller,
        run_api_client: Callable[[str], RunApiClient] = RunApiClient,
    ) -> None:
        self.ctx = ctx
        self.ctl = ctl
        self.run_api_client = run_api_client
        self.threads: list[threading.Thread] = []      # background warm-ups and run mirrors (tests join them)
        self._lock = threading.Lock()
        self._serving_locks: dict[str, threading.Lock] = {}

    def create(self, req: CreateReleaseRequest) -> Release:
        from wynd.controller.releases.store import RELEASES, to_release
        from wynd.runtime.ids import new_id

        if req.process_id not in self.ctx.workspace().processes:
            raise NotFound(f"unknown process '{req.process_id}'")
        commit = self._commit(req.commit)
        build = self._check(req.process_id, commit, req.trigger, req.env)
        now = self.ctx.clock()
        doc = {
            "id": new_id("rel"), "process_id": req.process_id, "commit": commit, "image": _image(build),
            "trigger": req.trigger.model_dump(mode="json"), "env": _dump_env(req.env), "enabled": req.enabled,
            "serving": self.ctx.serving.name, "triggers": self.ctx.triggers.name,
            "created_at": now, "updated_at": now, "state": "starting" if req.enabled else "stopped",
            "state_detail": None, "last_fire_key": None, "last_fired_at": None,
        }
        release = to_release(doc)
        self._require_bound(release)
        if release.trigger.kind == "webhook" and release.trigger.secret_env is None and not self._api_token():
            log.warning("release %s: its webhook has no secret_env and WYND_API_TOKEN is not set, so anyone who can "
                        "reach the controller can fire it", release.id)
        self.ctx.docs.put(RELEASES, release.id, doc)
        if release.enabled:
            self.ctx.triggers.install(release)
            self._background(self._warm, release)
        return self._dto(doc)

    def list(self, *, process_id: str | None = None) -> list[Release]:
        """Newest first."""
        from wynd.controller.releases.store import RELEASES

        where = None if process_id is None else {"process_id": process_id}
        return [self._dto(doc) for doc in self.ctx.docs.list(RELEASES, where=where)]

    def get(self, release_id: str) -> Release:
        from wynd.controller.releases.store import release_doc

        return self._dto(release_doc(self.ctx.docs, release_id))

    def update(self, release_id: str, patch: ReleasePatch) -> Release:
        """A changed trigger or env binding, or re-enabling, is validated as in `create`; disabling never is. A changed
        env or disabling removes the container; re-enabling or a changed env of an enabled release starts it warm."""
        from wynd.controller.releases.store import RELEASES, release_doc, to_release

        doc = release_doc(self.ctx.docs, release_id)
        old = to_release(doc)
        trigger = old.trigger if patch.trigger is None else patch.trigger
        env = old.env if patch.env is None else patch.env
        enabled = old.enabled if patch.enabled is None else patch.enabled
        changes: dict[str, Any] = {"trigger": trigger.model_dump(mode="json"), "env": _dump_env(env),
                                   "enabled": enabled, "updated_at": self.ctx.clock()}
        release = to_release({**doc, **changes})
        stop = old.enabled and (not enabled or changes["env"] != doc.get("env"))
        restart = enabled and (not old.enabled or changes["env"] != doc.get("env"))
        if patch.trigger is not None or patch.env is not None or restart:
            self._check(old.process_id, old.commit, trigger, env)
            self._require_bound(release)
        state = {"state": "starting" if restart else "stopped", "state_detail": None}
        # the document changes first, so a warm-up waiting for the serving lock sees them and stands down
        updated = self.ctx.docs.update(RELEASES, release_id, changes | (state if stop or restart else {}))
        if stop:
            with self._serving_lock(release_id):
                self.ctx.serving.remove(release_id)
                updated = self.ctx.docs.update(RELEASES, release_id, state)
        if changes["trigger"] != doc["trigger"] or enabled != old.enabled:
            if enabled:
                self.ctx.triggers.install(release)
            else:
                self.ctx.triggers.remove(release_id)
        if restart:
            self._background(self._warm, release)
        return self._dto(updated)

    def delete(self, release_id: str) -> None:
        """Removes the container and the trigger, then the document; the fire history is kept."""
        from wynd.controller.releases.store import RELEASES, release_doc

        release_doc(self.ctx.docs, release_id)
        with self._serving_lock(release_id):
            self.ctx.serving.remove(release_id)
            self.ctx.docs.delete(RELEASES, release_id)
        self.ctx.triggers.remove(release_id)

    def trigger(
        self,
        release_id: str,
        inputs: dict[str, Any] | None,
        *,
        source: Literal["manual", "schedule", "webhook"],
    ) -> Run:
        """Fire the release (`inputs` default to a schedule's fixed inputs, else `{}`); -> the run, `running`."""
        from wynd.controller.releases.store import FIRES, release_doc, to_release
        from wynd.runtime.ids import new_id

        release = to_release(release_doc(self.ctx.docs, release_id))
        fire = {"id": new_id("fire"), "release_id": release.id, "source": source, "at": self.ctx.clock(),
                "run_id": None, "ok": False, "error": None, "started_at": None, "finished_at": None}
        try:
            run, client, metadata = self._submit(release, inputs, source)
        except Exception as err:
            self.ctx.docs.put(FIRES, fire["id"], {**fire, "error": _message(err)})
            if isinstance(err, RunApiError):
                raise _translate(err) from None
            raise
        self.ctx.docs.put(FIRES, fire["id"], {**fire, "ok": True, "run_id": run.id, "started_at": run.started_at})
        self._background(self._mirror, client, run.id, metadata, fire["id"])
        return run

    def env_check(self, release_id: str) -> EnvCheckDTO:
        from wynd.controller.releases.store import release_doc, to_release

        return self._env_check(to_release(release_doc(self.ctx.docs, release_id)))

    def verify_webhook(self, release_id: str, presented: str | None) -> Release:
        """The presented secret must equal the value of the trigger's `secret_env` in the controller's env; without a
        `secret_env` it must equal `WYND_API_TOKEN` when that is set, and the hook is open otherwise."""
        from wynd.controller.api.models_web import WebhookTrigger
        from wynd.controller.releases.store import release_doc, to_release

        doc = release_doc(self.ctx.docs, release_id)
        release = to_release(doc)
        if not isinstance(release.trigger, WebhookTrigger):
            raise NotFound(f"release '{release_id}' has no webhook trigger")
        if not release.enabled:
            raise Conflict(f"release '{release_id}' is disabled")
        secret_env = release.trigger.secret_env
        resolved = self.ctl.env.resolve()
        expected = resolved.get(secret_env or API_TOKEN_VAR)
        if not expected:
            if secret_env is not None:
                raise Unauthorized(f"the webhook secret {secret_env} is not set in the controller's environment")
            return self._dto(doc)
        if presented is None or not hmac.compare_digest(presented.encode(), expected.encode()):
            raise Unauthorized("missing or invalid webhook secret")
        return self._dto(doc)

    def fires(self, release_id: str, limit: int = 50) -> list[TriggerFire]:
        """Newest first; kept after the release is deleted."""
        from wynd.controller.models import TriggerFire
        from wynd.controller.releases.store import FIRES

        docs = self.ctx.docs.list(FIRES, where={"release_id": release_id}, limit=limit)
        return [TriggerFire.model_validate(doc) for doc in docs]

    def warm_all(self) -> None:
        """Ensure every enabled release's container in background threads (serve-api startup, so a restarted
        controller serves its releases warm)."""
        from wynd.controller.releases.store import RELEASES, to_release

        for doc in self.ctx.docs.list(RELEASES, where={"enabled": True}):
            self._background(self._warm, to_release(doc))

    # --- validation ---------------------------------------------------------------------------------------------------

    def _commit(self, commit: str) -> str:
        from wynd.process.errors import GitError
        from wynd.process.git import rev_parse

        try:
            return rev_parse(self.ctx.root, f"{commit}^{{commit}}")
        except GitError:
            raise NotFound(f"'{commit}' is not a commit of this repository") from None

    def _build(self, pid: str, commit: str) -> BuildInfo:
        build = self.ctx.artefacts.get_build(pid, commit)
        if build is None or not build.image:
            raise NotBuilt(f"process '{pid}' has no image build at {commit[:7]}: only built processes can be released",
                           hint=f"run `wynd build {pid}`")
        return build

    def _check(self, pid: str, commit: str, trigger: Trigger, env: Mapping[str, EnvBinding]) -> BuildInfo:
        build = self._build(pid, commit)
        self._check_trigger(pid, commit, trigger)
        self._check_bindings(build.manifest, env)
        return build

    def _check_trigger(self, pid: str, commit: str, trigger: Trigger) -> None:
        from wynd.controller.api.models_web import ScheduleTrigger, WebhookTrigger
        from wynd.controller.releases.cron import CronExpr

        match trigger:
            case ScheduleTrigger(cron=cron, timezone=timezone, inputs=inputs):
                CronExpr.parse(cron).next_after(self.ctx.clock().astimezone(_zone(timezone)))
                schema = self.ctl.processes.interface(pid, commit).inputs or {}
                unknown = sorted(set(inputs) - set(schema.get("properties") or {}))
                missing = sorted(set(schema.get("required") or []) - set(inputs))
                if unknown or missing:
                    raise Invalid(f"schedule inputs must be the inputs of process '{pid}': "
                                  f"unknown {unknown or 'none'}, missing {missing or 'none'}",
                                  details={"unknown": unknown, "missing": missing})
            case WebhookTrigger(secret_env=secret_env) if secret_env is not None and not ENV_NAME.match(secret_env):
                raise Invalid(f"webhook secret_env {secret_env!r} is not a valid env var name")

    def _check_bindings(self, manifest: EnvManifest, env: Mapping[str, EnvBinding]) -> None:
        from wynd.controller.api.models_web import FromEnvBinding, ValueBinding
        from wynd.controller.serving import HOST_ONLY
        from wynd.runtime.storage import STORAGE_ENV

        declared = {var.name: var for var in manifest.vars}
        unknown = sorted(set(env) - set(declared))
        if unknown:
            raise Invalid(f"env binding(s) for var(s) the build's manifest does not declare: {', '.join(unknown)}",
                          details={"unknown": unknown})
        host_only = {var.name for var in STORAGE_ENV} | HOST_ONLY
        supported = self.ctx.serving.supported_bindings
        for name, binding in sorted(env.items()):
            kind = "value" if isinstance(binding, ValueBinding) else "from_env"
            if name in host_only:
                raise Invalid(f"{name} is set by the base image for the container and cannot be bound")
            if kind not in supported:
                raise Invalid(f"env var {name}: serving backend {self.ctx.serving.name!r} does not support "
                              f"{kind} bindings (supported: {', '.join(sorted(supported))})")
            if isinstance(binding, ValueBinding) and declared[name].secret:
                raise Invalid(f"secret env var {name} must be bound with from_env: secrets stay env references")
            if isinstance(binding, FromEnvBinding) and not ENV_NAME.match(binding.from_env):
                raise Invalid(f"env var {name}: from_env {binding.from_env!r} is not a valid env var name")

    def _require_bound(self, release: Release) -> None:
        unbound = self._env_check(release).unbound
        if unbound:
            raise EnvUnbound(f"required env var(s) with no binding: {', '.join(unbound)}",
                             details={"unbound": unbound}, hint="bind each with {value} or {from_env}")

    def _env_check(self, release: Release) -> EnvCheckDTO:
        """`EnvService.check_release`, except that `WYND_REGISTRY_JSON` is never unbound: the controller supplies it."""
        check = self.ctl.env.check_release(release)
        unbound = [name for name in check.unbound if name != REGISTRY_JSON]
        return check.model_copy(update={"unbound": unbound, "ok": not unbound and not check.missing})

    def _api_token(self) -> str | None:
        return self.ctl.env.resolve().get(API_TOKEN_VAR) or None

    # --- serving and firing -------------------------------------------------------------------------------------------

    def _container_env(self, release: Release) -> tuple[EnvManifest, dict[str, str]]:
        """The build's manifest and the container env: the bound values, `WYND_RUN_API_TOKEN` from the controller's
        env and the registry snapshot of the process at the release's commit (`EnvMissing` when unusable)."""
        from wynd.controller.api.models_web import FromEnvBinding, ValueBinding
        from wynd.controller.envcheck import check_manifest
        from wynd.controller.serving import container_env
        from wynd.process.envmanifest import registry_snapshot
        from wynd.process.workspace import CommitTree

        manifest = self._build(release.process_id, release.commit).manifest
        resolved = self.ctl.env.resolve()
        with translated():
            at_commit = self.ctx.workspace(CommitTree(self.ctx.root, release.commit))
            snapshot = registry_snapshot(at_commit.load_process(release.process_id), self.ctx.stores.registry)
        supplied = {TOKEN_VAR: resolved.get(TOKEN_VAR, "")}
        if snapshot:
            supplied[REGISTRY_JSON] = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
        bound: dict[str, str] = {}
        unset: list[str] = []
        for name, binding in sorted(release.env.items()):
            match binding:
                case ValueBinding(value=value):
                    bound[name] = value
                case FromEnvBinding(from_env=source) if resolved.get(source):
                    bound[name] = resolved[source]
                case FromEnvBinding(from_env=source):
                    unset.append(f"{name} (from {source})")
        if unset:
            raise EnvMissing(f"release {release.id}: env binding source(s) not set: {', '.join(unset)}",
                             details={"missing": unset},
                             hint="set them in the controller's environment or the workspace .env file")
        env = container_env(manifest, {**supplied, **bound})
        check = check_manifest(manifest, env, mode="image")
        if not check.ok:
            raise EnvMissing(f"release {release.id} cannot start: env var(s) not set: {', '.join(check.missing)}",
                             details={"missing": check.missing,
                                      "issues": [issue.model_dump(mode="json") for issue in check.issues]})
        return manifest, env

    def _submit(
        self, release: Release, inputs: dict[str, Any] | None, source: str
    ) -> tuple[Run, RunApiClient, dict[str, Any]]:
        from wynd.controller.api.models_web import ScheduleTrigger
        from wynd.controller.models import Run
        from wynd.controller.runs.image import path_inputs_to_files
        from wynd.runtime.ids import new_id
        from wynd.runtime.storage.models import RunRecord

        if not release.enabled:
            raise Conflict(f"release '{release.id}' is disabled", hint="enable it first")
        if inputs is None:
            inputs = dict(release.trigger.inputs) if isinstance(release.trigger, ScheduleTrigger) else {}
        manifest, env = self._container_env(release)
        sent, files = path_inputs_to_files(self.ctl, release.process_id, inputs)
        with self._serving_lock(release.id):
            if not self._current(release):
                raise Conflict(f"release '{release.id}' was disabled, rebound or deleted while it was being fired")
            try:
                url = self.ctx.serving.ensure(release, manifest, env)
            except Exception as err:
                self._set_state(release.id, "error", _message(err))
                raise
            self._set_state(release.id, "serving")

        run_id = new_id("run")
        metadata = {"trigger": source, "release_id": release.id,
                    "target": {"kind": "release", "commit": release.commit}}
        client = self.run_api_client(url)
        client.token = env.get(TOKEN_VAR) or None
        client.submit(sent, run_id=run_id, metadata=metadata,
                      files={name: base64.b64decode(f["content_base64"]) for name, f in files.items()})
        now = self.ctx.clock()
        record = RunRecord(id=run_id, process=release.process_id, status="running", created_at=now, updated_at=now,
                           started_at=now, ref=release.commit, mode="image", inputs=inputs,
                           trace=self.ctx.stores.traces.uri(run_id), meta=metadata).model_dump(mode="json")
        self.ctx.stores.runs.create(record)
        return Run.from_record(record), client, metadata

    def _mirror(self, client: RunApiClient, run_id: str, metadata: dict[str, Any], fire_id: str) -> None:
        """Mirror the run into the stores, then close the fire; a lost run is recorded as failed (cause internal)."""
        from wynd.controller.releases.store import FIRES
        from wynd.controller.runs.mirror import mirror_run

        try:
            record = mirror_run(self.ctx, client, run_id, metadata=metadata)
            patch: dict[str, Any] = {"finished_at": record.get("finished_at") or self.ctx.clock()}
        except Exception as err:  # noqa: BLE001 — background thread: the run and fire records report it
            message = f"lost run {run_id}: {_message(err)}"
            self._fail_run(run_id, message)
            patch = {"ok": False, "error": message, "finished_at": self.ctx.clock()}
        self.ctx.docs.update(FIRES, fire_id, patch)

    def _fail_run(self, run_id: str, message: str) -> None:
        from wynd.spec.records import ProcessError

        record = self.ctx.stores.runs.get(run_id) or {}
        error = ProcessError(run_id=run_id, process=record.get("process", ""), step=None, cause="internal",
                             message=message, inputs=record.get("inputs") or {}).model_dump(mode="json")
        self.ctx.stores.runs.update(run_id, {"status": "failed", "exit": "error", "outputs": {"error": error},
                                             "error": error, "finished_at": self.ctx.clock()})

    def _warm(self, release: Release) -> None:
        """Start the release's container (background); the outcome goes to the document's `state`."""
        with self._serving_lock(release.id):
            if not self._current(release):
                return
            try:
                manifest, env = self._container_env(release)
                self.ctx.serving.ensure(release, manifest, env)
            except Exception as err:  # noqa: BLE001 — background thread: the release's state reports it
                log.warning("release %s did not start: %s", release.id, _message(err))
                self._set_state(release.id, "error", _message(err))
                return
            self._set_state(release.id, "serving")

    def _current(self, release: Release) -> bool:
        """Whether the stored release is still enabled with the same env binding (checked under the serving lock:
        a disable, rebind or delete that came first wins)."""
        from wynd.controller.releases.store import RELEASES

        doc = self.ctx.docs.get(RELEASES, release.id)
        return doc is not None and doc["enabled"] and doc.get("env") == _dump_env(release.env)

    def _set_state(self, release_id: str, state: str, detail: str | None = None) -> None:
        from wynd.controller.releases.store import RELEASES

        try:
            self.ctx.docs.update(RELEASES, release_id, {"state": state, "state_detail": detail})
        except KeyError:
            return                                          # deleted meanwhile

    def _serving_lock(self, release_id: str) -> threading.Lock:
        with self._lock:
            return self._serving_locks.setdefault(release_id, threading.Lock())

    def _background(self, target: Callable[..., None], *args: Any) -> None:
        thread = threading.Thread(target=target, args=args, name=f"wynd-release-{target.__name__}", daemon=True)
        with self._lock:
            self.threads = [t for t in self.threads if t.is_alive()] + [thread]
        thread.start()

    # --- DTO ----------------------------------------------------------------------------------------------------------

    def _dto(self, doc: dict[str, Any]) -> Release:
        from wynd.controller.releases.store import to_release
        from wynd.controller.status import process_head
        from wynd.process.git import count_touching

        release = to_release(doc)
        with translated():
            found = process_head(self.ctx, release.process_id)
        behind = 0 if found is None else count_touching(self.ctx.root, release.commit, found[0], found[1])
        state, detail = self.ctx.serving.status(release)
        if state == "stopped" and release.enabled and doc.get("state") in ("starting", "error"):
            state, detail = doc["state"], doc.get("state_detail")
        return release.model_copy(update={"behind": behind, "state": state, "state_detail": detail,
                                          "next_fire_at": self._next_fire(release)})

    def _next_fire(self, release: Release) -> datetime | None:
        from wynd.controller.api.models_web import ScheduleTrigger
        from wynd.controller.releases.cron import CronExpr

        if not release.enabled or not isinstance(release.trigger, ScheduleTrigger):
            return None
        try:
            tz = _zone(release.trigger.timezone)
            return CronExpr.parse(release.trigger.cron).next_after(self.ctx.clock().astimezone(tz)).astimezone(UTC)
        except Invalid:
            return None


def _zone(name: str | None) -> tzinfo:
    if name is None:
        return UTC
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        raise Invalid(f"unknown timezone {name!r} (use an IANA name such as Europe/London, or null for UTC)") from None


def _image(build: BuildInfo) -> str:
    """The pushed `ref@digest` when the build was pushed, else the local tag (`image_digest` alone is a bare digest
    no container can be started from)."""
    return build.pushed[0] if build.pushed else build.image


def _dump_env(env: Mapping[str, EnvBinding]) -> dict[str, dict[str, str]]:
    return {name: binding.model_dump(mode="json") for name, binding in sorted(env.items())}


def _message(err: BaseException) -> str:
    return getattr(err, "message", None) or str(err) or type(err).__name__


def _translate(err: RunApiError) -> WyndError:
    match err.status:
        case 400 | 413 | 422:
            return Invalid(str(err), details=err.body)
        case 409:
            return Conflict(str(err), details=err.body)
    return Unavailable(f"run API: {err}", details=err.body)
