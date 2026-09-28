"""Kubernetes API seam (`$DRAFTS/08 §5.3`): the `KubeClient` protocol, the httpx `RestKubeClient` and
`KubeApiError`. The fake for tests is `wynd.kube.testing.FakeKubeClient`."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    import httpx


class KubeApiError(Exception):
    """A non-2xx API response that is not a handled 404; `reason`/`message` come from the k8s `Status` body."""

    def __init__(self, status: int, reason: str, message: str):
        super().__init__(f"{status} {reason}: {message}")
        self.status = status
        self.reason = reason
        self.message = message


class KubeClient(Protocol):
    """Namespace bound at construction."""

    def apply(self, obj: dict[str, Any]) -> dict[str, Any]: ...       # server-side apply, fieldManager "wynd", force
    def get(self, kind: str, name: str) -> dict[str, Any] | None: ...  # None on 404
    def list(self, kind: str, selector: dict[str, str]) -> list[dict[str, Any]]: ...
    def delete(self, kind: str, name: str) -> None: ...               # propagationPolicy Background; 404 ignored
    def logs(self, pod: str, container: str) -> str: ...


RESOURCES: dict[str, tuple[str, str]] = {  # kind -> (api prefix, plural)
    "Job": ("apis/batch/v1", "jobs"),
    "CronJob": ("apis/batch/v1", "cronjobs"),
    "Pod": ("api/v1", "pods"),
    "Service": ("api/v1", "services"),
    "ConfigMap": ("api/v1", "configmaps"),
    "Deployment": ("apis/apps/v1", "deployments"),
}


class RestKubeClient:
    """httpx against the API server.

    base_url: WYND_KUBE_API_URL, else https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT.
    token:    re-read on every request from WYND_KUBE_TOKEN_FILE
              (default /var/run/secrets/kubernetes.io/serviceaccount/token); no header if the file is absent
              (e.g. WYND_KUBE_API_URL=http://127.0.0.1:8001 behind `kubectl proxy`).
    ca:       WYND_KUBE_CA_FILE (default .../serviceaccount/ca.crt) if present, else system trust.
    """

    def __init__(self, namespace: str, *, base_url: str, token_file: str | None, ca_file: str | None,
                 transport: httpx.BaseTransport | None = None):
        self.namespace = namespace
        self.base_url = base_url
        self.token_file = token_file
        self.ca_file = ca_file
        self.transport = transport

    @classmethod
    def from_env(cls, env: Mapping[str, str], namespace: str) -> RestKubeClient:
        raise NotImplementedError("PLAN §11")

    def apply(self, obj: dict[str, Any]) -> dict[str, Any]:
        """PATCH /{prefix}/namespaces/{ns}/{plural}/{name}?fieldManager=wynd&force=true,
        Content-Type application/apply-patch+yaml, body = JSON of obj."""
        raise NotImplementedError("PLAN §11")

    def get(self, kind: str, name: str) -> dict[str, Any] | None:
        raise NotImplementedError("PLAN §11")

    def list(self, kind: str, selector: dict[str, str]) -> list[dict[str, Any]]:
        raise NotImplementedError("PLAN §11")

    def delete(self, kind: str, name: str) -> None:
        raise NotImplementedError("PLAN §11")

    def logs(self, pod: str, container: str) -> str:
        raise NotImplementedError("PLAN §11")
