"""`KubeTriggerBackend`: schedule triggers as CronJobs that run `wynd-kube fire` (`$DRAFTS/08 §5.8`,
`$DRAFTS/06 §6.7`). Entry point `wynd.trigger_backends: kube`, selected by `WYND_TRIGGER_BACKEND=kube`. Webhook and
manual triggers need no objects: the controller serves `/hooks/releases/{id}` and runs releases itself.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.controller.controller import Controller
    from wynd.kube.client import KubeClient
    from wynd.kube.config import KubeConfig
    from wynd.runtime.storage import Stores


class KubeTriggerBackend:
    name = "kube"

    def __init__(self, cfg: KubeConfig, client: KubeClient):
        self.cfg = cfg
        self.client = client

    @classmethod
    def from_env(cls, *, env: Mapping[str, str], workspace_root: Path, state_dir: Path, stores: Stores,
                 handlers: Mapping[str, str] | None = None) -> KubeTriggerBackend:
        raise NotImplementedError("PLAN §11")

    def install(self, release: Release) -> None:
        """Apply `render_cronjob` for a schedule trigger; nothing for webhook/manual."""
        raise NotImplementedError("PLAN §11")

    def remove(self, release_id: str) -> None:
        raise NotImplementedError("PLAN §11")

    def reconcile(self, releases: Sequence[Release]) -> None:
        """Delete trigger CronJobs with no schedule release; apply one per schedule release."""
        raise NotImplementedError("PLAN §11")

    def start(self, ctl: Controller) -> None:
        """No in-controller scheduler: the CronJobs fire."""

    def stop(self) -> None:
        """Nothing to stop."""
