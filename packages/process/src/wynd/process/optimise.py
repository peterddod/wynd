"""M5 optimise analysis: per-unit statistics from traces, tier rules and the report (PLAN §6.1, §15 item 55; owner
OPT-PROC, M5; models and algorithms `$DRAFTS/08 §3.3–§3.6`).

Reads §3.13 trace events (`step.end`, `model.call`, `edge.check`, `run.end`) through `wynd.runtime.trace.parse_event`.
A unit is a step path (`extract`, `child.validate`) or an agentic branch, `edge:` + the enclosing ProcessStep path
prefix + its branch key (`edge:validate.done[save]`, `edge:child.check.done[ok]`). Replayed calls count toward
executions and durations but never toward cost or tier evidence. An execution whose end event lacks the model, usage
or attempts (a `step.end` error exit carries no `model`; a failed `edge.check` carries no usage) takes them from the
`model.call` events of that execution. Lock paths are workspace-relative. The job handler lives in
`wynd.controller.optimise`.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, computed_field

from wynd.runtime.trace import EdgeCheck, EventBase, ModelCall, RunEnd, StepEnd, StepStart, parse_event
from wynd.runtime.usage import Usage
from wynd.spec.base import DEFAULT_PROVIDER
from wynd.spec.errors import Loc, SpecError
from wynd.spec.lockfiles import EdgesLock, Tier, branch_key
from wynd.spec.workspace import EDGES_LOCK_FILE, STEP_LOCK_FILE
from wynd.spec.yamlio import parse_model

from .workspace import join, read_text

if TYPE_CHECKING:
    from .loader import LoadedProcess, ResolvedStep

UnitKind = Literal["deterministic", "agentic", "shell", "process", "edge"]
TIERS: tuple[Tier, ...] = ("cheap", "standard", "strong")


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

    @computed_field
    @property
    def fail_rate(self) -> float | None:
        return self.fails / self.n if self.n else None

    @computed_field
    @property
    def vfail_rate(self) -> float | None:
        return self.vfail_execs / self.n if self.n else None

    @computed_field
    @property
    def mean_attempts(self) -> float | None:
        return self.attempts_sum / self.n if self.n else None

    @computed_field
    @property
    def cost_per_exec_usd(self) -> float | None:
        return self.cost_usd / self.n if self.n else None

    @computed_field
    @property
    def call_p50_ms(self) -> float | None:
        return percentile(self.call_latency_ms, 0.5)

    @computed_field
    @property
    def call_p95_ms(self) -> float | None:
        return percentile(self.call_latency_ms, 0.95)

    @computed_field
    @property
    def exec_p50_ms(self) -> float | None:
        return percentile(self.exec_duration_ms, 0.5)

    @computed_field
    @property
    def exec_p95_ms(self) -> float | None:
        return percentile(self.exec_duration_ms, 0.95)


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
    """Nearest-rank percentile; None for empty input. q in (0, 1]."""
    if not xs:
        return None
    s = sorted(xs)
    return s[max(0, math.ceil(q * len(s)) - 1)]


# --- statistics ------------------------------------------------------------------------------------------------------


@dataclass
class _Calls:
    """The `model.call` events of one execution still in flight."""

    provider: str | None = None
    tier: str | None = None
    usage: Usage = field(default_factory=Usage)
    attempts: int = 0                     # highest `attempt`
    live: int = 0                         # calls not answered from a cassette


@dataclass(frozen=True)
class _Exec:
    """One execution that counts as tier evidence."""

    provider: str
    tier: Tier
    usage: Usage
    attempts: int
    validation_failures: int
    failed: bool                          # ended with a validation failure
    duration_ms: float


def collect_stats(
    process: str, run_ids: Iterable[str], read_events: Callable[[str], Iterable[Mapping[str, Any]]]
) -> ProcessStats:
    """Single pass per run over its events (in seq order, as `TraceSink.read` returns them). Runs without `run.end`
    count for unit numbers but not for run-level ones (runs, run duration, dispatch overhead)."""
    stats = ProcessStats(process=process)
    for run_id in run_ids:
        _collect_run(stats, [parse_event(event) for event in read_events(run_id)])
    return stats


def _collect_run(stats: ProcessStats, events: list[EventBase]) -> None:
    paths: dict[int, str] = {}            # span -> step path, to qualify branches inside child processes
    pending: dict[str, _Calls] = {}       # unit -> model calls of its execution in flight
    step_time, transitions, run_ms = 0.0, 0, None
    for ev in events:
        match ev:
            case StepStart():
                paths[ev.span] = ev.step
            case ModelCall():
                _collect_call(stats, _call_unit(ev.step, ev.parent, paths), ev.model_extra or {}, pending)
            case StepEnd():
                unit = _unit(stats, ev.step, ev.kind)
                unit.kind = ev.kind
                duration = float(ev.timings.get("duration_ms") or 0.0)
                unit.executions += 1
                unit.duration_ms.append(duration)
                if ev.parent is None:
                    step_time += duration
                    transitions += 1
                calls = pending.pop(ev.step, None)
                if ev.kind == "agentic" and (x := _step_exec(ev, calls, duration)) is not None:
                    _add_exec(unit, x)
            case EdgeCheck():
                name = "edge:" + _prefix(ev.parent, paths) + ev.branch_key
                unit = _unit(stats, name, "edge")
                duration = float(ev.duration_ms or 0.0)
                unit.executions += 1
                unit.duration_ms.append(duration)
                if ev.parent is None:
                    step_time += duration
                if (x := _edge_exec(ev, pending.pop(name, None), duration)) is not None:
                    _add_exec(unit, x)
            case RunEnd():
                run_ms = ev.duration_ms
    if run_ms is not None:
        stats.runs += 1
        stats.run_duration_ms.append(run_ms)
        stats.dispatch_overhead_ms.append(max(0.0, run_ms - step_time) / max(1, transitions))


def _prefix(parent: int | None, paths: Mapping[int, str]) -> str:
    """"<ProcessStep path>." for events of a child process instance (their parent span is that node), else ""."""
    path = paths.get(parent) if parent is not None else None
    return f"{path}." if path else ""


def _call_unit(step: str, parent: int | None, paths: Mapping[int, str]) -> str:
    if step.startswith("edge:"):
        return "edge:" + _prefix(parent, paths) + step.removeprefix("edge:")
    return step


def _unit(stats: ProcessStats, name: str, kind: UnitKind) -> UnitStats:
    unit = stats.units.get(name)
    if unit is None:
        unit = stats.units[name] = UnitStats(unit=name, kind=kind)
    return unit


def _tier(unit: UnitStats, provider: str, tier: Tier) -> TierStats:
    key = f"{provider}/{tier}"
    ts = unit.by_tier.get(key)
    if ts is None:
        ts = unit.by_tier[key] = TierStats(provider=provider, tier=tier)
    return ts


def _collect_call(stats: ProcessStats, name: str, data: Mapping[str, Any], pending: dict[str, _Calls]) -> None:
    """Remember the call for its execution; a live call with usage adds its latency to the unit's tier stats."""
    provider, tier = data.get("provider"), data.get("tier")
    usage = Usage.model_validate(data["usage"]) if data.get("usage") else None
    live = data.get("cassette") != "replay"
    calls = pending.setdefault(name, _Calls())
    calls.provider, calls.tier = provider or calls.provider, tier or calls.tier
    calls.attempts = max(calls.attempts, int(data.get("attempt") or 1))
    calls.live += live
    if usage is not None:
        calls.usage += usage
    if live and usage is not None and provider and tier in TIERS:
        kind: UnitKind = "edge" if name.startswith("edge:") else "agentic"
        _tier(_unit(stats, name, kind), provider, tier).call_latency_ms.append(usage.latency_ms)


