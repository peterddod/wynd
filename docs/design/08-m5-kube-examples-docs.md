# 08 — M5 (optimise, agentic edges), `wynd.kube`, sample workspace, docs, CI

Design draft for the synthesizer. Source of truth: `docs/SPEC.md`. The lead's fixed decisions are taken as given and not reopened.
Everything below is concrete enough to build in parallel. Anything owned by another area appears only under **Interfaces consumed** (section 10), with the exact shape this area needs, so the synthesizer can reconcile names.

Checked on this machine while drafting: `git lfs version` gives git-lfs/3.8.0; uv 0.10.7; Docker Desktop BuildKit v0.27.1; kubectl v1.34.1 client with **no cluster context**, and no kind/minikube; `claude` 2.1.280; `claude-agent-sdk` resolves to 0.2.157 on PyPI; pypdf 6.19.0. A stdlib-only PDF writer (6.9) was prototyped and round-trips exactly through `pypdf.PdfReader(strict=True).pages[0].extract_text()`, blank lines excepted: pypdf drops them, so the writer forbids them.

## 0. Sub-areas and owner tags

| Tag | Scope |
|---|---|
| **A8-opt** | M5 optimise: trace statistics, the rule set, the report, data-driven latency warnings, the `optimise` job, `wynd optimise` |
| **A8-edge** | M5 agentic edges: schema additions, `edges.lock.yaml`, validator rules, the runtime verifier seam, the worker RPC method, trace events |
| **A8-kube** | `packages/kube` (`wynd-kube`), the toolchain/controller image, the cluster install |
| **A8-sample** | `examples/invoices/`: the dogfood workspace, PDFs, proto-steps, hand-written steps, tests, cassettes |
| **A8-docs** | README, `docs/*`, LICENSE files, `.gitignore`, `.gitattributes`, root `conftest.py`, repo hygiene tests, the GitHub Actions workflow |

Some A8 modules sit inside packages that other owners hold (for example `wynd/runtime/edges.py`). For those, A8 owns the module file and the other owner owns the single call site in their file, as spelled out in section 11.

---

## 1. Interpretations (where the spec is ambiguous; simplest faithful reading)

1. **The sample process drops `latency: fast`.** The lead made `claude-code` (an AgentProvider) the default, and 3.9 says the validator warns when an AgentProvider runs in a `latency: fast` process. Keeping `latency: fast` would make `wynd validate` warn on every run of the dogfood. The fast-path warnings are exercised by unit tests over synthetic traces instead.
2. **Sample process corrections to spec 6.2, all needed for validity or usefulness:**
   - `provider: claude-code`.
   - `extract` also outputs `supplier`.
   - `validate` outputs structured `errors: list[FieldError]` and always returns a normalised `record` (with a `key`), never an optional field.
   - `fix` also receives `invoice_text`, because it needs the source to make judgement fixes.
   - `escalate` receives `queue_dir: env.ESCALATIONS_DIR` and `run_id: run.id`.
   - The env vars are `RECORDS_DIR`, `REVIEW_DIR` and `ESCALATIONS_DIR`.
   - At M5 the `validate.done` edge becomes `kind: agentic`, with a `check:` on the `save` branch.
3. **Example path conventions**, which the compiler's test generator and the process test runner must implement:
   - A relative `path` value in any example is relative to the **process directory**.
   - The literal prefix `{tmp}` is replaced by a fresh per-example temporary directory.
   - Process-level examples may carry `env:` (values may use `{tmp}`). This is an additive spec extension; the sample needs it because the process reads `env.RECORDS_DIR` and similar.
4. **Agentic edge semantics.** `kind` stays on the edge, as in 3.4. An agentic edge's branches may carry `check:`, a natural-language condition, plus an optional pull-context list `context:`.
   - A branch is taken iff its `when:` (if present) is true **and** its `check:` (if present) is judged true.
   - `with:` and `limits:` stay deterministic expressions.
   - The else branch is the first branch with neither `when:` nor `check:`.
   - A negative verdict means "not taken". A verifier failure (transport, validation after retries, or timeout) routes to the process error handler.
   - `when:` is always an expression and is never overloaded with natural language.
5. **Edge knobs live in a committed `processes/<id>/edges.lock.yaml`**, the edge analogue of `step.lock.yaml`: provider, tier, thinking, retries, timeout and a `check_hash`. "Knobs live in the lockfile", and edges are "compiled like steps".
6. **Agentic edges have no per-edge unit tests.** Process-level examples exercise them. Their model calls land in the process-level cassettes.
7. **Optimise scope.**
   - It changes **tiers only**. Thinking effort and retries are untouched.
   - It applies changes only to units owned by the process: process-local steps (`./steps/...`) and the process's own agentic branches.
   - Steps from step roots (`alias:path`) and child-process steps are reported but marked `applicable: false`, because editing them silently changes other processes.
8. **Optimise evidence and quality gate.**
   - Evidence is every **non-replayed** model call recorded in the TraceSink for runs of the process, whatever their origin (served, CLI, live tests, compile).
   - The quality gate for any tier change is that the unit's own tests pass live at the new tier, and then the process-level tests pass live. Examples are the quality bar, so no additional judge is introduced.
9. **The rule set is fixed** (`RULES_V1`) and printed in every report. The only CLI knob is `--min-runs`.
10. **"Fast" thresholds**, derived from spec 1 ("complete in seconds, step-to-step latency in milliseconds"): per-call p95 ≤ 1.5 s, run p95 ≤ 10 s, and executor dispatch overhead p95 ≤ 50 ms per transition.
11. **Kubernetes client.** The REST API is called via httpx only; there is no kubectl subprocess backend. Laptops point it at `kubectl proxy`. Tests use `FakeKubeClient` and `httpx.MockTransport`.
12. **Cluster image builds** use **rootless BuildKit** (`buildctl-daemonless.sh`) as an init container. Kaniko is not used; BuildKit is the one the spec names first and the one used locally.
13. **Cluster storage** reuses the **local filesystem backends** on a user-supplied RWX PersistentVolumeClaim shared by the controller and job pods. No new storage backends are written.
    - Process (serving) pods do not mount it. The controller persists the trace events of runs it submits (it already streams them for the run panel).
    - For this to work, the local RunRegistry must be safe for multiple processes: one file per record, atomic rename.
14. **Git in the cluster.** Jobs clone from a remote (`WYND_KUBE_GIT_URL`) at a commit sha and push result branches to it. The controller pushes design commits before submitting a job and fetches result branches before integrating. Both are driven by the presence of `WYND_GIT_REMOTE`, which is configuration, not an environment branch.
15. **The workspace may be a subdirectory of the git repository.** The dogfood lives at `examples/invoices/` inside the wynd repo. Controller, jobs and closure/HEAD computation must carry `workspace_subdir`.
16. **`wynd-kube` is its own console script** (typer). It avoids a CLI plugin mechanism and keeps `wynd-cli` free of a dependency on `wynd-kube`.
17. **git-lfs is recommended, not required.**
    - `.gitattributes` tracks `**/cassettes/**` with LFS.
    - The runtime cassette loader detects LFS pointer files and fails with an explicit "install git-lfs and `git lfs pull`" error.
    - This repo's CI enforces that committed cassettes are LFS pointers.
18. **Sample PDFs are generated by a stdlib-only script and committed.** A drift test re-generates them and compares bytes.
19. **Licences.**
    - Each package carries a full `LICENSE` text, fetched verbatim from apache.org and gnu.org, never retyped.
    - The root `LICENSE` is a short notice mapping directories to licences.
    - `examples/` is Apache-2.0, so users can copy the sample freely. Docs and repo tooling are AGPL-3.0-or-later.
20. **Where agentic edges execute.** The verifier runs in a **venv worker** that has the provider's env fragment, reached through a new worker RPC method `edge.check`. The supervisor interpreter holds only `spec + runtime`, so it cannot import `claude-agent-sdk`. The venv planner assigns each agentic edge to a venv (6.2).
21. **Optimise is British-spelled**, as in the spec. There is no `optimize` alias.

---

## 2. Spec clauses this area is responsible for (coverage audit)

| Clause | What this area delivers |
|---|---|
| 1 (keep the fast path fast) | Data-driven latency warnings: `W-LATENCY-CALL`, `W-LATENCY-RUN`, `W-LATENCY-DISPATCH` (3.5) |
| 2 (non-goal: agentic edges in v1) | Delivered in M5 (4) |
| 3.4 `kind: agentic` | Semantics, schema additions, executor seam (4) |
| 3.4.1 counters | Unchanged. Agentic branches count as taken exactly like deterministic ones; `name:` keys the lock entry (4.3) |
| 3.5 routing failures | Verifier failure → process error handler, with `cause: "edge_check"` (4.2) |
| 3.7 pull context | Branch `context:` reuses the AgenticStep context grammar and assembler (4.1, 4.5) |
| 3.9 usage/latency, fast-process warning | Per-call latency drives the warnings. Edge usage goes into the trace and optimise (3, 4.7) |
| 4 env manifest → Secrets; secrets via k8s Secrets; warm pools | `wynd.kube` env mapping, serving Deployment (5.7, 5.8) |
| 4.1 (all: long-lived pod, run API contract, env manifest → Secret/ConfigMap, env check as init container, storage backends) | 5.7, 5.8, 5.12, `docs/run-api.md` (7) |
| 5 layout: `examples/`, `docs/` | 6, 7 |
| 5.1 workspace layout | Sample workspace tree (6.1) |
| 6.1 proto-step | Six proto-steps (6.5) |
| 6.2 process | Corrected sample `process.yaml` (6.4) |
| 6.3 compiled step packages | Six hand-written packages with locks, tests and cassettes (6.6–6.8) |
| 6.5 cassettes in git-lfs; "`test --live` is a job → branch" | `.gitattributes` and degrade policy (8.2); `optimise --apply` follows the same pattern (3.7) |
| 6.6 JobRunner (Kubernetes Job, post-M5); BuildKit/kaniko, never a Docker socket; jobs on a checkout at a ref | `KubeJobRunner`, rootless BuildKit, `wynd-kube checkout` (5) |
| 7 replay-tested agentic steps; 7.1 storage selectable by env for clusters | Sample cassettes (6.8); cluster storage on a PVC (5.10) |
| 9 "trace records cost and attempts so a later optimise…"; model tiering | M5 optimise (3) |
| 10 CLI | New commands `wynd optimise` and `wynd-kube` (3.8, 5.11) |
| 11 Release/triggers | Kubernetes trigger backend (CronJob), webhook Service, serving backend (5.8, 5.9) |
| 12 M5 and post-M5; "pick the dogfood" | 3, 4, 5, 6 |
| 13 decisions: licences, no environment branches, cassettes | 8, throughout |
| 15 licensing, cluster deploy is first-class, usage observable, telemetry off | 8.4; `docs/cluster-install.md` with a kind e2e (5.13); usage totals in the optimise report (3.4); telemetry documented as off |
| 16 prior art | README (7.1) |
| 17 "sample workspace is the integration test; every package ships tests" | 6.9, 12 |

---

## 3. M5 optimise (A8-opt)

### 3.1 Modules

| Path | Content |
|---|---|
| `packages/process/src/wynd/process/optimise.py` | Stats models, `collect_stats`, `Rules`/`RULES_V1`, `recommend`, `build_report`, `render_report_text`, the job input/result models |
| `packages/process/src/wynd/process/latency.py` | `latency_warnings(process, stats, rules) -> list[Diagnostic]` |
| `packages/controller/src/wynd/controller/optimise.py` | `optimise_report(...)`, `submit_optimise(...)`, the job handler `run_optimise_job(ctx)`, two API routes |
| `packages/cli/src/wynd/cli/optimise.py` | The typer command `wynd optimise` |

Placing the pure analysis in `process` keeps it next to the validator (which consumes the latency warnings) and outside any image. It needs only trace dicts and the resolved process.

### 3.2 Trace data consumed (runtime owner must emit these fields; see C3)

These are one JSON object per line from `TraceSink.read(run_id)`. Only the fields used are listed.

```jsonc
// step finished (every step kind, including shell/process)
{"type": "step.end", "run_id": "r_01J...", "step": "extract", "step_run": 1, "kind": "agentic",
 "exit": "done", "duration_ms": 2511.0,
 "attempts": 1, "validation_failures": 0, "error_cause": null,          // error_cause: "validation"|"exception"|"tool"|"hook"|"transport"|"timeout"|null
 "provider": "claude-code", "tier": "cheap", "model_id": "haiku",       // null for non-agentic
 "usage": {"calls": 1, "input_tokens": 912, "output_tokens": 88, "cost_usd": 0.0021, "latency_ms": 2430.0},  // null for non-agentic
 "replayed": false}
// one model call (inside a step or an edge check)
{"type": "model.call", "run_id": "r_01J...", "step": "extract", "provider": "claude-code", "tier": "cheap",
 "model_id": "haiku", "attempt": 1,
 "usage": {"input_tokens": 912, "output_tokens": 88, "cost_usd": 0.0021, "latency_ms": 2430.0}, "replayed": false}
// agentic edge verdict (A8-edge emits, 4.7)
{"type": "edge.check", "run_id": "...", "step": "validate", "edge": "validate.done", "branch": 0,
 "branch_key": "validate.done[save]", "target": "save", "take": false, "reason": "...",
 "attempts": 1, "validation_failures": 0, "error_cause": null, "provider": "claude-code", "tier": "cheap",
 "model_id": "haiku", "usage": {...}, "duration_ms": 2905.0, "replayed": false}
// run finished
{"type": "run.end", "run_id": "...", "exit": "done", "duration_ms": 9120.0}
```

For edges, `model.call.step` is `"edge:<branch_key>"`. Nested process steps use the dotted step path, for example `"child.validate"`.

### 3.3 Models and statistics

```python
# wynd/process/optimise.py
from typing import Any, Callable, Iterable, Literal, Mapping
from pydantic import BaseModel, Field

Tier = Literal["cheap", "standard", "strong"]
TIERS: tuple[Tier, ...] = ("cheap", "standard", "strong")
UnitKind = Literal["deterministic", "agentic", "shell", "process", "edge"]

class TierStats(BaseModel):
    provider: str
    tier: Tier
    n: int = 0                      # executions with >=1 live (non-replayed) model call
    fails: int = 0                  # executions ending error_cause == "validation"
    vfail_execs: int = 0            # executions with validation_failures >= 1
    attempts_sum: int = 0
    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    call_latency_ms: list[float] = Field(default_factory=list, exclude=True)
    exec_duration_ms: list[float] = Field(default_factory=list, exclude=True)
    # derived (computed_field): fail_rate, vfail_rate, mean_attempts, cost_per_exec_usd,
    #   call_p50_ms, call_p95_ms, exec_p50_ms, exec_p95_ms  (None when n == 0 / no samples)

class UnitStats(BaseModel):
    unit: str                        # "extract" | "edge:validate.done[save]" | "child.validate"
    kind: UnitKind
    executions: int = 0              # all executions incl. replayed and deterministic
    duration_ms: list[float] = Field(default_factory=list, exclude=True)
    by_tier: dict[str, TierStats] = {}   # key "<provider>/<tier>"

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

def collect_stats(process: str, run_ids: Iterable[str],
                  read_events: Callable[[str], Iterable[Mapping[str, Any]]]) -> ProcessStats: ...
```

The `collect_stats` algorithm is exact and single-pass per run:

```
stats = ProcessStats(process=process)
for run_id in run_ids:
    step_time = 0.0; transitions = 0; run_dur = None
    for ev in read_events(run_id):
        match ev["type"]:
            case "step.end":
                u = unit(ev["step"], ev["kind"]); u.executions += 1; u.duration_ms.append(ev["duration_ms"])
                step_time += ev["duration_ms"]; transitions += 1
                if ev.get("tier") and ev.get("usage") and not ev["replayed"]:
                    add_exec(u, ev)                               # see add_exec
            case "edge.check":
                u = unit("edge:" + ev["branch_key"], "edge"); u.executions += 1; u.duration_ms.append(ev["duration_ms"])
                step_time += ev["duration_ms"]
                if ev.get("usage") and not ev["replayed"]:
                    add_exec(u, ev)
            case "model.call":
                if not ev["replayed"]:
                    ts = tier_stats(unit_of_call(ev["step"]), ev["provider"], ev["tier"])
                    ts.call_latency_ms.append(ev["usage"]["latency_ms"])
            case "run.end":
                run_dur = ev["duration_ms"]
    if run_dur is not None:                                     # incomplete runs are skipped for run-level numbers
        stats.runs += 1; stats.run_duration_ms.append(run_dur)
        stats.dispatch_overhead_ms.append(max(0.0, run_dur - step_time) / max(1, transitions))

add_exec(u, ev):
    ts = tier_stats(u, ev["provider"], ev["tier"])
    ts.n += 1; ts.attempts_sum += ev["attempts"]
    ts.fails += ev.get("error_cause") == "validation"
    ts.vfail_execs += ev["validation_failures"] >= 1
    ts.calls += ev["usage"]["calls"]; ts.input_tokens += ev["usage"]["input_tokens"]
    ts.output_tokens += ev["usage"]["output_tokens"]; ts.cost_usd += ev["usage"].get("cost_usd") or 0.0
    ts.exec_duration_ms.append(ev["duration_ms"])
```

The unit kind for a step comes from `step.end.kind`. `unit_of_call("edge:...")` is an edge unit and anything else is a step unit. Replay runs still count toward `executions` and durations but never toward cost or tier evidence.

### 3.4 Rule set v1 (transparent, printed in every report)

```python
@dataclass(frozen=True)
class Rules:
    version: int = 1
    min_samples: int = 20            # R0 threshold (CLI --min-runs overrides)
    promote_fail_rate: float = 0.05  # P1
    promote_vfail_rate: float = 0.20 # P1
    demote_max_vfail_rate: float = 0.02  # D1
    demote_max_mean_attempts: float = 1.05  # D1
    hysteresis_min_samples: int = 5  # H1
    fast_call_p95_ms: float = 1500.0
    fast_run_p95_ms: float = 10000.0
    fast_dispatch_p95_ms: float = 50.0
    latency_min_samples: int = 5
RULES_V1 = Rules()
```

Rules are evaluated for each **agentic unit** (agentic step or edge) on the `TierStats` at its **current effective** `provider/tier`:
- For a step, the effective provider is `step.lock.provider or process.provider`.
- For an edge, it is `edges.lock[key].provider or process.provider`.

The first matching rule wins.

| Id | Condition | Action |
|---|---|---|
| **R0** | no stats at current provider/tier, or `n < min_samples` | `keep`: "only {n} live executions at {tier}; need {min}" |
| **P1** | `fail_rate >= 0.05` or `vfail_rate >= 0.20`, and tier ≠ strong | `promote` one tier |
| **P2** | same condition as P1, and tier = strong | `flag`: "failing at the strongest tier: improve examples/instruction or split the step" |
| **H1** | D1's condition holds, but the tier below (same provider) has `n >= 5` and would have triggered P1 | `keep`: "{lower} failed before: fail {x}%, vfail {y}% over {n}" |
| **D1** | tier ≠ cheap, `fails == 0`, `vfail_rate <= 0.02`, `mean_attempts <= 1.05` | `demote` one tier |
| **K1** | otherwise | `keep`: "within bounds" |

Deterministic, shell and process units get no recommendation, only duration statistics.

```python
class Recommendation(BaseModel):
    action: Literal["keep", "promote", "demote", "flag"]
    rule: Literal["R0", "P1", "P2", "H1", "D1", "K1"]
    from_tier: Tier | None = None
    to_tier: Tier | None = None
    reason: str
    applicable: bool                  # False for non-local units (Interpretation 7) or keep/flag
    not_applicable_reason: str | None = None

def recommend(unit: UnitStats, provider: str, tier: Tier, rules: Rules) -> Recommendation: ...
```

### 3.5 Data-driven latency warnings (`wynd/process/latency.py`)

```python
def latency_warnings(process: "ResolvedProcess", stats: ProcessStats, rules: Rules = RULES_V1) -> list["Diagnostic"]:
    """Only when process.latency == "fast". Pure; called by the validator when stats are available."""
```

| Code | Condition | Message template |
|---|---|---|
| `W-LATENCY-CALL` | agentic unit with ≥ 5 live calls at its current provider/tier, and p95 call latency > 1500 ms | `"{unit}: p95 model-call latency {p95:.1f}s over {n} live calls ({provider}/{tier}) exceeds the latency: fast budget of 1.5s; consider a lower tier or a ModelProvider"` |
| `W-LATENCY-RUN` | ≥ 5 completed runs, and p95 run duration > 10 s | `"p95 run duration {p95:.1f}s over {n} runs exceeds 10s"` |
| `W-LATENCY-DISPATCH` | ≥ 5 completed runs, and p95 per-transition overhead > 50 ms | `"p95 executor overhead {p95:.0f}ms per transition exceeds 50ms (step-to-step latency should be milliseconds)"` |

The static warnings (an AgentProvider step in a fast process, spec 3.9, owned by the process validator; and `W-AGENTIC-EDGE-FAST`, 4.4) stay as they are. When data exists, the message is enriched with the measured p95. `wynd validate` passes `stats` (computed by the controller from the configured TraceSink) whenever any runs exist.

### 3.6 Report (JSON contract, returned by the library and the API, printed by `--json`)

```python
class UnitReport(BaseModel):
    unit: str
    kind: UnitKind
    package: str | None               # "./steps/extract_invoice_fields" | "shared:extract" | None for edges
    lock_path: str | None             # repo-relative path of step.lock.yaml / edges.lock.yaml
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
    commit: str                       # process HEAD (closure) at report time
    generated_at: str                 # ISO-8601 UTC
    window: dict[str, Any]            # {"max_runs": 200, "runs_considered": 87, "live_calls": 312}
    rules: dict[str, Any]             # dataclasses.asdict(rules)
    units: list[UnitReport]           # process step order, then edges in edge order
    warnings: list[dict[str, str]]    # latency Diagnostics as {"code","unit","message"}
    totals: list[dict[str, Any]]      # per provider/tier: {"provider","tier","calls","input_tokens","output_tokens","cost_usd"}
    changes: list["TierChange"]       # applicable promote/demote recommendations, ready for --apply

class TierChange(BaseModel):
    unit: str                         # "extract" | "edge:validate.done[save]"
    lock_path: str                    # repo-relative
    lock_key: str | None              # None for step.lock.yaml; branch_key for edges.lock.yaml
    from_tier: Tier
    to_tier: Tier
    rule: Literal["P1", "D1"]
    evidence: dict[str, float]        # {"n":60,"fail_rate":0.0,"vfail_rate":0.0,"mean_attempts":1.0,"cost_per_exec_usd":0.0031}

def build_report(process: "ResolvedProcess", edge_lock: "EdgeLock", stats: ProcessStats, *,
                 commit: str, rules: Rules = RULES_V1, now: datetime | None = None) -> OptimiseReport: ...
def render_report_text(report: OptimiseReport) -> str: ...
```

