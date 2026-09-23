"""Uploaded files of a run request (PLAN §3.17; `$DRAFTS/03 §13.4`): written under
`$WYND_DATA_DIR/uploads/<run_id>/`, every `{"$file": name}` input value replaced by the absolute path."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def materialise_files(inputs: dict[str, Any], files: dict[str, Any], dir: Path) -> dict[str, Any]:
    raise NotImplementedError("PLAN §3.17")
