"""Where releases run: `ServingBackend`, `DockerServing` (PLAN §8.1 releases row; `$DRAFTS/06 §5.14` "Backends").

Selected by `WYND_SERVING_BACKEND` (default `docker`) from entry-point group `wynd.serving_backends`; factories take
`(*, env, workspace_root, state_dir, stores, handlers=None)`. No mounts: container data reaches it as env only
(`env` is built by `ReleaseService` with `wynd.controller.serving.container_env`, incl. `WYND_REGISTRY_JSON` and
`WYND_RUN_API_TOKEN`).

`DockerServing` runs release `<id>` as container `wynd-rel-<id>` (`--restart unless-stopped`) and records it in
`.wynd/serve/wynd-rel-<id>.json` (a `ServedContainer` with `release_id`, the env hash, never the values, and `state`).
The container is reused while its image + env hash matches and `/readyz` answers (HTTP only, no Docker call). When a
container is replaced or removed its record gets `stopped_at` and is kept as `wynd-rel-<id>.<started>.json`, so
every container's `started_at`/`stopped_at` stays on disk per release (SPEC §15). The run-API client used for
readiness probes is injectable (`run_api_client`, as for `ServeService`).
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from importlib import metadata
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

from wynd.controller import docker
from wynd.controller.errors import Invalid, Unavailable
from wynd.runtime.supervisor.client import RunApiClient, RunApiError

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.controller.models import ServedContainer
    from wynd.runtime.storage import Stores
    from wynd.spec.env_manifest import EnvManifest

ServingState = Literal["starting", "serving", "stopped", "error"]

GROUP = "wynd.serving_backends"
PORT_LABEL = "dev.wynd.run-api-port"
SERVED_LABEL = "dev.wynd.served"
RELEASE_LABEL = "dev.wynd.release"
PROBE_TIMEOUT_S = 1.0


class ServingBackend(Protocol):
    name: str
    supported_bindings: frozenset[str]

    def ensure(self, release: Release, manifest: EnvManifest, env: Mapping[str, str]) -> str: ...   # ready run-API URL
    def status(self, release: Release) -> tuple[ServingState, str | None]: ...
    def remove(self, release_id: str) -> None: ...


class DockerServing:
    name = "docker"
    supported_bindings = frozenset({"value", "from_env"})
    ready_timeout_s = 120.0

    def __init__(
        self,
        *,
        env: Mapping[str, str],
        workspace_root: Path,
        state_dir: Path,
        stores: Stores,
        handlers: Mapping[str, str] | None = None,
        run_api_client: Callable[[str], RunApiClient] = RunApiClient,
    ) -> None:
        self.serve_dir = Path(state_dir) / "serve"
        self.run_api_client = run_api_client

    def ensure(self, release: Release, manifest: EnvManifest, env: Mapping[str, str]) -> str:
        """Container `wynd-rel-<id>`; reused while its env hash matches and `/readyz` answers."""
        from wynd.controller.models import ServedContainer
        from wynd.spec.hashing import hash_obj

        name = container_name(release.id)
        env_hash = hash_obj({"image": release.image, "env": sorted(env.items())})
        current = self._current(name)
        if current is not None and current.env_hash == env_hash and self._ready(current.url):
            if current.state != "serving":
                self._save(current.model_copy(update={"state": "serving"}))
            return current.url

        self._retire(name)
        docker.rm(name)
        port = int(docker.image_labels(release.image).get(PORT_LABEL, "8080"))
        container_id = docker.run_detached(release.image, name=name, env=env, host_port=None, container_port=port,
                                           labels={RELEASE_LABEL: release.id, SERVED_LABEL: "1"},
                                           restart="unless-stopped")
        record = ServedContainer(name=name, container_id=container_id, image=release.image, url="",
                                 process=release.process_id, commit=release.commit, started_at=_now(),
                                 release_id=release.id, env_hash=env_hash, state="starting")
        self._save(record)
        try:
            record = record.model_copy(update={"url": f"http://127.0.0.1:{docker.host_port(name, port)}"})
            self._save(record)
            client = self.run_api_client(record.url)
            client.wait_ready(self.ready_timeout_s)
        except (RunApiError, Unavailable) as err:
            self._save(record.model_copy(update={"state": "error"}))
            tail = _logs_tail(name, 50) or "(no output)"
            raise Unavailable(f"release container {name} did not become ready: {err}\n--- last log lines ---\n{tail}",
                              details={"logs": tail}) from None
        self._save(record.model_copy(update={"state": "serving"}))
        return record.url

    def status(self, release: Release) -> tuple[ServingState, str | None]:
        """No live record -> stopped; a recorded error -> error with the last log line; `/readyz` answering within
        1 s -> serving; otherwise starting, or error once the ready timeout has passed since the start."""
        name = container_name(release.id)
        current = self._current(name)
        if current is None:
            return "stopped", None
        if current.state == "error":
            return "error", _logs_tail(name, 1)
        if current.url and self._ready(current.url):
            return "serving", None
        if _now() - current.started_at > timedelta(seconds=self.ready_timeout_s):
            return "error", _logs_tail(name, 1)
        return "starting", None

    def remove(self, release_id: str) -> None:
        name = container_name(release_id)
        if self._current(name) is not None:
            docker.rm(name)
        self._retire(name)

    # --- records ------------------------------------------------------------------------------------------------------

    def _ready(self, url: str) -> bool:
        client = self.run_api_client(url)
        client.timeout_s = PROBE_TIMEOUT_S
        try:
            client.ready()
        except RunApiError:
            return False
        return True

    def _path(self, name: str) -> Path:
        return self.serve_dir / f"{name}.json"

    def _current(self, name: str) -> ServedContainer | None:
        """The record of the running (or starting) container, None when there is none or it was stopped."""
        from wynd.controller.models import ServedContainer

        path = self._path(name)
        if not path.is_file():
            return None
        record = ServedContainer.model_validate_json(path.read_text())
        return None if record.stopped_at is not None else record

    def _retire(self, name: str) -> None:
        """Move the current record aside as `<name>.<started>.json`, with `stopped_at` set if it was running."""
        from wynd.controller.models import ServedContainer

        path = self._path(name)
        if not path.is_file():
            return
        record = ServedContainer.model_validate_json(path.read_text())
        if record.stopped_at is None:
            record = record.model_copy(update={"stopped_at": _now(), "state": "stopped"})
        _write(self.serve_dir / f"{name}.{record.started_at:%Y%m%dT%H%M%S%f}.json", record)
        path.unlink()

    def _save(self, record: ServedContainer) -> None:
        _write(self._path(record.name), record)


def container_name(release_id: str) -> str:
    return f"wynd-rel-{release_id}"


def open_serving_backend(name: str, **kw: Any) -> ServingBackend:
    """Entry point `name` of group `wynd.serving_backends`, called with the backend factory keywords (PLAN §8)."""
    found = list(metadata.entry_points(group=GROUP, name=name))
    if not found:
        known = sorted(ep.name for ep in metadata.entry_points(group=GROUP))
        raise Invalid(f"unknown serving backend {name!r} (WYND_SERVING_BACKEND); installed: {', '.join(known)}")
    return found[0].load()(**kw)


def _now() -> datetime:
    return datetime.now(UTC)


def _write(path: Path, record: ServedContainer) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(record.model_dump_json(indent=2) + "\n")
    os.replace(tmp, path)


def _logs_tail(name: str, lines: int) -> str | None:
    try:
        return docker.logs_tail(name, lines).strip() or None
    except Exception as err:  # noqa: BLE001 — the log tail only decorates a state; Docker may be gone
        return f"(no logs: {err})"
