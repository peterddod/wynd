"""`FakeKubeClient`: in-memory `KubeClient` for tests (`$DRAFTS/08 §5.3`)."""

from __future__ import annotations

from typing import Any


class FakeKubeClient:
    """`objects` keyed by (kind, name); `calls` records every client call for assertions."""

    def __init__(self, namespace: str = "wynd"):
        self.namespace = namespace
        self.objects: dict[tuple[str, str], dict[str, Any]] = {}
        self.calls: list[tuple] = []

    def apply(self, obj: dict[str, Any]) -> dict[str, Any]:
        """Stores a deep copy, keeping any existing `status`."""
        raise NotImplementedError("PLAN §11")

    def get(self, kind: str, name: str) -> dict[str, Any] | None:
        raise NotImplementedError("PLAN §11")

    def list(self, kind: str, selector: dict[str, str]) -> list[dict[str, Any]]:
        """Filters on metadata.labels."""
        raise NotImplementedError("PLAN §11")

    def delete(self, kind: str, name: str) -> None:
        raise NotImplementedError("PLAN §11")

    def logs(self, pod: str, container: str) -> str:
        raise NotImplementedError("PLAN §11")

    def set_status(self, kind: str, name: str, status: dict[str, Any]) -> None:
        raise NotImplementedError("PLAN §11")

    def set_logs(self, pod: str, container: str, text: str) -> None:
        raise NotImplementedError("PLAN §11")