Example (abridged):

```json
{
  "v": 1, "process": "process_supplier_invoice", "commit": "3f2a9c1d0e5b...", "generated_at": "2026-10-02T09:00:00Z",
  "window": {"max_runs": 200, "runs_considered": 87, "live_calls": 312},
  "rules": {"version": 1, "min_samples": 20, "promote_fail_rate": 0.05, "promote_vfail_rate": 0.2, "demote_max_vfail_rate": 0.02, "demote_max_mean_attempts": 1.05, "hysteresis_min_samples": 5, "fast_call_p95_ms": 1500.0, "fast_run_p95_ms": 10000.0, "fast_dispatch_p95_ms": 50.0, "latency_min_samples": 5},
  "units": [
    {"unit": "extract", "kind": "agentic", "package": "./steps/extract_invoice_fields",
     "lock_path": "examples/invoices/processes/process_supplier_invoice/steps/extract_invoice_fields/step.lock.yaml",
     "provider": "claude-code", "tier": "standard",
     "current": {"provider": "claude-code", "tier": "standard", "n": 60, "fails": 0, "vfail_execs": 0, "attempts_sum": 60, "calls": 60, "input_tokens": 54720, "output_tokens": 5280, "cost_usd": 0.186, "fail_rate": 0.0, "vfail_rate": 0.0, "mean_attempts": 1.0, "cost_per_exec_usd": 0.0031, "call_p50_ms": 1830.0, "call_p95_ms": 2410.0, "exec_p50_ms": 1900.0, "exec_p95_ms": 2500.0},
     "by_tier": {"claude-code/standard": {"...": "..."}},
     "duration_p50_ms": 1900.0, "duration_p95_ms": 2500.0,
     "recommendation": {"action": "demote", "rule": "D1", "from_tier": "standard", "to_tier": "cheap", "reason": "0 failures, 0.0% validation failures, 1.00 attempts over 60 executions at standard", "applicable": true, "not_applicable_reason": null}},
    {"unit": "read", "kind": "deterministic", "package": "./steps/read_pdf", "lock_path": "...", "provider": null, "tier": null, "current": null, "by_tier": {}, "duration_p50_ms": 11.0, "duration_p95_ms": 19.0, "recommendation": null}
  ],
  "warnings": [],
  "totals": [{"provider": "claude-code", "tier": "cheap", "calls": 252, "input_tokens": 201000, "output_tokens": 9800, "cost_usd": 0.41}],
  "changes": [{"unit": "extract", "lock_path": "examples/invoices/processes/process_supplier_invoice/steps/extract_invoice_fields/step.lock.yaml", "lock_key": null, "from_tier": "standard", "to_tier": "cheap", "rule": "D1", "evidence": {"n": 60, "fail_rate": 0.0, "vfail_rate": 0.0, "mean_attempts": 1.0, "cost_per_exec_usd": 0.0031}}]
}
```

On a Claude subscription, `cost_usd` is Claude Code's API-equivalent estimate (`total_cost_usd`). The text report footnotes this.

The text rendering is fixed-width and deterministic, so it can be golden-tested:

```
optimise process_supplier_invoice @ 3f2a9c1  (87 runs, 312 live calls; rules v1, min 20)
UNIT                       KIND     PROVIDER     TIER      N    FAIL   VFAIL  ATT   COST/EXEC  P95 CALL  ACTION
extract                    agentic  claude-code  standard  60   0.0%   0.0%   1.00  $0.0031    2.4s      demote -> cheap (D1)
fix                        agentic  claude-code  cheap     14   -      -      -     $0.0012    3.1s      keep (R0: 14 < 20)
edge:validate.done[save]   edge     claude-code  cheap     41   0.0%   2.4%   1.02  $0.0009    2.9s      keep (K1)
read                       determ.  -            -         87   -      -      -     -          -         -
warnings: none
totals: claude-code/cheap 252 calls, 201.0k in / 9.8k out tokens, $0.41 (API-equivalent)
1 applicable change. Run `wynd optimise process_supplier_invoice --apply` to submit an optimise job.
```

### 3.7 `--apply`: the `optimise` job

`optimise` is a new `JobKind` (C12), handled by the same `JobRunner`s as compile, build and test_live. Its output is a commit on the branch `wynd/optimise/<process>/<job-id>`, which the CLI integrates exactly like a compile branch (fast-forward, auto-rebase outside the closure, or left for review).

Job inputs (`JobRecord.inputs`):

```json
{"process": "process_supplier_invoice",
 "report_commit": "3f2a9c1d0e5b...",
 "changes": [ {"unit": "extract", "lock_path": "examples/invoices/processes/process_supplier_invoice/steps/extract_invoice_fields/step.lock.yaml", "lock_key": null, "from_tier": "standard", "to_tier": "cheap", "rule": "D1", "evidence": {"n": 60, "fail_rate": 0.0, "vfail_rate": 0.0, "mean_attempts": 1.0}} ]}
```

Job result, written as artefact `optimise-result.json` and summarised in `JobRecord.result`:

```json
{"branch": "wynd/optimise/process_supplier_invoice/01j9zq3k7m", "commit": "9b1e...",
 "applied":  [{"unit": "extract", "from_tier": "standard", "to_tier": "cheap", "tests": {"passed": 5, "failed": 0}}],
 "rejected": [{"unit": "fix", "to_tier": "standard", "reason": "tests failed live at standard: 1/3 (test_example_3)"}],
 "process_tests": {"passed": 6, "failed": 0},
 "usage": {"calls": 31, "input_tokens": 40210, "output_tokens": 2100, "cost_usd": 0.12}}
```

`branch` and `commit` are `null` when nothing was applied. The job still succeeds in that case.

```python
# wynd/process/optimise.py
class OptimiseJobInput(BaseModel):
    process: str
    report_commit: str
    changes: list[TierChange]

# wynd/controller/optimise.py
def optimise_report(ctl: "Controller", process_id: str, *, max_runs: int = 200, min_runs: int = 20) -> OptimiseReport: ...
def submit_optimise(ctl: "Controller", process_id: str, *, units: list[str] | None = None,
                    max_runs: int = 200, min_runs: int = 20) -> tuple[str, OptimiseReport]:   # (job_id, report)
def run_optimise_job(ctx: "JobContext") -> "JobResult": ...     # registered as the handler for kind "optimise"
```

`run_optimise_job` algorithm. It is exact, and the checkout is a clean git worktree/clone at `ref`:

```
inp = OptimiseJobInput.model_validate(ctx.inputs)
applied, rejected, edge_changes = [], [], []
for ch in inp.changes:
    path = ctx.checkout / ch.lock_path
    if ch.lock_key is None:                                   # step
        lock = load_step_lock(path)
        if lock.tier != ch.from_tier:
            rejected.append(reason=f"lock is now {lock.tier}, report assumed {ch.from_tier}"); continue
        path.write_text(dump_step_lock(lock.model_copy(update={"tier": ch.to_tier})))
        res = ctx.run_tests(step_dirs=[path.parent], live=True)     # records, promotes (replacing) cassettes, then replays
        if res.failed:
            ctx.git("restore", "--source=HEAD", "--staged", "--worktree", "--", str(path.parent))
            rejected.append(reason=f"tests failed live at {ch.to_tier}: {res.failed}/{res.total} ({', '.join(res.failed_ids[:3])})")
        else:
            applied.append(...)
    else:                                                     # agentic edge
        el = load_edge_lock(path); entry = el.edges.get(ch.lock_key)
        if entry is None or entry.tier != ch.from_tier: rejected.append(...); continue
        el.edges[ch.lock_key] = entry.model_copy(update={"tier": ch.to_tier})
        path.write_text(dump_edge_lock(el)); edge_changes.append(ch); applied.append(...)
if not applied:
    return JobResult(status="succeeded", result={..., "branch": None, "commit": None})
pres = ctx.run_tests(process=inp.process, process_level=True, live=True)   # re-record process-level cassettes
if pres.failed:
    return JobResult(status="failed", message="process-level tests failed after tier changes; nothing pushed", result={...})
ctx.git("switch", "-c", branch := f"wynd/optimise/{inp.process}/{ctx.job_id}")
ctx.git("add", "-A", "--", process_dir_rel)                  # every edited file is under processes/<id>/ (step locks, cassettes, edges.lock.yaml, tests/cassettes)
ctx.git("commit", "-m", commit_message(inp, applied, rejected))
commit = ctx.git_rev("HEAD"); ctx.push_branch(branch)
ctx.record_test_results(commit)                                  # replay pass on the new commit -> RunRegistry (6.5 build gate)
return JobResult(status="succeeded", result={..., "branch": branch, "commit": commit})
```

Commit message:

```
wynd optimise: process_supplier_invoice

extract: standard -> cheap (D1: 0 failures, 0.0% validation failures, 1.00 attempts over 60 executions)
rejected fix: cheap -> standard (tests failed live at standard: 1/3)

Wynd-Job: 01j9zq3k7m
Wynd-Report-Commit: 3f2a9c1d0e5b
```

### 3.8 CLI and API

```
wynd optimise <id> [--max-runs 200] [--min-runs 20] [--json] [--apply] [--unit NAME ...] [--no-wait]
```

- Without `--apply`, it prints the report (text, or JSON with `--json`) and exits 0. There is no LLM call and no job.
- With `--apply`, it calls `submit_optimise`. `--unit` filters `report.changes` by unit name, and zero remaining changes means exit 0 with "nothing to apply". It then polls the job like `wynd compile` does, prints the result, and integrates the branch with the shared integration function (C15). The exit code is 1 when the job fails.
- `--no-wait` prints the job id and exits.

The two controller HTTP routes live in the same module and are mounted by the controller app:
- `GET /api/processes/{process_id:path}/optimise?max_runs=&min_runs=` returns `OptimiseReport`.
- `POST /api/processes/{process_id:path}/optimise` takes `{"units": [...]|null, "max_runs": 200, "min_runs": 20}` and returns `{"job_id": "...", "changes": [...]}`.

### 3.9 Tests (A8-opt)

- `packages/process/tests/optimise/fixtures/*.jsonl`: hand-written synthetic traces.
  - `demote.jsonl`: 25 clean live runs at `standard`.
  - `promote.jsonl`: 20 runs with 5 validation-failure runs.
  - `hysteresis.jsonl`: clean at `standard` plus 6 failing executions at `cheap`.
  - `replay_only.jsonl`: all `replayed: true`, which must give R0.
  - `fast.jsonl`: slow calls, slow runs and slow dispatch.
  - `edge.jsonl`: `edge.check` events.
- `test_collect_stats.py`: counts, rates, percentiles (nearest-rank edge cases: 1 element, 20 elements), replay exclusion, incomplete-run skipping, edge unit keys, and nested `child.x` step paths.
- `test_recommend.py`: one test per rule id plus a boundary test at each threshold (for example vfail exactly 0.20 promotes and 0.02 still demotes).
- `test_report.py`: golden JSON and golden text for the `demote` fixture; non-local units come out `applicable: false`.
- `test_latency.py`: each warning code fires only with `latency: fast` and above `latency_min_samples`.
- `packages/controller/tests/test_optimise_job.py`, which works on a temporary git repo containing a copy of the sample's `extract_invoice_fields` package and a fake `JobContext.run_tests`:
  - apply succeeds: the lock is edited canonically, the branch is created, and the commit message matches.
  - step tests fail: the change is rejected and the lock is restored.
  - process tests fail: the job fails and no branch is created.
  - lock drift: the change is rejected.
  - nothing applied: `branch` is null.
- `packages/cli/tests/test_optimise_cmd.py`: typer `CliRunner` with a fake controller covering the text output, `--json`, `--apply --unit` filtering and the exit codes.
- Live (`@pytest.mark.live`) in `examples/invoices/tests/test_optimise_live.py`: seed the TraceSink with 20 live runs over the sample examples at `standard` for `extract`, run `--apply`, and assert the branch exists with `tier: cheap` and fresh cassettes. It is expensive, so it runs only on manual invocation.

---

## 4. M5 agentic edges (A8-edge)

### 4.1 Schema (spec package; the spec owner adds the fields and A8-edge owns `edges_lock.py`)

Process YAML:

```yaml
- from: validate.done
  kind: agentic                     # "deterministic" (default) | "agentic"
  to:
    - step: save
      name: save                    # optional; recommended: keys the lock entry and the counter
      when: steps.validate.outputs.valid            # optional deterministic pre-condition (evaluated first; no LLM call if false)
      check: >-                                     # NEW: natural-language condition, judged by a provider
        The document is a final supplier invoice that requests payment:
        not a pro-forma invoice, quote, estimate or statement.
      context: [steps.read.outputs]                 # NEW: optional pull context (3.7 grammar); default [previous.outputs]
      with: { ... }                                 # deterministic, unchanged
      limits: { ... }                               # unchanged
    - step: escalate                                # else: no when, no check
```

Shorthand: with `to: <step>`, `check:` and `context:` sit on the edge itself, next to `with:` and `limits:`. The loader normalises them into the single branch.

```python
# additions to wynd.spec process models (spec owner)
class Branch(BaseModel):
    ...                                   # step, name, when, with_, limits (existing)
    check: str | None = None              # NEW (M5)
    context: list[str] | None = None      # NEW (M5)

class Edge(BaseModel):
    ...
    kind: Literal["deterministic", "agentic"] = "deterministic"   # already in schema per 3.4

def is_else(branch: Branch) -> bool:      # replaces "no when" everywhere the else rule is applied
    return branch.when is None and branch.check is None
```

`packages/spec/src/wynd/spec/edges_lock.py` (A8-edge):

```python
Tier = Literal["cheap", "standard", "strong"]
Thinking = Literal["none", "low", "medium", "high"]

class EdgeLockEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    check_hash: str                   # "sha256:<hex>" of the canonical check (see check_hash)
    provider: str | None = None       # None -> process default provider
    tier: Tier = "cheap"
    thinking: Thinking = "low"
    retries: int = 2                  # validation retries, continuing the conversation (3.5)
    timeout_s: int = 120

class EdgeLock(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    edges: dict[str, EdgeLockEntry] = {}

EDGE_LOCK_FILE = "edges.lock.yaml"
DEFAULT_ENTRY = dict(provider=None, tier="cheap", thinking="low", retries=2, timeout_s=120)

def branch_key(edge_from: str, index: int, name: str | None) -> str:
    """'validate.done[save]' if the branch is named, else 'validate.done[0]'."""
    return f"{edge_from}[{name if name else index}]"

def check_hash(check: str, context: list[str] | None) -> str:
    payload = json.dumps({"check": " ".join(check.split()), "context": context or ["previous.outputs"]},
                         sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

def load_edge_lock(path: Path) -> EdgeLock: ...     # missing file -> EdgeLock()
def dump_edge_lock(lock: EdgeLock) -> str: ...      # yaml.safe_dump(lock.model_dump(), sort_keys=True), keys sorted
```

`processes/<id>/edges.lock.yaml` is committed and hand-editable:

```yaml
version: 1
edges:
  validate.done[save]:
    check_hash: sha256:5c1f...e2
    provider: null
    retries: 2
    thinking: low
    tier: cheap
    timeout_s: 120
```

`packages/process/src/wynd/process/edges_lock.py` (A8-edge):

```python
def sync_edge_lock(process_dir: Path, process: "Process") -> tuple[EdgeLock, bool]:
    """Add entries (defaults) for new agentic branches, refresh check_hash for edited checks,
    drop entries whose branch no longer exists. Preserves provider/tier/thinking/retries/timeout
    of surviving entries. Returns (lock, changed). Writes nothing."""
```

This is called by the **compile job**, which writes the file when `changed`, re-records process-level cassettes and includes both in its commit (C14). `wynd validate` never writes it.

### 4.2 Execution semantics (executor, runtime owner's call site)

The branch resolution loop in the executor, extended:

```python
for i, br in enumerate(edge.to):
    if br.when is not None and not truthy(evaluate(br.when, scope)):
        continue                                      # cheap short-circuit, no LLM call
    bindings = evaluate_mapping(br.with_, scope)
    if br.check is not None:                          # only valid when edge.kind == "agentic" (validator)
        verdict = self.edge_checker.check(EdgeCheckCall.for_branch(run, edge, i, br, bindings, scope))
        if not verdict.take:
            continue                                  # negative verdict: not taken, fall through
    return Resolution(branch_index=i, target=br.step, bindings=bindings)
return route_to_error_handler(cause="no_branch_matched")
```

- **Verifier failure.** An `EdgeCheckError` with cause `validation`, `transport` or `timeout` routes to the process error handler. The `ProcessError` has `step=<source step>`, `cause="edge_check"`, and `detail={"edge": "validate.done", "branch": 0, "branch_key": "...", "error_cause": "validation"}` (C6). The source step's outputs are preserved as partial outputs.
- **Counters.** `edges["validate.done"][i].taken` and `.save.taken` increment only for the taken branch, as for deterministic edges. The verdict itself is not addressable in expressions; the escalation ticket carries `run.id`, so a human can inspect it with `wynd trace`.
- **Traversal limits.** `max_traversals` auto-fill applies unchanged to agentic branches on cycles. A check is evaluated **after** the traversal-limit check for that branch, so an exhausted branch never costs a model call.
- **Timeout.** `timeout_s` in the lock entry bounds the whole check, including retries. Branch `limits.timeout` keeps its existing meaning.

### 4.3 Validator rules (`packages/process/src/wynd/process/validate_agentic.py`, A8-edge)

```python
def check_agentic_edges(process: "ResolvedProcess", edge_lock: EdgeLock) -> list["Diagnostic"]: ...
```

The process validator calls this once. `edge_lock` is `load_edge_lock(process_dir / EDGE_LOCK_FILE)`.

