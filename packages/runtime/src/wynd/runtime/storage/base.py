"""Storage interfaces (SPEC §7.1, PLAN §3.14). Backends are selected by env var through the `wynd.storage`
entry-point group (`wynd.runtime.storage.stores_from_env`)."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol

from wynd.runtime.storage.models import TestResult

REGISTRY_SECTIONS = ("mcp", "providers", "registries")


class WorkspaceStore(Protocol):
    def open(self, run_id: str) -> Path: ...                      # create; a LOCAL dir usable as cwd

    def close(self, run_id: str, *, keep: bool) -> str | None: ...   # keep: return URI; else delete, None

    def locate(self, run_id: str) -> str | None: ...


class TraceSink(Protocol):
    def write(self, event: dict[str, Any]) -> None: ...          # JSON-mode event dicts

    def close(self, run_id: str) -> None: ...

    def read(self, run_id: str) -> list[dict[str, Any]]: ...    # seq order; [] if unknown; skips a truncated last line

    def uri(self, run_id: str) -> str: ...


class RunRegistry(Protocol):
    """One registry for runs AND jobs (records carry "kind")."""

    def create(self, record: Mapping[str, Any]) -> None: ...
        # needs id, kind ("run"|"job"), status; sets created_at/updated_at; duplicate id -> FileExistsError

    def update(self, id: str, patch: Mapping[str, Any]) -> dict[str, Any]: ...   # atomic shallow merge; sets updated_at

    def get(self, id: str) -> dict[str, Any] | None: ...

    def list(
        self, *, kind: str | None = None, process: str | None = None, status: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]: ...                                # newest first

    def put_test_result(self, result: TestResult) -> None: ...

    def get_test_result(self, commit: str, key: str) -> TestResult | None: ...


class Registry(Protocol):
    """User-level registry (`WYND_HOME`), sections `REGISTRY_SECTIONS`."""

    def list(self, section: str) -> dict[str, dict[str, Any]]: ...

    def get(self, section: str, name: str) -> dict[str, Any] | None: ...

    def put(self, section: str, name: str, entry: Mapping[str, Any]) -> None: ...

    def remove(self, section: str, name: str) -> bool: ...

    def put_secret(self, name: str, value: str) -> None: ...    # local: $WYND_HOME/secrets.env, mode 0600

    def secrets(self) -> dict[str, str]: ...

    def location(self) -> str: ...
