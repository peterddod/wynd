"""`.env` parsing and loading (PLAN §8.1; `$DRAFTS/06 §5.11`). Stub; CTL-CORE.

Format: `KEY=VALUE`; optional `export `; `#` comments; blank lines; `'…'`/`"…"` quotes (double honours `\\n \\" \\\\`);
no interpolation. `load_into_environ(root)` sets only keys not already set, from `root/.env`.
"""

from __future__ import annotations

from pathlib import Path


def parse_env_file(text: str) -> dict[str, str]:
    raise NotImplementedError("PLAN §8.1")


def load_into_environ(root: Path) -> None:
    raise NotImplementedError("PLAN §8.1")