| Code | Level | Rule |
|---|---|---|
| `E-CHECK-NOT-AGENTIC` | error | a branch has `check:` or `context:` but its edge's `kind` is not `agentic` |
| `E-AGENTIC-NO-CHECK` | error | an agentic edge has no branch with `check:` |
| `E-CHECK-EMPTY` | error | `check` is blank, or longer than 2000 characters |
| `E-CHECK-CONTEXT` | error | a `context` entry is not one of `process.goal`, `previous.summary`, `previous.outputs`, `steps.<name>.outputs`, `steps.<name>.summary` or `full_trace`, or names an unknown step, or names a step that has not completed on every path reaching this edge (exit-aware, reusing the loader's path analysis) |
| `W-AGENTIC-NO-ELSE` | warning | an agentic edge has no else branch: "a negative verdict routes to the process error handler" |
| `W-AGENTIC-EDGE-FAST` | warning | the process declares `latency: fast` and has an agentic edge (enriched with measured p95 when stats exist, 3.5) |
| `W-EDGE-LOCK-MISSING` | warning | an agentic branch has no entry in `edges.lock.yaml`: "defaults used at run time (cheap, low, 2 retries); run `wynd compile` to lock" |
| `W-EDGE-LOCK-STALE` | warning | the entry's `check_hash` ≠ `check_hash(check, context)`: "check edited since it was locked; process cassettes will miss; re-run `wynd compile` or `wynd test --live`" |
| `W-EDGE-LOCK-ORPHAN` | warning | a lock entry has no matching agentic branch |
| `W-EDGE-PROVIDER-UNKNOWN` | warning | the effective provider is not a registered `wynd.providers` entry point |

The existing else and "ignored after else" rules must use `is_else()` (4.1). A branch with only `check:` is conditional.

Derived status (C15): a stale or missing edge lock counts as **design** (needs compile), the same as a proto-hash mismatch.

### 4.4 Runtime seam (`packages/runtime/src/wynd/runtime/edges.py`, A8-edge)

```python
class EdgeVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    take: bool
    reason: str = Field(max_length=500)

EDGE_VERDICT_SCHEMA: dict = EdgeVerdict.model_json_schema()   # {"take": bool, "reason": str}, both required

class EdgeCheckCall(BaseModel):          # executor -> worker payload (JSON-serialisable)
    run_id: str
    process: str
    process_goal: str | None
    edge: str                            # "validate.done"
    branch: int
    branch_key: str                      # "validate.done[save]"
    source_step: str                     # "validate"
    target: str                          # "save" | "$exit.done"
    check: str
    context: dict[str, Any]              # assembled by the executor with the AgenticStep context assembler (C4)
    bindings: dict[str, Any]             # the branch's evaluated with:
    lock: EdgeLockEntry                  # resolved (defaults if missing)
    provider: str                        # effective provider name

    @classmethod
    def for_branch(cls, run: "RunState", edge: "Edge", index: int, branch: "Branch",
                   bindings: dict[str, Any], scope: "Scope") -> "EdgeCheckCall":
        """Executor-side constructor: resolves the lock entry (defaults if missing), the effective provider,
        and assembles context from branch.context or [\"previous.outputs\"] via the AgenticStep assembler (C4)."""

class EdgeCheckResult(BaseModel):
    verdict: EdgeVerdict
    attempts: int
    validation_failures: int
    provider: str
    tier: str
    model_id: str
    usage: dict[str, float]              # {"calls","input_tokens","output_tokens","cost_usd","latency_ms"}
    replayed: bool
    duration_ms: float

class EdgeCheckError(Exception):
    def __init__(self, cause: Literal["validation", "transport", "timeout"], message: str): ...

VERIFIER_INSTRUCTION = """\
You are the transition verifier for one edge of an automated process.
Decide whether the process should take the transition described in the input.

Check: {check}

Take the transition (take = true) only if the check is clearly satisfied by the data
provided. If the data is missing, ambiguous or contradicts the check, do not take it
(take = false). Give a one-sentence reason that cites the data.
"""

def run_edge_check(call: EdgeCheckCall, ctx: "WorkerCallContext") -> EdgeCheckResult:
    """Worker-side. Uses the SAME structured-completion path AgenticStep uses (C4):
    provider resolution, cassette record/replay wrapping, validation retries that continue the
    conversation, usage + model.call trace events (step = "edge:" + branch_key). No tools, no MCP."""
    return complete_structured(
        provider=call.provider, tier=call.lock.tier, thinking=call.lock.thinking, retries=call.lock.retries,
        instruction=VERIFIER_INSTRUCTION.format(check=call.check),
        context=call.context,
        input={"process_goal": call.process_goal, "transition": {"from": call.edge, "to": call.target},
               "bindings": call.bindings},
        output_model=EdgeVerdict, tools=[], mcp=[], unit=f"edge:{call.branch_key}",
        timeout_s=call.lock.timeout_s, ctx=ctx)   # adapt result -> EdgeCheckResult

class EdgeChecker(Protocol):                       # executor-side
    def check(self, call: EdgeCheckCall) -> EdgeVerdict: ...

class WorkerEdgeChecker:
    """Dispatches edge.check to the venv worker assigned by the build/venv plan, emits the
    edge.check trace event, raises EdgeCheckError on worker error."""
    def __init__(self, pool: "WorkerPool", plan: "VenvPlan", trace: "TraceWriter"): ...
    def check(self, call: EdgeCheckCall) -> EdgeVerdict: ...
```

- **ModelProvider** (`anthropic`): single-turn `generate` calls with `output_schema=EDGE_VERDICT_SCHEMA`. On validation failure, the error is appended as a message and the schema re-requested, up to `retries`.
- **AgentProvider** (`claude-code`): `run(AgentRequest(instruction=..., context=..., input=..., output_schema=EDGE_VERDICT_SCHEMA, tools=[], mcp=[], workspace=<run workspace>, model_id=...))`, with runtime validation and the retry policy of spec 3.9. The claude-code provider must pass an empty tool allow-list when `tools=[]`.

### 4.5 Worker RPC method (runtime owner registers; A8-edge implements the handler)

The request carries the same `ctx` object as the step-run method (run id, workspace, cassette mode/dir, trace destination):

```json
{"jsonrpc": "2.0", "id": 17, "method": "edge.check",
 "params": {"ctx": {"...": "same object the step-run method carries"},
            "call": {"run_id": "r_01J...", "process": "process_supplier_invoice", "process_goal": "Turn a supplier invoice PDF into ...",
                     "edge": "validate.done", "branch": 0, "branch_key": "validate.done[save]", "source_step": "validate",
                     "target": "save", "check": "The document is a final supplier invoice ...",
                     "context": {"steps.read.outputs": {"text": "Umbrella Corporation\n...", "pages": 1}},
                     "bindings": {"record": {"key": "umbrella-corporation__PF-88", "...": "..."}, "dest": "/tmp/.../records"},
                     "lock": {"check_hash": "sha256:...", "provider": null, "tier": "cheap", "thinking": "low", "retries": 2, "timeout_s": 120},
                     "provider": "claude-code"}}}
```

Success:

```json
{"jsonrpc": "2.0", "id": 17, "result": {"verdict": {"take": false, "reason": "The document states it is a pro forma and not a request for payment."},
  "attempts": 1, "validation_failures": 0, "provider": "claude-code", "tier": "cheap", "model_id": "haiku",
  "usage": {"calls": 1, "input_tokens": 812, "output_tokens": 41, "cost_usd": 0.0011, "latency_ms": 2870.0},
  "replayed": true, "duration_ms": 2905.0}}
```

Failure:

```json
{"jsonrpc": "2.0", "id": 17, "error": {"code": -32001, "message": "structured output failed validation after 2 retries", "data": {"cause": "validation"}}}
```

The handler is `wynd.runtime.edges.handle_edge_check(params: dict, ctx: WorkerCallContext) -> dict`.

### 4.6 Venv assignment for edges (builder/venv-plan owner implements this rule)

For each agentic branch `E` with effective provider `P`:
- If a venv in the plan already contains `P`'s env fragment, assign `E` to the **first such venv in plan order**.
- Otherwise add a venv whose dependency set is exactly `P`'s fragment.

The plan records `edge_venvs: {"validate.done[save]": "venv_1"}` (C12). Local mode and image mode use the same plan, so there is no environment branch.

### 4.7 Trace, cassettes, cost

- **Trace.** Every check emits one `edge.check` event (shape in 3.2), with `take`/`reason` or `error_cause`. The `model.call` events carry `step: "edge:<branch_key>"`. The existing `edge.taken` event (runtime owner) gains `verdicts: [{"branch": 0, "take": false, "reason": "..."}]` for agentic edges.
- **Cassettes.** The calls pass through the same provider-boundary wrapper, so they are recorded into, and replayed from, whatever cassette context the run uses. For the sample, that is the process-level cassettes at `processes/<id>/tests/cassettes/`. The key hashes the full request, which includes the check text through the instruction. An edited check therefore misses loudly ("no recording for this request — re-record with `wynd test --live`").
- **Cost.** `edge.check.usage` is included in the run's usage totals (runtime's run.end/RunRecord totals sum all `usage`), in `wynd trace` output, and in optimise units `edge:<key>`. Optimise's P1/D1 rules apply to edges too, editing `edges.lock.yaml` (3.7).
- **`wynd trace`** renders `edge.check` as `validate.done → save  check: NOT TAKEN — "..." (claude-code/cheap, 2.9s, $0.0011)`. This line is owned by the CLI trace printer (C16), with the format given here.

### 4.8 Tests (A8-edge)

- `packages/spec/tests/test_edges_lock.py`: `branch_key`; `check_hash` is stable under whitespace changes and changes with the context; load and dump round-trip; unknown keys are rejected.
- `packages/spec/tests/test_agentic_schema.py`: `check`/`context` parse on branches and in shorthand; `is_else`.
- `packages/process/tests/test_validate_agentic.py`: one positive and one negative fixture per code in 4.3.
- `packages/process/tests/test_edges_lock_sync.py`: sync adds, refreshes and drops entries, preserving tier edits.
- `packages/runtime/tests/test_edges.py`:
  - with a `FakeModelProvider`: take, not take, invalid JSON then valid (the retry continues the conversation: the second request contains the first reply and the error), retries exhausted raises `EdgeCheckError("validation")`, transport error raises `("transport")`, and timeout.
  - with a `FakeAgentProvider`, the same cases.
  - cassettes: record, then replay gives an identical verdict; an edited check misses in replay with the explicit error.
  - usage appears in the result, and `model.call` events carry the `edge:` unit.
- `packages/runtime/tests/test_executor_agentic.py` (fake `EdgeChecker`):
  - a false `when` makes no checker call.
  - the first true verdict wins; a negative verdict falls through to the else.
  - no else plus a negative verdict routes to the error handler with `cause="no_branch_matched"`.
  - a checker error routes to the error handler with `cause="edge_check"`.
  - counters increment only for the taken branch; `edge.check` events are emitted.
- Sample integration: the process example `umbrella_proforma_88.pdf` has exit `needs_review` (6.9).

---

## 5. `wynd.kube` (A8-kube): the Kubernetes JobRunner, triggers and serving

`wynd-kube` is AGPL-3.0-or-later and lives at `packages/kube`.
- **Dependencies:** `wynd-controller` (for `JobRecord`, `Release`, `RunRegistry`, `EnvManifest` and the backend protocols), `httpx`, `pyyaml` and `typer`, all already approved in the workspace.
- **Console script:** `wynd-kube = "wynd.kube.cli:app"`.
- **Entry points** (C15), so the controller never imports `wynd.kube`:

```toml
[project.entry-points."wynd.job_runners"]
kube = "wynd.kube.runner:KubeJobRunner.from_env"
[project.entry-points."wynd.trigger_backends"]
kube = "wynd.kube.triggers:KubeTriggerBackend.from_env"
[project.entry-points."wynd.serving_backends"]
kube = "wynd.kube.serving:KubeServingBackend.from_env"
```

The controller selects a backend by env var: `WYND_JOB_RUNNER=kube`, `WYND_TRIGGER_BACKEND=kube`, `WYND_SERVING_BACKEND=kube`. The factories share the signature `from_env(env: Mapping[str, str], registry: RunRegistry) -> Self`.

### 5.1 Package layout

```
packages/kube/
  pyproject.toml   LICENSE (AGPL-3.0-or-later)   README.md
  src/wynd/kube/
    __init__.py          # empty (wynd/ itself has no __init__.py)
    config.py            # KubeConfig.from_env, InstallParams
    client.py            # KubeClient protocol, RestKubeClient, KubeApiError, RESOURCES
    names.py             # k8s_name, label_value, job_name, cronjob_name, release_name, secret_name_for
    envmap.py            # EnvManifest -> container env / ConfigMap data / Secret template
    manifests.py         # render_job, render_cronjob, render_process_release, render_env_template, render_install, to_yaml
    runner.py            # KubeJobRunner
    triggers.py          # KubeTriggerBackend
    serving.py           # KubeServingBackend
    checkout.py          # checkout(repo, dest, ref=|branch=, if_missing=)
    fire.py              # fire(release_id, controller_url, token) (used by CronJobs)
    cli.py               # typer app: render install|release, env-template, checkout, fire
    testing.py           # FakeKubeClient
  tests/
    golden/{job_compile,job_build,job_test_live,job_optimise,cronjob,release,env_template,install}.yaml
    test_names.py test_envmap.py test_manifests.py test_client.py test_runner.py
    test_triggers.py test_serving.py test_checkout.py test_fire.py test_cli.py test_e2e_kind.py
deploy/
  wynd.Dockerfile                     # toolchain + controller image (A8-kube)
  wynd.Dockerfile.dockerignore
docs/cluster-install.md               # (A8-kube writes; A8-docs links)
```

### 5.2 Configuration

```python
@dataclass(frozen=True)
class KubeConfig:
    namespace: str                         # WYND_KUBE_NAMESPACE (required)
    toolchain_image: str                   # WYND_KUBE_TOOLCHAIN_IMAGE (required) - the wynd image (5.12)
    git_url: str                           # WYND_KUBE_GIT_URL (required) - https clone URL of the workspace repo
    workspace_subdir: str = "."            # WYND_KUBE_WORKSPACE_SUBDIR
    buildkit_image: str = "moby/buildkit:v0.27.1-rootless"   # WYND_KUBE_BUILDKIT_IMAGE
    state_pvc: str = "wynd-state"          # WYND_KUBE_STATE_PVC
    state_dir: str = "/var/lib/wynd"       # WYND_STATE_DIR (shared with controller storage config)
    config_map: str = "wynd-config"        # WYND_KUBE_CONFIGMAP
    git_secret: str = "wynd-git"           # WYND_KUBE_GIT_SECRET      (key: .git-credentials)
    model_secret: str = "wynd-model-credentials"  # WYND_KUBE_MODEL_SECRET (CLAUDE_CODE_OAUTH_TOKEN / ANTHROPIC_API_KEY)
    registry_secret: str = "wynd-registry" # WYND_KUBE_REGISTRY_SECRET (type kubernetes.io/dockerconfigjson)
    api_token_secret: str = "wynd-api-token"      # WYND_KUBE_API_TOKEN_SECRET (key: WYND_API_TOKEN)
    registry_insecure: bool = False        # WYND_KUBE_REGISTRY_INSECURE=1 (kind/local registries only)
    job_ttl_s: int = 86400                 # WYND_KUBE_JOB_TTL_S
    job_deadline_s: int = 3600             # WYND_KUBE_JOB_DEADLINE_S
    controller_url: str = "http://wynd-controller:8000"   # WYND_KUBE_CONTROLLER_URL
    serving_port: int = 8080

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "KubeConfig": ...   # missing required -> ValueError naming the var

@dataclass(frozen=True)
class InstallParams:
    namespace: str = "wynd"
    image: str = "ghcr.io/OWNER/wynd:0.1.0"
    git_url: str = "https://github.com/OWNER/REPO.git"
    git_branch: str = "main"
    workspace_subdir: str = "."
    storage_class: str | None = None        # None -> cluster default
    storage_size: str = "20Gi"
    storage_access_mode: Literal["ReadWriteMany", "ReadWriteOnce"] = "ReadWriteMany"
    buildkit_image: str = "moby/buildkit:v0.27.1-rootless"
    create_namespace: bool = True
    registry_insecure: bool = False
```

`v0.27.1` is the BuildKit version Docker Desktop reports on this machine. The implementer pins the latest `v0.x.y-rootless` tag at implementation time and updates the goldens.

### 5.3 Client seam

```python
class KubeApiError(Exception):
    def __init__(self, status: int, reason: str, message: str): ...

class KubeClient(Protocol):                    # namespace bound at construction
    def apply(self, obj: dict[str, Any]) -> dict[str, Any]: ...          # server-side apply, fieldManager "wynd", force
    def get(self, kind: str, name: str) -> dict[str, Any] | None: ...    # None on 404
    def list(self, kind: str, selector: dict[str, str]) -> list[dict[str, Any]]: ...
    def delete(self, kind: str, name: str) -> None: ...                 # propagationPolicy Background; 404 ignored
    def logs(self, pod: str, container: str) -> str: ...

RESOURCES: dict[str, tuple[str, str]] = {      # kind -> (api prefix, plural)
    "Job": ("apis/batch/v1", "jobs"),
    "CronJob": ("apis/batch/v1", "cronjobs"),
    "Pod": ("api/v1", "pods"),
    "Service": ("api/v1", "services"),
    "ConfigMap": ("api/v1", "configmaps"),
    "Deployment": ("apis/apps/v1", "deployments"),
}

class RestKubeClient:
    """httpx against the API server.
    base_url: WYND_KUBE_API_URL, else https://$KUBERNETES_SERVICE_HOST:$KUBERNETES_SERVICE_PORT.
    token:    re-read on every request from WYND_KUBE_TOKEN_FILE
              (default /var/run/secrets/kubernetes.io/serviceaccount/token); no header if the file is absent
              (e.g. WYND_KUBE_API_URL=http://127.0.0.1:8001 behind `kubectl proxy`).
    ca:       WYND_KUBE_CA_FILE (default .../serviceaccount/ca.crt) if present, else system trust."""
    def __init__(self, namespace: str, *, base_url: str, token_file: str | None, ca_file: str | None,
                 transport: httpx.BaseTransport | None = None): ...
    @classmethod
    def from_env(cls, env: Mapping[str, str], namespace: str) -> "RestKubeClient": ...
```

The client makes exactly these HTTP calls:

| Method | Request |
|---|---|
| apply | `PATCH /{prefix}/namespaces/{ns}/{plural}/{name}?fieldManager=wynd&force=true`, `Content-Type: application/apply-patch+yaml`, body = JSON of `obj` (JSON is valid YAML) |
| get | `GET /{prefix}/namespaces/{ns}/{plural}/{name}`, where 404 returns `None` |
| list | `GET /{prefix}/namespaces/{ns}/{plural}?labelSelector=k1%3Dv1%2Ck2%3Dv2`, returning `items` |
| delete | `DELETE /{prefix}/namespaces/{ns}/{plural}/{name}` with body `{"kind":"DeleteOptions","apiVersion":"v1","propagationPolicy":"Background"}`, where 404 is ignored |
| logs | `GET /api/v1/namespaces/{ns}/pods/{pod}/log?container={c}` (text) |

Any other non-2xx response raises `KubeApiError(status, body.reason, body.message)`, parsed from the k8s `Status` body.

`FakeKubeClient` (`wynd.kube.testing`):
- `objects: dict[tuple[str, str], dict]`.
- `apply` stores a deep copy, keeping any existing `status`.
- `set_status(kind, name, status)`, `set_logs(pod, container, text)`.
- `list` filters on `metadata.labels`.
- `calls: list[tuple]` for assertions.

### 5.4 Names

```python
def k8s_name(*parts: str, max_len: int = 63) -> str:
    """DNS-1123 label: lowercase; [^a-z0-9-] -> '-'; collapse '-'; strip '-'.
    If longer than max_len: s[:max_len-9].rstrip('-') + '-' + sha1(s)[:8]."""
def label_value(s: str) -> str:
    """[A-Za-z0-9_.-], <=63, alnum at both ends; same truncation+hash rule."""
def job_name(kind: str, job_id: str) -> str:        return k8s_name("wynd", kind.replace("_", "-"), job_id)
def cronjob_name(release_id: str) -> str:           return k8s_name("wynd-trigger", release_id)
def release_name(release_id: str) -> str:           return k8s_name("wynd-rel", release_id)
def secret_name_for(process_id: str) -> str:        return k8s_name("wynd-p", process_id)   # per-process Secret, shared by its releases
```

Common labels:
- On every object: `app.kubernetes.io/managed-by: wynd` and `app.kubernetes.io/part-of: wynd`.
- `wynd.dev/component`, one of `job|trigger|serving|controller`.
- Where relevant: `wynd.dev/job-id`, `wynd.dev/job-kind`, `wynd.dev/process` (as `label_value`) and `wynd.dev/release`.
- Full, unsanitised ids go in annotations: `wynd.dev/process-id`, `wynd.dev/ref`.

### 5.5 Job manifests (`render_job`)

```python
def render_job(cfg: KubeConfig, *, job_id: str, kind: Literal["compile", "test_live", "build", "optimise"],
               ref: str, process: str) -> dict[str, Any]: ...
```

Container layout per kind:

| Kind | Init containers (in order) | Main container | Secrets mounted |
|---|---|---|---|
| compile, test_live, optimise | `checkout` | `job`: `python -m wynd.controller.jobmain <id> --checkout /work/repo` | git (read/push), model credentials |
| build | `checkout`, `prepare` (`jobmain <id> --checkout /work/repo --phase prepare`), `buildkit` | `finalize` (`jobmain <id> --checkout /work/repo --phase finalize`) | git (read), registry auth (buildkit only) |

All pods share these settings:
- `restartPolicy: Never`, `backoffLimit: 0` (side effects at most once), `activeDeadlineSeconds: cfg.job_deadline_s`, `ttlSecondsAfterFinished: cfg.job_ttl_s`.
- `serviceAccountName: wynd-job`, `automountServiceAccountToken: false`.
- Pod security context `runAsNonRoot/runAsUser 1000/runAsGroup 1000/fsGroup 1000`.
- An `emptyDir` `work` at `/work`, and the state PVC at `cfg.state_dir` in the wynd containers only (never in buildkit).
- The git credentials Secret mounted read-only at `/etc/wynd/git`.
- Git is configured via env, with no global gitconfig write:

```yaml
- {name: GIT_CONFIG_COUNT, value: "2"}
- {name: GIT_CONFIG_KEY_0, value: credential.helper}
- {name: GIT_CONFIG_VALUE_0, value: store --file=/etc/wynd/git/.git-credentials}
- {name: GIT_CONFIG_KEY_1, value: safe.directory}
- {name: GIT_CONFIG_VALUE_1, value: "*"}
- {name: HOME, value: /work/home}
- {name: WYND_JOB_PUSH_REMOTE, value: origin}         # job framework pushes result branches here (C15)
- {name: WYND_BUILD_CONTEXT_DIR, value: /work/ctx}    # build kind only
```

The golden `job_compile.yaml` (fixed config: namespace `wynd`, image `registry.example/wynd:0.1.0`, git URL `https://git.example/acme/ops.git`, job id `01j9zq3k7m`, ref `3f2a9c1d0e5b7a6c4d3e2f1a0b9c8d7e6f5a4b3c`):

```yaml
apiVersion: batch/v1
kind: Job
metadata:
  name: wynd-compile-01j9zq3k7m
  namespace: wynd
  labels:
    app.kubernetes.io/managed-by: wynd
    app.kubernetes.io/part-of: wynd
    wynd.dev/component: job
    wynd.dev/job-id: 01j9zq3k7m
    wynd.dev/job-kind: compile
    wynd.dev/process: process_supplier_invoice
  annotations:
    wynd.dev/process-id: process_supplier_invoice
    wynd.dev/ref: 3f2a9c1d0e5b7a6c4d3e2f1a0b9c8d7e6f5a4b3c
spec:
  backoffLimit: 0
  activeDeadlineSeconds: 3600
  ttlSecondsAfterFinished: 86400
  template:
    metadata:
      labels:
        app.kubernetes.io/managed-by: wynd
        app.kubernetes.io/part-of: wynd
        wynd.dev/component: job
        wynd.dev/job-id: 01j9zq3k7m
        wynd.dev/job-kind: compile
        wynd.dev/process: process_supplier_invoice
    spec:
      restartPolicy: Never
      serviceAccountName: wynd-job
      automountServiceAccountToken: false
      securityContext: {runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000}
      initContainers:
        - name: checkout
          image: registry.example/wynd:0.1.0
          command: [wynd-kube, checkout]
          args: [--repo, "https://git.example/acme/ops.git", --ref, 3f2a9c1d0e5b7a6c4d3e2f1a0b9c8d7e6f5a4b3c, --dest, /work/repo]
          env: &gitenv
            - {name: GIT_CONFIG_COUNT, value: "2"}
            - {name: GIT_CONFIG_KEY_0, value: credential.helper}
            - {name: GIT_CONFIG_VALUE_0, value: store --file=/etc/wynd/git/.git-credentials}
            - {name: GIT_CONFIG_KEY_1, value: safe.directory}
            - {name: GIT_CONFIG_VALUE_1, value: "*"}
            - {name: HOME, value: /work/home}
          volumeMounts:
            - {name: work, mountPath: /work}
            - {name: git-credentials, mountPath: /etc/wynd/git, readOnly: true}
      containers:
        - name: job
          image: registry.example/wynd:0.1.0
          command: [python, -m, wynd.controller.jobmain]
          args: ["01j9zq3k7m", --checkout, /work/repo]
          envFrom:
            - configMapRef: {name: wynd-config}
            - secretRef: {name: wynd-model-credentials, optional: true}
          env:
            - ... (same six git entries, rendered inline; no YAML anchors in real output)
            - {name: WYND_JOB_PUSH_REMOTE, value: origin}
          resources:
            requests: {cpu: 500m, memory: 1Gi}
            limits: {memory: 4Gi}
          volumeMounts:
            - {name: work, mountPath: /work}
            - {name: state, mountPath: /var/lib/wynd}
            - {name: git-credentials, mountPath: /etc/wynd/git, readOnly: true}
      volumes:
        - {name: work, emptyDir: {}}
        - {name: state, persistentVolumeClaim: {claimName: wynd-state}}
        - {name: git-credentials, secret: {secretName: wynd-git, optional: true}}
```

