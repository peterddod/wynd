"""`KubeServingBackend`: one ConfigMap + Deployment + Service per release (`$DRAFTS/08 §5.7`, PLAN §11 item 5).
Entry point `wynd.serving_backends: kube`, selected by `WYND_SERVING_BACKEND=kube`.

Secret vars come from the per-process Secret `wynd-p-<slug(process)>`, never from `env`; non-secret values from `env`
(incl. `WYND_REGISTRY_JSON` and `WYND_MCP_<NAME>_URL`) go into the release ConfigMap. No mounts (PLAN §8.1). Pod
start/stop times are read back into the release's serving record (uptime, SPEC §15).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.kube.client import KubeClient
    from wynd.kube.config import KubeConfig
    from wynd.runtime.storage import Stores
    from wynd.spec.env_manifest import EnvManifest


class KubeServingBackend:
    name = "kube"
    supported_bindings = frozenset({"value", "from_env"})

    def __init__(self, cfg: KubeConfig, client: KubeClient):
        self.cfg = cfg
        self.client = client

    @classmethod
    def from_env(cls, *, env: Mapping[str, str], workspace_root: Path, state_dir: Path, stores: Stores,
                 handlers: Mapping[str, str] | None = None) -> KubeServingBackend:
        raise NotImplementedError("PLAN §11")

    def ensure(self, release: Release, manifest: EnvManifest, env: Mapping[str, str]) -> str:
        """Apply `render_process_release`; returns http://<release name>.<namespace>.svc:<serving port>."""
        raise NotImplementedError("PLAN §11")

    def status(self, release: Release) -> tuple[Literal["starting", "serving", "stopped", "error"], str | None]:
        raise NotImplementedError("PLAN §11")

    def remove(self, release_id: str) -> None:
        """Delete the release's Service, Deployment and ConfigMap (never the per-process Secret)."""
        raise NotImplementedError("PLAN §11")
