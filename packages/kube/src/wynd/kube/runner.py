"""`KubeJobRunner`: the `JobRunner` (PLAN §3.18) that runs each job as a Kubernetes Job (`$DRAFTS/08 §5.6`,
PLAN §11 items 1-2). Entry point `wynd.job_runners: kube`, selected by `WYND_JOB_RUNNER=kube`.

Records are `JobRecord` dicts in the shared `RunRegistry` (`stores.runs`; the file backend on the RWX state PVC),
created before the Job is applied because the pod loads its record by id; job ids come from `new_id("job")`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from wynd.kube.client import KubeClient
    from wynd.kube.config import KubeConfig
    from wynd.process.jobs import JobKind, JobRecord
    from wynd.runtime.storage import Stores
    from wynd.runtime.storage.base import RunRegistry


class KubeJobRunner:
    name = "kube"

    def __init__(self, cfg: KubeConfig, client: KubeClient, runs: RunRegistry, *,
                 handlers: Mapping[str, str] | None = None):
        self.cfg = cfg
        self.client = client
        self.runs = runs
        self.handlers = handlers

    @classmethod
    def from_env(cls, *, env: Mapping[str, str], workspace_root: Path, state_dir: Path, stores: Stores,
                 handlers: Mapping[str, str] | None = None) -> KubeJobRunner:
        raise NotImplementedError("PLAN §11")

    def submit(self, kind: JobKind, ref: str, inputs: Mapping[str, Any]) -> str:
        """Create the record, then apply `render_job`; a KubeApiError marks the record failed and re-raises."""
        raise NotImplementedError("PLAN §11")

    def status(self, job_id: str) -> JobRecord:
        """The record, after failing it when its Kubernetes Job is gone, has a Failed condition, or completed without
        the pod recording a result."""
        raise NotImplementedError("PLAN §11")

    def logs(self, job_id: str, offset: int = 0) -> tuple[str, int, bool]:
        """Newest pod's containers in spec order ('==> <container> <==' headers); the job's stored log when the pod
        is gone."""
        raise NotImplementedError("PLAN §11")

    def artefacts(self, job_id: str) -> dict[str, Any]:
        raise NotImplementedError("PLAN §11")

    def cancel(self, job_id: str) -> None:
        """Delete the Kubernetes Job and mark the record cancelled."""
        raise NotImplementedError("PLAN §11")

    def requeue(self, job_id: str, ref: str, inputs: Mapping[str, Any]) -> None:
        """Same job id, attempt + 1: update the record and apply a new Job."""
        raise NotImplementedError("PLAN §11")