The real renderer emits plain lists; the anchors above only shorten the doc. `to_yaml` uses `yaml.safe_dump(obj, sort_keys=False, default_flow_style=False)`, with objects built in canonical key order (apiVersion, kind, metadata, spec). A multi-document stream is joined with `---\n`.

For the `build` kind, the differences are the `prepare` init container (same image and mounts as `job`, no model secret, env `WYND_BUILD_CONTEXT_DIR=/work/ctx`), the `buildkit` init container below, and a main `finalize` container:

```yaml
        - name: buildkit
          image: moby/buildkit:v0.27.1-rootless
          command: [sh, -c]
          args:
            - >-
              read -r ref < /work/ctx/image.ref &&
              exec buildctl-daemonless.sh build
              --frontend dockerfile.v0
              --local context=/work/ctx --local dockerfile=/work/ctx
              --output type=image,name=${ref},push=true
              --metadata-file /work/ctx/buildkit-metadata.json
          env:
            - {name: BUILDKITD_FLAGS, value: --oci-worker-no-process-sandbox}
            - {name: DOCKER_CONFIG, value: /home/user/.docker}
          securityContext:
            runAsUser: 1000
            runAsGroup: 1000
            seccompProfile: {type: Unconfined}
            appArmorProfile: {type: Unconfined}
          volumeMounts:
            - {name: work, mountPath: /work}
            - {name: buildkitd, mountPath: /home/user/.local/share/buildkit}
            - {name: registry-auth, mountPath: /home/user/.docker, readOnly: true}
      # extra volumes for build:
        - {name: buildkitd, emptyDir: {}}
        - {name: registry-auth, secret: {secretName: wynd-registry, optional: true, items: [{key: .dockerconfigjson, path: config.json}]}}
```

- With `registry_insecure`, the output option becomes `type=image,name=${ref},push=true,registry.insecure=true`.
- The script deliberately avoids `$(...)`, because Kubernetes would try to expand it.
- `appArmorProfile` needs Kubernetes ≥ 1.30, which is documented as the minimum.

**Build phase contract (C12/C15):**
- `prepare` does all non-image work: the replay test gate, wheels, Dockerfile, `process.env.yaml` and `process.lock.yaml`, all written into the artefact store as usual. It also writes the Docker build context to `$WYND_BUILD_CONTEXT_DIR` (`Dockerfile` at its root) and the full target reference to `$WYND_BUILD_CONTEXT_DIR/image.ref`.
- `finalize` reads `$WYND_BUILD_CONTEXT_DIR/buildkit-metadata.json` (key `containerimage.digest`) and records the build (image `ref@digest`) in the RunRegistry.
- Local runners run prepare, then their own image-builder backend (which writes the same metadata file), then finalize, in one process.

### 5.6 `KubeJobRunner`

```python
TERMINAL = {"succeeded", "failed", "cancelled", "awaiting_input"}

class KubeJobRunner:                    # implements wynd.process JobRunner (C12)
    def __init__(self, cfg: KubeConfig, client: KubeClient, registry: "RunRegistry"): ...
    @classmethod
    def from_env(cls, env: Mapping[str, str], registry: "RunRegistry") -> "KubeJobRunner":
        cfg = KubeConfig.from_env(env); return cls(cfg, RestKubeClient.from_env(env, cfg.namespace), registry)

    def submit(self, kind: "JobKind", ref: str, inputs: dict[str, Any]) -> str:
        job_id = new_job_id()                                         # C11 (same generator as other runners)
        rec = JobRecord(id=job_id, kind=kind, ref=ref, inputs=inputs, status="queued", runner="kube",
                        process=inputs["process"], created_at=utcnow())
        self.registry.put_job(rec)                                    # record first: the pod loads it by id
        try:
            self.client.apply(render_job(self.cfg, job_id=job_id, kind=kind, ref=ref, process=rec.process))
        except KubeApiError as e:
            self.registry.update_job(job_id, status="failed", error=f"kubernetes rejected the Job: {e}")
            raise
        return job_id

    def status(self, job_id: str) -> "JobStatus":
        rec = self.registry.get_job(job_id)
        if rec.status in TERMINAL:
            return rec.status
        kjob = self.client.get("Job", job_name(rec.kind, job_id))
        if kjob is None:
            return self._fail(job_id, "kubernetes Job not found (deleted or expired before recording a result)")
        conds = {c["type"]: c for c in kjob.get("status", {}).get("conditions", []) if c.get("status") == "True"}
        if "Failed" in conds:
            c = conds["Failed"]
            return self._fail(job_id, f"kubernetes Job failed: {c.get('reason', '')}: {c.get('message', '')}".strip())
        if "Complete" in conds:
            rec = self.registry.get_job(job_id)                       # re-read: the pod writes before exiting
            return rec.status if rec.status in TERMINAL else self._fail(job_id, "job pod exited without recording a result")
        return rec.status                                             # "queued" or "running" (written by jobmain)

    def logs(self, job_id: str) -> str:
        """Newest pod with label wynd.dev/job-id=<id>: '==> <container> <==' + log for each init container,
        then each main container, in spec order; unavailable containers print '(not started)'. With no pod
        (TTL-collected), fall back to the job's stored log artefact (C15: jobmain tees output to artefact 'log.txt')."""

    def artefacts(self, job_id: str) -> dict[str, str]:
        return self.registry.get_job(job_id).artefacts                # same as every runner

    def _fail(self, job_id: str, message: str) -> "JobStatus":
        self.registry.update_job(job_id, status="failed", error=message); return "failed"
```

### 5.7 Serving (`KubeServingBackend`) and env manifest mapping

```python
class KubeServingBackend:               # implements the controller serving backend (C15)
    def __init__(self, cfg: KubeConfig, client: KubeClient): ...
    @classmethod
    def from_env(cls, env, registry) -> "KubeServingBackend": ...
    def ensure(self, release: "Release", manifest: "EnvManifest") -> str:
        for obj in render_process_release(self.cfg, release, manifest):
            self.client.apply(obj)
        return f"http://{release_name(release.id)}.{self.cfg.namespace}.svc:{self.cfg.serving_port}"
    def ready(self, release_id: str) -> bool:       # Deployment status.availableReplicas >= 1
    def remove(self, release_id: str) -> None:      # delete Service, Deployment, ConfigMap (never the Secret)
```

Mapping rules (`envmap.py`), where `manifest.vars[*]` is `{name, description, secret, required, default?, used_by}` (C12):

```python
def container_env(manifest: "EnvManifest", *, secret_name: str, config_name: str
                  ) -> tuple[list[dict], list[dict]]:            # (env, envFrom)
    env = [{"name": v.name, "valueFrom": {"secretKeyRef": {"name": secret_name, "key": v.name,
                                                           "optional": not v.required}}}
           for v in manifest.vars if v.secret]
    env_from = [{"configMapRef": {"name": config_name}}]
    return env, env_from

def config_data(manifest: "EnvManifest", binding: Mapping[str, str], defaults: Mapping[str, str]) -> dict[str, str]:
    """Non-secret vars only. Value precedence: release binding, then manifest default, then serving defaults
    (e.g. WYND_* storage selectors for the pod: workspace root /var/lib/wynd/workspaces on an emptyDir).
    Boundary validation: a binding key not in the manifest -> ValueError; a binding for a secret var -> ValueError
    ("secret values come from Secret <name>, never from a Release")."""
```

The rendered release is ConfigMap + Deployment + Service (golden `release.yaml`):

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: wynd-rel-rel-01j9zr
  namespace: wynd
  labels: {app.kubernetes.io/managed-by: wynd, app.kubernetes.io/part-of: wynd, wynd.dev/component: serving,
           wynd.dev/release: rel-01j9zr, wynd.dev/process: process_supplier_invoice}
spec:
  replicas: 1
  selector: {matchLabels: {wynd.dev/release: rel-01j9zr}}
  template:
    metadata:
      labels: {app.kubernetes.io/managed-by: wynd, wynd.dev/component: serving, wynd.dev/release: rel-01j9zr}
    spec:
      serviceAccountName: wynd-process
      automountServiceAccountToken: false
      securityContext: {runAsNonRoot: true, runAsUser: 1000, runAsGroup: 1000, fsGroup: 1000}
      initContainers:
        - name: env-check                                   # spec 4.1: `wynd env check` as init container
          image: registry.example/wynd/process_supplier_invoice@sha256:ab12...
          command: [wynd-supervisor, env-check]             # in-image script from wynd-runtime (C10)
          envFrom: [{configMapRef: {name: wynd-rel-rel-01j9zr}}]
          env:
            - {name: CLAUDE_CODE_OAUTH_TOKEN, valueFrom: {secretKeyRef: {name: wynd-p-process-supplier-invoice, key: CLAUDE_CODE_OAUTH_TOKEN, optional: true}}}
            - {name: ANTHROPIC_API_KEY, valueFrom: {secretKeyRef: {name: wynd-p-process-supplier-invoice, key: ANTHROPIC_API_KEY, optional: true}}}
      containers:
        - name: supervisor
          image: registry.example/wynd/process_supplier_invoice@sha256:ab12...
          command: [wynd-supervisor, serve, --host, 0.0.0.0, --port, "8080"]
          ports: [{name: http, containerPort: 8080}]
          envFrom: [{configMapRef: {name: wynd-rel-rel-01j9zr}}]
          env: [ ...same secretKeyRef entries... ]
          readinessProbe: {httpGet: {path: /readyz, port: http}, periodSeconds: 5}
          livenessProbe: {httpGet: {path: /healthz, port: http}, periodSeconds: 10, failureThreshold: 3}
          volumeMounts: [{name: workspaces, mountPath: /var/lib/wynd/workspaces}]
      volumes: [{name: workspaces, emptyDir: {}}]
---
apiVersion: v1
kind: Service
metadata: {name: wynd-rel-rel-01j9zr, namespace: wynd, labels: {...}}
spec:
  selector: {wynd.dev/release: rel-01j9zr}
  ports: [{name: http, port: 8080, targetPort: http}]
```

- **No state PVC in process pods.** The controller persists the trace events of the runs it submits (C15), which is how triggered runs feed `wynd optimise`.
- **The per-process Secret** is user-created. `wynd-kube env-template` renders its skeleton:

```yaml
# Secret for process process_supplier_invoice. Fill values, then: kubectl apply -f <this file>
#   CLAUDE_CODE_OAUTH_TOKEN  (optional) Claude Code subscription token from `claude setup-token`; used by provider:claude-code
#   ANTHROPIC_API_KEY        (optional) Anthropic API key; used by provider:claude-code
apiVersion: v1
kind: Secret
metadata: {name: wynd-p-process-supplier-invoice, namespace: wynd}
type: Opaque
stringData:
  CLAUDE_CODE_OAUTH_TOKEN: ""
  ANTHROPIC_API_KEY: ""
```

### 5.8 Triggers (`KubeTriggerBackend`) and the webhook Service

```python
class KubeTriggerBackend:              # implements the controller trigger backend (C15)
    def install(self, release: "Release") -> None:
        if release.trigger.kind == "schedule":
            self.client.apply(render_cronjob(self.cfg, release))
        # "webhook" / "manual": nothing to create; the controller serves POST /hooks/{release_id} (C15)
    def remove(self, release_id: str) -> None:
        self.client.delete("CronJob", cronjob_name(release_id))
    def reconcile(self, releases: list["Release"]) -> None:            # controller calls on startup
        want = {cronjob_name(r.id): r for r in releases if r.trigger.kind == "schedule"}
        have = {o["metadata"]["name"] for o in self.client.list("CronJob", {"wynd.dev/component": "trigger"})}
        for name in sorted(have - want.keys()): self.client.delete("CronJob", name)
        for name in sorted(want): self.client.apply(render_cronjob(self.cfg, want[name]))
```

Golden `cronjob.yaml`:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: wynd-trigger-rel-01j9zr
  namespace: wynd
  labels: {app.kubernetes.io/managed-by: wynd, app.kubernetes.io/part-of: wynd, wynd.dev/component: trigger, wynd.dev/release: rel-01j9zr}
spec:
  schedule: "0 9 * * 1-5"                  # Release.trigger.cron: standard 5-field cron (C15 validates at the boundary)
  timeZone: Europe/London                  # Release.trigger.timezone (omitted if null)
  concurrencyPolicy: Forbid
  startingDeadlineSeconds: 300
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 0
      ttlSecondsAfterFinished: 3600
      template:
        metadata: {labels: {app.kubernetes.io/managed-by: wynd, wynd.dev/component: trigger, wynd.dev/release: rel-01j9zr}}
        spec:
          restartPolicy: Never
          serviceAccountName: wynd-job
          automountServiceAccountToken: false
          securityContext: {runAsNonRoot: true, runAsUser: 1000}
          containers:
            - name: fire
              image: registry.example/wynd:0.1.0
              command: [wynd-kube, fire, rel-01j9zr]
              args: [--controller-url, "http://wynd-controller:8000"]
              envFrom: [{secretRef: {name: wynd-api-token, optional: true}}]
              resources: {requests: {cpu: 10m, memory: 64Mi}, limits: {memory: 256Mi}}
```

`fire.py`:

```python
def fire(release_id: str, controller_url: str, token: str | None, *, timeout_s: float = 30.0) -> str:
    """POST {controller_url}/api/releases/{release_id}/runs  body {"trigger": "schedule"}
    header Authorization: Bearer <token> when set. Returns the run id from {"run_id": ...}.
    urllib (stdlib); one attempt; non-2xx -> SystemExit(1) with the response body on stderr."""
```

The controller uses the release's fixed trigger inputs (C15). Firing through the controller, rather than calling the process Service directly, keeps "triggers live in the controller": run records, trace persistence and usage accounting stay in one place.

The **webhook Service** is static and part of the install (5.10):
- `wynd-webhooks` is a ClusterIP Service on port 80 that targets the controller pods on port 8000.
- It exists so operators can expose only `/hooks/` publicly, through their own Ingress; `docs/cluster-install.md` gives an Ingress example with `pathType: Prefix` and `path: /hooks/`.
- The API Service `wynd-controller` stays internal.

### 5.9 `checkout.py` (init containers)

```python
def checkout(repo: str, dest: Path, *, ref: str | None = None, branch: str | None = None,
             if_missing: bool = False, lfs: bool = True) -> None:
    """Exactly one of ref (commit sha, detached) or branch (tracking checkout, for the controller's clone).
    if_missing: return immediately if dest/.git exists (controller restart keeps its clone)."""
```

Command sequence (subprocess, `check=True`, stderr surfaced on failure):

```
git init -q <dest> && git -C <dest> remote add origin <repo>
# ref mode
git -C <dest> fetch -q --filter=blob:none origin <ref>             # servers allowing fetch-by-sha (GitHub, GitLab)
  on failure: git -C <dest> fetch -q --filter=blob:none origin '+refs/heads/*:refs/remotes/origin/*'
git -C <dest> checkout -q --detach <ref>
# branch mode
git -C <dest> fetch -q --filter=blob:none origin <branch> && git -C <dest> checkout -q -B <branch> --track origin/<branch>
# both
git -C <dest> lfs pull          (if lfs)
```

### 5.10 Install manifests (`render_install`) and the cluster install

`render_install(p: InstallParams) -> list[dict]` renders these objects, in order:
1. The Namespace (if `create_namespace`), labelled `pod-security.kubernetes.io/enforce: privileged`, `pod-security.kubernetes.io/warn: baseline` and `pod-security.kubernetes.io/audit: baseline`. The rootless BuildKit init container needs Unconfined seccomp and AppArmor (R6). With `--no-namespace`, the docs tell operators to apply these labels themselves.
2. ServiceAccounts `wynd-controller`, `wynd-job` and `wynd-process`.
3. Role `wynd-controller`, with rules:
   - `batch`: `jobs`, `cronjobs`: get, list, watch, create, update, patch, delete
   - `""`: `pods`: get, list, watch; `pods/log`: get
   - `""`: `services`, `configmaps`: get, list, create, update, patch, delete
   - `apps`: `deployments`: get, list, watch, create, update, patch, delete

   There is no `secrets` access at all.
4. The RoleBinding.
5. PVC `wynd-state` (`accessModes: [p.storage_access_mode]`, `storage: p.storage_size`, and `storageClassName` if set).
6. ConfigMap `wynd-config`:

```yaml
data:
  WYND_STATE_DIR: /var/lib/wynd
  WYND_HOME: /var/lib/wynd/home
  WYND_WORKSPACE: /var/lib/wynd/repo            # + "/<workspace_subdir>" when not "."  (C15 name)
  WYND_GIT_REMOTE: origin                       # C15: push design commits / fetch job branches
  WYND_JOB_RUNNER: kube
  WYND_TRIGGER_BACKEND: kube
  WYND_SERVING_BACKEND: kube
  WYND_KUBE_NAMESPACE: wynd
  WYND_KUBE_TOOLCHAIN_IMAGE: <p.image>
  WYND_KUBE_BUILDKIT_IMAGE: <p.buildkit_image>
  WYND_KUBE_GIT_URL: <p.git_url>
  WYND_KUBE_WORKSPACE_SUBDIR: <p.workspace_subdir>
  WYND_KUBE_CONTROLLER_URL: http://wynd-controller:8000
  WYND_KUBE_REGISTRY_INSECURE: "0"
  GIT_AUTHOR_NAME: wynd
  GIT_AUTHOR_EMAIL: wynd@localhost
  GIT_COMMITTER_NAME: wynd
  GIT_COMMITTER_EMAIL: wynd@localhost
  # + the storage-backend selectors for the local FS backends rooted at WYND_STATE_DIR (C11 names)
```

7. Deployment `wynd-controller`:
   - `replicas: 1` and `strategy: Recreate`, because the state is single-writer.
   - An init container that runs `wynd-kube checkout --repo <git_url> --branch <git_branch> --dest /var/lib/wynd/repo --if-missing`.
   - Container `wynd serve-api --host 0.0.0.0 --port 8000`, with `envFrom` taking `wynd-config`, `wynd-api-token` (optional) and `wynd-model-credentials` (optional; the web compile chat uses the LLM).
   - The git env as in 5.5, and probes `httpGet /api/health` (C15).
   - Mounts: the state PVC at `/var/lib/wynd` and the git credentials.
8. Service `wynd-controller` (ClusterIP 8000) and Service `wynd-webhooks` (ClusterIP 80 → 8000).

Secrets are **user-created and never rendered with values**:

| Secret | Contents |
|---|---|
| `wynd-git` | key `.git-credentials`, e.g. `https://x-access-token:<token>@github.com` |
| `wynd-model-credentials` | `CLAUDE_CODE_OAUTH_TOKEN` and/or `ANTHROPIC_API_KEY` |
| `wynd-registry` | type `kubernetes.io/dockerconfigjson` |
| `wynd-api-token` | `WYND_API_TOKEN` |
| `wynd-p-<process>` | per-process Secret, rendered by `env-template` |

**Storage is user-supplied.** An RWX PVC is shared by the controller and job pods. On single-node clusters (kind, minikube) RWO works, because all pods land on one node.

### 5.11 `wynd-kube` CLI

```
wynd-kube render install [--namespace wynd] --image IMG --git-url URL [--git-branch main] [--workspace-subdir .]
                         [--storage-class SC] [--storage-size 20Gi] [--access-mode ReadWriteMany|ReadWriteOnce]
                         [--buildkit-image IMG] [--no-namespace] [--registry-insecure]      > install.yaml
wynd-kube render release --release-id ID --process ID --image REF@DIGEST --manifest process.env.yaml
                         [--env KEY=VAL ...] [--namespace wynd]                                 (GitOps use)
wynd-kube env-template   --process ID --manifest process.env.yaml [--namespace wynd]
wynd-kube checkout       --repo URL (--ref SHA | --branch NAME) --dest DIR [--if-missing] [--no-lfs]
wynd-kube fire RELEASE_ID [--controller-url URL]          # token from $WYND_API_TOKEN
```

### 5.12 Toolchain/controller image (`deploy/wynd.Dockerfile`)

```dockerfile
# syntax=docker/dockerfile:1.7
FROM node:26-bookworm-slim AS web
WORKDIR /src/packages/web
COPY packages/web/package.json packages/web/package-lock.json ./
RUN npm ci
COPY packages/web/ ./
RUN npm run build

FROM python:3.12-slim-bookworm
COPY --from=ghcr.io/astral-sh/uv:0.10.7 /uv /uvx /usr/local/bin/
RUN apt-get update \
 && apt-get install -y --no-install-recommends git git-lfs ca-certificates \
 && git lfs install --system \
 && rm -rf /var/lib/apt/lists/*
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/opt/wynd PATH=/opt/wynd/bin:$PATH
WORKDIR /src
COPY pyproject.toml uv.lock ./
COPY packages/ packages/
RUN uv sync --locked --no-dev --all-packages --no-editable \
 && uv pip install --python /opt/wynd/bin/python "claude-agent-sdk>=0.2,<0.3"   # match runtime's claude-code env fragment
COPY --from=web /src/packages/web/dist /opt/wynd/web
ENV WYND_WEB_DIST=/opt/wynd/web
RUN useradd --uid 1000 --create-home wynd
USER 1000
WORKDIR /home/wynd
```

`deploy/wynd.Dockerfile.dockerignore` (BuildKit reads `<Dockerfile>.dockerignore`):

```
.git
**/.wynd
**/.venv
**/node_modules
**/__pycache__
examples
docs
packages/web/dist
```

`WYND_WEB_DIST` is the controller's static-assets setting (C15). One image serves the controller, job pods, checkout and fire.

### 5.13 Tests (A8-kube)

