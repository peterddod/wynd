"""Local filesystem backends and their `wynd.storage` entry-point factories (PLAN §3.14; `$DRAFTS/02 §9.3`).

Multi-process safe on a shared filesystem: one JSON file per record, temp file + `os.replace`, `fcntl.flock` on a lock
file, no index file. Factory signature: `(location, root, env) -> backend`.

On-disk layout:
    <root>/<run_id>/                                   FileWorkspaceStore
    <root>/<run_id>.jsonl                              JsonlTraceSink
    <root>/records/<id>.json                           FileRunRegistry (runs and jobs)
    <root>/tests/<commit>/<sha256(key)[:32]>.json      FileRunRegistry test results
    <home>/{mcp,providers,registries}.json             FileRegistry: {"version": 1, "entries": {name: {...}}}
    <home>/secrets.env                                 FileRegistry secrets, NAME="json string" lines, mode 0600
"""

from __future__ import annotations

import copy
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import threading
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any

from pydantic import TypeAdapter

from wynd.runtime.ids import valid_id
from wynd.runtime.storage import StorageConfigError
from wynd.runtime.storage.base import REGISTRY_SECTIONS
from wynd.runtime.storage.models import TestResult

_JSON = TypeAdapter(Any)
_ENTRY_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*")
_ENV_NAME_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_READ_ONLY = "registry is read-only (WYND_REGISTRY=env)"


def _jsonable(value: Any) -> Any:
    """JSON-mode copy: datetimes become ISO strings (UTC as `Z`), models become dicts."""
    return _JSON.dump_python(value, mode="json")


def _now() -> str:
    return _jsonable(datetime.now(UTC))


def _checked(name: str, what: str = "id") -> str:
    if not valid_id(name):
        raise ValueError(f"invalid {what} {name!r}: must match ^[A-Za-z0-9][A-Za-z0-9_.-]{{0,127}}$")
    return name


def _write_atomic(path: Path, text: str, mode: int = 0o666) -> None:
    """Write via a sibling temp file + `os.replace`; readers see the old or the new file, never a partial one."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(6)}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def _read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None


@contextmanager
def _locked(lock_path: Path) -> Iterator[None]:
    """Exclusive `flock` on `lock_path`; excludes other processes and, through separate opens, other threads."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_path, "a") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        yield


def _created_key(record: Mapping[str, Any]) -> tuple[datetime, str]:
    created = datetime.fromisoformat(record["created_at"])
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    return created, record["id"]


def _check_section(section: str) -> None:
    if section not in REGISTRY_SECTIONS:
        raise ValueError(f"unknown registry section {section!r}; expected one of {', '.join(REGISTRY_SECTIONS)}")


class FileWorkspaceStore:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).absolute()

    def open(self, run_id: str) -> Path:
        path = self.root / _checked(run_id, "run id")
        path.mkdir(parents=True, exist_ok=False)
        return path

    def close(self, run_id: str, *, keep: bool) -> str | None:
        if keep:
            return self.locate(run_id)
        shutil.rmtree(self.root / _checked(run_id, "run id"), ignore_errors=True)
        return None

    def locate(self, run_id: str) -> str | None:
        if not valid_id(run_id):
            return None
        path = self.root / run_id
        return path.as_uri() if path.is_dir() else None


class JsonlTraceSink:
    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).absolute()
        self._files: dict[str, IO[str]] = {}
        self._lock = threading.Lock()

    def _path(self, run_id: str) -> Path:
        return self.root / f"{_checked(run_id, 'run id')}.jsonl"

    def write(self, event: dict[str, Any]) -> None:
        run_id = event["run_id"]
        line = json.dumps(event, ensure_ascii=False, separators=(",", ":"), default=_jsonable) + "\n"
        with self._lock:
            f = self._files.get(run_id)
            if f is None:
                path = self._path(run_id)
                path.parent.mkdir(parents=True, exist_ok=True)
                f = self._files[run_id] = open(path, "a", encoding="utf-8")
            f.write(line)
            f.flush()

    def close(self, run_id: str) -> None:
        with self._lock:
            f = self._files.pop(run_id, None)
        if f is not None:
            f.close()

    def read(self, run_id: str) -> list[dict[str, Any]]:
        if not valid_id(run_id):
            return []
        try:
            text = self._path(run_id).read_text(encoding="utf-8")
        except FileNotFoundError:
            return []
        lines = text.split("\n")
        events = []
        for i, line in enumerate(lines):
            if not line.strip():
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                if i == len(lines) - 1:          # a truncated last line: its writer has not finished it
                    break
                raise
        events.sort(key=lambda e: e.get("seq", 0))
        return events

    def uri(self, run_id: str) -> str:
        return self._path(run_id).as_uri()


