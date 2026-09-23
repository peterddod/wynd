"""Stdlib (urllib) HTTP client for steps and tools (PLAN §5.1; `$DRAFTS/02 §3.6`).

Non-2xx responses are returned, not raised; network failures raise `HttpError(response=None)`.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class HttpResponse:
    """`url, status, headers, body` with `text()`, `json()`, `raise_for_status()`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.1")


class HttpError(Exception):
    def __init__(self, message: str, *, response: HttpResponse | None = None) -> None:
        super().__init__(message)
        self.response = response


class HttpClient:
    def __init__(self, *, timeout: float = 30.0, user_agent: str | None = None) -> None:
        raise NotImplementedError("PLAN §5.1")

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
        json: Any = None,
        data: bytes | None = None,
        timeout: float | None = None,
    ) -> HttpResponse:
        raise NotImplementedError("PLAN §5.1")

    def get(self, url: str, **kw: Any) -> HttpResponse:
        raise NotImplementedError("PLAN §5.1")

    def post(self, url: str, **kw: Any) -> HttpResponse:
        raise NotImplementedError("PLAN §5.1")