- `test_names.py`: sanitisation, the 63-character truncation with hash, and label rules (property-style loops over tricky ids: `finance/invoices`, `A_B`, 80-character ids).
- `test_envmap.py`: secret vs config split, `optional` = not required, and boundary errors for unknown or secret bindings.
- `test_manifests.py`: goldens for every renderer. Regenerate with `WYND_UPDATE_GOLDEN=1`. Structural asserts in addition to the goldens:
  - no `secrets` in the Role.
  - no state PVC in the buildkit or process containers.
  - `backoffLimit == 0`.
  - `automountServiceAccountToken: false` except on the controller.
  - every container has a name, image and resources, or is explicitly exempted.
- `test_client.py`: `httpx.MockTransport` checks, for each verb, the method, URL, query, content type, bearer header (token re-read per call), 404 handling and Status-body errors.
- `test_runner.py` (`FakeKubeClient` and an in-memory RunRegistry fake):
  - `submit` writes the record before applying the Job.
  - apply failure marks the record failed and re-raises.
  - status matrix: terminal record; Job missing; Failed condition; Complete with a terminal record; Complete without one; active.
  - logs ordering, with fallback to the artefact.
- `test_triggers.py`: install, remove, and reconcile (deletes orphans, applies wanted, leaves non-schedule releases alone).
- `test_serving.py`: ensure returns the URL, then ready and remove.
- `test_checkout.py`, using a temporary bare repo reached through a `file://` URL:
  - ref mode, both with and without fetch-by-sha (the second through `uploadpack.allowReachableSHA1InWant=false`, forcing the fallback).
  - branch mode and `--if-missing`.
  - `--no-lfs`.
- `test_fire.py`: a `ThreadingHTTPServer` stub checks the path, body and auth header, and that a non-2xx response exits 1.
- `test_cli.py`: `render install` output equals the golden for fixed arguments.
- `test_e2e_kind.py` (`@pytest.mark.kube`, needs `WYND_KUBE=1` and a kind cluster with a local registry; the CI job is in 9):
  1. Render and apply install.
  2. Port-forward the controller.
  3. Submit a `build` job for `process_supplier_invoice` at `WYND_KUBE_E2E_REF` through the controller API, and poll it to `succeeded`.
  4. Create a Release with a `manual` trigger; the serving Deployment becomes ready, which proves the env-check init container passed.
  5. Create a Release with a `schedule` trigger `* * * * *` and wait at most 150 s for a run record with origin `schedule` (any exit).
  6. Only with `WYND_LIVE=1` and model credentials: run `acme_inv_1042.pdf` through the run API's file input (C10) and expect exit `done`.

---

## 6. Sample workspace `examples/invoices/` (A8-sample)

This is the dogfood process from spec 6.2. It is also the fixture for the M1 local e2e run, the M2 image run, the M3 compile acceptance, the M5 agentic edge, and the kind e2e.

### 6.1 File tree (final state; items marked `(M5)` are added by the M5 implementer)

```
examples/invoices/
  wynd.yaml
  .env.example
  README.md
  LICENSE                                   # Apache-2.0 (copyable sample)
  tools/
    make_sample_pdfs.py                     # stdlib-only PDF writer + SAMPLES (single source of invoice text)
    test_make_sample_pdfs.py                # drift tests (run by root pytest)
  tests/
    test_compile_from_proto.py              # M3 acceptance (@live)
    test_optimise_live.py                   # M5 acceptance (@live)
  processes/process_supplier_invoice/
    process.yaml
    edges.lock.yaml                         (M5)
    examples/
      acme_inv_1042.pdf
      globex_inv_77810.pdf
      initech_inv_5521.pdf
      shipping_notice.pdf
      hooli_credit_note_311.pdf
      umbrella_proforma_88.pdf              (M5)
    proto/
      read_pdf.yaml
      extract_invoice_fields.yaml
      validate_fields.yaml
      fix_fields.yaml
      save_record.yaml
      escalate_to_human.yaml
    steps/
      read_pdf/               pyproject.toml  step.lock.yaml  read_pdf.py               test_read_pdf.py
      extract_invoice_fields/ pyproject.toml  step.lock.yaml  extract_invoice_fields.py test_extract_invoice_fields.py  cassettes/
      validate_fields/        pyproject.toml  step.lock.yaml  validate_fields.py        test_validate_fields.py
      fix_fields/             pyproject.toml  step.lock.yaml  fix_fields.py             test_fix_fields.py        cassettes/
      save_record/            pyproject.toml  step.lock.yaml  save_record.py            test_save_record.py
      escalate_to_human/      pyproject.toml  step.lock.yaml  escalate_to_human.py      test_escalate_to_human.py
    tests/
      test_process.py                       # generated-style: one test per process example (replay)
      test_side_effects.py                  # hand-written: files written where the edges say
      test_process_live.py                  # @live smoke run
      cassettes/                            # process-level cassettes (git-lfs)
```

Step package layout assumption (C1/C8/C14, to reconcile with the runtime and compiler owners):
- Flat layout, with the module named after the package directory (`read_pdf.py`) and the test at `test_<step>.py` beside it.
- `cassettes/` sits in the package.
- `step.lock.yaml` names the class via `entry: <module>:<Class>`.
- The loader must reject two step packages with the same module name within one process closure, since they would collide in a venv (proposed code `E-STEP-MODULE-CLASH`).

### 6.2 `wynd.yaml`

```yaml
# Wynd workspace marker (spec 5.1). Sample workspace for the supplier-invoice dogfood.
process_roots:
  - processes
step_roots: {}
```

This includes whatever required header the spec owner's workspace schema defines, for example `kind: workspace`.

### 6.3 `.env.example`

```sh
# Copy to .env (git-ignored). `uv run wynd env check process_supplier_invoice` verifies these.
# Validated records (edge validate.done -> save)
RECORDS_DIR=/tmp/wynd-invoices/records
# Records with total > 10,000 are written here for approval instead
REVIEW_DIR=/tmp/wynd-invoices/review
# Escalation tickets for a human reviewer
ESCALATIONS_DIR=/tmp/wynd-invoices/escalations

# LLM auth for provider claude-code. Locally a logged-in `claude` CLI needs neither.
# In containers/CI set ONE of these:
# CLAUDE_CODE_OAUTH_TOKEN=   # Claude subscription "dev key": run `claude setup-token`
# ANTHROPIC_API_KEY=         # API billing instead of the subscription
```

### 6.4 `processes/process_supplier_invoice/process.yaml` (final, M5 state)

```yaml
kind: process
name: process_supplier_invoice
goal: Turn a supplier invoice PDF into a validated record in the records store.
provider: claude-code                 # default provider (user requirement); tiers map via `wynd provider`
env:
  base: debian-slim-python
entry: read                           # read.Input declares pdf_path
inputs:
  pdf_path: path
outputs:
  done:
    record: object
  not_an_invoice: {}
  needs_review: {}
examples:
  - inputs: { pdf_path: examples/acme_inv_1042.pdf }
    env: &dirs { RECORDS_DIR: "{tmp}/records", REVIEW_DIR: "{tmp}/review", ESCALATIONS_DIR: "{tmp}/escalations" }
    outputs:
      record: { key: acme-supplies-ltd__INV-1042, supplier: ACME Supplies Ltd, invoice_number: INV-1042,
                total: 1200.5, currency: GBP, due_date: 2026-10-01 }
    exit: done
  - inputs: { pdf_path: examples/globex_inv_77810.pdf }          # > 10,000: saved to REVIEW_DIR
    env: *dirs
    outputs:
      record: { key: globex-corporation__GX-77810, supplier: Globex Corporation, invoice_number: GX-77810,
                total: 14250.0, currency: USD, due_date: 2026-11-15 }
    exit: done
  - inputs: { pdf_path: examples/initech_inv_5521.pdf }          # "$" -> validate fixable -> fix -> CAD
    env: *dirs
    outputs:
      record: { key: initech-canada-inc__IC-5521, supplier: Initech Canada Inc., invoice_number: IC-5521,
                total: 980.0, currency: CAD, due_date: 2026-10-20 }
    exit: done
  - inputs: { pdf_path: examples/shipping_notice.pdf }
    env: *dirs
    exit: not_an_invoice
  - inputs: { pdf_path: examples/hooli_credit_note_311.pdf }     # negative total: not fixable -> escalate
    env: *dirs
    exit: needs_review
  - inputs: { pdf_path: examples/umbrella_proforma_88.pdf }      # (M5) valid fields, agentic check says no -> escalate
    env: *dirs
    exit: needs_review
steps:
  read:     { use: ./steps/read_pdf }
  extract:  { use: ./steps/extract_invoice_fields }
  validate: { use: ./steps/validate_fields }
  fix:      { use: ./steps/fix_fields }
  save:     { use: ./steps/save_record }
  escalate: { use: ./steps/escalate_to_human }
edges:
  - from: read.done
    to: extract
    with: { invoice_text: steps.read.outputs.text }
  - from: extract.done
    to: validate
    with: { fields: steps.extract.outputs }
  - from: extract.not_an_invoice
    to: $exit.not_an_invoice
  # Branches evaluated top to bottom; first taken wins; the first branch with no when/check is the else.
  - from: validate.done
    kind: agentic                                                  # (M5)
    to:
      - step: save
        name: save
        when: steps.validate.outputs.valid
        check: >-                                                  # (M5)
          The document is a final supplier invoice that requests payment:
          not a pro forma invoice, quote, estimate or statement.
        context: [steps.read.outputs]                              # (M5)
        with:
          record: steps.validate.outputs.record
          dest: if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR
                else env.RECORDS_DIR
      - step: fix
        name: fix
        when: steps.validate.outputs.fixable and steps.fix.runs < 3
        with:
          invoice_text: steps.read.outputs.text
          fields: steps.validate.outputs.fields                    # the fields just validated: extract's or fix's
          errors: steps.validate.outputs.errors
      - step: escalate                                             # else: not fixable, fixed 3 times, or check said no
        with:
          fields: steps.validate.outputs.fields
          errors: steps.validate.outputs.errors
          queue_dir: env.ESCALATIONS_DIR
          run_id: run.id
  - from: fix.done
    to: validate
    with: { fields: steps.fix.outputs }
  - from: save.done
    to: $exit.done
    with: { record: steps.save.outputs.record }
  - from: escalate.done
    to: $exit.needs_review
```

- **M1 state:** the same file without the `(M5)` lines and without the umbrella example. `kind:` defaults to deterministic.
- **Anchors:** the YAML `&dirs`/`*dirs` anchors are plain YAML (safe_load resolves them), so no loader support is needed.
- **`steps.<x>.outputs`:** it is bound whole in `fields: steps.extract.outputs`. Whether the spec owner includes `exit` in `outputs` or not, the sample's `InvoiceFields` uses pydantic's default `extra="ignore"`, so both work (C1).

`edges.lock.yaml` (M5) is written by `sync_edge_lock` during the M5 compile; the hash below is illustrative:

```yaml
version: 1
edges:
  validate.done[save]:
    check_hash: sha256:<check_hash(check, ["steps.read.outputs"])>
    provider: null
    retries: 2
    thinking: low
    tier: cheap
    timeout_s: 120
```

### 6.5 Proto-steps (`proto/*.yaml`)

The type syntax these files assume (C1, to reconcile with the spec owner):
- Scalar type names: `string`, `number`, `integer`, `boolean`, `date`, `path`, `object`.
- A nested mapping is an object with those fields.
- A one-element YAML list `[T]` is `list[T]`.
- `outputs` is nested by exit **only when `exits:` is declared**. Otherwise it is a flat done-only mapping, which is required because done-only steps have object-typed output fields.

The invoice texts are exactly `"\n".join(SAMPLES[name])` from 6.9, and `test_make_sample_pdfs.py` enforces the equality. Below, `<TEXT acme_inv_1042.pdf>` stands for that literal, written as a `|-` block scalar in the real files.

**`proto/read_pdf.yaml`**

```yaml
kind: proto_step
name: read_pdf
instruction: |
  Read a PDF file and return its text and page count. Extract text page by page, in order,
  with a PDF text-extraction library (no OCR); strip trailing newlines from each page's text
  and join pages with a single newline. A PDF with no extractable text returns empty text.
inputs:
  pdf_path: path
outputs:
  text: string
  pages: integer
examples:
  - inputs: { pdf_path: examples/acme_inv_1042.pdf }
    outputs: { pages: 1, text: <TEXT acme_inv_1042.pdf> }
    exit: done
  - inputs: { pdf_path: examples/shipping_notice.pdf }
    outputs: { pages: 1, text: <TEXT shipping_notice.pdf> }
    exit: done
  - inputs: { pdf_path: examples/does_not_exist.pdf }
    exit: error                  # edge case: a missing file resolves to the implicit error exit (C1: allow `exit: error` in examples)
env:
  deps: ["pypdf>=6,<7"]          # minimum requirement: the exact example text depends on pypdf's extraction
```

**`proto/extract_invoice_fields.yaml`**

```yaml
kind: proto_step
name: extract_invoice_fields
instruction: |
  Extract the supplier, invoice number, total, currency and due date from the text of a supplier invoice.

  Treat any document that states an amount to pay or to credit as an invoice, including pro forma
  invoices and credit notes; use not_an_invoice only for documents with no amount due at all, such
  as shipping notices, marketing or letters.

  supplier: the supplier's name exactly as printed at the top of the document.
  invoice_number: the document's own number exactly as printed (invoice, credit note or pro forma
  number), not a customer, order or tax registration number.
  total: the final amount due as a number without currency symbols or thousands separators;
  negative for credits.
  currency: the ISO 4217 code if the document states one or the symbol is unambiguous (£ is GBP,
  € is EUR); otherwise the symbol exactly as printed, for example "$".
  due_date: the payment due date (or the refund-by or valid-until date if there is no due date).
inputs:
  invoice_text: string
outputs:
  done:
    supplier: string
    invoice_number: string
    total: number
    currency: string
    due_date: date
  not_an_invoice: {}
exits: [done, not_an_invoice]
examples:
  - inputs: { invoice_text: <TEXT acme_inv_1042.pdf> }
    outputs: { supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.50, currency: GBP, due_date: 2026-10-01 }
    exit: done
  - inputs: { invoice_text: <TEXT globex_inv_77810.pdf> }
    outputs: { supplier: Globex Corporation, invoice_number: GX-77810, total: 14250.00, currency: USD, due_date: 2026-11-15 }
    exit: done
  - inputs: { invoice_text: <TEXT initech_inv_5521.pdf> }
    outputs: { supplier: Initech Canada Inc., invoice_number: IC-5521, total: 980.00, currency: "$", due_date: 2026-10-20 }
    exit: done
  - inputs: { invoice_text: <TEXT hooli_credit_note_311.pdf> }
    outputs: { supplier: Hooli Ltd, invoice_number: CN-311, total: -250.00, currency: GBP, due_date: 2026-10-18 }
    exit: done
  - inputs: { invoice_text: <TEXT shipping_notice.pdf> }
    exit: not_an_invoice
env:
  deps: []
```

**`proto/validate_fields.yaml`**

```yaml
kind: proto_step
name: validate_fields
instruction: |
  Validate invoice fields against the accounts-payable rules and normalise them into a record.
  Normalise first: collapse runs of whitespace in supplier to single spaces and trim; strip and
  upper-case invoice_number and currency; round total to 2 decimal places.
  Then check, in this order, collecting every error as {field, code, message, fixable}:
  - supplier empty: missing_supplier, "supplier is empty", fixable
  - invoice_number not matching [A-Z0-9][A-Z0-9/-]{1,31}: bad_invoice_number,
    "invoice_number '<value>' must be 2-32 characters of A-Z, 0-9, '/' or '-'", fixable
  - total <= 0: non_positive_total, "total <total, 2 dp> must be greater than zero", not fixable
  - otherwise total > 1000000: total_over_limit, "total <total, 2 dp> exceeds the 1,000,000 approval limit", not fixable
  - currency not in AUD CAD CHF CNY CZK DKK EUR GBP HKD INR JPY NOK NZD PLN SEK SGD USD ZAR:
    unsupported_currency, "currency '<value>' is not a supported ISO 4217 code", fixable
  - due_date outside 2000-01-01..2100-12-31: due_date_out_of_range,
    "due_date <ISO date> is outside 2000-01-01..2100-12-31", fixable
  valid: there are no errors. fixable: there is at least one error and every error is fixable.
  fields: the normalised fields. record: the normalised fields plus key, which is
  "<slug>__<invoice_number>" where slug is the lower-cased supplier with each run of characters
  outside a-z and 0-9 replaced by "-" and leading/trailing "-" removed.
inputs:
  fields: { supplier: string, invoice_number: string, total: number, currency: string, due_date: date }
outputs:
  valid: boolean
  fixable: boolean
  errors: [ { field: string, code: string, message: string, fixable: boolean } ]
  fields: { supplier: string, invoice_number: string, total: number, currency: string, due_date: date }
  record: { key: string, supplier: string, invoice_number: string, total: number, currency: string, due_date: date }
examples:
  - inputs: { fields: { supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: 2026-10-01 } }
    outputs:
      valid: true
      fixable: false
      errors: []
      fields: { supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: 2026-10-01 }
      record: { key: acme-supplies-ltd__INV-1042, supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: 2026-10-01 }
    exit: done
  - inputs: { fields: { supplier: "  Globex   Corporation ", invoice_number: " gx-77810", total: 14250.004, currency: usd, due_date: 2026-11-15 } }
    outputs:
      valid: true
      fixable: false
      errors: []
      fields: { supplier: Globex Corporation, invoice_number: GX-77810, total: 14250.0, currency: USD, due_date: 2026-11-15 }
      record: { key: globex-corporation__GX-77810, supplier: Globex Corporation, invoice_number: GX-77810, total: 14250.0, currency: USD, due_date: 2026-11-15 }
    exit: done
  - inputs: { fields: { supplier: Initech Canada Inc., invoice_number: IC-5521, total: 980.0, currency: "$", due_date: 2026-10-20 } }
    outputs:
      valid: false
      fixable: true
      errors: [ { field: currency, code: unsupported_currency, message: "currency '$' is not a supported ISO 4217 code", fixable: true } ]
      fields: { supplier: Initech Canada Inc., invoice_number: IC-5521, total: 980.0, currency: "$", due_date: 2026-10-20 }
      record: { key: initech-canada-inc__IC-5521, supplier: Initech Canada Inc., invoice_number: IC-5521, total: 980.0, currency: "$", due_date: 2026-10-20 }
    exit: done
  - inputs: { fields: { supplier: Hooli Ltd, invoice_number: CN-311, total: -250.0, currency: GBP, due_date: 2026-10-18 } }
    outputs:
      valid: false
      fixable: false
      errors: [ { field: total, code: non_positive_total, message: "total -250.00 must be greater than zero", fixable: false } ]
      fields: { supplier: Hooli Ltd, invoice_number: CN-311, total: -250.0, currency: GBP, due_date: 2026-10-18 }
      record: { key: hooli-ltd__CN-311, supplier: Hooli Ltd, invoice_number: CN-311, total: -250.0, currency: GBP, due_date: 2026-10-18 }
    exit: done
  - inputs: { fields: { supplier: Hooli Ltd, invoice_number: CN-311, total: -250.0, currency: "$", due_date: 2026-10-18 } }
    outputs:
      valid: false
      fixable: false                        # one unfixable error makes the whole set unfixable
      errors:
        - { field: total, code: non_positive_total, message: "total -250.00 must be greater than zero", fixable: false }
        - { field: currency, code: unsupported_currency, message: "currency '$' is not a supported ISO 4217 code", fixable: true }
      fields: { supplier: Hooli Ltd, invoice_number: CN-311, total: -250.0, currency: "$", due_date: 2026-10-18 }
      record: { key: hooli-ltd__CN-311, supplier: Hooli Ltd, invoice_number: CN-311, total: -250.0, currency: "$", due_date: 2026-10-18 }
    exit: done
  - inputs: { fields: { supplier: "", invoice_number: "", total: 10.0, currency: EUR, due_date: 2026-12-01 } }
    outputs:
      valid: false
      fixable: true
      errors:
        - { field: supplier, code: missing_supplier, message: "supplier is empty", fixable: true }
        - { field: invoice_number, code: bad_invoice_number, message: "invoice_number '' must be 2-32 characters of A-Z, 0-9, '/' or '-'", fixable: true }
      fields: { supplier: "", invoice_number: "", total: 10.0, currency: EUR, due_date: 2026-12-01 }
      record: { key: __, supplier: "", invoice_number: "", total: 10.0, currency: EUR, due_date: 2026-12-01 }
    exit: done
  - inputs: { fields: { supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: 1999-10-01 } }
    outputs:
      valid: false
      fixable: true
      errors: [ { field: due_date, code: due_date_out_of_range, message: "due_date 1999-10-01 is outside 2000-01-01..2100-12-31", fixable: true } ]
      fields: { supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: 1999-10-01 }
      record: { key: acme-supplies-ltd__INV-1042, supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: 1999-10-01 }
    exit: done
  - inputs: { fields: { supplier: Globex Corporation, invoice_number: GX-1, total: 2000000.0, currency: USD, due_date: 2026-12-01 } }
    outputs:
      valid: false
      fixable: false
      errors: [ { field: total, code: total_over_limit, message: "total 2000000.00 exceeds the 1,000,000 approval limit", fixable: false } ]
      fields: { supplier: Globex Corporation, invoice_number: GX-1, total: 2000000.0, currency: USD, due_date: 2026-12-01 }
      record: { key: globex-corporation__GX-1, supplier: Globex Corporation, invoice_number: GX-1, total: 2000000.0, currency: USD, due_date: 2026-12-01 }
    exit: done
env:
  deps: []
```

**`proto/fix_fields.yaml`**

