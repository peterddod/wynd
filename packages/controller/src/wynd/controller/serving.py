"""`ServeService`: warm process containers for `wynd serve` and image runs (PLAN §8.1; `$DRAFTS/06 §5.12`).

Container env = `EnvService.resolve_image(pid)` filtered to the manifest's vars, plus `WYND_REGISTRY_JSON` whenever
the current snapshot is non-empty and `WYND_RUN_API_TOKEN`. The manifest also lists the storage selectors
(`STORAGE_ENV`, incl. `WYND_HOME`) and `WYND_HOST`/`WYND_PORT`; the controller's values of those describe the host and
are never passed (the base image sets the container's own). Records `.wynd/serve/<container>.json` carry
`started_at`/`stopped_at`; container names are `wynd-<slug(pid)>-<commit[:7]>`.

A stopped or unreachable container keeps its record with `stopped_at` set (uptime accounting, SPEC §15); `list` and
`acquire` only consider records without `stopped_at`. Release containers (`release_id` set, CTL-REL) are never reused
by `acquire` nor stopped by image. Ephemeral containers (`acquire` when nothing warm is ready) are not recorded;
`ephemeral` maps their URL to their name so the caller can `stop` them.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from wynd.controller import docker
from wynd.controller.errors import EnvMissing, Invalid, Unavailable
from wynd.runtime.supervisor.client import RunApiClient, RunApiError

if TYPE_CHECKING:
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import ServedContainer
    from wynd.spec.env_manifest import EnvManifest

MANIFEST_LABEL = "dev.wynd.env-manifest"
PORT_LABEL = "dev.wynd.run-api-port"
SERVED_LABEL = "dev.wynd.served"
TOKEN_VAR = "WYND_RUN_API_TOKEN"
REGISTRY_JSON = "WYND_REGISTRY_JSON"
HOST_ONLY = {"WYND_HOST", "WYND_PORT"}                  # plus every STORAGE_ENV var (WYND_DATA_DIR, WYND_HOME, ...)
PROBE_TIMEOUT_S = 1.0
LOG_TAIL = 50


class ServeService:
    def __init__(
        self,
        ctx: ControllerContext,
        ctl: Controller,
        run_api_client: Callable[[str], RunApiClient] = RunApiClient,
    ) -> None:
        self.ctx = ctx
        self.ctl = ctl
        self.run_api_client = run_api_client
        self.ephemeral: dict[str, str] = {}          # url -> container name of unrecorded (ephemeral) containers

    def serve(
        self,
        image: str,
        *,
        port: int | None = None,
        extra_env_files: Sequence[Path] = (),
        timeout: float = 120.0,
        register: bool = True,
        name: str | None = None,
    ) -> ServedContainer:
        from wynd.controller.envcheck import check_manifest
        from wynd.controller.models import ServedContainer
        from wynd.spec.env_manifest import EnvManifest
        from wynd.spec.hashing import hash_obj
        from wynd.spec.workspace import slug

        labels = docker.image_labels(image)
        if MANIFEST_LABEL not in labels:
            raise Invalid(f"{image} is not a wynd process image (no {MANIFEST_LABEL} label)")
        manifest = EnvManifest.model_validate_json(labels[MANIFEST_LABEL])
        pid = manifest.process
        commit = labels.get("dev.wynd.commit") or manifest.commit or ""
        resolved = self.ctl.env.resolve_image(pid, extra_env_files)
        check = check_manifest(manifest, resolved, mode="image")
        if not check.ok:
            raise EnvMissing(
                f"image {image} needs env var(s) that are not set: {', '.join(check.missing)}",
                details={"missing": check.missing, "issues": [i.model_dump(mode="json") for i in check.issues]},
                hint="set them in the environment, the workspace .env file or an --env-file",
            )
        env = container_env(manifest, resolved)
        name = name or f"wynd-{slug(pid)}-{commit[:7]}"
        container_port = int(labels.get(PORT_LABEL, "8080"))

        self._mark_stopped(name)
        docker.rm(name)
        container_id = docker.run_detached(image, name=name, env=env, host_port=port, container_port=container_port,
                                           labels={SERVED_LABEL: "1"})
        try:
            url = f"http://127.0.0.1:{docker.host_port(name, container_port)}"
            self._client(url, env.get(TOKEN_VAR)).wait_ready(timeout)
        except (RunApiError, Unavailable) as err:
            tail = _logs_tail(name)
            docker.rm(name)
            raise Unavailable(f"container {name} did not become ready: {err}\n--- last {LOG_TAIL} log lines ---\n"
                              f"{tail}", details={"logs": tail}) from None
        served = ServedContainer(
            name=name, container_id=container_id, image=image, url=url, process=pid, commit=commit,
            started_at=self.ctx.clock(), env_hash=hash_obj({"image": image, "env": sorted(env.items())}),
        )
        if register:
            self._save(served)
        return served

    def acquire(self, image: str) -> tuple[str, bool]:
        """-> (run-API base URL, ephemeral); a ready registered container is found over HTTP only."""
        token = self.ctl.env.resolve().get(TOKEN_VAR)
        for served in self.list():
            if served.image != image or served.release_id is not None:
                continue
            client = self._client(served.url, token)
            client.timeout_s = PROBE_TIMEOUT_S
            try:
                client.ready()
            except RunApiError:
                self._mark_stopped(served.name)
                continue
            return served.url, False
        served = self.serve(image, register=False, name=f"wynd-run-{secrets.token_hex(4)}")
        self.ephemeral[served.url] = served.name
        return served.url, True

    def stop(self, name_or_image: str) -> list[str]:
        """Remove the recorded containers named `name_or_image` or serving that image (release containers only by
        name) and any ephemeral container of that name; their records keep `stopped_at`. -> the names stopped."""
        names = [s.name for s in self.list()
                 if s.name == name_or_image or (s.image == name_or_image and s.release_id is None)]
        names += [n for n in self.ephemeral.values() if n == name_or_image and n not in names]
        for name in names:
            docker.rm(name)
            self._mark_stopped(name)
            self.ephemeral = {url: n for url, n in self.ephemeral.items() if n != name}
        return names

    def list(self) -> list[ServedContainer]:
        """The recorded containers that have not been stopped, by name (no Docker call)."""
        return [served for served in self._records() if served.stopped_at is None]

    # --- records ------------------------------------------------------------------------------------------------------

    def _client(self, url: str, token: str | None) -> RunApiClient:
        client = self.run_api_client(url)
        client.token = token or None
        return client

    def _dir(self) -> Path:
        return self.ctx.state_dir / "serve"

    def _records(self) -> list[ServedContainer]:
        from wynd.controller.models import ServedContainer

        if not self._dir().is_dir():
            return []
        return [ServedContainer.model_validate_json(path.read_text()) for path in sorted(self._dir().glob("*.json"))]

    def _save(self, served: ServedContainer) -> None:
        path = self._dir() / f"{served.name}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(served.model_dump_json(indent=2) + "\n")
        os.replace(tmp, path)

    def _mark_stopped(self, name: str) -> None:
        for served in self._records():
            if served.name == name and served.stopped_at is None:
                self._save(served.model_copy(update={"stopped_at": self.ctx.clock(), "state": "stopped"}))


def container_env(manifest: EnvManifest, resolved: dict[str, str]) -> dict[str, str]:
    """The values of the manifest's vars that are set, plus `WYND_REGISTRY_JSON` (current snapshot) and
    `WYND_RUN_API_TOKEN` when set; never the controller's own storage selection or bind address (`HOST_ONLY`),
    which the base image sets for the container."""
    from wynd.runtime.storage import STORAGE_ENV

    host_only = {var.name for var in STORAGE_ENV} | HOST_ONLY
    names = ({var.name for var in manifest.vars} | {REGISTRY_JSON, TOKEN_VAR}) - host_only
    return {name: resolved[name] for name in sorted(names) if resolved.get(name)}


def _logs_tail(name: str) -> str:
    try:
        return docker.logs_tail(name, LOG_TAIL)
    except Exception as err:  # noqa: BLE001 — the tail only decorates the readiness error
        return f"(no logs: {err})"