class FileRunRegistry:
    """Runs and jobs, one JSON file each; `create`/`update` are read-modify-write under `<root>/.lock`."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).absolute()
        self.records = self.root / "records"
        self.tests = self.root / "tests"
        self._lock_path = self.root / ".lock"

    def _record_path(self, id: str) -> Path:
        return self.records / f"{_checked(id, 'record id')}.json"

    def _test_path(self, commit: str, key: str) -> Path:
        digest = hashlib.sha256(key.encode()).hexdigest()[:32]
        return self.tests / _checked(commit, "commit") / f"{digest}.json"

    def create(self, record: Mapping[str, Any]) -> None:
        for field in ("id", "kind", "status"):
            if field not in record:
                raise ValueError(f"record needs {field!r}")
        if record["kind"] not in ("run", "job"):
            raise ValueError(f"record kind must be 'run' or 'job', got {record['kind']!r}")
        data = _jsonable(dict(record))
        data["created_at"] = data.get("created_at") or _now()
        data["updated_at"] = data.get("updated_at") or data["created_at"]
        path = self._record_path(data["id"])
        with _locked(self._lock_path):
            if path.exists():
                raise FileExistsError(f"record {data['id']!r} already exists")
            _write_atomic(path, json.dumps(data, ensure_ascii=False))

    def update(self, id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        path = self._record_path(id)
        with _locked(self._lock_path):
            current = _read_json(path)
            if current is None:
                raise KeyError(f"no record {id!r}")
            merged = {**current, **_jsonable(dict(patch)), "updated_at": _now()}
            _write_atomic(path, json.dumps(merged, ensure_ascii=False))
        return merged

    def get(self, id: str) -> dict[str, Any] | None:
        if not valid_id(id):
            return None
        return _read_json(self._record_path(id))

    def list(
        self, *, kind: str | None = None, process: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        wanted = {k: v for k, v in (("kind", kind), ("process", process), ("status", status)) if v is not None}
        records = []
        for path in self.records.glob("*.json"):
            record = _read_json(path)
            if record is not None and all(record.get(k) == v for k, v in wanted.items()):
                records.append(record)
        records.sort(key=_created_key, reverse=True)
        return records[:limit]

    def put_test_result(self, result: TestResult) -> None:
        _write_atomic(self._test_path(result.commit, result.key), result.model_dump_json())

    def get_test_result(self, commit: str, key: str) -> TestResult | None:
        if not valid_id(commit):
            return None
        data = _read_json(self._test_path(commit, key))
        return None if data is None else TestResult.model_validate(data)


class FileRegistry:
    """User registry under `WYND_HOME`: one JSON file per section plus `secrets.env` (mode 0600)."""

    def __init__(self, home: str | Path) -> None:
        self.home = Path(home).absolute()
        self._lock_path = self.home / ".lock"
        self._secrets_path = self.home / "secrets.env"

    def _entries(self, section: str) -> dict[str, dict[str, Any]]:
        _check_section(section)
        data = _read_json(self.home / f"{section}.json")
        return {} if data is None else data.get("entries", {})

    def _write_entries(self, section: str, entries: Mapping[str, Any]) -> None:
        text = json.dumps({"version": 1, "entries": entries}, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        _write_atomic(self.home / f"{section}.json", text)

    def list(self, section: str) -> dict[str, dict[str, Any]]:
        return self._entries(section)

    def get(self, section: str, name: str) -> dict[str, Any] | None:
        return self._entries(section).get(name)

    def put(self, section: str, name: str, entry: Mapping[str, Any]) -> None:
        if not _ENTRY_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid registry entry name {name!r}: must match ^[A-Za-z0-9][A-Za-z0-9_.-]*$")
        with _locked(self._lock_path):
            entries = self._entries(section)
            entries[name] = _jsonable(dict(entry))
            self._write_entries(section, entries)

    def remove(self, section: str, name: str) -> bool:
        with _locked(self._lock_path):
            entries = self._entries(section)
            if entries.pop(name, None) is None:
                return False
            self._write_entries(section, entries)
            return True

    def put_secret(self, name: str, value: str) -> None:
        if not _ENV_NAME_RE.fullmatch(name):
            raise ValueError(f"invalid secret name {name!r}: must be an environment variable name")
        with _locked(self._lock_path):
            values = {**self.secrets(), name: value}
            text = "".join(f"{k}={json.dumps(v, ensure_ascii=False)}\n" for k, v in sorted(values.items()))
            _write_atomic(self._secrets_path, text, mode=0o600)

    def secrets(self) -> dict[str, str]:
        """Parse `NAME=value` lines; a value starting with `"` is a JSON string; blank and `#` lines are skipped."""
        try:
            text = self._secrets_path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        values = {}
        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            value = value.strip()
            values[name.strip()] = json.loads(value) if value.startswith('"') else value
        return values

    def location(self) -> str:
        return str(self.home)


class EnvRegistry:
    """Read-only view of `WYND_REGISTRY_JSON` (`WYND_REGISTRY=env`, selected by the base image)."""

    def __init__(self, env: Mapping[str, str]) -> None:
        raw = env.get("WYND_REGISTRY_JSON") or ""
        if not raw.strip():
            self._data: dict[str, Any] = {}
            return
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise StorageConfigError(f"WYND_REGISTRY_JSON is not valid JSON: {e}") from None
        if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
            raise StorageConfigError("WYND_REGISTRY_JSON must be a JSON object {section: {name: entry}}")
        self._data = data

    def list(self, section: str) -> dict[str, dict[str, Any]]:
        _check_section(section)
        return copy.deepcopy(self._data.get(section, {}))

    def get(self, section: str, name: str) -> dict[str, Any] | None:
        return self.list(section).get(name)

    def put(self, section: str, name: str, entry: Mapping[str, Any]) -> None:
        raise StorageConfigError(_READ_ONLY)

    def remove(self, section: str, name: str) -> bool:
        raise StorageConfigError(_READ_ONLY)

    def put_secret(self, name: str, value: str) -> None:
        raise StorageConfigError(_READ_ONLY)

    def secrets(self) -> dict[str, str]:
        return {}

    def location(self) -> str:
        return "env:WYND_REGISTRY_JSON"


def _base(location: str | None, root: Path | None, var: str) -> Path:
    """A location overrides the default root; with neither the backend has nowhere to live."""
    if location:
        return Path(location)
    if root is None:
        raise StorageConfigError(f"{var} is not set")
    return root


def workspace_file(location: str | None, root: Path | None, env: Mapping[str, str]) -> FileWorkspaceStore:
    return FileWorkspaceStore(_base(location, root, "WYND_DATA_DIR"))


def trace_jsonl(location: str | None, root: Path | None, env: Mapping[str, str]) -> JsonlTraceSink:
    return JsonlTraceSink(_base(location, root, "WYND_DATA_DIR"))


def runs_file(location: str | None, root: Path | None, env: Mapping[str, str]) -> FileRunRegistry:
    return FileRunRegistry(_base(location, root, "WYND_DATA_DIR"))


def registry_file(location: str | None, root: Path | None, env: Mapping[str, str]) -> FileRegistry:
    return FileRegistry(_base(location, root, "WYND_HOME"))


def registry_env(location: str | None, root: Path | None, env: Mapping[str, str]) -> EnvRegistry:
    return EnvRegistry(env)
