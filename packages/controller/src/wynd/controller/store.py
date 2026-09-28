"""Controller document store: releases, trigger fires, chats (PLAN §8.1; `$DRAFTS/06 §10.2`).

`FileDocStore(state_dir / "controller")` keeps `<root>/<collection>/<id>.json`: one file per document, temp file +
`os.replace`, `fcntl.flock` on `<root>/.lock`, no index (multi-process safe on a shared filesystem). Listing is
newest id first (ids sort by time, PLAN §3.1).
"""

from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Protocol

from pydantic_core import to_jsonable_python

COLLECTION = re.compile(r"^[a-z][a-z0-9_-]*$")
DOC_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")


class DocStore(Protocol):
    def put(self, collection: str, id: str, doc: dict) -> None: ...
    def get(self, collection: str, id: str) -> dict | None: ...
    def update(self, collection: str, id: str, patch: dict) -> dict: ...     # atomic shallow merge
    def list(self, collection: str, where: Mapping[str, Any] | None = None, limit: int | None = None) -> list[dict]: ...
    def delete(self, collection: str, id: str) -> None: ...


class FileDocStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._lock_path = self.root / ".lock"

    def put(self, collection: str, id: str, doc: dict) -> None:
        path = self._path(collection, id)
        with self._locked():
            _write_atomic(path, doc)

    def get(self, collection: str, id: str) -> dict | None:
        return _read(self._path(collection, id))

    def update(self, collection: str, id: str, patch: dict) -> dict:
        """Shallow merge under the store lock; KeyError when the document does not exist."""
        path = self._path(collection, id)
        with self._locked():
            current = _read(path)
            if current is None:
                raise KeyError(f"no document {collection}/{id}")
            merged = {**current, **to_jsonable_python(patch)}
            _write_atomic(path, merged)
        return merged

    def list(self, collection: str, where: Mapping[str, Any] | None = None, limit: int | None = None) -> list[dict]:
        """Documents whose fields equal every `where` item, newest id first."""
        directory = self.root / _checked(COLLECTION, collection, "collection")
        docs = []
        for path in sorted(directory.glob("*.json"), key=lambda p: p.stem, reverse=True):
            doc = _read(path)
            if doc is not None and all(doc.get(key) == value for key, value in (where or {}).items()):
                docs.append(doc)
        return docs if limit is None else docs[:limit]

    def delete(self, collection: str, id: str) -> None:
        path = self._path(collection, id)
        with self._locked():
            path.unlink(missing_ok=True)

    def _path(self, collection: str, id: str) -> Path:
        directory = self.root / _checked(COLLECTION, collection, "collection")
        return directory / f"{_checked(DOC_ID, id, 'document id')}.json"

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        with open(self._lock_path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            yield


def _checked(pattern: re.Pattern[str], value: str, what: str) -> str:
    if not pattern.match(value):
        raise ValueError(f"invalid {what} {value!r}: must match {pattern.pattern}")
    return value


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


def _write_atomic(path: Path, doc: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        tmp.write_text(json.dumps(to_jsonable_python(dict(doc)), ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
