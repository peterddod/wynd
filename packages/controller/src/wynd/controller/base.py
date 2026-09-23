"""`wynd base build|publish` wrappers (PLAN §8.1, §15 item 50; `$DRAFTS/06 §5.15`). Stub; CTL-M2.

Plain functions, not jobs: they delegate to `wynd.process.base` and raise `VersionMismatch` unless
`version == wynd.runtime.__version__`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Literal


def build_base(version: str, variants: Sequence[Literal["slim", "alpine"]], *, log: Callable[[str], None]) -> list[str]:
    """-> image references built."""
    raise NotImplementedError("PLAN §8.1")


def publish_base(
    version: str, variants: Sequence[Literal["slim", "alpine"]], registry_name: str, *, log: Callable[[str], None]
) -> list[str]:
    """-> image references pushed."""
    raise NotImplementedError("PLAN §8.1")