def _step_exec(ev: StepEnd, calls: _Calls | None, duration: float) -> _Exec | None:
    if ev.model is not None and ev.model.tier:
        provider, tier = ev.model.provider, ev.model.tier
    elif calls is not None:
        provider, tier = calls.provider, calls.tier
    else:
        return None
    usage = ev.usage if ev.usage is not None else calls.usage if calls is not None and calls.usage.calls else None
    attempts = ev.attempts or (calls.attempts if calls is not None else 0)     # a timed-out step.end says 0
    failed = ev.exit == "error" and (ev.outputs or {}).get("cause") == "output_validation"
    return _evidence(provider, tier, usage, ev.replayed, attempts, ev.validation_failures, failed, duration)


def _edge_exec(ev: EdgeCheck, calls: _Calls | None, duration: float) -> _Exec | None:
    provider = ev.provider or (calls.provider if calls else None)
    tier = ev.tier or (calls.tier if calls else None)
    usage = ev.usage if ev.usage is not None else calls.usage if calls is not None and calls.usage.calls else None
    replayed = ev.replayed if ev.replayed is not None else calls is None or calls.live == 0
    attempts = ev.attempts if ev.attempts is not None else calls.attempts if calls is not None else 0
    failed = ev.error_cause == "validation"
    vfails = ev.validation_failures if ev.validation_failures is not None else attempts if failed else 0
    return _evidence(provider, tier, usage, replayed, attempts, vfails, failed, duration)


