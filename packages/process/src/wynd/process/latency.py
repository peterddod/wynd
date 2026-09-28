"""M5 data-driven latency warnings for `latency: fast` processes (PLAN §3.2, §6.3 pass 8; owner OPT-PROC, M5;
`$DRAFTS/08 §3.5`).

Codes `W-LATENCY-CALL` (an agentic step or branch whose live calls at its current provider/tier are slow),
`W-LATENCY-RUN` and `W-LATENCY-DISPATCH` (run duration and executor overhead per top-level transition). Each needs
`Rules.latency_min_samples` samples and fires only when the root process declares `latency: fast`. Pure; the
validator calls `latency_warnings` when `stats` is given, and the optimise report lists `latency_findings`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wynd.spec.errors import Loc, Severity
from wynd.spec.yamlio import located

from .optimise import RULES_V1, Rules, edges_lock_of, percentile, units_of

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic
    from wynd.spec.lockfiles import EdgesLock

    from .loader import LoadedProcess
    from .optimise import ProcessStats

CODES: dict[str, tuple[Severity, str]] = {
    "W-LATENCY-CALL": (
        "warning",
        "{unit}: p95 model-call latency {p95:.1f}s over {n} live calls ({provider}/{tier}) exceeds the latency: fast "
        "budget of {budget}; consider a lower tier or a ModelProvider",
    ),
    "W-LATENCY-RUN": ("warning", "p95 run duration {p95:.1f}s over {n} runs exceeds {budget}"),
    "W-LATENCY-DISPATCH": (
        "warning",
        "p95 executor overhead {p95:.0f}ms per transition exceeds {budget} (step-to-step latency should be "
        "milliseconds)",
    ),
}


@dataclass(frozen=True)
class LatencyFinding:
    code: str
    unit: str                             # the step/branch unit; "" for run-level findings
    message: str
    loc: Loc                              # where in the root process.yaml


def latency_findings(
    lp: LoadedProcess, stats: ProcessStats, edges_lock: EdgesLock, rules: Rules = RULES_V1
) -> list[LatencyFinding]:
    if lp.doc.latency != "fast":
        return []
    found: list[LatencyFinding] = []
    for target in units_of(lp, edges_lock):
        unit = stats.units.get(target.unit)
        ts = unit.by_tier.get(f"{target.provider}/{target.tier}") if unit is not None and target.tier else None
        if ts is None or len(ts.call_latency_ms) < rules.latency_min_samples:
            continue
        p95 = percentile(ts.call_latency_ms, 0.95)
        if p95 > rules.fast_call_p95_ms:
            found.append(_finding("W-LATENCY-CALL", target.unit, target.loc, p95=p95 / 1000,
                                  n=len(ts.call_latency_ms), provider=target.provider, tier=target.tier,
                                  budget=_seconds(rules.fast_call_p95_ms)))
    if stats.runs < rules.latency_min_samples:
        return found
    run_p95 = percentile(stats.run_duration_ms, 0.95)
    if run_p95 > rules.fast_run_p95_ms:
        found.append(_finding("W-LATENCY-RUN", "", (), p95=run_p95 / 1000, n=stats.runs,
                              budget=_seconds(rules.fast_run_p95_ms)))
    dispatch_p95 = percentile(stats.dispatch_overhead_ms, 0.95)
    if dispatch_p95 > rules.fast_dispatch_p95_ms:
        found.append(_finding("W-LATENCY-DISPATCH", "", (), p95=dispatch_p95,
                              budget=f"{rules.fast_dispatch_p95_ms:g}ms"))
    return found


def latency_warnings(lp: LoadedProcess, stats: ProcessStats, rules: Rules = RULES_V1) -> list[Diagnostic]:
    """`latency_findings` as diagnostics on the root `process.yaml`, with the root's `edges.lock.yaml` read through the
    loader's tree."""
    return [
        located(lp.source, CODES[f.code][0], f.code, f.message, f.loc, process=lp.id)
        for f in latency_findings(lp, stats, edges_lock_of(lp), rules)
    ]


def _finding(code: str, unit: str, loc: Loc, **fields: object) -> LatencyFinding:
    return LatencyFinding(code, unit, CODES[code][1].format(unit=unit, **fields), loc)


def _seconds(ms: float) -> str:
    return f"{ms / 1000:g}s"
