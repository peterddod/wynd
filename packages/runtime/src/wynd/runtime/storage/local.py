"""Local filesystem backends and their `wynd.storage` entry-point factories (PLAN §3.14; `$DRAFTS/02 §9.3`).

Multi-process safe on a shared filesystem: one JSON file per record, temp file + `os.replace`, `fcntl.flock` on a lock
file, no index file. Factory signature: `(location, root, env) -> backend`.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from wynd.runtime.storage.models import TestResult


class FileWorkspaceStore:
    def __init__(self, root: str | Path) -> None:
        raise NotImplementedError("PLAN §3.14")

    def open(self, run_id: str) -> Path:
        raise NotImplementedError("PLAN §3.14")

    def close(self, run_id: str, *, keep: bool) -> str | None:
        raise NotImplementedError("PLAN §3.14")

    def locate(self, run_id: str) -> str | None:
        raise NotImplementedError("PLAN §3.14")


class JsonlTraceSink:
    def __init__(self, root: str | Path) -> None:
        raise NotImplementedError("PLAN §3.14")

    def write(self, event: dict[str, Any]) -> None:
        raise NotImplementedError("PLAN §3.14")

    def close(self, run_id: str) -> None:
        raise NotImplementedError("PLAN §3.14")

    def read(self, run_id: str) -> list[dict[str, Any]]:
        raise NotImplementedError("PLAN §3.14")

    def uri(self, run_id: str) -> str:
        raise NotImplementedError("PLAN §3.14")


class FileRunRegistry:
    def __init__(self, root: str | Path) -> None:
        raise NotImplementedError("PLAN §3.14")

    def create(self, record: Mapping[str, Any]) -> None:
        raise NotImplementedError("PLAN §3.14")

    def update(self, id: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        raise NotImplementedError("PLAN §3.14")

    def get(self, id: str) -> dict[str, Any] | None:
        raise NotImplementedError("PLAN §3.14")

    def list(
        self, *, kind: str | None = None, process: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        raise NotImplementedError("PLAN §3.14")

    def put_test_result(self, result: TestResult) -> None:
        raise NotImplementedError("PLAN §3.14")

    def get_test_result(self, commit: str, key: str) -> TestResult | None:
        raise NotImplementedError("PLAN §3.14")


class FileRegistry:
    def __init__(self, home: str | Path) -> None:
        raise NotImplementedError("PLAN §3.14")

    def list(self, section: str) -> dict[str, dict[str, Any]]:
        raise NotImplementedError("PLAN §3.14")

    def get(self, section: str, name: str) -> dict[str, Any] | None:
        raise NotImplementedError("PLAN §3.14")

    def put(self, section: str, name: str, entry: Mapping[str, Any]) -> None:
        raise NotImplementedError("PLAN §3.14")

    def remove(self, section: str, name: str) -> bool:
        raise NotImplementedError("PLAN §3.14")

    def put_secret(self, name: str, value: str) -> None:
        raise NotImplementedError("PLAN §3.14")

    def secrets(self) -> dict[str, str]:
        raise NotImplementedError("PLAN §3.14")

    def location(self) -> str:
        raise NotImplementedError("PLAN §3.14")


class EnvRegistry:
    """Read-only view of `WYND_REGISTRY_JSON` (`WYND_REGISTRY=env`, selected by the base image)."""

    def __init__(self, env: Mapping[str, str]) -> None:
        raise NotImplementedError("PLAN §3.14")

    def list(self, section: str) -> dict[str, dict[str, Any]]:
        raise NotImplementedError("PLAN §3.14")

    def get(self, section: str, name: str) -> dict[str, Any] | None:
        raise NotImplementedError("PLAN §3.14")

    def put(self, section: str, name: str, entry: Mapping[str, Any]) -> None:
        raise NotImplementedError("PLAN §3.14")

    def remove(self, section: str, name: str) -> bool:
        raise NotImplementedError("PLAN §3.14")

    def put_secret(self, name: str, value: str) -> None:
        raise NotImplementedError("PLAN §3.14")

    def secrets(self) -> dict[str, str]:
        raise NotImplementedError("PLAN §3.14")

    def location(self) -> str:
        raise NotImplementedError("PLAN §3.14")


def workspace_file(location: str | None, root: Path | None, env: Mapping[str, str]) -> FileWorkspaceStore:
    raise NotImplementedError("PLAN §3.14")


def trace_jsonl(location: str | None, root: Path | None, env: Mapping[str, str]) -> JsonlTraceSink:
    raise NotImplementedError("PLAN §3.14")


def runs_file(location: str | None, root: Path | None, env: Mapping[str, str]) -> FileRunRegistry:
    raise NotImplementedError("PLAN §3.14")


def registry_file(location: str | None, root: Path | None, env: Mapping[str, str]) -> FileRegistry:
    raise NotImplementedError("PLAN §3.14")


def registry_env(location: str | None, root: Path | None, env: Mapping[str, str]) -> EnvRegistry:
    raise NotImplementedError("PLAN §3.14")
