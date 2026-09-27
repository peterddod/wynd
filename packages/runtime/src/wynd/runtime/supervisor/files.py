"""Uploaded files of a run request (PLAN §3.17; `$DRAFTS/03 §13.4`): written under
`$WYND_DATA_DIR/uploads/<run_id>/`, every `{"$file": name}` input value replaced by the absolute path."""

from __future__ import annotations

import base64
import binascii
import shutil
from collections.abc import Mapping
from pathlib import Path
from typing import Any

FILE_KEY = "$file"


class UploadError(ValueError):
    """An unknown `{"$file": name}` reference or undecodable file content (HTTP 422)."""


def substitute_files(value: Any, paths: Mapping[str, str]) -> Any:
    """`value` with every `{"$file": name}` (at any depth) replaced by `paths[name]`; UploadError for unknown names."""
    match value:
        case {"$file": str(name)} if len(value) == 1:
            if name not in paths:
                raise UploadError(f"input references unknown file {name!r} (not in `files`)")
            return paths[name]
        case dict():
            return {key: substitute_files(item, paths) for key, item in value.items()}
        case list():
            return [substitute_files(item, paths) for item in value]
        case _:
            return value


def materialise_files(inputs: dict[str, Any], files: Mapping[str, Any], dir: Path) -> dict[str, Any]:
    """Decode `files` (`{name: FileUpload | {"content_base64"}}`) into `dir/<name>` and return `inputs` with the
    references substituted. Nothing is written when a reference or a file is invalid."""
    dir = Path(dir).absolute()
    contents: dict[str, bytes] = {}
    for name, upload in files.items():
        encoded = upload["content_base64"] if isinstance(upload, Mapping) else upload.content_base64
        try:
            contents[name] = base64.b64decode(encoded, validate=True)
        except (binascii.Error, ValueError):
            raise UploadError(f"file {name!r}: content_base64 is not valid base64") from None
    resolved = substitute_files(inputs, {name: str(dir / name) for name in contents})
    if contents:
        dir.mkdir(parents=True, exist_ok=True)
        for name, data in contents.items():
            (dir / name).write_bytes(data)
    return resolved


def release_files(dir: Path, keep_in: Path | None) -> None:
    """End of run: move the upload dir into `keep_in` (a kept workspace), else delete it."""
    if not dir.exists():
        return
    if keep_in is None:
        shutil.rmtree(dir, ignore_errors=True)
        return
    keep_in.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(dir), str(keep_in))
