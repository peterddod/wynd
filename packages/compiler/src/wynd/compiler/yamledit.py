"""Comment-preserving block edits on YAML text, verified by re-parse (`$DRAFTS/05 §9.6`, §10.2).

Edits are line-based and touch only a top-level key's block: everything else (comments, anchors, flow style) is kept
byte for byte. A form these functions cannot edit safely (a non-empty flow collection, an unterminated marker) raises
`ValueError`; callers verify the result by re-parsing and fall back to a full re-dump.
"""

from __future__ import annotations

import re
from typing import Any

import yaml

_DUMP = {"sort_keys": False, "allow_unicode": True, "width": 100, "default_flow_style": None}
_DEFAULT_INDENT = "  "


def append_block_items(text: str, key: str, items: list[Any], *, comment: str, marker: str | None = None,
                       note: str = "") -> str:
    """Append `items` to the top-level block list `key:` after its last content line.

    `comment` (if non-empty) becomes a comment line before the items. With `marker`, the items are wrapped in
    `# <marker> begin[ (<note>)]` / `# <marker> end` lines (removed again by `remove_marked`).
    """
    if not items:
        return text
    return _append(text, key, yaml.safe_dump(items, **_DUMP), list_block=True, comment=comment, marker=marker,
                   note=note)


def append_mapping_entries(text: str, key: str, entries: dict[str, Any], *, marker: str, note: str = "") -> str:
    """Append `entries` to the top-level block mapping `key:`, wrapped in `# <marker> begin/end` lines."""
    if not entries:
        return text
    return _append(text, key, yaml.safe_dump(entries, **_DUMP), list_block=False, comment="", marker=marker,
                   note=note)


def remove_marked(text: str, marker: str) -> str:
    """Delete every region from `# <marker> begin` to `# <marker> end` (inclusive). Text without markers is returned
    unchanged; a begin without an end raises ValueError."""
    begin = re.compile(rf"^\s*#\s*{re.escape(marker)} begin\b")
    end = re.compile(rf"^\s*#\s*{re.escape(marker)} end\b")
    out: list[str] = []
    inside = False
    for line in text.splitlines(keepends=True):
        if not inside and begin.match(line):
            inside = True
            continue
        if inside:
            if end.match(line):
                inside = False
            continue
        out.append(line)
    if inside:
        raise ValueError(f"marker '# {marker} begin' has no matching end")
    return "".join(out)


def has_marker(text: str, marker: str) -> bool:
    return re.search(rf"^\s*#\s*{re.escape(marker)} begin\b", text, re.M) is not None


def _append(text: str, key: str, dumped: str, *, list_block: bool, comment: str, marker: str | None,
            note: str) -> str:
    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith("\n"):
        lines[-1] += "\n"
    head = re.compile(rf"^{re.escape(key)}:\s*(?P<flow>\[\]|\{{\}})?\s*(?:#.*)?$")
    other = re.compile(rf"^{re.escape(key)}:")
    at = next((i for i, line in enumerate(lines) if other.match(line)), None)

    if at is None:
        return "".join(lines) + f"{key}:\n" + _render(dumped, _DEFAULT_INDENT, comment, marker, note)

    match = head.match(lines[at].rstrip("\n"))
    if match is None:
        raise ValueError(f"top-level '{key}:' is not a block collection; cannot append surgically")
    if match.group("flow"):
        lines[at] = f"{key}:\n"
        block = _render(dumped, _DEFAULT_INDENT, comment, marker, note)
        return "".join(lines[: at + 1]) + block + "".join(lines[at + 1:])

    last, indent = _block_extent(lines, at, list_block)
    block = _render(dumped, indent, comment, marker, note)
    return "".join(lines[: last + 1]) + block + "".join(lines[last + 1:])


def _block_extent(lines: list[str], at: int, list_block: bool) -> tuple[int, str]:
    """(index of the block's last content line, the item indent) for the block that starts at line `at`."""
    last = at
    indent: str | None = None
    for i in range(at + 1, len(lines)):
        line = lines[i]
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        width = len(line) - len(line.lstrip(" "))
        if width == 0 and not (list_block and stripped.startswith("-")):
            break
        if indent is None and (not list_block or stripped.startswith("-")):
            indent = " " * width
        last = i
    # a marked region that closes the block stays closed: append after its `# ... end` line
    while last + 1 < len(lines) and _MARKER_END.match(lines[last + 1]):
        last += 1
    return last, _DEFAULT_INDENT if indent is None else indent


_MARKER_END = re.compile(r"^\s*#\s*\S.* end\s*$")


def _render(dumped: str, indent: str, comment: str, marker: str | None, note: str) -> str:
    body = "".join(indent + line if line.strip() else line for line in dumped.splitlines(keepends=True))
    if comment:
        body = f"{indent}# {comment}\n" + body
    if marker:
        suffix = f" ({note})" if note else ""
        body = f"{indent}# {marker} begin{suffix}\n" + body + f"{indent}# {marker} end\n"
    return body
