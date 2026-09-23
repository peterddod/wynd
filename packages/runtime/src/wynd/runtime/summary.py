"""Deterministic step summaries (SPEC §3.6 step 5, PLAN §5.1; `$DRAFTS/02 §4.4`): never a model call; 200-char
cap; error summaries omit traceback/inputs/partial_outputs/child."""

from __future__ import annotations

from typing import Any

from wynd.spec.records import Summary


def summarise(step: str, exit: str, outputs: dict[str, Any], note: str = "") -> Summary:
    raise NotImplementedError("PLAN §5.1")


def project(value: Any) -> Any:
    raise NotImplementedError("PLAN §5.1")
