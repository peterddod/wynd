"""Process search (PLAN §8.1; `$DRAFTS/06 §5.6`).

Tokens (the lower-cased query split on whitespace) are AND-matched as substrings of the lower-cased fields: id and
name (weight 3), goal (2), and each step's instruction and name (1). A process scores the sum over tokens of the
highest weight each token matched; with no tokens every process matches with score 0. Requested status flags are
ANDed against the derived status. Hits sort by `(-score, id)` and carry up to 3 matches (field order id, name, goal,
then steps; one per field, a step's instruction preferred over its name), each a snippet of 40 characters either side
of the field's first match, with `…` where the field text was cut.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from wynd.controller.errors import Invalid

if TYPE_CHECKING:
    from wynd.controller.models import Match, ProcessStatus, StatusFlag

FLAGS = ("design", "compiled", "built", "released")
CONTEXT = 40
MAX_MATCHES = 3


def search(
    docs: list[dict],
    statuses: dict[str, ProcessStatus],
    q: str,
    flags: Sequence[StatusFlag],
    limit: int = 200,
) -> list[tuple[str, int, list[Match]]]:
    """`docs` items are `{"id", "name", "goal", "steps": [(step_key, instruction or "")]}`; -> (id, score, matches)."""
    unknown = [flag for flag in flags if flag not in FLAGS]
    if unknown:
        raise Invalid(f"unknown status flag '{unknown[0]}' (flags: {', '.join(FLAGS)})")
    tokens = q.lower().split()
    hits = []
    for doc in docs:
        status = statuses.get(doc["id"])
        if flags and (status is None or not all(getattr(status, flag) for flag in flags)):
            continue
        fields = _fields(doc)
        best = [max((weight for _, _, text, weight in fields if token in text.lower()), default=0) for token in tokens]
        if 0 in best:
            continue
        hits.append((doc["id"], sum(best), _matches(fields, tokens)))
    hits.sort(key=lambda hit: (-hit[1], hit[0]))
    return hits[:limit]


def _fields(doc: dict[str, Any]) -> list[tuple[str, str | None, str, int]]:
    """(field, step, whitespace-collapsed text, weight) in match order."""
    fields = [("id", None, doc["id"], 3), ("name", None, doc.get("name") or "", 3),
              ("goal", None, doc.get("goal") or "", 2)]
    for key, instruction in doc.get("steps") or ():
        fields += [("instruction", key, instruction or "", 1), ("instruction", key, key, 1)]
    return [(field, step, " ".join(text.split()), weight) for field, step, text, weight in fields]


def _matches(fields: list[tuple[str, str | None, str, int]], tokens: list[str]) -> list[Match]:
    from wynd.controller.models import Match

    matches: list[Match] = []
    seen = set()
    for field, step, text, _ in fields:
        lowered = text.lower()
        found = [(lowered.find(token), token) for token in tokens if token in lowered]
        if not found or (field, step) in seen:
            continue
        seen.add((field, step))
        start, token = min(found)
        begin, end = max(0, start - CONTEXT), min(len(text), start + len(token) + CONTEXT)
        snippet = ("…" if begin > 0 else "") + text[begin:end] + ("…" if end < len(text) else "")
        matches.append(Match(field=field, step=step, snippet=snippet))
        if len(matches) == MAX_MATCHES:
            break
    return matches