def _evidence(
    provider: str | None, tier: str | None, usage: Usage | None, replayed: bool, attempts: int, vfails: int,
    failed: bool, duration: float,
) -> _Exec | None:
    """An execution is tier evidence iff it made >= 1 live model call at a known provider/tier."""
    if replayed or usage is None or usage.calls < 1 or not provider or tier not in TIERS:
        return None
    return _Exec(provider, tier, usage, attempts, vfails, failed, duration)


def _add_exec(unit: UnitStats, x: _Exec) -> None:
    ts = _tier(unit, x.provider, x.tier)
    ts.n += 1
    ts.attempts_sum += x.attempts
    ts.fails += x.failed
    ts.vfail_execs += x.validation_failures >= 1
    ts.calls += x.usage.calls
    ts.input_tokens += x.usage.input_tokens
    ts.output_tokens += x.usage.output_tokens
    ts.cost_usd += x.usage.cost_usd or 0.0
    ts.exec_duration_ms.append(x.duration_ms)


# --- rules -----------------------------------------------------------------------------------------------------------


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
    """The first matching rule of `$DRAFTS/08 §3.4` (R0, P1, P2, H1, D1, K1) on the stats at `provider/tier`.
    `applicable` is True for promote/demote; the report clears it for units the process does not own."""
    ts = unit.by_tier.get(f"{provider}/{tier}")
    n = ts.n if ts is not None else 0
    if ts is None or n < rules.min_samples:
        return Recommendation(action="keep", rule="R0", from_tier=tier, applicable=False,
                              reason=f"only {n} live executions at {tier}; need {rules.min_samples}")
    index = TIERS.index(tier)
    if _failing(ts, rules):
        if tier != "strong":
            return Recommendation(action="promote", rule="P1", from_tier=tier, to_tier=TIERS[index + 1],
                                  reason=_evidence_text(ts), applicable=True)
        return Recommendation(action="flag", rule="P2", from_tier=tier, applicable=False,
                              reason="failing at the strongest tier: improve examples/instruction or split the step")
    demotable = (tier != "cheap" and ts.fails == 0 and ts.vfail_rate <= rules.demote_max_vfail_rate
                 and ts.mean_attempts <= rules.demote_max_mean_attempts)
    if demotable:
        lower = TIERS[index - 1]
        below = unit.by_tier.get(f"{provider}/{lower}")
        if below is not None and below.n >= rules.hysteresis_min_samples and _failing(below, rules):
            return Recommendation(action="keep", rule="H1", from_tier=tier, applicable=False,
                                  reason=f"{lower} failed before: fail {below.fail_rate:.1%}, "
                                         f"vfail {below.vfail_rate:.1%} over {below.n}")
        return Recommendation(action="demote", rule="D1", from_tier=tier, to_tier=lower,
                              reason=_evidence_text(ts), applicable=True)
    return Recommendation(action="keep", rule="K1", from_tier=tier, reason="within bounds", applicable=False)


def _failing(ts: TierStats, rules: Rules) -> bool:
    return ts.fail_rate >= rules.promote_fail_rate or ts.vfail_rate >= rules.promote_vfail_rate


def _evidence_text(ts: TierStats) -> str:
    return (f"{ts.fails} failures, {ts.vfail_rate:.1%} validation failures, {ts.mean_attempts:.2f} attempts "
            f"over {ts.n} executions at {ts.tier}")


