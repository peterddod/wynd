"""M5 data-driven latency warnings for `latency: fast` processes (PLAN §3.2, §6.3 pass 8; owner OPT-PROC, M5;
`$DRAFTS/08 §3.5`).

Codes `W-LATENCY-CALL`, `W-LATENCY-RUN`, `W-LATENCY-DISPATCH`. Pure; the validator calls it when `stats` is given.
Until M5 it reports nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .optimise import RULES_V1, Rules

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic

    from .loader import LoadedProcess
    from .optimise import ProcessStats


def latency_warnings(lp: LoadedProcess, stats: ProcessStats, rules: Rules = RULES_V1) -> list[Diagnostic]:
    return []
