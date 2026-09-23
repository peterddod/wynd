"""Process search (PLAN §8.1; `$DRAFTS/06 §5.6`). Stub; CTL-DESIGN.

Tokens AND-matched as substrings over id/name (weight 3), goal (2), step instructions and names (1); flags ANDed
against derived status; sorted by `(-score, id)`; up to 3 matches with 40-char snippets.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.controller.models import Match, ProcessStatus, StatusFlag


def search(
    docs: list[dict],
    statuses: dict[str, ProcessStatus],
    q: str,
    flags: Sequence[StatusFlag],
    limit: int = 200,
) -> list[tuple[str, int, list[Match]]]:
    """`docs` items are `{"id", "name", "goal", "steps": [(step_key, instruction or "")]}`; -> (id, score, matches)."""
    raise NotImplementedError("PLAN §8.1")