# --- units of a process ----------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class UnitTarget:
    """One reportable unit of a process closure: where its tier lives and whether optimise may change it."""

    unit: str                             # stats key (module docstring)
    kind: UnitKind | None                 # None for a step without a lock (taken from the stats)
    package: str | None                   # the `use:` text; None for branches
    lock_path: str | None                 # workspace-relative step.lock.yaml / edges.lock.yaml
    lock_key: str | None                  # branch key in edges.lock.yaml; None for steps
    provider: str | None                  # agentic steps and branches: the effective provider
    tier: Tier | None                     # agentic steps and branches: the current tier
    loc: Loc                              # the unit's place in the root process.yaml (a child's: its node)
    blocked: str | None = None            # why a tier change cannot be applied (None: it can)


def units_of(lp: LoadedProcess, edges_lock: EdgesLock) -> list[UnitTarget]:
    """Every step of the closure in `steps:` order (a ProcessStep node followed by its child's units), then every
    agentic branch in edge order (the root's, then each child's). The effective provider is the ROOT's for every unit
    (SPEC §3.2); a child's branches read the child's own `edges.lock.yaml` (`edges_lock_of`)."""
    provider = lp.doc.provider or DEFAULT_PROVIDER
    return [*_step_units(lp, provider, "", None), *_edge_units(lp, edges_lock, provider, "", None)]


def _step_units(lp: LoadedProcess, provider: str, prefix: str, node: Loc | None) -> Iterator[UnitTarget]:
    for key, rs in lp.steps.items():
        unit, loc = prefix + key, node or ("steps", key)
        if rs.ref_kind == "process":
            yield UnitTarget(unit, "process", rs.use, None, None, None, None, loc)
            child = lp.children.get(rs.child)
            if child is not None:
                yield from _step_units(child, provider, unit + ".", loc)
            continue
        yield _step_unit(lp, rs, unit, provider, loc, child_process=bool(prefix))


def _step_unit(lp: LoadedProcess, rs: ResolvedStep, unit: str, provider: str, loc: Loc,
               child_process: bool) -> UnitTarget:
    lock = rs.package.lock if rs.package is not None else None
    if lock is None:
        return UnitTarget(unit, None, rs.use, None, None, None, None, loc)
    lock_path = join(rs.package.dir, STEP_LOCK_FILE)
    if lock.kind != "agentic":
        return UnitTarget(unit, lock.kind, rs.use, lock_path, None, None, None, loc)
    if child_process:
        blocked = _child_blocked(lp.id)
    elif rs.ref_kind == "root":
        blocked = f"shared step {rs.use}: changing its tier changes every process that uses it"
    else:
        blocked = None
    return UnitTarget(unit, "agentic", rs.use, lock_path, None, lock.provider or provider, lock.tier or "cheap", loc,
                      blocked)


def _edge_units(
    lp: LoadedProcess, edges_lock: EdgesLock, provider: str, prefix: str, node: Loc | None
) -> Iterator[UnitTarget]:
    lock_path = join(lp.dir, EDGES_LOCK_FILE)
    for i, edge in enumerate(lp.doc.edges):
        for j, branch in enumerate(edge.to):
            if branch.check is None:
                continue
            key = branch_key(edge.from_, j, branch.name)
            entry = edges_lock.edges.get(key)
            if prefix:
                blocked = _child_blocked(lp.id)
            elif entry is None:
                blocked = f"no edges.lock.yaml entry for {key}: run wynd compile first"
            else:
                blocked = None
            yield UnitTarget(
                "edge:" + prefix + key, "edge", None, lock_path, key,
                (entry.provider if entry is not None else None) or provider,
                entry.tier if entry is not None else "cheap",
                node or ("edges", i, "to", j), blocked,
            )
    for key, rs in lp.steps.items():
        child = lp.children.get(rs.child) if rs.ref_kind == "process" else None
        if child is not None:
            yield from _edge_units(child, edges_lock_of(child), provider, f"{prefix}{key}.",
                                   node or ("steps", key))


def _child_blocked(pid: str) -> str:
    return f"belongs to child process {pid}: optimise changes only the process's own steps and branches"


