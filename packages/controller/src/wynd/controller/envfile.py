"""`.env` parsing and loading (PLAN §8.1; `$DRAFTS/06 §5.11`).

Format: `KEY=VALUE`; optional `export `; `#` comments (whole lines, or after whitespace in an unquoted value); blank
lines; `'…'` (literal) and `"…"` (honours `\\n \\t \\" \\\\`) quotes; no interpolation.
`load_into_environ(root)` sets only keys not already set, from `root/.env`.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from wynd.controller.errors import Invalid

ENV_FILE = ".env"
LINE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")
DOUBLE_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\"}


def parse_env_file(text: str, *, source: str = ENV_FILE) -> dict[str, str]:
    """`Invalid` naming `source` and the line for anything that is not a comment, blank or `KEY=VALUE`."""
    values: dict[str, str] = {}
    for number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = LINE.match(line)
        if match is None:
            raise Invalid(f"{source}:{number}: expected KEY=VALUE")
        key, value = match.groups()
        try:
            values[key] = _value(value)
        except ValueError as err:
            raise Invalid(f"{source}:{number}: {err}") from None
    return values


def read_env_file(path: Path) -> dict[str, str]:
    """`parse_env_file` of `path`; `{}` when it does not exist."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return {}
    return parse_env_file(text, source=str(path))


def load_into_environ(root: Path) -> None:
    for key, value in read_env_file(Path(root) / ENV_FILE).items():
        os.environ.setdefault(key, value)


def _value(text: str) -> str:
    if not text:
        return ""
    match text[0]:
        case "'":
            end = text.find("'", 1)
            if end < 0:
                raise ValueError("unterminated single quote")
            _rest(text[end + 1:])
            return text[1:end]
        case '"':
            out: list[str] = []
            i = 1
            while i < len(text):
                ch = text[i]
                if ch == '"':
                    _rest(text[i + 1:])
                    return "".join(out)
                if ch == "\\" and i + 1 < len(text):
                    nxt = text[i + 1]
                    out.append(DOUBLE_ESCAPES.get(nxt, "\\" + nxt))
                    i += 2
                    continue
                out.append(ch)
                i += 1
            raise ValueError("unterminated double quote")
    comment = re.search(r"\s#", text)
    return (text[:comment.start()] if comment else text).strip()


def _rest(text: str) -> None:
    """After a closing quote only whitespace or a comment may follow."""
    rest = text.strip()
    if rest and not rest.startswith("#"):
        raise ValueError(f"unexpected text after the closing quote: {rest!r}")
