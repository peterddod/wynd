"""Where releases run: `ServingBackend`, `DockerServing` (PLAN §8.1 releases row; `$DRAFTS/06 §5.14` "Backends").
Stub; CTL-REL.

Selected by `WYND_SERVING_BACKEND` (default `docker`) from entry-point group `wynd.serving_backends`; factories take
`(*, env, workspace_root, state_dir, stores, handlers=None)`. No mounts: container data reaches it as env only
(`env` = `EnvService.resolve_image`, incl. `WYND_REGISTRY_JSON` and `WYND_RUN_API_TOKEN`). Container start/stop times
are kept per release (SPEC §15).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, Protocol

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.runtime.storage import Stores
    from wynd.spec.env_manifest import EnvManifest

ServingState = Literal["starting", "serving", "stopped", "error"]


class ServingBackend(Protocol):
    name: str
    supported_bindings: frozenset[str]

    def ensure(self, release: Release, manifest: EnvManifest, env: Mapping[str, str]) -> str: ...   # ready run-API URL
    def status(self, release: Release) -> tuple[ServingState, str | None]: ...
    def remove(self, release_id: str) -> None: ...


class DockerServing:
    name = "docker"
    supported_bindings = frozenset({"value", "from_env"})

    def __init__(
        self,
        *,
        env: Mapping[str, str],
        workspace_root: Path,
        state_dir: Path,
        stores: Stores,
        handlers: Mapping[str, str] | None = None,
    ) -> None:
        raise NotImplementedError("PLAN §8.1")

    def ensure(self, release: Release, manifest: EnvManifest, env: Mapping[str, str]) -> str:
        """Container `wynd-rel-<id>`; reused while its env hash matches and `/readyz` answers."""
        raise NotImplementedError("PLAN §8.1")

    def status(self, release: Release) -> tuple[ServingState, str | None]:
        raise NotImplementedError("PLAN §8.1")

    def remove(self, release_id: str) -> None:
        raise NotImplementedError("PLAN §8.1")


def open_serving_backend(name: str, **kw: Any) -> ServingBackend:
    """Entry point `name` of group `wynd.serving_backends`, called with the backend factory keywords (PLAN §8)."""
    raise NotImplementedError("PLAN §8")
