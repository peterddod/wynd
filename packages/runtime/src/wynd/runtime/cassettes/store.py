"""Cassette files (PLAN §3.16; `$DRAFTS/03 §11.3`): `<dir>/<key[:32]>.json`, entry `{wynd_cassette: 1, kind, key,
package, step, provider, tier, thinking, model_id, recorded_at, request (normalised), response}`.

`response` omits `raw` and `session`, with the run's literals (workspace, run id, `CassetteConfig.literals`) replaced
by their placeholders (restored on replay); for `kind: "tool"` it is `{"result": {"text", "is_error"}}` or
`{"error": "<message>"}`.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from wynd.runtime.cassettes import CassetteError
from wynd.runtime.providers.types import ToolResult

LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"
MISSES_DIR = Path(".wynd") / "cassettes" / "misses"


@dataclass(frozen=True)
class CassetteEntry:
    kind: str                       # "generate" | "agent" | "agent_continue" | "tool"
    key: str
    package: str | None
    step: str | None
    provider: str | None
    tier: str | None
    thinking: str | None
    model_id: str | None
    recorded_at: str
    request: dict[str, Any]
    response: dict[str, Any]

    @property
    def result(self) -> ToolResult | None:
        """Tool entries: the recorded result, None if the tool raised."""
        result = self.response.get("result")
        if result is None:
            return None
        return ToolResult(text=result["text"], is_error=result.get("is_error", False))

    @property
    def error(self) -> str | None:
        """Tool entries: the recorded failure message, None if the tool returned."""
        return self.response.get("error")

    def to_json(self) -> dict[str, Any]:
        return {
            "wynd_cassette": 1,
            "kind": self.kind,
            "key": self.key,
            "package": self.package,
            "step": self.step,
            "provider": self.provider,
            "tier": self.tier,
            "thinking": self.thinking,
            "model_id": self.model_id,
            "recorded_at": self.recorded_at,
            "request": self.request,
            "response": self.response,
        }


def entry_path(dir: Path, key: str) -> Path:
    return Path(dir) / f"{key[:32]}.json"


def read_entry(path: Path) -> CassetteEntry:
    """Raises `CassetteError` for a git-lfs pointer file or a file that is not a wynd cassette entry."""
    data = Path(path).read_bytes()
    if data.startswith(LFS_POINTER_PREFIX):
        raise CassetteError(
            f"cassette {path} is a git-lfs pointer, not a recording: "
            "install git-lfs (https://git-lfs.com) and run 'git lfs pull'"
        )
    try:
        doc = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise CassetteError(f"cassette {path} is not valid JSON: {e}") from None
    if not isinstance(doc, dict) or doc.get("wynd_cassette") != 1:
        raise CassetteError(f"cassette {path} is not a wynd cassette entry (wynd_cassette: 1)")
    try:
        return CassetteEntry(
            kind=doc["kind"],
            key=doc["key"],
            package=doc.get("package"),
            step=doc.get("step"),
            provider=doc.get("provider"),
            tier=doc.get("tier"),
            thinking=doc.get("thinking"),
            model_id=doc.get("model_id"),
            recorded_at=doc.get("recorded_at", ""),
            request=doc["request"],
            response=doc["response"],
        )
    except KeyError as e:
        raise CassetteError(f"cassette {path} is missing {e.args[0]!r}") from None


def write_entry(dir: Path, entry: CassetteEntry) -> Path:
    """Write atomically to `<dir>/<key[:32]>.json` (replacing a previous recording of the same request)."""
    path = entry_path(dir, entry.key)
    _write_json(path, entry.to_json())
    return path


def find(dir: Path | None, key: str) -> CassetteEntry | None:
    """The recording of `key` in `dir`, or None. A file whose full key differs (a 32-char prefix collision) is a
    miss."""
    if dir is None:
        return None
    path = entry_path(dir, key)
    if not path.is_file():
        return None
    entry = read_entry(path)
    return entry if entry.key == key else None


def dump_miss(workspace: Path, key: str, request: dict[str, Any]) -> Path:
    """Write the normalised request of a miss to `<workspace>/.wynd/cassettes/misses/<key[:32]>.request.json`."""
    path = Path(workspace) / MISSES_DIR / f"{key[:32]}.request.json"
    _write_json(path, request)
    return path


def _write_json(path: Path, doc: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
