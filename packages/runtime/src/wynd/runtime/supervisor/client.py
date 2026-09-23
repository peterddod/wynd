"""`RunApiClient`, the only run-API client (stdlib urllib + SSE parser; PLAN §3.17; `$DRAFTS/03 §13.6`)."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any


class RunApiError(Exception):
    def __init__(self, message: str, *, status: int, code: str, body: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.body = body or {}


class RunApiClient:
    def __init__(self, base_url: str, *, token: str | None = None, timeout_s: float = 30) -> None:
        raise NotImplementedError("PLAN §3.17")

    def health(self) -> bool:
        raise NotImplementedError("PLAN §3.17")

    def ready(self) -> dict[str, Any]:
        raise NotImplementedError("PLAN §3.17")

    def wait_ready(self, timeout: float = 120.0, interval: float = 0.25) -> None:
        raise NotImplementedError("PLAN §3.17")

    def info(self) -> Any:
        raise NotImplementedError("PLAN §3.17")

    def submit(
        self,
        inputs: dict[str, Any],
        run_id: str | None = None,
        files: dict[str, bytes] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Any:
        raise NotImplementedError("PLAN §3.17")

    def get(self, run_id: str, *, wait: float | None = None) -> Any:
        raise NotImplementedError("PLAN §3.17")

    def outputs(self, run_id: str) -> dict[str, Any]:
        raise NotImplementedError("PLAN §3.17")

    def events(self, run_id: str, after: int | None = None) -> Iterator[tuple[int, str, dict[str, Any]]]:
        raise NotImplementedError("PLAN §3.17")

    def run(self, inputs: dict[str, Any], **kw: Any) -> Any:
        raise NotImplementedError("PLAN §3.17")
