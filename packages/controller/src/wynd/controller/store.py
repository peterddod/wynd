"""Controller document store: releases, trigger fires, chats (PLAN §8.1; `$DRAFTS/06 §10.2`). Stub; CTL-CORE.

`FileDocStore(state_dir / "controller")` keeps `<root>/<collection>/<id>.json`: one file per document, temp file +
`os.replace`, `fcntl.flock` on `<root>/.lock`, no index (multi-process safe on a shared filesystem).
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol


class DocStore(Protocol):
    def put(self, collection: str, id: str, doc: dict) -> None: ...
    def get(self, collection: str, id: str) -> dict | None: ...
    def update(self, collection: str, id: str, patch: dict) -> dict: ...     # atomic shallow merge
    def list(self, collection: str, where: Mapping[str, Any] | None = None, limit: int | None = None) -> list[dict]: ...
    def delete(self, collection: str, id: str) -> None: ...


class FileDocStore:
    def __init__(self, root: Path) -> None:
        raise NotImplementedError("PLAN §8.1")

    def put(self, collection: str, id: str, doc: dict) -> None:
        raise NotImplementedError("PLAN §8.1")

    def get(self, collection: str, id: str) -> dict | None:
        raise NotImplementedError("PLAN §8.1")

    def update(self, collection: str, id: str, patch: dict) -> dict:
        raise NotImplementedError("PLAN §8.1")

    def list(self, collection: str, where: Mapping[str, Any] | None = None, limit: int | None = None) -> list[dict]:
        raise NotImplementedError("PLAN §8.1")

    def delete(self, collection: str, id: str) -> None:
        raise NotImplementedError("PLAN §8.1")
