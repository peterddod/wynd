"""Cassettes: record and replay at the provider boundary (SPEC §7, PLAN §3.16; `$DRAFTS/03 §11`).

Modes: `live` (real calls, nothing written), `record` (real calls, one JSON file per call in `record_dir`), `replay`
(read only from `dir`; never builds the real provider; a miss raises `CassetteMissError`).

`CassetteSession` is defined in `wrap.py` and re-exported lazily: the submodules import the exceptions below from this
module, so it imports none of them at load time.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

NO_RECORDING = "no recording for this request — re-record with `wynd test --live`"

__all__ = ["NO_RECORDING", "CassetteError", "CassetteMissError", "CassetteSession", "promote"]


class CassetteMissError(Exception):
    """A replayed request (model call, agent run or recorded tool call) has no recording; the step resolves with
    cause `cassette_miss`.

    With `key`, the message is `NO_RECORDING` + `\\n  key: <key>\\n  cassettes: <dir>` (+ `\\n  request: <dump>`);
    without it, `message` is used verbatim (re-raising a recorded miss text).
    """

    def __init__(
        self,
        message: str = NO_RECORDING,
        *,
        key: str | None = None,
        dir: str | Path | None = None,
        dump: str | Path | None = None,
    ) -> None:
        text = message
        if key is not None:
            text = f"{message}\n  key: {key}\n  cassettes: {dir}"
            if dump is not None:
                text += f"\n  request: {dump}"
        super().__init__(text)
        self.key = key
        self.dir = dir
        self.dump = dump


class CassetteError(Exception):
    """A cassette directory or entry cannot be used (e.g. a git-lfs pointer file instead of a recording)."""


def __getattr__(name: str) -> Any:
    if name == "CassetteSession":
        from wynd.runtime.cassettes.wrap import CassetteSession

        return CassetteSession
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


def promote(staging: Path, dest: Path) -> list[Path]:
    """Replace `dest/*.json` with the staged recordings (only after the tests passed); returns the written paths.

    Every cassette entry (`"wynd_cassette": 1`) under `staging` is copied to the same relative path under `dest`;
    every other `*.json` under `dest` is removed. Non-JSON files in `dest` are never touched, and other files in
    `staging` (e.g. miss dumps) are never promoted. A missing `staging` counts as no recordings.
    """
    staging, dest = Path(staging), Path(dest)
    staged = sorted(p for p in staging.rglob("*.json") if _is_entry(p)) if staging.is_dir() else []
    keep = {dest / p.relative_to(staging) for p in staged}
    if dest.is_dir():
        for old in dest.rglob("*.json"):
            if old not in keep:
                old.unlink()
    written = []
    for src in staged:
        target = dest / src.relative_to(staging)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
        written.append(target)
    return written


def _is_entry(path: Path) -> bool:
    try:
        doc = json.loads(path.read_bytes())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return False
    return isinstance(doc, dict) and doc.get("wynd_cassette") == 1