def edges_lock_of(lp: LoadedProcess) -> EdgesLock:
    """The process's `edges.lock.yaml`, read through the tree its loader attached (PLAN §6.3); empty when there is no
    tree, no file or the file does not load (the validator reports that)."""
    tree = getattr(lp, "_tree", None)
    path = join(lp.dir, EDGES_LOCK_FILE)
    if tree is None or path not in tree.files():
        return EdgesLock()
    try:
        return parse_model(read_text(tree, path), EdgesLock, path)
    except SpecError:
        return EdgesLock()


# --- report ----------------------------------------------------------------------------------------------------------


class TierChange(BaseModel):
    unit: str                             # "extract" | "edge:validate.done[save]"
    lock_path: str                        # workspace-relative
    lock_key: str | None                  # None for step.lock.yaml; branch_key for edges.lock.yaml
    from_tier: Tier
    to_tier: Tier
    rule: Literal["P1", "D1"]
    evidence: dict[str, float]


class UnitReport(BaseModel):
    unit: str
    kind: UnitKind
    package: str | None                   # "./steps/extract_invoice_fields" | "shared:extract" | None for edges
    lock_path: str | None                 # workspace-relative path of step.lock.yaml / edges.lock.yaml
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
    totals: list[dict[str, Any]]          # per provider/tier: {"provider", "tier", "calls", "input_tokens",
                                          #                     "output_tokens", "cost_usd"}
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
    """The report over `stats` for the process as loaded. `edges_lock` is the root's; `window.max_runs` is None here
    (the caller that chose the runs fills it)."""
    from .latency import latency_findings

    units: list[UnitReport] = []
    changes: list[TierChange] = []
    for target in units_of(lp, edges_lock):
        unit = _unit_report(target, stats.units.get(target.unit), rules)
        units.append(unit)
        rec = unit.recommendation
        if rec is not None and rec.applicable:
            ts = unit.current
            changes.append(TierChange(
                unit=target.unit, lock_path=target.lock_path, lock_key=target.lock_key, from_tier=rec.from_tier,
                to_tier=rec.to_tier, rule=rec.rule,
                evidence={"n": ts.n, "fail_rate": ts.fail_rate, "vfail_rate": ts.vfail_rate,
                          "mean_attempts": ts.mean_attempts, "cost_per_exec_usd": ts.cost_per_exec_usd},
            ))
    live_calls = sum(len(ts.call_latency_ms) for u in stats.units.values() for ts in u.by_tier.values())
    return OptimiseReport(
        process=lp.id,
        commit=commit,
        generated_at=(now or datetime.now(UTC)).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        window={"max_runs": None, "runs_considered": stats.runs, "live_calls": live_calls},
        rules=asdict(rules),
        units=units,
        warnings=[{"code": f.code, "unit": f.unit, "message": f.message}
                  for f in latency_findings(lp, stats, edges_lock, rules)],
        totals=_totals(stats),
        changes=changes,
    )


def _unit_report(target: UnitTarget, stats: UnitStats | None, rules: Rules) -> UnitReport:
    kind = target.kind or (stats.kind if stats is not None else "deterministic")
    durations = stats.duration_ms if stats is not None else []
    by_tier = stats.by_tier if stats is not None else {}
    current = rec = None
    if target.tier is not None:
        current = by_tier.get(f"{target.provider}/{target.tier}")
        rec = recommend(stats or UnitStats(unit=target.unit, kind=kind), target.provider, target.tier, rules)
        if rec.applicable and target.blocked is not None:
            rec = rec.model_copy(update={"applicable": False, "not_applicable_reason": target.blocked})
    return UnitReport(
        unit=target.unit, kind=kind, package=target.package, lock_path=target.lock_path, provider=target.provider,
        tier=target.tier, current=current, by_tier=by_tier, duration_p50_ms=percentile(durations, 0.5),
        duration_p95_ms=percentile(durations, 0.95), recommendation=rec,
    )


def _totals(stats: ProcessStats) -> list[dict[str, Any]]:
    """Live usage per provider/tier over every unit, sorted by provider then tier strength."""
    totals: dict[tuple[str, str], dict[str, Any]] = {}
    for unit in stats.units.values():
        for ts in unit.by_tier.values():
            row = totals.setdefault((ts.provider, ts.tier), {
                "provider": ts.provider, "tier": ts.tier, "calls": 0, "input_tokens": 0, "output_tokens": 0,
                "cost_usd": 0.0})
            row["calls"] += ts.calls
            row["input_tokens"] += ts.input_tokens
            row["output_tokens"] += ts.output_tokens
            row["cost_usd"] += ts.cost_usd
    return [totals[key] for key in sorted(totals, key=lambda k: (k[0], TIERS.index(k[1])))]


