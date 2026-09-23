"""Stdlib (urllib) HTTP client for steps and tools (PLAN §5.1; `$DRAFTS/02 §3.6`).

Non-2xx responses are returned, not raised; network failures raise `HttpError(response=None)`.
"""

from __future__ import annotations

import json as jsonlib
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from email.message import Message
from http.client import HTTPException
from typing import Any

from wynd.runtime import __version__


@dataclass(frozen=True)
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]                # names lower-cased
    body: bytes

    def text(self) -> str:
        """The body decoded with the Content-Type charset (UTF-8 when absent), undecodable bytes replaced."""
        message = Message()
        message["content-type"] = self.headers.get("content-type", "")
        return self.body.decode(message.get_content_charset() or "utf-8", errors="replace")

    def json(self) -> Any:
        return jsonlib.loads(self.body)

    def raise_for_status(self) -> HttpResponse:
        if self.status >= 400:
            raise HttpError(f"HTTP {self.status} for {self.url}", response=self)
        return self


class HttpError(Exception):
    def __init__(self, message: str, *, response: HttpResponse | None = None) -> None:
        super().__init__(message)
        self.response = response


class HttpClient:
    def __init__(self, *, timeout: float = 30.0, user_agent: str | None = None) -> None:
        self.timeout = timeout
        self.user_agent = user_agent or f"wynd-runtime/{__version__}"

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
        if params:
            url = f"{url}{'&' if '?' in url else '?'}{urllib.parse.urlencode(params)}"
        sent = {"User-Agent": self.user_agent, **(headers or {})}
        if json is not None:
            data = jsonlib.dumps(json, ensure_ascii=False).encode()
            sent.setdefault("Content-Type", "application/json")
        request = urllib.request.Request(url, data=data, headers=sent, method=method.upper())
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                return HttpResponse(response.geturl(), response.status, _headers(response.headers), response.read())
        except urllib.error.HTTPError as err:
            with err:
                return HttpResponse(url, err.code, _headers(err.headers), err.read())
        except (OSError, HTTPException) as err:     # URLError, timeouts, resets, malformed responses
            raise HttpError(f"{method.upper()} {url} failed: {err}", response=None) from err

    def get(self, url: str, **kw: Any) -> HttpResponse:
        return self.request("GET", url, **kw)

    def post(self, url: str, **kw: Any) -> HttpResponse:
        return self.request("POST", url, **kw)


def _headers(headers: Message | None) -> dict[str, str]:
    return {} if headers is None else {name.lower(): value for name, value in headers.items()}