```yaml
kind: proto_step
name: fix_fields
instruction: |
  Correct invoice fields that failed validation by re-reading the invoice text.
  Change only the fields named in errors; copy every other field unchanged. The invoice text is the
  only source of truth. For an ambiguous currency symbol such as "$", decide the ISO 4217 code from
  the supplier's address or other currency mentions in the text. If the text does not support a
  correction, return the field unchanged; the validator will then escalate to a human.
inputs:
  invoice_text: string
  fields: { supplier: string, invoice_number: string, total: number, currency: string, due_date: date }
  errors: [ { field: string, code: string, message: string, fixable: boolean } ]
outputs:
  supplier: string
  invoice_number: string
  total: number
  currency: string
  due_date: date
examples:
  - inputs:
      invoice_text: <TEXT initech_inv_5521.pdf>
      fields: { supplier: Initech Canada Inc., invoice_number: IC-5521, total: 980.0, currency: "$", due_date: 2026-10-20 }
      errors: [ { field: currency, code: unsupported_currency, message: "currency '$' is not a supported ISO 4217 code", fixable: true } ]
    outputs: { supplier: Initech Canada Inc., invoice_number: IC-5521, total: 980.0, currency: CAD, due_date: 2026-10-20 }
    exit: done
  - inputs:
      invoice_text: |-
        North Wind Traders
        Supplier ref: SUP/2026/0098
        Amount payable: EUR 312.40 by 30 November 2026
      fields: { supplier: North Wind Traders, invoice_number: "", total: 312.4, currency: EUR, due_date: 2026-11-30 }
      errors: [ { field: invoice_number, code: bad_invoice_number, message: "invoice_number '' must be 2-32 characters of A-Z, 0-9, '/' or '-'", fixable: true } ]
    outputs: { supplier: North Wind Traders, invoice_number: SUP/2026/0098, total: 312.4, currency: EUR, due_date: 2026-11-30 }
    exit: done
  - inputs:                                   # edge case: text cannot support a fix -> unchanged
      invoice_text: |-
        Arcade Tokens Co
        Invoice AT-7
        Total: 50.00 credits, due 1 December 2026
      fields: { supplier: Arcade Tokens Co, invoice_number: AT-7, total: 50.0, currency: CREDITS, due_date: 2026-12-01 }
      errors: [ { field: currency, code: unsupported_currency, message: "currency 'CREDITS' is not a supported ISO 4217 code", fixable: true } ]
    outputs: { supplier: Arcade Tokens Co, invoice_number: AT-7, total: 50.0, currency: CREDITS, due_date: 2026-12-01 }
    exit: done
env:
  deps: []
```

**`proto/save_record.yaml`**

```yaml
kind: proto_step
name: save_record
instruction: |
  Write the invoice record as JSON (2-space indent, trailing newline) into the destination directory,
  creating the directory if needed. Name the file after the record key with every character outside
  A-Z, a-z, 0-9, ".", "_" and "-" replaced by "_", plus ".json". Write atomically (temporary file in
  the same directory, then rename), replacing an existing file of the same name. Return the record
  unchanged and the file path.
inputs:
  record: { key: string, supplier: string, invoice_number: string, total: number, currency: string, due_date: date }
  dest: path
outputs:
  record: { key: string, supplier: string, invoice_number: string, total: number, currency: string, due_date: date }
  path: path
examples:
  - inputs:
      record: { key: acme-supplies-ltd__INV-1042, supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: 2026-10-01 }
      dest: "{tmp}/records"
    outputs:
      record: { key: acme-supplies-ltd__INV-1042, supplier: ACME Supplies Ltd, invoice_number: INV-1042, total: 1200.5, currency: GBP, due_date: 2026-10-01 }
      path: "{tmp}/records/acme-supplies-ltd__INV-1042.json"
    exit: done
  - inputs:
      record: { key: north-wind-traders__SUP/2026/0098, supplier: North Wind Traders, invoice_number: SUP/2026/0098, total: 312.4, currency: EUR, due_date: 2026-11-30 }
      dest: "{tmp}/nested/records"
    outputs:
      record: { key: north-wind-traders__SUP/2026/0098, supplier: North Wind Traders, invoice_number: SUP/2026/0098, total: 312.4, currency: EUR, due_date: 2026-11-30 }
      path: "{tmp}/nested/records/north-wind-traders__SUP_2026_0098.json"
    exit: done
env:
  deps: []
```

**`proto/escalate_to_human.yaml`**

```yaml
kind: proto_step
name: escalate_to_human
instruction: |
  Write an escalation ticket for a human reviewer into the queue directory, creating it if needed.
  Name it "<run_id>.json" with every character outside A-Z, a-z, 0-9, ".", "_" and "-" replaced by "_".
  The ticket is a JSON object with run_id, fields, errors, and reasons: the error messages, or when
  there are no errors the single reason "routed to review by the process; see `wynd trace <run_id>`".
  Write it with sorted keys, 2-space indent and a trailing newline, atomically. Return the ticket path.
inputs:
  fields: { supplier: string, invoice_number: string, total: number, currency: string, due_date: date }
  errors: [ { field: string, code: string, message: string, fixable: boolean } ]
  queue_dir: path
  run_id: string
outputs:
  ticket_path: path
examples:
  - inputs:
      fields: { supplier: Hooli Ltd, invoice_number: CN-311, total: -250.0, currency: GBP, due_date: 2026-10-18 }
      errors: [ { field: total, code: non_positive_total, message: "total -250.00 must be greater than zero", fixable: false } ]
      queue_dir: "{tmp}/escalations"
      run_id: run-test-1
    outputs: { ticket_path: "{tmp}/escalations/run-test-1.json" }
    exit: done
  - inputs:
      fields: { supplier: Umbrella Corporation, invoice_number: PF-88, total: 3400.0, currency: GBP, due_date: 2026-10-10 }
      errors: []
      queue_dir: "{tmp}/queue"
      run_id: run-test-2
    outputs: { ticket_path: "{tmp}/queue/run-test-2.json" }
    exit: done
env:
  deps: []
```

### 6.6 Hand-written step modules (M1)

Each step package has its own copy of the small data models. Steps are independent wheels, and explicit, verbose code is the spec's style (3.1).

**`steps/read_pdf/read_pdf.py`**

```python
"""Wynd step read_pdf (deterministic)."""
from typing import Literal

from pydantic import BaseModel
from pypdf import PdfReader

from wynd.runtime import DeterministicStep


class ReadPdf(DeterministicStep):
    """Read a PDF file and return its text and page count."""

    class Input(BaseModel):
        pdf_path: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        text: str
        pages: int

    def run(self, input: Input) -> Output:
        reader = PdfReader(input.pdf_path)
        texts = [(page.extract_text() or "").rstrip("\n") for page in reader.pages]
        return self.Output(text="\n".join(texts), pages=len(reader.pages))
```

**`steps/extract_invoice_fields/extract_invoice_fields.py`**

```python
"""Wynd step extract_invoice_fields (agentic)."""
from datetime import date
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import AgenticStep


class ExtractInvoiceFields(AgenticStep):
    """<exactly the proto instruction text, 6.5>"""

    class Input(BaseModel):
        invoice_text: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        supplier: str
        invoice_number: str
        total: float
        currency: str
        due_date: date

    class NotAnInvoice(BaseModel):
        exit: Literal["not_an_invoice"] = "not_an_invoice"

    Output = Done | NotAnInvoice

    def run(self, input: Input) -> Output: ...
```

**`steps/validate_fields/validate_fields.py`**

```python
"""Wynd step validate_fields (deterministic)."""
import re
from datetime import date
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep

SUPPORTED_CURRENCIES = frozenset({"AUD", "CAD", "CHF", "CNY", "CZK", "DKK", "EUR", "GBP", "HKD", "INR",
                                  "JPY", "NOK", "NZD", "PLN", "SEK", "SGD", "USD", "ZAR"})
INVOICE_NUMBER = re.compile(r"[A-Z0-9][A-Z0-9/-]{1,31}")
EARLIEST_DUE = date(2000, 1, 1)
LATEST_DUE = date(2100, 12, 31)
APPROVAL_LIMIT = 1_000_000


class InvoiceFields(BaseModel):
    supplier: str
    invoice_number: str
    total: float
    currency: str
    due_date: date


class FieldError(BaseModel):
    field: str
    code: str
    message: str
    fixable: bool


class InvoiceRecord(BaseModel):
    key: str
    supplier: str
    invoice_number: str
    total: float
    currency: str
    due_date: date


class ValidateFields(DeterministicStep):
    """Validate extracted invoice fields against the accounts-payable rules and normalise them into a record."""

    class Input(BaseModel):
        fields: InvoiceFields

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        valid: bool
        fixable: bool
        errors: list[FieldError]
        fields: InvoiceFields
        record: InvoiceRecord

    def run(self, input: Input) -> Output:
        fields = self._normalise(input.fields)
        errors = self._check(fields)
        return self.Output(
            valid=not errors,
            fixable=bool(errors) and all(e.fixable for e in errors),
            errors=errors,
            fields=fields,
            record=InvoiceRecord(key=self._key(fields), **fields.model_dump()),
        )

    def _normalise(self, f: InvoiceFields) -> InvoiceFields:
        return InvoiceFields(
            supplier=" ".join(f.supplier.split()),
            invoice_number=f.invoice_number.strip().upper(),
            total=round(f.total, 2),
            currency=f.currency.strip().upper(),
            due_date=f.due_date,
        )

    def _check(self, f: InvoiceFields) -> list[FieldError]:
        errors: list[FieldError] = []
        if not f.supplier:
            errors.append(FieldError(field="supplier", code="missing_supplier",
                                     message="supplier is empty", fixable=True))
        if not INVOICE_NUMBER.fullmatch(f.invoice_number):
            errors.append(FieldError(field="invoice_number", code="bad_invoice_number",
                                     message=f"invoice_number {f.invoice_number!r} must be 2-32 characters of A-Z, 0-9, '/' or '-'",
                                     fixable=True))
        if f.total <= 0:
            errors.append(FieldError(field="total", code="non_positive_total",
                                     message=f"total {f.total:.2f} must be greater than zero", fixable=False))
        elif f.total > APPROVAL_LIMIT:
            errors.append(FieldError(field="total", code="total_over_limit",
                                     message=f"total {f.total:.2f} exceeds the 1,000,000 approval limit", fixable=False))
        if f.currency not in SUPPORTED_CURRENCIES:
            errors.append(FieldError(field="currency", code="unsupported_currency",
                                     message=f"currency {f.currency!r} is not a supported ISO 4217 code", fixable=True))
        if not EARLIEST_DUE <= f.due_date <= LATEST_DUE:
            errors.append(FieldError(field="due_date", code="due_date_out_of_range",
                                     message=f"due_date {f.due_date.isoformat()} is outside 2000-01-01..2100-12-31",
                                     fixable=True))
        return errors

    def _key(self, f: InvoiceFields) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", f.supplier.lower()).strip("-")
        return f"{slug}__{f.invoice_number}"
```

**`steps/fix_fields/fix_fields.py`** repeats the `InvoiceFields` and `FieldError` models, then:

```python
class FixFields(AgenticStep):
    """<exactly the proto instruction text>"""

    class Input(BaseModel):
        invoice_text: str
        fields: InvoiceFields
        errors: list[FieldError]

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        supplier: str
        invoice_number: str
        total: float
        currency: str
        due_date: date

    def run(self, input: Input) -> Output: ...
```

**`steps/save_record/save_record.py`** repeats `InvoiceRecord`, then:

```python
class SaveRecord(DeterministicStep):
    """Write an invoice record as JSON into the destination directory, named after its key."""

    class Input(BaseModel):
        record: InvoiceRecord
        dest: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        record: InvoiceRecord
        path: str

    def run(self, input: Input) -> Output:
        dest = Path(input.dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / (re.sub(r"[^A-Za-z0-9._-]", "_", input.record.key) + ".json")
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(input.record.model_dump_json(indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return self.Output(record=input.record, path=str(path))
```

**`steps/escalate_to_human/escalate_to_human.py`** repeats `InvoiceFields` and `FieldError`, then:

```python
class EscalateToHuman(DeterministicStep):
    """Write an escalation ticket for a human reviewer into the queue directory."""

    class Input(BaseModel):
        fields: InvoiceFields
        errors: list[FieldError]
        queue_dir: str
        run_id: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        ticket_path: str

    def run(self, input: Input) -> Output:
        queue = Path(input.queue_dir)
        queue.mkdir(parents=True, exist_ok=True)
        path = queue / (re.sub(r"[^A-Za-z0-9._-]", "_", input.run_id) + ".json")
        ticket = {
            "run_id": input.run_id,
            "fields": input.fields.model_dump(mode="json"),
            "errors": [e.model_dump() for e in input.errors],
            "reasons": [e.message for e in input.errors]
                       or [f"routed to review by the process; see `wynd trace {input.run_id}`"],
        }
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(ticket, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)
        return self.Output(ticket_path=str(path))
```

### 6.7 `pyproject.toml` and `step.lock.yaml` per step

`steps/read_pdf/pyproject.toml` is the template for all six; only the name, dependencies and module change:

```toml
[project]
name = "process-supplier-invoice-read-pdf"
version = "0.1.0"
description = "Wynd step read_pdf (process_supplier_invoice)"
requires-python = ">=3.12"
license = "Apache-2.0"
dependencies = ["pypdf>=6,<7"]
# wynd-spec / wynd-runtime are not listed: the builder installs them into every venv (spec 4).

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
only-include = ["read_pdf.py"]
```

`dependencies` is `["pypdf>=6,<7"]` for read_pdf and `[]` for the other five. The `claude-agent-sdk` dependency of the agentic steps arrives through the claude-code **provider's** env fragment, never through the step.