# --- text rendering --------------------------------------------------------------------------------------------------

_COLUMNS = (("UNIT", 27), ("KIND", 9), ("PROVIDER", 13), ("TIER", 10), ("N", 5), ("FAIL", 7), ("VFAIL", 7),
            ("ATT", 6), ("COST/EXEC", 11), ("P95 CALL", 10), ("ACTION", 0))


def render_report_text(report: OptimiseReport) -> str:
    """Fixed-width, deterministic text (golden-tested)."""
    rules = report.rules
    lines = [
        f"optimise {report.process} @ {report.commit[:7]}  ({report.window['runs_considered']} runs, "
        f"{report.window['live_calls']} live calls; rules v{rules['version']}, min {rules['min_samples']})",
        _row([name for name, _ in _COLUMNS]),
        *(_row(_cells(unit, rules)) for unit in report.units),
    ]
    if report.warnings:
        lines.append("warnings:")
        lines += [f"  {w['code']}: {w['message']}" for w in report.warnings]
    else:
        lines.append("warnings: none")
    totals = "; ".join(
        f"{t['provider']}/{t['tier']} {t['calls']} calls, {_count(t['input_tokens'])} in / "
        f"{_count(t['output_tokens'])} out tokens, ${t['cost_usd']:.2f}"
        for t in report.totals
    )
    lines.append(f"totals: {totals} (API-equivalent)" if totals else "totals: none")
    n = len(report.changes)
    if n:
        lines.append(f"{n} applicable change{'s' if n != 1 else ''}. Run `wynd optimise {report.process} --apply` "
                     "to submit an optimise job.")
    else:
        lines.append("No applicable changes.")
    return "\n".join(lines) + "\n"


def _row(cells: list[str]) -> str:
    padded = (f"{cell:<{width - 1}} " if width else cell for cell, (_, width) in zip(cells, _COLUMNS, strict=True))
    return "".join(padded).rstrip()


def _cells(unit: UnitReport, rules: Mapping[str, Any]) -> list[str]:
    """N is the live executions at the current tier (tier evidence), so units without a tier show "-"."""
    kind = "determ." if unit.kind == "deterministic" else unit.kind
    rec, ts = unit.recommendation, unit.current
    if unit.tier is None:
        return [unit.unit, kind, "-", "-", "-", "-", "-", "-", "-", "-", "-"]
    n = ts.n if ts is not None else 0
    rated = rec is not None and rec.rule != "R0" and ts is not None
    return [
        unit.unit, kind, unit.provider or "-", unit.tier, str(n),
        f"{ts.fail_rate:.1%}" if rated else "-",
        f"{ts.vfail_rate:.1%}" if rated else "-",
        f"{ts.mean_attempts:.2f}" if rated else "-",
        f"${ts.cost_per_exec_usd:.4f}" if ts is not None and ts.n else "-",
        f"{ts.call_p95_ms / 1000:.1f}s" if ts is not None and ts.call_p95_ms is not None else "-",
        _action(rec, n, rules),
    ]


def _count(tokens: int) -> str:
    if tokens < 1000:
        return str(tokens)
    if tokens < 1_000_000:
        return f"{tokens / 1000:.1f}k"
    return f"{tokens / 1_000_000:.1f}M"


def _action(rec: Recommendation | None, n: int, rules: Mapping[str, Any]) -> str:
    if rec is None:
        return "-"
    match rec.action, rec.rule:
        case ("promote" | "demote"), _:
            note = "" if rec.applicable else ", not applicable"
            return f"{rec.action} -> {rec.to_tier} ({rec.rule}{note})"
        case "keep", "R0":
            return f"keep (R0: {n} < {rules['min_samples']})"
        case _:
            return f"{rec.action} ({rec.rule})"


class OptimiseJobInput(BaseModel):
    process: str
    report_commit: str
    changes: list[TierChange]
