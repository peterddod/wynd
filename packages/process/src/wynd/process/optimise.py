"""M5 optimise analysis: per-unit statistics from traces, tier rules and the report (PLAN §6.1, §15 item 55; owner
OPT-PROC, M5; models and algorithms `$DRAFTS/08 §3.3–§3.7`).

Reads §3.13 trace events (`step.end`, `model.call`, `edge.check`, `run.end`). Replayed calls count toward executions
and durations but never toward cost or tier evidence. The job handler lives in `wynd.controller.optimise`.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field

from wynd.spec.lockfiles import Tier

if TYPE_CHECKING:
    from wynd.spec.lockfiles import EdgesLock

    from .loader import LoadedProcess

UnitKind = Literal["deterministic", "agentic", "shell", "process", "edge"]


class TierStats(BaseModel):
    provider: str
    tier: Tier
    n: int = 0                            # executions with >= 1 live (non-replayed) model call
    fails: int = 0                        # executions ending with a validation failure
    vfail_execs: int = 0                  # executions with validation_failures >= 1
    attempts_sum: int = 0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    call_latency_ms: list[float] = Field(default_factory=list, exclude=True)
    exec_duration_ms: list[float] = Field(default_factory=list, exclude=True)


class UnitStats(BaseModel):
    unit: str                             # "extract" | "edge:validate.done[save]" | "child.validate"
    kind: UnitKind
    executions: int = 0                   # all executions incl. replayed and deterministic
    duration_ms: list[float] = Field(default_factory=list, exclude=True)
    by_tier: dict[str, TierStats] = {}    # key "<provider>/<tier>"


class ProcessStats(BaseModel):
    process: str
    runs: int = 0
    run_duration_ms: list[float] = Field(default_factory=list, exclude=True)
    dispatch_overhead_ms: list[float] = Field(default_factory=list, exclude=True)
    units: dict[str, UnitStats] = {}


def percentile(xs: list[float], q: float) -> float | None:
    raise NotImplementedError("PLAN §6.1 optimise.percentile")


def collect_stats(
    process: str, run_ids: Iterable[str], read_events: Callable[[str], Iterable[Mapping[str, Any]]]
) -> ProcessStats:
    raise NotImplementedError("PLAN §6.1 optimise.collect_stats")


@dataclass(frozen=True)
class Rules:
    version: int = 1
    min_samples: int = 20                 # R0 threshold (CLI --min-runs overrides)
    promote_fail_rate: float = 0.05       # P1
    promote_vfail_rate: float = 0.20      # P1
    demote_max_vfail_rate: float = 0.02   # D1
    demote_max_mean_attempts: float = 1.05  # D1
    hysteresis_min_samples: int = 5       # H1
    fast_call_p95_ms: float = 1500.0
    fast_run_p95_ms: float = 10000.0
    fast_dispatch_p95_ms: float = 50.0
    latency_min_samples: int = 5


RULES_V1 = Rules()


class Recommendation(BaseModel):
    action: Literal["keep", "promote", "demote", "flag"]
    rule: Literal["R0", "P1", "P2", "H1", "D1", "K1"]
    from_tier: Tier | None = None
    to_tier: Tier | None = None
    reason: str
    applicable: bool                      # False for non-local units or keep/flag
    not_applicable_reason: str | None = None


def recommend(unit: UnitStats, provider: str, tier: Tier, rules: Rules) -> Recommendation:
    raise NotImplementedError("PLAN §6.1 optimise.recommend")


class TierChange(BaseModel):
    unit: str                             # "extract" | "edge:validate.done[save]"
    lock_path: str                        # repo-relative
    lock_key: str | None                  # None for step.lock.yaml; branch_key for edges.lock.yaml
    from_tier: Tier
    to_tier: Tier
    rule: Literal["P1", "D1"]
    evidence: dict[str, float]


class UnitReport(BaseModel):
    unit: str
    kind: UnitKind
    package: str | None                   # "./steps/extract_invoice_fields" | "shared:extract" | None for edges
    lock_path: str | None                 # repo-relative path of step.lock.yaml / edges.lock.yaml
    provider: str | None
    tier: Tier | None
    current: TierStats | None
    by_tier: dict[str, TierStats]
    duration_p50_ms: float | None
    duration_p95_ms: float | None
    recommendation: Recommendation | None


class OptimiseReport(BaseModel):
    v: Literal[1] = 1
    process: str
    commit: str                           # process HEAD (closure) at report time
    generated_at: str                     # ISO-8601 UTC
    window: dict[str, Any]                # {"max_runs", "runs_considered", "live_calls"}
    rules: dict[str, Any]                 # dataclasses.asdict(rules)
    units: list[UnitReport]               # process step order, then edges in edge order
    warnings: list[dict[str, str]]        # latency diagnostics as {"code", "unit", "message"}
    totals: list[dict[str, Any]]          # per provider/tier: {"provider", "tier", "calls", "input_tokens", "output_tokens", "cost_usd"}
    changes: list[TierChange]             # applicable promote/demote recommendations, ready for --apply


def build_report(
    lp: LoadedProcess,
    edges_lock: EdgesLock,
    stats: ProcessStats,
    *,
    commit: str,
    rules: Rules = RULES_V1,
    now: datetime | None = None,
) -> OptimiseReport:
    raise NotImplementedError("PLAN §6.1 optimise.build_report")


def render_report_text(report: OptimiseReport) -> str:
    raise NotImplementedError("PLAN §6.1 optimise.render_report_text")


class OptimiseJobInput(BaseModel):
    process: str
    report_commit: str
    changes: list[TierChange]
