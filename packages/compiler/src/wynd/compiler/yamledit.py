"""Comment-preserving block edits on YAML text, verified by re-parse (`$DRAFTS/05 §9.6`, §10.2)."""

from __future__ import annotations

from typing import Any


def append_block_items(text: str, key: str, items: list[Any], *, comment: str) -> str:
    raise NotImplementedError("PLAN §7")


def append_mapping_entries(text: str, key: str, entries: dict[str, Any], *, marker: str) -> str:
    raise NotImplementedError("PLAN §7")


def remove_marked(text: str, marker: str) -> str:
    raise NotImplementedError("PLAN §7")