`step.lock.yaml` has the shape assumed here (C1; conform to the spec owner's `StepLock`). For read_pdf:

```yaml
version: 1
proto_hash: sha256:<wynd.spec proto_hash of proto/read_pdf.yaml, computed with the real tool>
kind: deterministic
entry: read_pdf:ReadPdf
effects: []
tools: []
mcp: []
env_vars: []
env: { deps: ["pypdf>=6,<7"], system: [], requires: null }
lock: ["pypdf==6.19.0"]
retries: 0
```

For extract_invoice_fields (fix_fields is identical apart from the names):

```yaml
version: 1
proto_hash: sha256:<...>
kind: agentic
entry: extract_invoice_fields:ExtractInvoiceFields
provider: null            # process default: claude-code
tier: cheap
thinking: low
retries: 2
effects: []
tools: []
mcp: []
env_vars: []
env: { deps: [], system: [], requires: null }
lock: []
```

validate_fields matches read_pdf with `lock: []`. save_record and escalate_to_human have `effects: [filesystem]`.

The proto-hash values **must** come from the real `proto_hash` function once `wynd.spec` exists. Then M3's compiler sees matching hashes and treats the hand-written packages as the committed baseline (6.5).

### 6.8 Generated-style tests, and how cassettes are recorded

Each `test_<step>.py` has one test per proto example, numbered in example order. Additional hand-confirmed edge cases carry a descriptive name. The tests use the runtime helper (C8), so the full middleware chain runs.

```python
# steps/extract_invoice_fields/test_extract_invoice_fields.py
"""Tests for extract_invoice_fields: one per proto-step example. Replays cassettes/ by default."""
from wynd.runtime.testing import run_step

from extract_invoice_fields import ExtractInvoiceFields

ACME_TEXT = """ACME Supplies Ltd
12 Foundry Lane, Sheffield S1 2AB, United Kingdom
...
Payment terms: 30 days. Pay by bank transfer to sort code 00-00-00, account 12345678."""


def test_example_1():
    r = run_step(ExtractInvoiceFields, {"invoice_text": ACME_TEXT})
    assert r.exit == "done"
    assert r.output.model_dump(mode="json", exclude={"exit"}) == {
        "supplier": "ACME Supplies Ltd", "invoice_number": "INV-1042", "total": 1200.5,
        "currency": "GBP", "due_date": "2026-10-01"}


def test_example_5():
    r = run_step(ExtractInvoiceFields, {"invoice_text": SHIPPING_TEXT})
    assert r.exit == "not_an_invoice"
```

- **Paths.** `test_read_pdf.py` resolves fixtures as `Path(__file__).resolve().parents[2] / "examples" / "acme_inv_1042.pdf"`, following the convention that paths are relative to the process dir. `test_example_3` asserts `r.exit == "error"`.
- **`{tmp}` examples** use pytest's `tmp_path`. `test_escalate_to_human.py` also has a hand-confirmed edge case, `test_ticket_contents`, which asserts the exact JSON written.
- **Process tests** use the process testing helper (C13):

```python
# processes/process_supplier_invoice/tests/test_process.py
from pathlib import Path
from wynd.process.testing import run_process

PROCESS_DIR = Path(__file__).resolve().parents[1]

def _env(tmp: Path) -> dict[str, str]:
    return {"RECORDS_DIR": str(tmp / "records"), "REVIEW_DIR": str(tmp / "review"),
            "ESCALATIONS_DIR": str(tmp / "escalations")}

def test_example_1(tmp_path):
    r = run_process(PROCESS_DIR, {"pdf_path": str(PROCESS_DIR / "examples/acme_inv_1042.pdf")},
                    env=_env(tmp_path), run_id="run-example-1")
    assert r.exit == "done"
    assert r.outputs == {"record": {"key": "acme-supplies-ltd__INV-1042", "supplier": "ACME Supplies Ltd",
                                    "invoice_number": "INV-1042", "total": 1200.5, "currency": "GBP",
                                    "due_date": "2026-10-01"}}
# test_example_2 .. test_example_6 likewise (6 is M5)
```

`test_side_effects.py` checks where things land:

| Example | Expected side effect |
|---|---|
| 1 | `records/acme-supplies-ltd__INV-1042.json` exists and `review/` does not |
| 2 | `review/globex-corporation__GX-77810.json` exists |
| 3 | the trace contains `fix` exactly once |
| 5 | `escalations/run-example-5.json` holds the `non_positive_total` reason |
| 6 (M5) | the trace has an `edge.check` event with `take: false` for `validate.done[save]` and a generic-reason ticket |

**Recording procedure** (the implementer, once; there is no bespoke script):
1. Authenticate Claude Code: a logged-in `claude` CLI on the subscription, or export `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`.
2. Run `cd examples/invoices && uv run wynd test process_supplier_invoice --live`. This is a test_live job; its output is a commit on a `wynd/…` branch holding step cassettes for extract/fix and the process-level cassettes, which the CLI integrates.
3. Run `uv run wynd test process_supplier_invoice` (replay). It must be green with no credentials: `env -u CLAUDE_CODE_OAUTH_TOKEN`, and with `HOME` pointed at an empty directory to prove the replay path never touches the CLI login.
4. If a live answer disagrees with an example, fix the **instruction or the example** (the normal clarification loop) and re-record. Never hand-edit a cassette.
5. Confirm with `git lfs ls-files` that every file under `cassettes/` is LFS-tracked.

### 6.9 Sample PDFs: `tools/make_sample_pdfs.py` (stdlib only; prototyped and verified with pypdf 6.19.0 strict mode)

```python
"""Generate the sample invoice PDFs byte-for-byte deterministically (stdlib only).

Run from examples/invoices:  uv run python tools/make_sample_pdfs.py
Writes processes/process_supplier_invoice/examples/*.pdf. tools/test_make_sample_pdfs.py fails if a
committed PDF or a proto-step example text drifts from SAMPLES.
Constraints: no blank lines (pypdf drops them); characters must be cp1252 (WinAnsiEncoding).
"""
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "processes" / "process_supplier_invoice" / "examples"

SAMPLES: dict[str, list[str]] = {
    "acme_inv_1042.pdf": [
        "ACME Supplies Ltd",
        "12 Foundry Lane, Sheffield S1 2AB, United Kingdom",
        "VAT registration: GB123456789",
        "INVOICE",
        "Invoice number: INV-1042",
        "Invoice date: 1 September 2026",
        "Due date: 1 October 2026",
        "Bill to: Wynd Test Ltd, 1 Example Street, London EC1A 1AA",
        "Steel brackets (box of 100)      4 x £150.00      £600.00",
        "Galvanised bolts M8 (box of 500)      2 x £200.25      £400.50",
        "Delivery      £200.00",
        "Total due: £1,200.50",
        "Payment terms: 30 days. Pay by bank transfer to sort code 00-00-00, account 12345678.",
    ],
    "globex_inv_77810.pdf": [
        "Globex Corporation",
        "500 Market Street, Springfield, OR 97477, USA",
        "INVOICE",
        "Invoice No: GX-77810",
        "Invoice date: 15 September 2026",
        "Payment due: 15 November 2026",
        "Bill to: Wynd Test Ltd",
        "Consulting services, September 2026: 95 hours at USD 150.00      USD 14,250.00",
        "Amount due (USD): 14,250.00",
    ],
    "initech_inv_5521.pdf": [
        "Initech Canada Inc.",
        "200 King Street West, Toronto, ON M5H 3T4, Canada",
        "GST/HST registration: 123456789 RT0001",
        "INVOICE",
        "Invoice #: IC-5521",
        "Issued: 20 September 2026",
        "Due: 20 October 2026",
        "Bill to: Wynd Test Ltd",
        "TPS report cover sheets (1,000)      $780.00",
        "Printer maintenance      $200.00",
        "Total: $980.00",
    ],
    "shipping_notice.pdf": [
        "Globex Corporation",
        "Shipping notification",
        "Dear customer, your order GX-ORD-3321 has shipped.",
        "Carrier: Example Parcel Co.",
        "Tracking number: EP123456789GB",
        "Expected delivery: 3 October 2026",
        "This is not an invoice. No payment is required.",
    ],
    "hooli_credit_note_311.pdf": [
        "Hooli Ltd",
        "1 Silicon Way, Cambridge CB1 1AA, United Kingdom",
        "CREDIT NOTE",
        "Credit note number: CN-311",
        "Relates to invoice: HL-2291",
        "Date: 18 September 2026",
        "Refund due by: 18 October 2026",
        "Returned: 2 x Nucleus compression licence      -£250.00",
        "Total: -£250.00",
    ],
    "umbrella_proforma_88.pdf": [                      # (M5)
        "Umbrella Corporation",
        "Raccoon Business Park, Unit 4, Leeds LS1 4AP, United Kingdom",
        "PRO FORMA INVOICE",
        "Pro forma number: PF-88",
        "Date: 10 September 2026",
        "Valid until: 10 October 2026",
        "Bill to: Wynd Test Ltd",
        "Protective suits (quotation)      20 x £170.00      £3,400.00",
        "Total: £3,400.00",
        "This pro forma is not a request for payment. A final invoice will be issued on delivery.",
    ],
}


def text_of(name: str) -> str:
    """The exact text read_pdf returns for a sample."""
    return "\n".join(SAMPLES[name])


def _escape(line: str) -> str:
    return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def render_pdf(lines: list[str]) -> bytes:
    for line in lines:
        if not line.strip():
            raise ValueError("blank lines are dropped by text extraction; remove them")
        line.encode("cp1252")                         # raises for characters outside WinAnsiEncoding
    ops = ["BT", "/F1 10 Tf", "13 TL", "56 780 Td", *(f"({_escape(l)}) Tj T*" for l in lines), "ET"]
    content = "\n".join(ops).encode("cp1252")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
        b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>",
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % number + body + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    out += b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, xref)
    return bytes(out)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, lines in SAMPLES.items():
        (OUT / name).write_bytes(render_pdf(lines))


if __name__ == "__main__":
    main()
```

`tools/test_make_sample_pdfs.py` runs in the root dev environment, where pyyaml comes via wynd-spec. It has three tests:
1. Each committed PDF equals `render_pdf(SAMPLES[name])` byte for byte.
2. Every proto example whose `pdf_path` or `invoice_text`/`text` refers to a sample equals `text_of(name)`. It parses `proto/*.yaml` and `process.yaml` and maps `examples/<name>` to its sample.
3. `render_pdf` rejects blank lines and non-cp1252 characters.

The M1 implementer commits the output of `make_sample_pdfs.py`, and the M5 implementer adds the umbrella entry. PDFs are tracked normally (≈1 KB each) and marked `binary`.

### 6.10 Acceptance tests owned by the sample

- **M1:**
  - `uv run wynd validate process_supplier_invoice` is clean (no warnings).
  - `uv run wynd test process_supplier_invoice` passes offline (step tests and the process tests for examples 1–5).
  - `uv run wynd run process_supplier_invoice --local --input pdf_path=processes/process_supplier_invoice/examples/acme_inv_1042.pdf` with `.env` set runs live and exits `done`. The CLI absolutises `path` inputs (C16).
- **M2:** the docker-marked test in the M2 area runs example 1 through the image with the run API's file input and asserts a trace equal to local (modulo `mode` and timings).
- **M3:** `examples/invoices/tests/test_compile_from_proto.py` (`@live`):
  1. Copy `examples/invoices` into a temporary git repo, `git rm -r processes/process_supplier_invoice/steps` and commit.
  2. Run `wynd validate`, then `wynd compile process_supplier_invoice` (in-process runner; wait; integrate).
  3. Assert six packages with kinds read/validate/save/escalate = deterministic and extract/fix = agentic.
  4. `wynd test process_supplier_invoice` passes in replay, using the compile job's recorded step and process cassettes (C14).
- **M5:**
  - Example 6 passes in replay after `wynd compile`, which writes `edges.lock.yaml`, and `wynd test --live` (re-records the process cassettes).
  - `test_optimise_live.py` (`@live`) as in 3.9.

---

## 7. Docs (A8-docs; `cluster-install.md` by A8-kube)

Each file is Markdown; relative links are checked by `tests/test_repo_hygiene.py`.

| File | Contents (outline) |
|---|---|
| `README.md` (rewrite) | See 7.1 |
| `docs/architecture.md` | See 7.2 |
| `docs/run-api.md` | See 7.3 |
| `docs/cluster-install.md` | See 7.4 |
| `docs/providers.md` | See 7.5 |
| `docs/optimise.md` | What optimise reads (non-replayed calls in the TraceSink); the rule table from 3.4, verbatim; the report fields; the `--apply` job flow and quality gate; latency warnings and thresholds; the note on subscription cost estimates |
| `docs/agentic-edges.md` | Syntax (`kind`, `check`, `context`, shorthand); semantics (when → check → take; negative falls through; failure goes to the error handler); `edges.lock.yaml`; validator codes; cost and latency; testing and cassettes; a worked example from the sample |
| `docs/testing.md` | Test layers; the opt-in markers `live`/`docker`/`kube` and their env flags; offline by default; replay vs `--live`; how cassettes are recorded and promoted; git-lfs (8.2); goldens (`WYND_UPDATE_GOLDEN=1`); what CI runs (9) |
| `LICENSE` | Root notice (8.4) |

### 7.1 `README.md`

1. **Title and one-paragraph pitch**, the spec's opening quote.
2. **Why Wynd**: compile once, run cheap; deterministic first; examples become tests; git-native.
3. **Status**: the milestones checklist M1–M5 plus Kubernetes.
4. **Quickstart (laptop)**.
   - Prerequisites: uv, git, git-lfs (recommended), the Claude Code CLI logged in (or `CLAUDE_CODE_OAUTH_TOKEN`), and Docker for image mode only.
   - Commands:

```sh
git clone https://github.com/peterddod/wynd && cd wynd && git lfs pull
uv sync --all-packages
cd examples/invoices && cp .env.example .env
uv run wynd validate process_supplier_invoice
uv run wynd test process_supplier_invoice                  # offline: replays recorded model calls
uv run wynd run process_supplier_invoice --local \
  --input pdf_path=processes/process_supplier_invoice/examples/acme_inv_1042.pdf   # live: uses your Claude subscription
uv run wynd trace <run_id>
uv run wynd build process_supplier_invoice && uv run wynd run process_supplier_invoice --image
uv run wynd optimise process_supplier_invoice
uv run wynd serve-api                                      # web UI
```

5. **Concepts in 60 seconds**: step and exits, edges and `to:` branches, proto-step → compiled step, Design → Compile → Build, jobs vs runs.
6. **Repository layout**: a table of the packages with their licences and the dependency direction.
7. **LLM access**: the default provider is `claude-code`; the three ways to authenticate; a link to `docs/providers.md`.
8. **Deploying to Kubernetes**: a link to `docs/cluster-install.md`.
9. **Prior art** (spec 16, cited rather than claimed as novel). Each entry gets its arXiv id and link:
   - *Compiled AI* (arXiv 2604.05150; github.com/XY-Corp/CompiledAI)
   - *PlanCompiler* (arXiv 2604.13092)
   - *LLM-as-Code* (arXiv 2606.15874, KDD 2026 AgenticSE workshop)
   - *llm-compiler* (github.com/LiboWorks/llm-compiler)
   - *NOOA* (NVIDIA Labs, July 2026)
   - adjacent products: LangGraph, n8n, Gumloop, Lindy, Relevance; Temporal, Prefect, Inngest

   The section ends with the "What Wynd combines" list (1–5) from spec 16.
10. **Licences**: the split, as in 8.4. **Telemetry**: none is collected; any future telemetry will be opt-in and off by default (spec 15).
11. **Docs index**.

### 7.2 `docs/architecture.md`

- The package map and dependency direction `spec ← runtime ← process ← compiler ← controller ← {cli, web}`, plus `kube → controller`, as an ASCII diagram.
- The in-image set is exactly `spec + runtime`.
- Phases and gates.
- Jobs vs runs, and where each JobRunner lives.
- The execution path: CLI → controller → JobRunner / executor → venv workers (JSON-RPC over stdio) → supervisor run API.
- **Backends are selected by env var**, one table covering the storage backends (C11 names), `WYND_JOB_RUNNER`, `WYND_TRIGGER_BACKEND`, `WYND_SERVING_BACKEND`, `WYND_KUBE_*` and `WYND_GIT_REMOTE`. It restates the no-environment-branches rule (spec 15).
- The trace event catalogue: the `type`s and the key fields, from C3 and 3.2.
- A table mapping spec sections to modules, for navigation.

### 7.3 `docs/run-api.md` (content from the M2 schema, C10)

- Base URL and auth (none in-image; network policy is the boundary).
- The route table: at minimum `POST /runs`, `GET /runs/{run_id}`, `GET /runs/{run_id}/events` (SSE), `GET /runs/{run_id}/outputs`, `GET /healthz`, `GET /readyz`, plus file inputs for `path`-typed inputs.
- JSON request and response examples. SSE frames: `event: <trace type>`, `data: <trace event JSON>`.
- The error envelope, the run statuses, and the versioning rule (additive only).
- `tests/test_repo_hygiene.py::test_run_api_doc_covers_routes` asserts that every `(method, path)` in `wynd.runtime.supervisor.api.ROUTES` appears in this file.

### 7.4 `docs/cluster-install.md` (A8-kube)

1. **What you get**: a controller Deployment, Kubernetes Jobs for compile, test_live, build and optimise, one Deployment per Release, CronJob triggers, and a webhook Service. Requires Kubernetes ≥ 1.30.
2. **Prerequisites**:
   - an image registry;
   - a git remote holding the workspace, with an HTTPS token that has push access;
   - an RWX StorageClass (or a single node);
   - Claude credentials, where `claude setup-token` gives the subscription "dev key", or an Anthropic API key.
3. **Build and push the wynd image**: `docker buildx build -f deploy/wynd.Dockerfile --platform linux/amd64,linux/arm64 -t REG/wynd:VER --push .`. Then publish the base family with `uv run wynd base build VER && uv run wynd base publish VER`.
4. **Namespace and Secrets**, each with an exact `kubectl create secret …` command: `wynd-git`, `wynd-model-credentials`, `wynd-registry` (docker-registry type) and `wynd-api-token`.
5. **Install**: `wynd-kube render install --image … --git-url … [--workspace-subdir …] > install.yaml && kubectl apply -f install.yaml`.
6. **Seed the user registries inside the controller**:
   - `kubectl -n wynd exec deploy/wynd-controller -- wynd registry add default REG/wynd`
   - optionally, `wynd provider add claude-code --tier cheap=haiku …`
7. **Reach the UI and API**: `kubectl port-forward svc/wynd-controller 8000`, or an Ingress. Expose webhooks through an Ingress on `svc/wynd-webhooks` with the prefix `/hooks/` only (example manifest).
8. **First build**, from the web UI Build button or the API.
9. **Release a process**:
   1. `wynd-kube env-template --process … --manifest …`, fill the values, `kubectl apply`.
   2. Create the Release in the UI; the controller applies the Deployment, Service and ConfigMap.
   3. `wynd env check` runs as the `env-check` init container. A failed check shows as `Init:Error`, diagnosed with `kubectl logs <pod> -c env-check`.
10. **Triggers**: schedule becomes a CronJob, webhook goes to `/hooks/{release}`, manual goes through the API.
11. **Storage and backups**: the `wynd-state` layout (`registry`, `traces`, `build`, `home`, `repo`) and what to back up.
12. **Security notes**:
    - The controller Role has no `secrets` access.
    - Job and process pods get no ServiceAccount token.
    - The buildkit init container needs Unconfined seccomp/AppArmor, so the namespace must allow it (Pod Security `privileged`; see R6).
    - Model credentials are mounted only into compile, test_live and optimise jobs and into the controller.
13. **GitOps**: `wynd-kube render release …` renders the Release manifests instead of letting the controller apply them.
14. **Troubleshooting**: fetch-by-sha fallback, LFS pull failures, registry auth, RWX.
15. **How this is tested**: goldens, fake-client tests, and the manual kind e2e job in CI (9).

### 7.5 `docs/providers.md`

- **Providers:** `claude-code` (AgentProvider, the default) and `anthropic` (ModelProvider). Others plug in through the `wynd.providers` entry points.
- **claude-code authentication**, three options:
  1. **Local**: a logged-in `claude` CLI (subscription). Nothing to configure.
  2. **Containers/CI**: run `claude setup-token` once on a logged-in machine, then set the printed token as `CLAUDE_CODE_OAUTH_TOKEN` (a Secret in Kubernetes, a repository secret in CI).
  3. **API billing**: `ANTHROPIC_API_KEY`.

  Advice: set exactly one of the two env vars. Both appear in `process.env.yaml` as secret, optional vars. `wynd env check` passes without them locally, and the provider fails at the first call with a clear error if it has no auth.
- **Tiers.** Defaults: `claude-code`: cheap → `haiku`, standard → `sonnet`, strong → `opus` (Claude Code aliases). Change them with `wynd provider add claude-code --tier cheap=<model> …` (C16 syntax). Changing a mapping changes cassette keys: replays then fail loudly until you re-record with `wynd test --live`.
- **anthropic**: `ANTHROPIC_API_KEY`, with tier defaults as shown by `wynd provider list`. The doc never hand-copies model ids.
- **Cost reporting**: usage is recorded per call. On a subscription, `cost_usd` is Claude Code's API-equivalent estimate.
- Cassettes never contain credentials.

---

## 8. Repo infrastructure (A8-docs)

### 8.1 `.gitignore` (root)

```gitignore
# Wynd state: venvs, build artefacts, registries, job worktrees (spec 5.1)
.wynd/
# Python
__pycache__/
*.py[cod]
.venv/
*.egg-info/
.pytest_cache/
packages/*/dist/
examples/**/dist/
# Web
node_modules/
packages/web/dist/
# Env files hold secrets; the example is committed
.env
.env.*
!.env.example
# OS / editors
.DS_Store
```

A bare `build/` pattern is deliberately absent: it would ignore source modules named `build` (for example a builder package).

### 8.2 `.gitattributes` and git-lfs policy

```gitattributes
# Recorded model calls are tracked with git-lfs (spec 6.5)
**/cassettes/** filter=lfs diff=lfs merge=lfs -text
# Sample fixtures
*.pdf binary
```

The policy: git-lfs is **recommended, not required**. It is installed on the dev machine (3.8.0) and enabled in CI.

- **Clone without git-lfs.** Cassettes arrive as pointer files. The runtime cassette loader (C7) detects the pointer header `version https://git-lfs.github.com/spec/v1` and fails with: `"cassette <path> is a git-lfs pointer, not a recording: install git-lfs (https://git-lfs.com) and run 'git lfs pull'"`. Replay never passes silently.
- **Commit without git-lfs.** Cassettes are stored as ordinary blobs, and everything still works. For this repository, `tests/test_repo_hygiene.py::test_cassettes_are_lfs_pointers` fails CI when a cassette blob in the index is not a pointer.
- **Job worktrees** share the repository's LFS object store. The toolchain image runs `git lfs install --system`.
- **The compile job** warns when git-lfs is unavailable while `.gitattributes` requests it (C15; one `git lfs version` probe). `wynd init` writes the same `.gitattributes` into new workspaces (C16).

### 8.3 Root pytest config (root `pyproject.toml` owner adds this) and root `conftest.py` (A8-docs)

```toml
[tool.pytest.ini_options]
addopts = "-ra --import-mode=importlib"
testpaths = [
  "packages/spec/tests", "packages/runtime/tests", "packages/process/tests", "packages/compiler/tests",
  "packages/controller/tests", "packages/cli/tests", "packages/kube/tests",
  "tests", "examples/invoices/tools", "examples/invoices/tests",
]
norecursedirs = ["steps", "node_modules", ".wynd", ".venv"]
```

Step tests and process-level tests of the sample run under `wynd test`: step tests need the step venvs. They are not in the root `testpaths`.

```python
# conftest.py (repo root). Imports only stdlib + pytest: it is also loaded when `wynd test` runs pytest in step venvs.
import os

import pytest

OPT_IN = {"live": "WYND_LIVE", "docker": "WYND_DOCKER", "kube": "WYND_KUBE"}


def pytest_configure(config):
    for mark, env in OPT_IN.items():
        config.addinivalue_line("markers", f"{mark}: opt-in; runs only with {env}=1")


def pytest_collection_modifyitems(config, items):
    for item in items:
        for mark, env in OPT_IN.items():
            if item.get_closest_marker(mark) and os.environ.get(env) != "1":
                item.add_marker(pytest.mark.skip(reason=f"{mark} test: set {env}=1 to run"))
```

### 8.4 Licences

```
LICENSE                              # notice (below)
LICENSES/Apache-2.0.txt              # verbatim from https://www.apache.org/licenses/LICENSE-2.0.txt
LICENSES/AGPL-3.0-or-later.txt       # verbatim from https://www.gnu.org/licenses/agpl-3.0.txt
packages/spec/LICENSE                # copy of Apache-2.0.txt
packages/runtime/LICENSE             # copy of Apache-2.0.txt
packages/{process,compiler,controller,cli,web,kube}/LICENSE   # copies of AGPL-3.0-or-later.txt
examples/invoices/LICENSE            # copy of Apache-2.0.txt
```

The texts are **downloaded with curl, never retyped**. The root `LICENSE` reads:

```
Wynd is open source. Licences are per directory:

  packages/spec, packages/runtime, examples/    Apache License 2.0            (LICENSES/Apache-2.0.txt)
  everything else, including packages/process,
  compiler, controller, cli, web, kube, docs,
  deploy and CI configuration                   GNU AGPL v3.0 or later        (LICENSES/AGPL-3.0-or-later.txt)

Each package directory carries its own LICENSE file, which is what its distribution ships.
```

Package metadata:
- `pyproject.toml`: `license = "Apache-2.0"` or `"AGPL-3.0-or-later"` (PEP 639) and `license-files = ["LICENSE"]` (hatchling ≥ 1.27).
- `packages/web/package.json`: `"license": "AGPL-3.0-or-later"`.

### 8.5 `tests/test_repo_hygiene.py` (A8-docs)

| Test | What it checks |
|---|---|
| `test_cassettes_are_lfs_pointers` | Runs `git ls-files -z` for paths containing `/cassettes/`. Each index blob (`git cat-file blob :<path>`) must start with the LFS pointer header. Skipped outside a git checkout. |
| `test_licence_files` | Each package `LICENSE` is byte-identical to the matching `LICENSES/*.txt`. Each `pyproject.toml` `license` matches the table. The web `package.json` licence is correct. |
| `test_namespace_package` | No `packages/*/src/wynd/__init__.py` exists. |
| `test_gitignore` | `git check-ignore` succeeds for `examples/invoices/.wynd/x` and `examples/invoices/.env`, and fails for `examples/invoices/.env.example`. |
| `test_docs_links` | Every relative link in `README.md` and `docs/*.md` resolves. |
| `test_run_api_doc_covers_routes` | As in 7.3. |

---

## 9. CI: `.github/workflows/ci.yml` (A8-docs; the kind script by A8-kube)

```yaml
name: ci

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:
    inputs:
      docker: { description: "Run Docker tests (image build + serve)", type: boolean, default: false }
      kube:   { description: "Run the kind cluster e2e", type: boolean, default: false }

permissions:
  contents: read

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  python:
    name: python (offline)
    runs-on: ubuntu-24.04
    timeout-minutes: 30
    steps:
      - uses: actions/checkout@v5
        with: { lfs: true }
      - uses: astral-sh/setup-uv@v6
        with: { version: "0.10.7", enable-cache: true }
      - name: Install Python 3.12 and the workspace
        run: |
          uv python install 3.12
          uv sync --locked --all-packages
      - name: Git identity and an empty WYND_HOME (built-in provider defaults)
        run: |
          git config --global user.name "wynd-ci"
          git config --global user.email "ci@wynd.invalid"
          git config --global init.defaultBranch main
          echo "WYND_HOME=$RUNNER_TEMP/wynd-home" >> "$GITHUB_ENV"
      - name: Package, repo and sample-tool tests
        run: uv run pytest -q
      - name: Sample workspace (validate, env check, replay tests)
        working-directory: examples/invoices
        env:
          RECORDS_DIR: ${{ runner.temp }}/invoices/records
          REVIEW_DIR: ${{ runner.temp }}/invoices/review
          ESCALATIONS_DIR: ${{ runner.temp }}/invoices/escalations
        run: |
          uv run wynd validate process_supplier_invoice
          uv run wynd env check process_supplier_invoice
          uv run wynd test process_supplier_invoice

  web:
    name: web
    runs-on: ubuntu-24.04
    timeout-minutes: 15
    defaults:
      run: { working-directory: packages/web }
    steps:
      - uses: actions/checkout@v5
      - uses: actions/setup-node@v5
        with: { node-version: "26", cache: npm, cache-dependency-path: packages/web/package-lock.json }
      - run: npm ci
      - run: npm run typecheck
      - run: npm test
      - run: npm run build

  docker:
    name: docker (image build + serve)
    needs: python
    if: >-
      github.event_name == 'push' ||
      (github.event_name == 'workflow_dispatch' && inputs.docker) ||
      (github.event_name == 'pull_request' && contains(github.event.pull_request.labels.*.name, 'ci:docker'))
    runs-on: ubuntu-24.04
    timeout-minutes: 45
    services:
      registry:
        image: registry:2
        ports: ["5000:5000"]
    steps:
      - uses: actions/checkout@v5
        with: { lfs: true }
      - uses: docker/setup-buildx-action@v3
        with: { driver-opts: network=host }
      - uses: astral-sh/setup-uv@v6
        with: { version: "0.10.7", enable-cache: true }
      - run: |
          uv python install 3.12
          uv sync --locked --all-packages
          git config --global user.name "wynd-ci" && git config --global user.email "ci@wynd.invalid"
          echo "WYND_HOME=$RUNNER_TEMP/wynd-home" >> "$GITHUB_ENV"
      - name: Docker-marked tests
        env: { WYND_DOCKER: "1", WYND_TEST_REGISTRY: "localhost:5000" }
        run: uv run pytest -q -m docker

  kube:
    name: kube (kind e2e)
    needs: python
    if: github.event_name == 'workflow_dispatch' && inputs.kube
    runs-on: ubuntu-24.04
    timeout-minutes: 60
    steps:
      - uses: actions/checkout@v5
        with: { lfs: true }
      - uses: astral-sh/setup-uv@v6
        with: { version: "0.10.7", enable-cache: true }
      - name: kind cluster with a local registry
        run: bash deploy/ci/kind-with-registry.sh
      - name: Build and push the wynd image
        run: |
          docker build -f deploy/wynd.Dockerfile -t localhost:5001/wynd:ci .
          docker push localhost:5001/wynd:ci
      - name: Install workspace and publish base images
        run: |
          uv python install 3.12
          uv sync --locked --all-packages
          uv run wynd registry add default localhost:5001
          uv run wynd base build ci && uv run wynd base publish ci
      - name: e2e
        env:
          WYND_KUBE: "1"
          WYND_KUBE_E2E_IMAGE: kind-registry:5000/wynd:ci
          WYND_KUBE_E2E_REGISTRY: kind-registry:5000
          WYND_KUBE_E2E_GIT_URL: https://github.com/${{ github.repository }}.git
          WYND_KUBE_E2E_REF: ${{ github.sha }}
          WYND_LIVE: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN != '' && '1' || '0' }}
          CLAUDE_CODE_OAUTH_TOKEN: ${{ secrets.CLAUDE_CODE_OAUTH_TOKEN }}
        run: uv run pytest -q -m kube
```

Notes:
- **Action versions.** Pin to the current majors at implementation time (checkout v5, setup-node v5, setup-uv v6, setup-buildx v3). The uv version matches the dev machine (0.10.7).
- **Offline** means no LLM calls and no Docker. Building step venvs still downloads wheels from PyPI.
- **`WYND_HOME` is empty**, so provider tier maps fall back to the built-in defaults (C7). Cassettes recorded with the defaults therefore replay.
- **Web scripts.** The `web` job requires `typecheck`, `test` and `build` scripts in `packages/web/package.json` (C17).
- **`deploy/ci/kind-with-registry.sh`** (A8-kube) does three things:
  1. It downloads a pinned `kind` release.
  2. It creates the cluster with containerd `hosts.toml` entries for `localhost:5001` and `kind-registry:5000`, both mapped to `http://kind-registry:5000`.
  3. It starts the `registry:2` container `kind-registry` on the `kind` network. This follows kind's documented local-registry recipe.
- **The e2e test seeds the in-cluster user registry** with `kind-registry:5000` through `kubectl exec … wynd registry add`.

---

## 10. Interfaces CONSUMED (to reconcile; the owner is the likely area)

| Id | Owner | Exactly what this area needs |
|---|---|---|
| **C1** | spec | See the C1 list below |
| **C2** | spec (expressions) | `run.id`, `env.X`, nested `steps.x.outputs.a.b`, `if … then … else`, and `edges[...]` counters, all unchanged. No new expression features are needed. |
| **C3** | runtime (trace) | Trace JSONL events with at least the fields in 3.2 (`step.end`, `model.call`, `run.end`), with `step_run`, `attempts`, `validation_failures`, `error_cause`, `provider`, `tier`, `model_id`, `usage{calls, input_tokens, output_tokens, cost_usd, latency_ms}` and `replayed`. `edge.taken` gains `verdicts` for agentic edges. |
| **C4** | runtime (agentic) | See the C4 list below |
| **C5** | runtime (worker) | JSON-RPC method registration (`edge.check` → `wynd.runtime.edges.handle_edge_check(params, ctx)`), and the `ctx` object that step-run requests already carry (`WorkerCallContext`: run id, workspace, cassette mode/dir, trace destination). |
| **C6** | runtime (executor) | The branch loop in 4.2 with an injected `EdgeChecker` (constructed from `WorkerEdgeChecker` in both local and image mode); `ProcessError.cause` accepts `"edge_check"`; a traversal-limit check comes before the check call. |
| **C7** | runtime (cassettes/providers) | See the C7 list below |
| **C8** | runtime (testing) | `wynd.runtime.testing.run_step(step_cls, input: dict) -> StepResult(exit: str, output: BaseModel)`. It runs the full middleware chain, uses `<module dir>/cassettes/` and replays by default. |
| **C9** | runtime (providers) | See the C9 list below |
| **C10** | runtime (supervisor, M2) | The in-image script `wynd-supervisor` with `serve --host --port` and `env-check` (reads the manifest baked into the image; exit 0/1 with the missing vars on stderr). `GET /healthz` and `GET /readyz`. `wynd.runtime.supervisor.api.ROUTES: list[tuple[str, str]]`. Run API file inputs for `path` inputs. |
| **C11** | runtime (storage) | See the C11 list below |
| **C12** | process | See the C12 list below |
| **C13** | process (testing) | `wynd.process.testing.run_process(process_dir: Path, inputs: dict, *, env: Mapping[str, str] | None = None, cassettes: Path | None = None, run_id: str | None = None) -> ProcessResult(exit: str, outputs: dict, run_id: str, events: list[dict])`. Local mode; cassettes default to `<process_dir>/tests/cassettes`; replay unless `WYND_CASSETTE_MODE=record`. |
| **C14** | compiler | See the C14 list below |
| **C15** | controller | See the C15 list below |
| **C16** | cli | See the C16 list below |
| **C17** | web | The graph editor round-trips and edits `kind`, `check` and `context` (never drops unknown keys). Agentic branches get a distinct edge style. `package.json` has the scripts `typecheck`, `test` and `build` and the `license` field. |

**C1 (spec)**
- Branch gains `check` and `context`; `Edge.kind` exists; `is_else` is used by the else rules.
- `Process.latency: Literal["fast"] | None`, and `Process.provider`.
- Example extensions:
  - process examples may carry `env:`;
  - a `{tmp}` prefix in values;
  - relative `path` values resolve against the process dir;
  - `exit: error` is allowed in examples.
- Proto type syntax: scalar names, a nested mapping is an object, `[T]` is a list; outputs are nested by exit only when `exits:` is present.
- `StepLock` fields (`version, proto_hash, kind, entry, provider, tier, thinking, retries, effects, tools, mcp, env_vars, env, lock`), with `load_step_lock(path) -> StepLock`, `dump_step_lock(lock) -> str` (canonical) and `proto_hash(proto) -> str`.
- `Tier = Literal["cheap", "standard", "strong"]`.
- `steps.x.outputs` may or may not include `exit`; the sample works either way.
- A flat step package layout with `entry: module:Class`; duplicate module names in one closure are an error.

**C4 (runtime, agentic)**
- A single shared structured-completion function used by `AgenticStep` and by edges: `complete_structured(*, provider, tier, thinking, retries, instruction, context, input, output_model, tools, mcp, unit, timeout_s, ctx) -> (output, attempts, validation_failures, usage, model_id, replayed)`. It covers ModelProvider loop-continuation retries, AgentProvider validation retries, the cassette wrapper, and `model.call` events with `step=unit`.
- The context assembler `assemble_context(refs: list[str], run_view) -> dict`, which accepts the 4.3 reference grammar.

**C7 (runtime, cassettes and providers)**
- The loader raises the LFS-pointer error in 8.2.
- Promotion **replaces** a package's `cassettes/` directory with the new recording.
- Process-level cassettes live in `processes/<id>/tests/cassettes/`.
- Request hashes exclude auth and include the model id and the instruction.
- The provider registry supplies **built-in default tier maps** when `WYND_HOME` has none (claude-code: haiku/sonnet/opus).

**C9 (runtime, providers)**
- `claude-code` honours `tools=[]` with no built-in harness tools.
- It reports `usage.latency_ms` and `usage.cost_usd` (`total_cost_usd`).
- Its env fragment declares `claude-agent-sdk>=0.2,<0.3`, plus `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY` as `secret: true, required: false`.
- `anthropic` uses stdlib urllib with `ANTHROPIC_API_KEY`.

**C11 (runtime, storage)**
- The env var names of the storage selectors (listed in the docs and in the install ConfigMap).
- The local backends (RunRegistry, TraceSink, WorkspaceStore, user Registry) are **multi-process safe on a shared filesystem**: one file per record, writes by temp file + rename, no global index file.
- `new_job_id()` returns lowercase `[a-z0-9]`.
- `RunRegistry`: `put_job(rec)`, `get_job(id)`, `update_job(id, **fields)`, `list_runs(process: str, kinds=("run",), limit: int, since: datetime | None) -> list[RunRecord]`.
- `TraceSink.read(run_id) -> Iterable[dict]`.

**C12 (process)**
- `load_process(workspace, process_id) -> ResolvedProcess`, providing:
  - `process_dir`, and `steps: dict[name, ResolvedStep(package_dir, lock: StepLock | None, is_local: bool, kind)]`;
  - normalised edges (shorthand expanded);
  - reachability/path analysis reusable by `check_agentic_edges`.
- `Diagnostic(level, code, message, where)`. The validator calls `check_agentic_edges()` and, when `stats` is given, `latency_warnings()`.
- The `JobRunner` protocol exactly as in spec 6.6; `JobKind` includes `"optimise"`; `JobStatus` includes `queued/running/awaiting_input/succeeded/failed/cancelled`.
- The venv plan records `edge_venvs` using the rule in 4.6.
- The builder split into `prepare`/`finalize` with the build-context contract in 5.5.
- `process.env.yaml` entries carry `name, description, secret, required, default?, used_by`, and include `env.X` references from process expressions as `secret: false, required: true`.
- The Dockerfile `FROM` uses the registry entry's base repository.

**C14 (compiler)**
- The compile job calls `sync_edge_lock()`, writes `edges.lock.yaml` when it changes, and **records process-level cassettes live** (running the process tests) as well as step cassettes.
- Generated test layout matches 6.8 (`test_<step>.py` next to the module; process tests at `processes/<id>/tests/test_process.py`, with names `test_example_<n>`).
- It implements `{tmp}` and example `env:`.

**C15 (controller)**
- Job framework:
  - `JobContext(checkout: Path, job_id: str, inputs: dict, log(msg), git(*args), git_rev(ref) -> str, run_tests(*, step_dirs: list[Path] | None = None, process: str | None = None, process_level: bool = False, live: bool) -> TestReport(total, failed, failed_ids), push_branch(branch), record_test_results(commit))`.
  - `JobResult(status, message, result, artefacts)`; the handler registry gets `"optimise": run_optimise_job`.
  - `integrate_job_branch(workspace, process_id, branch)`, the same function used for compile.
- Job entrypoint `python -m wynd.controller.jobmain <job_id> [--checkout DIR] [--phase all|prepare|finalize]`. It sets `running` and the terminal status, tees output to artefact `log.txt`, and pushes result branches to `$WYND_JOB_PUSH_REMOTE` when set.
- With `WYND_GIT_REMOTE` set, it pushes design commits before submitting and fetches result branches before integrating.
- Backends are resolved from the entry-point groups `wynd.job_runners`, `wynd.trigger_backends` and `wynd.serving_backends`, keyed by `WYND_JOB_RUNNER`, `WYND_TRIGGER_BACKEND` and `WYND_SERVING_BACKEND`, with factory `from_env(env, registry)`.
- Protocols:
  - `TriggerBackend.install(release)`, `.remove(release_id)`, `.reconcile(releases)`;
  - `ServingBackend.ensure(release, manifest) -> url`, `.ready(release_id)`, `.remove(release_id)`.
- `Release(id, process, image, trigger{kind: schedule|webhook|manual, cron, timezone, inputs}, env: dict[str, str])`, with cron validated as 5-field at creation.
- Routes: `POST /api/releases/{id}/runs` taking `{"trigger": ...}` and returning `{"run_id"}`; `POST /hooks/{release_id}`; `GET /api/health`. Bearer auth with `WYND_API_TOKEN` when it is set.
- It persists the trace events of the runs it submits.
- `workspace_subdir` support, everywhere git paths are computed (closure, HEAD).
- It serves the web assets from `WYND_WEB_DIST`.
- Derived status treats a missing or stale edge lock as design.
- `Controller.load_process`, `.process_head`, `.run_registry`, `.trace_sink`, and `process_stats(process_id) -> ProcessStats` (for `wynd validate`).

**C16 (cli)**
- It registers `wynd optimise` (`wynd.cli.optimise`).
- `wynd validate` passes trace stats.
- `path`-typed `--input` values are made absolute at the boundary.
- `wynd init` writes `.gitignore` (`.wynd/`, `.env`) and `.gitattributes` (8.2).
- `wynd trace` renders `edge.check` as in 4.7.
- `wynd provider add <name> --tier <tier>=<model>`, `wynd registry add <name> <url>`, `wynd base build|publish <ver>`.

## 11. Interfaces PROVIDED

| Id | Interface | Where |
|---|---|---|
| **P1** | `wynd.process.optimise`: `ProcessStats`, `TierStats`, `UnitStats`, `percentile`, `collect_stats`, `Rules`, `RULES_V1`, `Recommendation`, `recommend`, `TierChange`, `UnitReport`, `OptimiseReport`, `build_report`, `render_report_text`, `OptimiseJobInput` | 3.3–3.7 |
| **P2** | The job kind `optimise` (input/result JSON; branch `wynd/optimise/<process>/<job-id>`; commit message format); `wynd.controller.optimise.{optimise_report, submit_optimise, run_optimise_job}`; `GET`/`POST /api/processes/{id}/optimise` | 3.7–3.8 |
| **P3** | `wynd.process.latency.latency_warnings` and the codes `W-LATENCY-CALL`, `-RUN` and `-DISPATCH` | 3.5 |
| **P4** | The agentic edge schema (`kind`, `check`, `context`, `is_else`); `wynd.spec.edges_lock` (`EdgeLock`, `EdgeLockEntry`, `branch_key`, `check_hash`, `load_edge_lock`, `dump_edge_lock`, `EDGE_LOCK_FILE`); `wynd.process.edges_lock.sync_edge_lock`; `wynd.process.validate_agentic.check_agentic_edges` with its codes | 4.1, 4.3 |
| **P5** | `wynd.runtime.edges`: `EdgeVerdict`, `EDGE_VERDICT_SCHEMA`, `EdgeCheckCall`, `EdgeCheckResult`, `EdgeCheckError`, `VERIFIER_INSTRUCTION`, `run_edge_check`, `handle_edge_check`, `EdgeChecker`, `WorkerEdgeChecker`; the RPC `edge.check`; the trace event `edge.check`; the unit naming `edge:<branch_key>` | 4.4–4.7 |
| **P6** | `wynd.kube`: `KubeConfig`, `InstallParams`, `KubeClient`, `RestKubeClient`, `KubeApiError`, `FakeKubeClient`, the name helpers, the `render_*` functions, `container_env`/`config_data`, `KubeJobRunner`, `KubeTriggerBackend`, `KubeServingBackend`, `checkout`, `fire`; the entry points; the `wynd-kube` CLI; `deploy/wynd.Dockerfile` | 5 |
| **P7** | The sample workspace, `tools/make_sample_pdfs.py` (`SAMPLES`, `text_of`, `render_pdf`) and its acceptance tests | 6 |
| **P8** | The root `conftest.py` markers (`live`/`docker`/`kube`); `.gitignore`; `.gitattributes`; the LFS policy; the licence layout; `tests/test_repo_hygiene.py`; the CI workflow | 8, 9 |
| **P9** | The docs set | 7 |

---

## 12. File list with owners

Files **created** by this area, by owner:

| Owner | Files |
|---|---|
| A8-opt | `packages/process/src/wynd/process/optimise.py`, `packages/process/src/wynd/process/latency.py`, `packages/process/tests/optimise/{fixtures/*.jsonl, test_collect_stats.py, test_recommend.py, test_report.py, golden_report.json, golden_report.txt}`, `packages/process/tests/test_latency.py`, `packages/controller/src/wynd/controller/optimise.py`, `packages/controller/tests/test_optimise_job.py`, `packages/cli/src/wynd/cli/optimise.py`, `packages/cli/tests/test_optimise_cmd.py`, `docs/optimise.md` |
| A8-edge | `packages/spec/src/wynd/spec/edges_lock.py`, `packages/spec/tests/{test_edges_lock.py, test_agentic_schema.py}`, `packages/process/src/wynd/process/{edges_lock.py, validate_agentic.py}`, `packages/process/tests/{test_validate_agentic.py, test_edges_lock_sync.py}`, `packages/runtime/src/wynd/runtime/edges.py`, `packages/runtime/tests/{test_edges.py, test_executor_agentic.py}`, `docs/agentic-edges.md` |
| A8-kube | `packages/kube/**` (all of 5.1), `deploy/wynd.Dockerfile`, `deploy/wynd.Dockerfile.dockerignore`, `deploy/ci/kind-with-registry.sh`, `docs/cluster-install.md` |
| A8-sample | `examples/invoices/**` (all of 6.1) |
| A8-docs | `README.md`, `LICENSE`, `LICENSES/{Apache-2.0.txt, AGPL-3.0-or-later.txt}`, `packages/*/LICENSE` (8 copies, dropped into each package dir — the package owners don't touch them), `.gitignore`, `.gitattributes`, `conftest.py`, `tests/test_repo_hygiene.py`, `.github/workflows/ci.yml`, `docs/{architecture.md, run-api.md, providers.md, testing.md}` |

**Touch points in files owned by others.** Each is a small, specified edit; the owner makes it.

| File (owner) | Edit |
|---|---|
| spec process models (spec) | Add `Branch.check` and `Branch.context`, `is_else`; the example `env:` and `exit: error` support (4.1, C1) |
| runtime executor (runtime) | The branch loop and `EdgeChecker` injection; the `ProcessError` cause (4.2, C6) |
| runtime worker (runtime) | Register `edge.check` (C5) |
| runtime cassettes (runtime) | LFS pointer detection (8.2, C7) |
| process validator (process) | Call `check_agentic_edges` and `latency_warnings`; use `is_else` (C12) |
| process venv planner (process/M2) | `edge_venvs` (4.6) |
| process builder (M2) | prepare/finalize split and the build-context contract (5.5) |
| process `JobKind` (process) | Add `"optimise"` |
| compiler compile job (compiler) | `sync_edge_lock` and process-level cassette recording (C14) |
| controller job registry and app (controller) | Register `run_optimise_job`; mount the optimise routes; resolve backends by entry point (C15) |
| cli app (cli) | Register the `optimise` command; the `wynd trace` rendering of `edge.check` (C16) |
| root `pyproject.toml` (whoever owns it) | The `[tool.pytest.ini_options]` block from 8.3; `packages/kube` as a workspace member |
| web editor (web) | Round-trip `kind`, `check` and `context` (C17) |

## 13. Test plan (summary)

| Layer | Default (offline, deterministic) | Opt-in |
|---|---|---|
| optimise | Synthetic-trace fixtures → stats, rules and goldens; latency warnings; the job with a fake `run_tests` on a temporary git repo; the CLI via CliRunner | `@live` optimise on the sample (3.9) |
| agentic edges | Schema, lock, sync and validator codes; `run_edge_check` with fake Model and Agent providers (take, reject, retry continuation, exhaustion, transport, timeout, cassette hit and miss); the executor with a fake checker | — |
| sample | PDF drift and text-equality tests (root pytest); `wynd validate` / `env check` / `test` in replay in CI (step tests in venvs, plus 5 process examples, 6 at M5, plus side effects) | `@live` process smoke run; M3 compile-from-proto; M5 optimise |
| kube | Names, envmap, goldens for every manifest, the REST client via MockTransport, the runner status matrix and log fallback via FakeKubeClient, triggers and serving, `checkout` against a local bare repo (both fetch paths), `fire` against a stub server, CLI render | `@kube` kind e2e (install → build job → release → schedule trigger; plus a live run with credentials) |
| repo | LFS pointers, licences, namespace package, gitignore, doc links, run-API doc coverage | — |
| CI | `python` (every push/PR), `web` (every push/PR) | `docker` (push to main, dispatch, or label `ci:docker`), `kube` (dispatch only) |

## 14. Sequencing within this area

1. **Day 0, no dependencies (A8-docs, A8-sample, A8-kube):**
   - `.gitignore`, `.gitattributes`, `LICENSES/` and the package `LICENSE`s, the root `conftest.py`, and the CI skeleton (the `python` job only).
   - `make_sample_pdfs.py` with the committed PDFs; the proto YAMLs.
   - The pure `wynd.kube` modules (names, envmap, manifests, client, checkout, fire, testing) and their goldens.
   - `wynd.process.optimise` and `latency` against fixtures.
2. **After the spec models and runtime step API exist (C1/C8):**
   - The six hand-written step packages and their tests.
   - `edges_lock.py`; the lock hashes computed with the real `proto_hash`.
3. **After the claude-code provider and `wynd test --live` (C7/C9/C15):** record the sample cassettes (6.8); the sample's M1 acceptance.
4. **After the executor, worker, validator and venv plan exist:** `wynd.runtime.edges` and the call-site edits; `validate_agentic`; M5 sample changes (umbrella example, `kind: agentic`, `compile`, re-record).
5. **After JobContext, Release and the backend protocols (C15):** the optimise job, CLI and routes; `KubeJobRunner`, triggers and serving; the Dockerfile; the kind script and e2e.
6. **Last:** README and docs, once the contracts are final; `run-api.md` after M2; the CI `docker`/`kube`/`web` jobs enabled once those tests exist.

## 15. Risks and open items for the synthesizer

- **R1: workspace in a subdirectory.** The dogfood lives at `examples/invoices` inside the wynd repo. Closure/HEAD, job worktrees, branch integration and the cluster clone must carry `workspace_subdir` (Interpretation 15). If the controller design assumes the workspace root is the git root, the sample breaks.
- **R2: cassettes and tier maps.** CI replay depends on built-in default tier maps (C7). A user who changes their tier map will see explicit replay misses; this is by design and documented.
- **R3: the process-level cassettes are brittle** (spec 7). The agentic edge adds a call per valid invoice. Re-recording is `wynd test --live`.
- **R4: subscription costs are estimates.** `cost_usd` under the subscription is API-equivalent, and optimise's cost columns say so.
- **R5: cluster storage needs RWX** (or a single node). Without it, controller and job pods cannot share the local-FS backends. Writing database or object-store backends was deliberately left out (Interpretation 13).
- **R6: rootless BuildKit** needs Unconfined seccomp and AppArmor, which the Pod Security `baseline` and `restricted` levels forbid. `render_install` therefore labels the Namespace `pod-security.kubernetes.io/enforce: privileged` (with `warn`/`audit: baseline`) and says so in a comment. Operators who refuse can move build Jobs to a dedicated namespace later; a `WYND_KUBE_BUILD_NAMESPACE` could be added then, but is not added now.
- **R7: `exit: error` in examples** (the read_pdf edge case) needs spec support. If it is rejected, drop that example; nothing else depends on it.
- **R8: cluster jobs need push access.** The git token must be able to push `wynd/*` branches. Branch protection on `main` is compatible, since jobs never push `main`.
- **R9: the toolchain image is multi-arch.** `claude-agent-sdk` wheels are platform-specific (bundled CLI), so the image is built with `--platform linux/amd64,linux/arm64`.
- **R10: the kind e2e is manual and best-effort.** Registry DNS inside kind is the usual pain point. The default gate for Kubernetes correctness is goldens plus fake-client tests.
