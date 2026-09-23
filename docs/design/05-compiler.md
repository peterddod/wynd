# 05 — `wynd.compiler`: session, compile decision, generate-test-revise loop

Area: **wynd-compiler** (AGPL-3.0-or-later, milestone M3).
Source of truth: `docs/SPEC.md`. This document designs the compiler package and pins down every contract it has
with `spec`, `runtime`, `process`, `controller`, `cli` and `web`.

Contents

1. Spec clauses this area owns
2. Package layout, modules and owners
3. Interfaces consumed (for reconciliation)
4. Interfaces provided
5. Compile job: git ref in, commit out
6. The session (section 9 / 6.6)
7. Per-step pipeline
8. The compile decision (rules 1–4)
9. Generated artefacts: step package, `step.lock.yaml`, tests, cassettes
10. Two-step split: rewriting `process.yaml`
11. Process-level examples as integration tests
12. Test running, venvs and the `wynd test --live` job
13. The compiler's own LLM use
14. Prompts (actual text)
15. Compile report
16. Tests: offline suite, live tests, acceptance test
17. Interpretations (where the spec is ambiguous)
18. Risks and open points for the lead
19. Reconciliation with the parallel drafts (01 spec, 02 runtime, 04 process, 06 controller/CLI, 07 web, 08 sample/M5)

---

## 1. Spec clauses this area owns

Coverage audit list. "Owns" means this package implements it. "Contributes" means it writes data another package consumes.

| Clause | What | Status here |
|---|---|---|
| 3.1 | Examples are not part of a step; the compiler generates one pytest file per compiled step | Owns (9.3) |
| 3.2 | Generates `DeterministicStep`, `AgenticStep`, `ShellStep`; `ProcessStep` children are compiled recursively, not generated | Owns (7.1, 8) |
| 3.4 | "Edges are compiled like steps" | Interpretation I-14: v1 edges are evaluated by the spec's Lark evaluator, so no edge code is emitted. The compiler supplies interfaces that make M3 type checking possible |
| 3.4.1 | M3 type checking "alongside schema inference" | Contributes: `interface` snapshot in `step.lock.yaml` (9.2), used by the process validator |
| 3.5 | Retry defaults set by the compiler in the lockfile (0 / 2 / 1-if-idempotent) | Owns (9.2) |
| 3.7 | `context` class attribute on compiled agentic steps | Owns (compiler chooses it, 14.8) |
| 3.8 | Builtins first, then `@tool` methods, then registry MCP servers with the narrowest `allow`; MCP discovered and snapshotted at compile time; effects, idempotency and env recorded | Owns (7.7) |
| 3.9 | Provider and tier recorded per step; `wynd compile` sets only the process default; cassettes wrap the provider boundary | Owns lock fields (9.2); interpretation I-10. The compiler's own LLM calls go through the provider interfaces (13) |
| 4 | "The compiler records each step's required env vars in `step.lock.yaml`"; env fragments (deps, system, requires) | Owns (9.2) |
| 5.1 | Step package marker (`pyproject.toml` + `step.lock.yaml`) | Contributes (writer, 9.1) |
| 6.1 | Consumes proto-steps; decides kind and tier, records them in the lockfile; schema inference presented in plain language | Owns (7.3) |
| 6.3 | Compiled step package contents; test results kept out of the lockfile (RunRegistry) | Owns (9) |
| 6.5 | Compile output is always a commit on `wynd/compile/<process>/<job-id>`; only re-touch a step when its proto hash changes; hand edits are the baseline; `wynd test --live` is a job whose output is a branch; cassette size warning | Owns (5, 7.1, 9.4). Specifies the live-test job's behaviour (12.3; 04 implements it). Integration belongs to the controller and CLI |
| 6.6 | Session serialisable in the job record; `awaiting_input`; answering resubmits with the answer appended; web chat is a view | Owns (6) |
| 7 | Generated tests use `run_step`; agentic tests replay; "the compile job always records live while generating or revising a step and includes fresh cassettes in its output commit" | Owns (9.3, 9.4) |
| 7.1 | Cassettes recorded into the run workspace, promoted into `steps/<name>/cassettes/` by `wynd compile` and `wynd test --live`; RunRegistry holds serialised sessions and test results | Owns promotion calls and records (9.4, 12) |
| 9 | Everything in section 9 | Owns |
| 11 | Compile button opens a chat bound to the serialised session | Contributes session JSON and view mapping (6.8) |
| 12 (M3) | `wynd compile` produces the M1 process from proto-steps alone | Owns (16.4 acceptance) |
| 13 | Examples become tests; deterministic first; compile emits commits; context pull-based; tier mapping never in the lockfile | Upheld |
| 15 | Usage observable (tokens, cost, latency per call); no `if <environment>:` branches | Upheld (15, 13.4) |
| 16.1–3 | Examples-as-tests; compile decision as a reviewable output; git-native compilation | Owns |

---

## 2. Package layout, modules and owners

```
packages/compiler/
  LICENSE                                  # AGPL-3.0-or-later
  pyproject.toml                           # name = "wynd-compiler"; deps: wynd-spec, wynd-runtime, wynd-process, pyyaml
  src/wynd/compiler/                       # no src/wynd/__init__.py (PEP 420)
    __init__.py                            # re-exports the public API (section 4)
    jobs.py           # run_compile_job(ctx) -> JobOutcome: the `compile` handler ("wynd.compiler.jobs:run_compile_job", 06)
    session.py        # CompileSession, SessionData, Question, SessionEvent, CompileStep, CompileOptions
    gitops.py         # squash / restore paths in the job worktree (subprocess git); commits go through ctx.commit
    pipeline.py       # compile_closure, compile_node: the per-step state machine; CompileEnv, CompileDeps
    decision.py       # plan + generate-test-revise loops, split eligibility, tier escalation
    split.py          # process.yaml rewrite for rule 3 (apply / remove), expression renaming
    yamledit.py       # comment-preserving block append/remove on YAML text, verified by re-parse
    examples.py       # proposal handling, answer interpretation, contradiction check, appending to proto YAML
    schemas.py        # models-block source rendering, canonicalisation of examples (uses wynd.spec typelang/build_models)
    testgen.py        # generated step test files (golden-stable)
    codegen.py        # prompt inputs, LLM calls, agentic module rendering, attempt files
    astcheck.py       # compiler-specific static checks + extraction (runs wynd.runtime's check_step_module first)
    tools.py          # tool catalog (builtins + MCP discovery), allow narrowing, lock tool/MCP snapshots
    lockfile.py       # StepLock construction (wynd.spec model) and YAML rendering; pyproject rendering; dep locking
    attempts.py       # run_attempt: venv, interface check, per-package test suite, WYND-EXPECT classification
    llm.py            # CompilerLLM protocol, ProviderLLM, MemoLLM, usage accounting, strict-schema helper
    calls.py          # CallKind, tier/thinking table, response models per call kind
    prompts/
      __init__.py     # load(name) -> str via importlib.resources; render(sections) -> str
      preamble.md  infer_schema.md  propose_examples.md  revise_example.md  decide.md
      write_deterministic.md  write_shell.md  write_agentic.md  revise_code.md  revise_agentic.md
      runtime_api.md  # reference excerpt given to codegen calls (kept in sync with runtime by a test)
    report.py         # CompileReport models, commit message rendering
    testing.py        # ScriptedLLM, RecordingLLM, FakeJobContext (offline use by compiler/controller/web/cli tests)
  tests/
    fixtures/ws_mini/                      # synthetic workspace (stdlib-only steps + runtime `fake` provider), 16.2
    fixtures/scripts/*.yaml                # ScriptedLLM scripts
    fixtures/code/*.py                     # module sources returned by scripted write_* calls
    fixtures/golden/*                      # golden generated files
    test_session.py test_schemas.py test_yamledit.py test_examples.py test_testgen.py test_astcheck.py
    test_lockfile.py test_tools.py test_llm.py test_prompts.py test_decision.py test_split.py
    test_attempts.py test_pipeline_offline.py test_jobs.py test_dogfood_offline.py
    live/test_llm_calls_live.py  live/test_compile_live.py
tests/acceptance/test_m3_compile_invoice_live.py   # repo-level, owned by this area (16.4)
```

`optimise.py` (`run_optimise_job`, M5) also lives in this package per 06's handler table, but it belongs to the M5 area
(draft 08) and is not designed here.

Owners (sub-owners inside the compiler area, so parallel agents never share a file):

| Owner | Modules and their tests |
|---|---|
| **CMP-A** session and jobs | `session.py`, `jobs.py`, `gitops.py`, `report.py`; `test_session.py`, `test_jobs.py`; `tests/acceptance/test_m3_compile_invoice_live.py` |
| **CMP-B** pipeline and decisions | `pipeline.py`, `decision.py`, `split.py`, `yamledit.py`, `examples.py`; `test_decision.py`, `test_split.py`, `test_yamledit.py`, `test_examples.py`, `test_pipeline_offline.py`, `test_dogfood_offline.py` |
| **CMP-C** generation and attempts | `schemas.py`, `testgen.py`, `codegen.py`, `astcheck.py`, `tools.py`, `lockfile.py`, `attempts.py` and their tests; `fixtures/golden` |
| **CMP-D** LLM and prompts | `llm.py`, `calls.py`, `prompts/*`, `testing.py`; `test_llm.py`, `test_prompts.py`, `live/test_llm_calls_live.py`, `fixtures/scripts` |

Not in this package (owned elsewhere, reconciled in 19): the replay test runner `wynd test`, the process-examples
runner and the `test_live` job handler live in `wynd.process.testing` (drafts 04 and 06). This document specifies
what the compiler needs from them (3.3) and the behaviour the live-test job must have (12.3).

---

## 3. Interfaces consumed (for reconciliation)

Each item has an id. Names follow the parallel drafts where they already exist: **01** (spec), **02**
(runtime core), **04** (process), **06** (controller and CLI), **07** (web). **REQUIREMENT** marks behaviour those
drafts do not yet provide.

### 3.1 From `wynd.spec` (01)

| Id | Interface | Use here |
|---|---|---|
| C-S1 | `ProtoStep` (`name, instruction, inputs, outputs` normalised by exit, `exits, exit_codes, examples: list[Example], env: EnvFragment`). `Example(inputs, outputs, exit="done", description)` | Read protos. Confirmed edge cases are appended with `description` = the confirmed question |
| C-S2 | Type language `parse_type`/`render_type` (`string number integer boolean date datetime path object list[T]`, `T?`, nested mapping, one-item sequence `[T]`), `type_schema`, `describe_fields`, `infer_fields` (deterministic baseline, 7.3) | Schema inference, plain-language rendering |
| C-S3 | `build_models(name, inputs, outputs) -> ModelSet(input, outputs, output, adapter)`, `interface_from_fields`, `interfaces_equivalent(a, b) -> list[str]`, `Interface(input, outputs)` | Example conformance, canonicalisation, the interface check |
| C-S4 | `proto_hash(proto)`; `step_hash(package_dir)` | Skip rule (7.1); test-result keys go through 04's `run_tests` |
| C-S5 | `StepLock` (01 §6.5: `wynd, name, kind, entrypoint, proto_hash, provider, tier, thinking, retries: RetryPolicy(run, validation, tool), effects, tools: [ToolSnapshot], mcp: [McpSnapshot], context, shell: ShellLock, fragment: EnvFragment, locked_deps, interface`), `DEFAULT_RETRIES`, `DEFAULT_TIER`, `DEFAULT_THINKING`, `load_step_lock` | The compiler is the main writer (9.2). **REQUIREMENT**: one optional field `compiled: CompiledInfo \| None` (9.2.1), because StepLock rejects unknown keys |
| C-S6 | `ProtoStep.to_authoring()` + `dump_yaml` | Fallback when surgical YAML edits cannot be verified (9.6) |
| C-S7 | `wynd.spec.expr.references(expr) -> list[Ref(root, path, line, column, guarded)]`, `env_names` | Constraints (7.2.1) and split renaming (10.2) |
| C-S8 | `WorkspaceConfig` with `cassette_warn_mb: float = 5.0` | **REQUIREMENT** if 01 lacks it (spec 6.5: "configurable") |
| C-S9 | Implicit error-exit payload with the step's bound inputs under `inputs` (02's `ErrorOutput.inputs`, 04's validator schema). **REQUIREMENT**: 01's `StepError` must add `inputs: dict[str, Any] \| None` so the three drafts agree and `steps.<n>.outputs.inputs.<f>` validates on the error path | Split input binding (10.2) |

### 3.2 From `wynd.runtime` (02 and the agentic/provider area)

| Id | Interface | Use here |
|---|---|---|
| C-R1 | `get_provider(name) -> ModelProvider \| AgentProvider` (entry points `wynd.providers`). `GenerateRequest(system, messages, tools, output_schema, thinking, model_id)`, `AgentRequest(instruction, context, input, output_schema, tools, mcp, workspace, model_id)`, responses with `structured_output` and `usage: Usage(input_tokens, output_tokens, cost_usd, latency_ms)` (02 owns `Usage`). **REQUIREMENT**: `AgentRequest.prompt: str \| None` (verbatim user message) and `AgentRequest.thinking` | The compiler's own LLM calls (13) |
| C-R2 | **REQUIREMENT** for `claude-code`: `tools=[]` and `mcp=[]` means no tools at all (`ClaudeAgentOptions(tools=[])`). It always sets `setting_sources=[]`, so the user's CLAUDE.md, hooks and settings never leak in. It uses a custom `system_prompt`, `output_format={"type":"json_schema","schema":…}`, maps `thinking` to `effort`, and runs with `cwd=workspace`. Usage reports `total_cost_usd` and `duration_ms` | Verified against `claude-agent-sdk` 0.2.157 sources: `tools=[]` disables built-ins, `setting_sources=[]` is isolation mode, `output_format`/`effort` exist, and `ResultMessage` has `structured_output` and `total_cost_usd` |
| C-R3 | `wynd.runtime.testing`: `load_step(step_dir)` and `run_step(step, input, *, mode, cassettes, lock, context, workspace, retry) -> StepResult(exit, output, outputs, summary, attempts, usage, model, events, workspace)` (02 §4.5). **REQUIREMENT**: add `expect(result, *, exit, outputs=None, present=())`, `match_outputs(expected, actual, *, present=()) -> list[Mismatch]` and `sub_tmp(value, tmp)` with the semantics in 9.3.2–9.3.3. 04's process-examples runner should use the same `match_outputs`. `run_step`'s default provider honours `WYND_DEFAULT_PROVIDER` (03 R3), which the compiler and `wynd test` set from the root `process.yaml` | Generated tests |
| C-R4 | Record mode: when a package's tests run with `WYND_CASSETTE_MODE=record` and its `cassettes/` dir is empty, afterwards `cassettes/` holds exactly that run's recordings (via 02/03's `record_dir` plus 03's `cassettes.promote(workspace_root, dest, *, package)`, or 04's `WYND_STEP_DIR`, whichever is settled). Process examples record into `<process dir>/tests/cassettes/` (03 §cassettes, 08) | Cassette promotion (9.4) |
| C-R5 | **REQUIREMENT**: when env `WYND_EVENTS_FILE` is set, `run_step` also appends every event it emits (`model.call` with usage, `tool.call` with tool name and source) as one JSON line to that file | MCP allow narrowing (7.7) and recording usage (13.6), since the events are otherwise trapped inside the pytest process |
| C-R6 | `check_step_module(source, path) -> list[LintIssue]` (02 `lint.py`) | Static checks before tests (9.7) |
| C-R7 | `python -m wynd.runtime.describe <entrypoint>` prints the step's interface JSON (used by 04's drift check) | Interface check of every attempt (7.6) |
| C-R8 | Step API as in 02: `DeterministicStep`, `AgenticStep` (`context`, `tools`, `mcp`), `ShellStep`, `tool(...)`, `McpServer(name, allow)`, `self.runtime` handle; `wynd.runtime.tools.BUILTINS: list[ToolSpec]` with metadata (`name, description, input_schema, effects, idempotent, env`) (03) | Codegen reference (14.11), catalog (7.7) |
| C-R9 | MCP discovery `list_tools(server) -> list[McpToolSpec(name, description, input_schema, annotations)]`; user `Registry` with `provider_tiers(name)` (claude-code seed: cheap→haiku, standard→sonnet, strong→opus), `mcp_servers()`, and the provider's env fragment | Catalog (7.7), tier resolution (13.1) |
| C-R10 | **REQUIREMENT**: a `fake` provider (AgentProvider, entry point `wynd.providers:fake`) answering from the JSON script named by `WYND_FAKE_PROVIDER_SCRIPT` (`{"responses":[{"match":{"input":{…}},"output":{…}}]}`; subset match on input, first match wins, a miss raises) | Offline end-to-end tests of agentic and split paths (16.2). Also useful to 06/07 tests |

### 3.3 From `wynd.process` (04) and the job harness (06)

| Id | Interface | Use here |
|---|---|---|
| C-P1 | `load_workspace(root)`, `ws.load_process(pid) -> LoadedProcess(id, dir, spec, raw, steps: dict[str, ResolvedStep], children)`, `ResolvedStep(name, use, ref_kind, package: StepPackage, child)`, `StepPackage(id, dir, proto_path, proto, proto_hash, lock, phase, stale, interface)` | Closure iteration, skip rule. Local resolution `pkg = P.dir/steps/n`, `proto = P.dir/proto/n.yaml`. A compiled package needs no proto, so a split's agentic half resolves fine |
| C-P2 | `validate(lp) -> ValidationReport` (04), including M3 type checks against lock interfaces | Fail fast (5.4), split verification (10.2) |
| C-P3 | `detect_runtime_source()`, `ensure_local_venv(VenvGroup, *, venv_root, runtime, log) -> id` (installs spec + runtime + pytest), `normalize_requirement`. **REQUIREMENT**: a helper `step_python(requirements: Sequence[str], *, venv_root, runtime, log) -> Path` returning the venv interpreter for one step's requirement set (the step's `fragment.deps` plus the provider fragment's deps for agentic steps) | Attempt venvs (7.6) |
| C-P4 | `UvResolver.compile(requirements, universal=True, python_version="3.12") -> list[str]` | `locked_deps` (9.2) |
| C-P5 | **REQUIREMENT**: expose the per-package inner loop of 04's `run_tests` as `run_step_suite(pkg_dir: Path, *, python: Path, mode: Literal["replay","record"], env: Mapping[str,str], junit: Path, basetemp: Path, timeout_s: int) -> SuiteResult`, where `TestCase.message` carries the full failure text. Also expose part 3 as `run_process_examples(ws, pid, *, mode) -> SuiteResult` | Attempts (7.6), the process-level phase (11) |
| C-P6 | `run_tests(ws, pid, *, mode, commit, registry) -> TestReport` (04 §7), which records results keyed by commit + step hash | The final replay run on the output commit (5.2) |
| C-P7 | `JobContext`, `JobOutcome` (06 §6.1, in `wynd.process.jobs`): `ctx.inputs` (`process, target_branch, answers, accept_proposals, resume_from, root_job_id`), `ctx.session` (pre-loaded from `resume_from`), `ctx.checkout`, `ctx.worktree`, `ctx.branch` (already checked out), `ctx.ref`, `ctx.workspace_root`, `ctx.state_dir`, `ctx.run_registry`, `ctx.user_registry`, `ctx.log`, `ctx.commit(msg) -> sha \| None` (stages everything under `checkout`), `ctx.save_session(dict)` | The handler's whole environment |
| C-P8 | **REQUIREMENT** (06 harness): a resumed compile job (`resume_from` set) checks out the **existing** result branch `wynd/compile/<pid>/<root_job_id>` at its current head, keeping `ctx.ref` = the root job's base commit | Keeps WIP commits across resumes (6.1). Without it the design still works, but re-records completed agentic steps |

### 3.4 From the controller, CLI and web (06, 07)

| Id | Interface |
|---|---|
| C-C1 | The controller resubmits with cumulative `answers` when no pending questions remain (06 `jobs.answer`, 07), and maps the web's structured answers onto answer text: `confirm` → `"accept"`, `reject` → `"reject"`, `correct` with `example` → `json.dumps(example)`, and `text` passed as is |
| C-C2 | The CLI and web integrate the result branch (fast-forward, auto-rebase or PR) using `artefacts.branch/commit/base` |
| C-C3 | The web renders `session.steps`, `session.questions`, `session.inferred_schemas` and `session.events` (07 `CompileSession`), a shape this document adopts (6.5) |

---

## 4. Interfaces provided

```python
# wynd/compiler/__init__.py
from wynd.compiler.jobs import run_compile_job
from wynd.compiler.session import (CompileSession, SessionData, SessionState, Question, SessionEvent, CompileStep,
                                   CompileOptions)
from wynd.compiler.report import CompileReport
from wynd.compiler.testing import ScriptedLLM, RecordingLLM, FakeJobContext
```

| Id | Provided to | What |
|---|---|---|
| P1 | controller (06), web (07), CLI | Session JSON (6.5): `state`, `steps[]`, `questions[]`, `inferred_schemas`, `events[]`, plus compiler-private fields |
| P2 | controller harness (06) | `wynd.compiler.jobs:run_compile_job(ctx: JobContext) -> JobOutcome` (5) |
| P3 | web, CLI | Compile report JSON (15) |
| P4 | spec (01), runtime (02), process (04), M1 examples author | Step package layout and lock contents as written by the compiler (9.1, 9.2). The hand-written M1 dogfood steps must follow the same layout |
| P5 | runtime (implements the helpers), M1 examples author | Generated test file format (9.3). M1 hand-written tests use exactly this format |
| P6 | web graph editor (07) | Split markers in `process.yaml` (10.2) |
| P7 | 06/07/CLI tests | `ScriptedLLM`, `FakeJobContext` (16) |
| P8 | process (04) `test_live` handler | Behaviour requirements for the live-test job (12.3) |

---

## 5. Compile job: git ref in, commit out

### 5.1 Handler

```python
def run_compile_job(ctx: JobContext) -> JobOutcome: ...
```

Inputs (`ctx.inputs`, 06 §6.6): `process`, `target_branch`, `answers: {qid: text}` (cumulative across resumes),
`accept_proposals: bool`, `resume_from`, `root_job_id`. The compiler also reads the optional `max_revisions`
(default 3). `ctx.session` is the previous job's session when resuming.

Output: `JobOutcome(status, artefacts, report, session, questions, usage, error)`:

| Field | Value |
|---|---|
| `status` | `succeeded`, `failed` or `awaiting_input` |
| `artefacts` | `{"steps_compiled": n, "steps_skipped": n, "split": [node, …]}`. The harness adds `branch`, `commit`, `base` and `commits` |
| `report` | CompileReport JSON (15) |
| `session` | SessionData JSON (6.5) |
| `questions` | Pending `Question` JSON objects (06 `JobRecord.questions`) |
| `usage` | `{"input_tokens", "output_tokens", "cost_usd", "latency_ms", "by": {"claude-code/strong": {…}, "recording": {…}}}` |
| `error` | Set on failure; the plain-language cause from the report |

### 5.2 Algorithm

```
run_compile_job(ctx):
  session = CompileSession.from_json(ctx.session) if ctx.session else
            CompileSession.new(session_id=ctx.inputs.get("root_job_id") or ctx.job_id, process=ctx.inputs["process"],
                               base_commit=ctx.ref, options=CompileOptions.from_inputs(ctx.inputs))
  session.data.jobs.append(ctx.job_id)
  session.apply_answers(ctx.inputs["answers"], accept_proposals=ctx.inputs["accept_proposals"])
  env = CompileEnv(checkout=ctx.checkout, worktree=ctx.worktree,
                   scratch=ctx.state_dir / "jobs" / ctx.job_id / "compile",        # outside the checkout
                   deps=default_deps(ctx), checkpoint=lambda d: ctx.save_session(d.to_json()), log=ctx.log,
                   commit=ctx.commit)
  state = session.run(env)                         # = next(); sections 6 and 7
  return JobOutcome(status={DONE: "succeeded", AWAITING_INPUT: "awaiting_input"}.get(state, "failed"),
                    report=…, session=session.to_json(), questions=[q for q in pending], usage=…, error=…)
```

`session.run` ends in one of three ways:

- **awaiting_input.** Steps that completed in this pass (proto appended, package written) are committed with
  `ctx.commit("wynd compile (in progress): <pid>\n\n<trailers>")`. This is the WIP commit on the result branch. Steps
  stopped at a question leave no trace: their package dirs are restored first (`gitops.restore_paths`:
  `git checkout -- <dir>` and `git clean -fdq -- <dir>`).
- **done.** Everything is committed and **squashed into one commit on the base**. `gitops.squash_to(ctx.worktree,
  base=session.base_commit)` runs `git reset --soft <base>`, then `ctx.commit(render_commit_message(report))`
  commits. The branch is checked out in the worktree, so the reset moves it. The harness publishes whatever the
  branch head is. Then `run_tests(ws, pid, mode="replay", commit=<sha>, registry=ctx.run_registry)` (C-P6) records the
  replay results against the new commit, which is what lets `wynd build` reuse them.
- **failed.** Whatever changed is still squashed and committed, so the harness publishes the branch for
  inspection. The report says why. The CLI and web do not integrate failed jobs.

Nothing changed (every step skipped, no new cassettes): `ctx.commit` returns `None` and the harness drops the
branch (06 §6.4). The status is `succeeded` and the report says "nothing to compile".

The handler only writes inside `ctx.checkout` (the job's worktree under `.wynd/jobs/<job-id>/`) and its scratch dir
under `ctx.state_dir`. It never writes the user's working tree. `test_jobs.py` checks this by comparing `git status
--porcelain` and file hashes of the main tree before and after.

### 5.3 Git details

- Commits go through `ctx.commit`, which is the harness's identity, hook policy and `Wynd-Job` trailer (06 §6.4).
  The compiler adds its own trailers to the message:

```
Wynd-Session: job_01j9z6…      (root job id)
Wynd-Process: process_supplier_invoice
Wynd-Base: 4f1c2e9d0b…
```

- `ctx.commit` stages everything under `checkout`. The compiler therefore keeps the checkout clean of anything it
  does not mean to commit. Tests run with `PYTHONDONTWRITEBYTECODE=1`, `-p no:cacheprovider`, and pytest's
  `--basetemp` under the scratch dir. Attempt venvs live in `ctx.state_dir/venvs` (04). Junit and event files go to
  the scratch dir.
- The branch is `wynd/compile/<pid>/<root_job_id>`, chosen by 06. It stays the same across resumes.

### 5.4 Fail-fast validation

Before any LLM call, the compiler runs `validate(lp)` (C-P2) with design-phase rules. Errors end the job
immediately, with the diagnostics in the report and no spend. At the end, after all steps compile, it runs
`validate` again with M3 type checks against the new lock interfaces. Errors fail the job, and the branch is still
published for inspection.

---

## 6. The session (section 9, 6.6)

### 6.1 Principle: resuming means re-executing

The session does **not** serialise a program counter. Every job of a session runs the whole closure pipeline from
the top, against three things:

1. **The tree.** A resumed job's checkout is the result branch, which already holds the WIP commit of every step
   that finished earlier (C-P8). Those steps are skipped by the ordinary proto-hash rule (7.1).
2. **Answers.** `ctx.inputs.answers` is cumulative. `ask()` is idempotent: a question that already has an answer,
   and whose fingerprint still matches, returns that answer at once.
3. **The memo.** The session memoises every compiler LLM call, and every *failing* attempt's test summary, by
   request hash. Re-executing the path up to a question costs nothing and reproduces the same proposals, so question
   ids and fingerprints are stable. The first call whose prompt includes a new answer misses the memo and goes live.

This keeps the session small. It implements "answering resubmits the job with the answer appended" literally: the
appended answer is the only new input. If C-P8 is not provided (the resumed checkout starts from the base), the
design still works. The memo then replays the compiler calls, and only the completed steps' test runs are repeated
(for agentic steps that means live re-recording).

### 6.2 State machine

```
            run()                  pass ends with pending questions
   new ───────────────▶ running ───────────────────────────────▶ awaiting_input
                           │  ▲                                     │ answers for every pending question
                           │  │ run() in the resumed job            ▼   (the controller resubmits, C-C1)
                           │  └──────────────────────────────── ready
                           ├──▶ done      (every step compiled or skipped, tests pass, validation clean)
                           └──▶ failed    (error, validation or test failure; a later job may resume it)
   awaiting_input | ready | failed ──cancel()──▶ cancelled (terminal)
```

- `run()` (spec: `next()`) is legal from `new`, `ready` and `failed`.
- `answer()` is legal in `new` (pre-answers), `awaiting_input` and `ready`. The state becomes `ready` when no
  pending question is left.
- `running` is visible only through checkpoints (`ctx.save_session`). The web shows it as "compiling" (07).

### 6.3 API (`session.py`)

```python
class SessionState(StrEnum):
    NEW = "new"; RUNNING = "running"; AWAITING_INPUT = "awaiting_input"; READY = "ready"
    DONE = "done"; FAILED = "failed"; CANCELLED = "cancelled"

class Question(BaseModel):
    id: str                          # "<node>.example<n>", "<node>.clarify<k>"; "<child-pid>:<node>.…" in child processes
    kind: Literal["example_proposal", "clarification"]
    process: str
    step: str                        # node name in the process (07 calls it `step`)
    package: str                     # step package name, e.g. extract_invoice_fields
    text: str                        # one plain-language question
    detail: str = ""                 # markdown shown under it (example YAML, failing tests, diagnosis)
    proposed: dict | None = None     # example_proposal: {"inputs": …, "exit": …, "outputs": …}
    expects: Literal["decision", "text", "examples"] = "text"   # example_proposal → decision; need-examples → examples
    default: str | None = None       # "accept" for proposals; None for clarifications
    fingerprint: str                 # "sha256:" + hash(kind, text, canonical proposed)
    status: Literal["pending", "answered", "stale"] = "pending"
    answer: Answer | None = None
    answered_by: Literal["user", "accept_proposals"] | None = None
    asked_at: str
    asked_in_job: str

class Answer(BaseModel):             # the interpreted answer (07 renders it directly)
    text: str                        # raw answer text as received
    decision: Literal["confirm", "correct", "reject"] | None = None   # proposals only
    example: dict | None = None      # the confirmed or corrected example

class SessionEvent(BaseModel):
    seq: int
    at: str                          # ISO-8601 UTC
    type: Literal["decision", "question", "answer", "note", "test", "split", "warning", "error", "commit"]
    process: str
    step: str | None                 # node name
    text: str                        # plain-language line (07 shows `at`, `step`, `text`)
    data: dict = {}

class CompileStep(BaseModel):        # one row of 07's decisions table
    process: str
    step: str                        # node name
    use: str
    package: str | None
    phase: Literal["pending", "generating", "testing", "revising", "awaiting_input", "done", "skipped", "failed"]
    decision: StepDecision | None = None
    tests: TestCounts | None = None  # {passed, failed, total}
    attempts: int = 0
    skipped_reason: str | None = None

class StepDecision(BaseModel):
    kind: Literal["deterministic", "agentic", "shell", "process"]
    rule: Literal[1, 2, 3, 4] | None
    tier: str | None
    thinking: str | None
    reason: str
    split: dict | None = None        # {"deterministic": "<node>", "agentic": "<node>_agentic", "handled": [...], "deferred": [...]}

class CompileOptions(BaseModel):
    accept_proposals: bool = False
    max_revisions: int = 3           # N in "revise up to N times"
    max_proposals: int = 3

class SessionData(BaseModel):
    version: Literal[1] = 1
    id: str                          # the root job id; it names the branch
    process: str
    base_commit: str
    state: SessionState = SessionState.NEW
    options: CompileOptions
    jobs: list[str]
    steps: list[CompileStep] = []
    questions: list[Question] = []
    inferred_schemas: dict[str, dict] = {}   # "<node>" -> {"interface": Interface JSON, "plain": [lines]}
    events: list[SessionEvent] = []
    pending_answers: dict[str, str] = {}     # answers for ids not asked yet (pre-answers from --answer)
    memo: Memo = Memo()                      # compiler-private
    usage: UsageTotals = UsageTotals()
    report: CompileReport

class CompileSession:
    data: SessionData
    @classmethod
    def new(cls, *, session_id: str, process: str, base_commit: str, options: CompileOptions) -> "CompileSession": ...
    @classmethod
    def from_json(cls, data: dict) -> "CompileSession": ...
    def to_json(self) -> dict: ...
    @property
    def state(self) -> SessionState: ...
    @property
    def pending_questions(self) -> list[Question]: ...
    def answer(self, question_id: str, text: str) -> None: ...
    def apply_answers(self, answers: Mapping[str, str], *, accept_proposals: bool) -> None: ...
    def run(self, env: "CompileEnv") -> SessionState: ...      # the spec's next(); a name clash with the builtin avoided
    def cancel(self) -> None: ...
    # pipeline-only
    def ask(self, q: Question) -> Answer | None: ...
    def emit(self, type: str, text: str, *, process: str, step: str | None, data: dict | None = None) -> None: ...
```

`CompileEnv` (in `pipeline.py`) is everything `run()` needs besides the data:

```python
@dataclass
class CompileEnv:
    checkout: Path                   # workspace root inside the job checkout (ctx.checkout)
    worktree: Path                   # git top level (ctx.worktree)
    scratch: Path                    # ctx.state_dir / "jobs" / job_id / "compile"
    deps: "CompileDeps"
    checkpoint: Callable[[SessionData], None]    # -> ctx.save_session
    commit: Callable[[str], str | None]          # -> ctx.commit
    log: Callable[[str], None]

@dataclass
class CompileDeps:                   # injected so tests can fake every boundary
    llm: CompilerLLM
    registry: Registry
    step_python: Callable[[Sequence[str]], Path]              # C-P3
    lock_requirements: Callable[[Sequence[str]], list[str]]   # C-P4
    run_step_suite: Callable[..., SuiteResult]                # C-P5
    run_process_suite: Callable[..., SuiteResult]             # pytest over <process>/tests (11)
    describe: Callable[[Path, Path, str], dict]               # (python, pkg_dir, entrypoint) -> interface JSON (C-R7)
    list_mcp_tools: Callable[[McpServerConfig], list[McpToolSpec]]   # C-R9
    now: Callable[[], datetime]
```

### 6.4 `ask()` and answer semantics

```
ask(q):
  existing = question with q.id
  if existing:
      if existing.fingerprint != q.fingerprint:         # regenerated differently (memo evicted, prompt changed)
          existing.status = "stale"; append q as pending; emit note "question changed; please answer again"; return None
      if existing.status == "answered": return existing.answer
      return None                                        # still pending
  if q.id in pending_answers: record interpret(q, pending_answers[q.id]) (by "user"); return it
  if options.accept_proposals and q.kind == "example_proposal": record interpret(q, "accept") (by "accept_proposals"); return it
  append q as pending; emit("question", q.text); return None
```

`interpret(q, text) -> Answer` (case-insensitive, whitespace trimmed):

| Question | Answer text | `Answer` |
|---|---|---|
| `example_proposal` | `""`, `accept`, `confirm`, `yes`, `y` | `decision=confirm, example=q.proposed` |
| | `reject`, `no`, `skip`, `not a real case` | `decision=reject`. The question text is persisted in the lock's `compiled.rejected` (9.2.1) so it is not proposed again |
| | JSON or YAML mapping with `exit` (07's `correct` sends `json.dumps(example)`) | `decision=correct, example=<parsed>` after schema validation. If validation fails, it is treated as free text |
| | any other text | `decision=correct`. The example comes from the `revise_example` call (7.5). Its `understood` sentence is emitted as an `answer` event |
| `clarification`, `expects=text` | any text | `text` only. It becomes guidance for this node from the start of its pipeline, and is persisted to the lock's `compiled.guidance` (I-9) |
| `clarification`, `expects=examples` | a YAML list of `{inputs, exit, outputs}` | The examples are appended as confirmed. If they are invalid, a new question `<node>.examples<k+1>` repeats the error |

**Schema inference never asks.** Following spec 9 ("infer them and present in plain language; the user edits
examples, not schemas"), inferred schemas are *presented*: they are in `session.inferred_schemas`, as a `note` event
and in the report. The user corrects them by editing examples and compiling again (I-1).

### 6.5 Serialised form (`record.session`; pending questions are also in `record.questions`)

```json
{
  "version": 1,
  "id": "job_01j9z6q4m8x2",
  "process": "process_supplier_invoice",
  "base_commit": "4f1c2e9d0b7a3e1f5c2d8a9b6e4f1a2b3c4d5e6f",
  "state": "awaiting_input",
  "options": {"accept_proposals": false, "max_revisions": 3, "max_proposals": 3},
  "jobs": ["job_01j9z6q4m8x2"],
  "steps": [
    {"process": "process_supplier_invoice", "step": "read", "use": "./steps/read_pdf", "package": "read_pdf",
     "phase": "done",
     "decision": {"kind": "deterministic", "rule": 1, "tier": null, "thinking": null,
                  "reason": "Extracting the text layer of a PDF is a pure function of the file.", "split": null},
     "tests": {"passed": 3, "failed": 0, "total": 3}, "attempts": 1, "skipped_reason": null},
    {"process": "process_supplier_invoice", "step": "extract", "use": "./steps/extract_invoice_fields",
     "package": "extract_invoice_fields", "phase": "awaiting_input", "decision": null, "tests": null,
     "attempts": 0, "skipped_reason": null},
    {"process": "process_supplier_invoice", "step": "save", "use": "./steps/save_record", "package": "save_record",
     "phase": "skipped", "decision": null, "tests": null, "attempts": 0, "skipped_reason": "proto-step unchanged"}
  ],
  "questions": [
    {"id": "extract.example1", "kind": "example_proposal", "process": "process_supplier_invoice",
     "step": "extract", "package": "extract_invoice_fields",
     "text": "What should happen if the invoice has no due date?",
     "detail": "Why: every invoice we pay has a due date; without one it may be a quote or a receipt.",
     "proposed": {"inputs": {"invoice_text": "INVOICE INV-2001\nTotal due: £80.00"}, "exit": "not_an_invoice", "outputs": {}},
     "expects": "decision", "default": "accept", "fingerprint": "sha256:a07e…",
     "status": "pending", "answer": null, "answered_by": null,
     "asked_at": "2026-09-22T21:16:02Z", "asked_in_job": "job_01j9z6q4m8x2"}
  ],
  "inferred_schemas": {
    "fix": {"interface": {"input": {"…": "…"}, "outputs": {"done": {"…": "…"}, "cannot_fix": {"…": "…"}}},
            "plain": ["fix_fields takes:", "  - fields (a group with fields supplier, invoice_number, …)", "…"]}
  },
  "events": [
    {"seq": 1, "at": "2026-09-22T21:14:03Z", "type": "note", "process": "process_supplier_invoice", "step": null,
     "text": "Compiling process_supplier_invoice at 4f1c2e9: 6 steps, 5 with changed proto-steps.", "data": {}},
    {"seq": 7, "at": "2026-09-22T21:16:40Z", "type": "decision", "process": "process_supplier_invoice", "step": "read",
     "text": "read_pdf: deterministic (rule 1). Extracting the text layer of a PDF is a pure function of the file.",
     "data": {"rule": 1, "classification": [{"example": 1, "needs": "pure"}, {"example": 2, "needs": "pure"}]}}
  ],
  "pending_answers": {},
  "memo": {"calls": {"3f9a0c…": {"kind": "propose_examples", "step": "extract", "response": {"…": "…"},
                                 "usage": {"input_tokens": 2210, "output_tokens": 640, "cost_usd": 0.021, "latency_ms": 9120}}},
           "tests": {}},
  "usage": {"calls": 9, "input_tokens": 51210, "output_tokens": 14120, "cost_usd": 2.14, "latency_ms": 402000},
  "report": {"…": "partial CompileReport, section 15"}
}
```

Size bound: the memo is capped at 2 MB of JSON. Entries belonging to steps that are already in a WIP commit are
dropped first (those steps will be skipped by hash anyway), and after that the oldest entries go.

### 6.6 Memo keys

```
call key = sha256(canonical_json({"kind", "tier", "thinking", "system", "prompt", "schema"}))[:32]
test key = sha256(canonical_json({"files": {relpath: sha256(content)}, "mode", "requirements"}))[:32]
```

Only failing attempts are memoised. A passing attempt leads to completion and its cassettes are needed, so it is
never skipped.

### 6.7 CLI (06 owns the command; this is what the session requires of it)

06 already specifies: `wynd compile <id> [--answer QID=TEXT]… [--answers-file PATH] [--accept-proposals] [--resume
JOB_ID] …`, with exit code **4** for "stopped at `awaiting_input` with unanswered questions". Compiler-side
requirements:

- Pre-answers are allowed: `--answer`/`--answers-file` given on the first run are stored as `pending_answers` and
  applied when a question with that id is asked. Ids are readable and deterministic (`extract.example1`,
  `fix.clarify1`), so a CI script can pre-answer.
- `--accept-proposals` answers every `example_proposal` with `accept`. Clarifications have no default, so they still
  stop the CLI ("fail on unanswered questions").
- Pending questions are printed from `record.questions` as id, text and default, followed by `detail` indented, and
  then the exact resume command.
- Answers file (YAML, question id → answer text):

```yaml
extract.example1: accept
extract.example2: |
  It should be not_an_invoice: we never pay anything without a due date.
extract.example3: reject
fix.clarify1: When the currency is missing, assume GBP only if the supplier address is in the UK; otherwise escalate.
```

### 6.8 The web chat is a view (07)

07 renders `session.steps` (decisions table: the `split` badge "split → extract + extract_agentic" comes from
`decision.split`), `session.inferred_schemas` ("Inferred from examples"), `session.questions` (`ProposalCard` for
`example_proposal`, and `QuestionCard` for `clarification`) and `session.events` (timeline). Answers go through the
controller (C-C1), which maps 07's `{decision, example, text}` onto answer text. The chat never talks to the
compiler directly.

---

## 7. Per-step pipeline

### 7.1 Closure iteration and the skip rule (`pipeline.py`)

```python
def compile_closure(session: CompileSession, env: CompileEnv) -> SessionState: ...
def compile_node(session: CompileSession, env: CompileEnv, lp: LoadedProcess, rs: ResolvedStep) -> NodeOutcome: ...

@dataclass
class NodeOutcome:
    status: Literal["compiled", "skipped", "awaiting", "failed"]
    reason: str
    split: SplitPlan | RemoveSplit | None = None      # process.yaml change to apply (10)
```

```
compile_closure(session, env):
  ws = load_workspace(env.checkout)
  lp = ws.load_process(session.process)
  report = validate(lp) → errors: fail fast (5.4)
  order = post-order over lp.closure_processes() (children first), then the target   # spec 5.1: a parent's hash includes children
  awaiting = failed = False
  for p in order:
      for rs in p.steps.values() in YAML order:
          if rs.ref_kind == "process": record CompileStep(kind="process", phase="done"); continue   # compiled as its own entry in `order`
          if rs.package.lock and rs.package.lock.compiled and rs.package.lock.compiled.split \
             and rs.package.lock.compiled.split.role == "agentic": continue                        # handled with its partner
          out = compile_node(session, env, p, rs)
          awaiting |= out.status == "awaiting"; failed |= out.status == "failed"
          if out.split: apply_split / remove_split in p's process.yaml (10)
          env.checkpoint(session.data)
      if not awaiting: write the process integration test file (11) and, from M5, sync_edge_lock (11.3)
  if awaiting:
      restore the package dirs of awaiting nodes; env.commit(WIP message) (5.2); return AWAITING_INPUT
  process-level phase (11): run process tests in record mode, promote process cassettes
  validate(lp) with M3 types → errors: FAILED
  squash + commit (5.2); run_tests(replay, commit=sha) records results; return DONE if all passed else FAILED
```

**Skip rule** (spec 6.5: "only re-touches a step when its proto-step hash changes"), checked first in `compile_node`:

| Condition | Action | `skipped_reason` / report reason |
|---|---|---|
| No proto (`rs.package.proto is None`) | Skip | "no proto-step (hand-written step)" |
| `lock.proto_hash == proto_hash(proto)` | Skip (and its split partner) | "proto-step unchanged" |
| No lock, or the hash differs | Compile | "not compiled yet" / "proto-step changed" |

This applies equally to hand-written packages (M1). A hand-written step whose lock carries the correct proto hash
is the committed baseline and is never touched. If its proto changes, it is recompiled. The previous module source
goes into the codegen prompt as "the previous implementation, which may contain hand edits; keep them where still
consistent" (14.6), and the diff is reviewed in git like any other change.

### 7.2 `compile_node` in full

```
compile_node(session, env, lp, rs):
  proto = rs.package.proto; node = rs.name; pkg = env.checkout / rs.package.dir
  progress(node, phase="generating")
  guidance = [a.text for answered clarifications of node] + (lock.compiled.guidance if lock and lock.compiled else [])
  constraints = derive_constraints(lp, node)                              # 7.2.1

  # 1. schema (presented, never asked) ------------------------------------------
  if proto declares both inputs and outputs:  iface = proto.interface(); inferred = False
  else:                                       iface = infer_interface(...); inferred = True        # 7.3
  models = build_models(proto.name, iface fields)                         # wynd.spec (C-S3)
  problems = check_examples(proto.examples, models) + contradictions(proto.examples)   # 7.4
  if problems: q = clarification(node, problems, expects="text"); ask(q); return awaiting   # the user fixes the proto
  if inferred: session.inferred_schemas[node] = {...}; emit("note", describe_fields(...))

  # 2. edge-case proposals --------------------------------------------------------
  examples = [canonical(e) for e in proto.examples]
  added, pending = propose_and_confirm(...)                               # 7.5
  if pending: return awaiting                                             # every question for the node is asked at once
  examples += added
  if not examples: ask(clarification(node, "This step has no examples…", expects="examples")); return awaiting
  new_proto_text = append_examples(read(proto_path), added)              # 9.6, in memory until finalize
  new_hash = proto_hash(parse_proto(new_proto_text))

  # 3. decide, generate, test, revise (section 8) --------------------------------
  result = decide_and_build(session, env, lp, rs, iface, models, examples, guidance, new_hash)
  if result.status in ("awaiting", "failed"): restore attempt files; return result.status

  # 4. finalize --------------------------------------------------------------------
  write proto_path <- new_proto_text (if examples were added)
  write package files: module, test file, pyproject.toml, step.lock.yaml; cassettes are already in place (9.4)
  if result.split: also the agentic package; return NodeOutcome("compiled", split=SplitPlan(...))
  if the old lock had compiled.split and the result is not a split: return NodeOutcome("compiled", split=RemoveSplit(node))
```

#### 7.2.1 Constraints from the process graph

```python
@dataclass
class Constraints:
    input_names: list[str]                       # keys of `with:` on every branch targeting this node
    input_types: dict[str, str]                  # entry node: process.inputs types (names must match, spec 3.3)
    output_fields: dict[str, list[str]]          # exit -> fields used as steps.<node>.outputs.<f> in edges from <node>.<exit>
    downstream_fields: list[str]                 # fields referenced by other edges (exit unknown)
    whole_output_targets: list[str]              # "validate.fields" for `fields: steps.extract.outputs`
```

These are computed with `wynd.spec.expr.references()` (C-S7) over every `when:` and `with:` value. They are passed to
schema inference ("field names used by edges are fixed") and to codegen as context. A recompile therefore keeps
downstream edges valid.

### 7.3 Schema inference (only when the proto lacks `inputs` or `outputs`)

```
infer_interface(...):
  baseline = {"inputs": infer_fields([e.inputs for e in examples]),
              "outputs": {x: infer_fields([e.outputs for e in examples if e.exit == x]) for x in proto.exits}}   # C-S2
  resp = llm(infer_schema, payload{step, instruction, exits, examples (numbered), baseline (as proto YAML types),
                                   constraints, given half (if one half is declared),
                                   previous_interface = lock.interface if a lock exists,
                                   guidance})
  iface = to_fields(resp)                               # each type string parsed with spec.parse_type
  errs = check_examples(examples, build_models(...))
  if errs: one re-call with the errors appended ("problems with your previous answer"); if still errs or
           resp.problems: ask clarification(problems) -> awaiting
  return iface
```

`infer_fields` is the deterministic baseline. The LLM refines what values alone cannot show: `path` (never inferred
by the baseline), identifiers made of digits (string, not integer), fields of exits that have no example, which
fields are optional, `free_text` flags and descriptions. The result is presented, not asked (6.4). The plain
language comes from `describe_fields` (C-S2), for example `due_date — a date (YYYY-MM-DD)`, plus the LLM's
one-line descriptions and a free-text note:

```
fix_fields takes:
  - fields — a group with fields supplier, invoice_number, total, currency, due_date: the fields to repair
  - errors — a list of groups with fields field, code, message, fixable: what validation found
  - invoice_text — text: the invoice text the fields came from
It finishes in one of 2 ways:
  - done, giving back supplier, invoice_number, total, currency, due_date (the repaired fields)
  - cannot_fix, giving back reason — text (free text: tests check it is present, not its wording)
```

Inferred schemas are **not** written into the proto, because the user edits examples, not schemas. They live in
the lock as `interface`, marked `compiled.inferred_interface: true`. On a recompile the previous interface is
passed as a stability hint, so field names stay put unless the examples contradict them.

### 7.4 Example checks (deterministic, no LLM)

- **Conformance.** `check_proto_step` (01) validates every example against the models. This also covers proto-steps
  that declare schemas.
- **Contradiction.** Two examples whose canonical inputs are equal but whose exit or outputs differ produce a
  clarification naming both example numbers.
- **Unknown exit.** An example exit that is not in `exits` and is not `error` produces a clarification.
- **Canonicalisation.** `canonical(e)` coerces values through the models, dumps them in JSON mode (dates become ISO
  strings) and keeps only the keys the example gave. Strings starting with `{tmp}` are kept verbatim (9.3).
  Canonical examples are what the tests embed.

### 7.5 Edge-case proposals

```
propose_and_confirm(...):
  resp = llm(propose_examples, payload{step, instruction, interface + plain text, examples (numbered),
                                       fixture_files = paths (relative to the example base dir) of files under it, max 200,
                                       rejected = lock.compiled.rejected (on recompile) + rejected in this session,
                                       process_context = {goal, upstream/downstream nodes with their instructions},
                                       max_proposals, guidance})
  valid = []
  for p in resp.proposals[:max_proposals]:
      ex = {"inputs": json.loads(p.inputs_json), "exit": p.exit, "outputs": json.loads(p.outputs_json)}
      keep if it conforms, is not a duplicate, and every relative path input exists (or the example expects exit "error");
      otherwise emit a warning "dropped an invalid proposal: <reason>"
  added, pending = [], False
  for i, (p, ex) in enumerate(valid, 1):
      a = ask(Question(kind="example_proposal", id=f"{node}.example{i}", text=p.question,
                       detail="Why: " + p.rationale, proposed=ex, expects="decision", default="accept"))
      if a is None: pending = True; continue
      match a.decision:
          "confirm" -> added.append(ex | {"description": p.question})
          "reject"  -> rejected.append(p.question)
          "correct" -> ex2 = a.example or llm(revise_example, {interface, proposal: ex, answer: a.text}).example
                       (a drop from revise_example counts as reject)
                       added.append(canonical(ex2) | {"description": p.question})
  return added, pending
```

Every compiled step gets proposals, including shell steps. A split shares one proto, so proposals are made once for
both halves. Confirmed examples are appended to the proto-step YAML with `description` set to the question (01's
`Example.description`), which is how they become permanent tests.

### 7.6 Running one attempt (`attempts.py`)

```python
@dataclass
class AttemptResult:
    n: int
    kind: Literal["deterministic", "agentic", "shell"]
    tier: str | None
    cases: dict[int, Literal["handled", "deferred", "wrong"]]     # example number -> class
    failures: list[FailureInfo]                                   # for the revise prompt (13.4)
    static_errors: list[str]
    all_passed: bool
    handled: int; deferred: int; wrong: int
    cassettes: Path | None                                        # snapshot of this attempt's recordings
    events_file: Path | None
    duration_ms: int

def run_attempt(env: CompileEnv, pkg_dir: Path, files: dict[str, str], *, kind, entrypoint: str,
                requirements: list[str], expected: Interface, mode: Literal["replay", "record"], n: int) -> AttemptResult
```

```
run_attempt(...):
  write files into pkg_dir (the module, and the test file generated in 9.3; overwrite the previous attempt)
  lint = check_step_module(source, path) (C-R6) + astcheck.check(...) (9.7)
  if lint errors: return every example as "wrong", with the lint messages as the failure text
  key = test key (6.6); if key in memo.tests: return memo.tests[key]
  python = deps.step_python(requirements)                                  # C-P3; cached venv per requirement set
  got = deps.describe(python, pkg_dir, entrypoint)                         # C-R7
  diffs = interfaces_equivalent(expected, got)                             # C-S3
  if diffs: return every example as "wrong", with "interface differs from the models block: …"
  if mode == "record": empty pkg_dir/"cassettes"
  suite = deps.run_step_suite(pkg_dir, python=python, mode=mode,
                              env=job env + {WYND_DEFAULT_PROVIDER: <process provider>, WYND_EVENTS_FILE: scratch/…/events.jsonl},
                              junit=scratch/…/junit.xml, basetemp=scratch/…/pytest, timeout_s=1800)   # C-P5
  classify each case (table below)
  if mode == "record": copy pkg_dir/"cassettes" -> scratch/attempts/<node>/<n>/cassettes     # restorable (9.4)
  if not all passed: memo.tests[key] = summary
```

Per-example classification uses the `WYND-EXPECT` JSON line in each failure message (9.3.3):

| Case outcome | Payload | Class |
|---|---|---|
| passed | | `handled` |
| failed | `actual_exit == "error"`, `expected_exit != "error"`, `error.error_type == "NotImplementedError"` | `deferred` |
| failed | anything else (wrong exit, wrong outputs, another exception) | `wrong` |
| error (collection error, import error, timeout) | | `wrong` (the message is kept for revision) |

### 7.7 Tool selection, MCP discovery and snapshot (`tools.py`)

```python
@dataclass
class ToolCatalog:
    builtins: list[BuiltinToolInfo]            # from wynd.runtime.tools (C-R8)
    mcp: list[McpServerInfo]                   # registry servers plus discovered tools (C-R9); discovered once per job

def build_catalog(registry: Registry, list_tools) -> ToolCatalog            # a discovery failure omits the server and warns
def lock_tools(static: StaticReport, catalog: ToolCatalog) -> tuple[list[ToolSnapshot], list[McpSnapshot]]
def used_tools(events_file: Path) -> set[tuple[str, str]]                   # (server, tool) from tool.call events (C-R5)
def narrow_allow(mcp: list[McpSnapshot], used: set[tuple[str, str]]) -> list[McpSnapshot] | None   # None: nothing to narrow
```

The order of preference is set in the prompts (14.8) and checked afterwards:

1. **`runtime.tools` builtins.** The agentic prompt lists them first, with signatures and effects. Deterministic code
   may call them directly as functions. Snapshot: `ToolSnapshot(source="library")`.
2. **`@tool` methods** the compiler writes, only for step-specific logic. They are extracted from the AST with their
   decorator arguments. Snapshot: `ToolSnapshot(source="method", effects, idempotent, env)`.
3. **Registry MCP servers** (`McpServer(name, allow=[...])`). An `allow` that names a tool missing from the
   discovered list fails the attempt statically. After the agentic tests pass in record mode, `used_tools()` gives
   the MCP tools that were actually called. If `allow` is a strict superset, the compiler narrows it to that set
   and re-runs the tests once in record mode. If that passes, it keeps the narrowed list; if not, it keeps the
   original and the report says so. This is "the narrowest allow list that covers the step's examples", measured
   rather than guessed. A server whose `allow` ends up empty is removed.

Lock snapshots follow 01: `McpSnapshot(server, allow, tools=[McpToolSnapshot(name, description, input_schema,
output_schema, idempotent)], hash)`. Only allowed tools are snapshotted, and the runtime verifies them against the
live server (spec 3.8). An MCP tool is idempotent only if the server annotates it `idempotentHint: true`. Retries
follow 01's `RetryPolicy`: `tool: 1` applies only to idempotent tools (the runtime enforces this). A server's
`auth_env` names go into `fragment.vars` with `used_by: ["mcp:<server>"]`.

---

## 8. The compile decision (rules 1–4)

### 8.1 How the decision is made

The decision combines **LLM judgement** (a per-example classification) with **empirical evidence** (deterministic
code is actually tried whenever it could plausibly win). This follows "Deterministic first: anything that can be a
function is a function" (spec 13) and gives rule 3 a real trigger, since "passes most but not all examples" is a
measured fact, not a guess.

Rule precedence: **4, then 1 and 3 (tried together), then 2.**

```
decide_and_build(...):
  # Rule 4: shell
  if proto.exit_codes is not None:
      return shell_loop(reason="proto-step declares exit_codes", rule=4)
  d = llm(decide, payload{step, instruction, interface, examples (numbered), catalog summary, process context, guidance})
  record classification in report
  if d.command:
      return shell_loop(reason=d.summary, rule=4)

  total = len(examples); pure = count(e.needs == "pure")
  # Rule 2 directly: deterministic cannot win when at most half the examples are rule-shaped
  if pure * 2 <= total:
      return agentic_with_escalation(rule=2, reason=f"{total - pure} of {total} examples need judgement/knowledge/data")

  # Rules 1 and 3: try deterministic first
  det = generate_test_revise(kind="deterministic")        # 8.2
  if det.inconsistent: return ask_clarification(det)     # the model flagged contradictory examples: ask, don't fall back
  if det.best.all_passed:
      return Built(kind="deterministic", rule=1, ...)
  if split_eligible(det.best):                            # 8.3
      if not (why_not := split_applicable(proc, node)):
          agt = agentic_with_escalation(rule=3, split_half=True)
          if agt.passed: return Built(kind="split", rule=3, det=det.best, agentic=agt.best, split=SplitPlan(...))
          return agt                                      # clarify
      emit("decision", f"deterministic code handled {h}/{total}; a split is not possible here: {why_not}")
  return agentic_with_escalation(rule=2, reason=f"deterministic code handled only {h}/{total} examples")
```

`shell_loop` failing asks a clarification (`<node>.clarify<k>`) and never falls back to an agent, because a step
described as a command is a command. `agentic_with_escalation` failing, or any loop ending with verdict
`examples_inconsistent`, asks a clarification too.

### 8.2 Generate, test, revise loop

```
generate_test_revise(kind, tier=None, max_revisions=options.max_revisions) -> LoopResult:
  tests = testgen.step_tests(...)                           # generated BEFORE code, from canonical examples (9.3)
  attempt 1:  files = codegen.write(kind, ...)              # write_deterministic | write_shell | write_agentic
              r1 = run_attempt(files, mode)                 # mode: record for agentic, replay for det/shell
  for i in 2 .. max_revisions + 1:
      if r_{i-1}.all_passed: break
      rev = codegen.revise(kind, previous files, failures(r_{i-1}), earlier diagnoses)   # revise_code | revise_agentic
      emit("test", f"attempt {i-1}: {passed}/{total} passed; {rev.diagnosis}")
      if rev.verdict == "examples_inconsistent": stop loop; mark suspects
      if kind == "deterministic" and rev.verdict == "needs_judgement" and r_{i-1}.wrong == 0: stop loop   # split-shaped already
      if kind == "agentic" and rev.verdict == "needs_stronger_model": stop loop; mark escalate
      r_i = run_attempt(rev files, mode)
  best = max(attempts, key=score)
  return LoopResult(best, attempts, suspects, last_diagnosis)

score(deterministic) = (all_passed, wrong == 0, handled, -attempt_index)
score(agentic|shell) = (all_passed, handled, -attempt_index)
```

- **Deterministic code is always told to defer** (raise `NotImplementedError`) on inputs it cannot handle with
  confidence. A loud failure can be routed to a fallback; a wrong answer is silently wrong. A deferral counts as a
  failure for rule 1, but it is exactly what rule 3 needs.
- **Agentic with escalation:** the loop runs at `cheap` (the spec default, with `thinking: low`). If it does not
  pass, one further loop runs at `standard` with `max_revisions=1`. The tier that passed is recorded in the lock
  with the reason: "cheap failed examples 3 and 5 after 4 attempts; standard passed". If both fail, the step asks
  `clarify` (I-7).
- **Budget per step** (worst case, `max_revisions=3`): deterministic 4 attempts, agentic cheap 4 + standard 2,
  shell 4. Every agentic attempt re-records all examples live (spec 7: "always records live while generating or
  revising").
- **Clarification on persistent failure.** This is the question text, built deterministically from the last
  diagnosis and the suspects:

```
I could not make extract_invoice_fields pass its examples after 6 attempts
(deterministic: 3/6 at best; agentic at cheap and standard tiers: 5/6 at best).
What went wrong: the expected total in example 4 does not match any amount in the invoice text.
These examples look inconsistent:
  - Example 4: the text says "Amount due: 1,250.00" but the expected total is 1200.50.
Answer with guidance (for example "treat credit notes as not_an_invoice"); it will be used when this step
is compiled again. To change an example itself, edit the proto-step and compile again.
```

  Id `<node>.clarify<k>`, where `k` = 1 + the number of already-answered clarify questions for that node. No
  default.

### 8.3 Split eligibility and applicability

```
split_eligible(r) = r.wrong == 0 and r.handled * 2 > r.total and r.deferred >= 1   # deferred = raised NotImplementedError (7.6)
```

"Most" means strictly more than half (I-2). Zero wrong answers is required: the deterministic half must hand off
rather than guess.

`split_applicable(proc, node) -> str | None` (returns the reason it is *not* applicable):

| Check | Reason when it fails |
|---|---|
| `rs.ref_kind == "local"` | "the step comes from a step root and is shared with other processes" (I-8) |
| exactly one node in the process uses this package | "the package is used by several nodes" |
| no existing edge `from: <node>.error` | "an edge already handles `<node>.error`" |
| no expression outside the edges `from: <node>.*` references `steps.<node>` or `edges["<node>.…"]` | "edge `<from>` references `steps.<node>.outputs.<f>`, which would be missing when the agentic half ran" |
| names `<node>_agentic` and package `<step>_agentic` are free | "name `<node>_agentic` is already taken" |

When eligible but not applicable, the step becomes a plain `AgenticStep` (rule 2). The report states both
numbers and the reason.

### 8.4 Decision record (in report and lock)

```yaml
decision:
  kind: split                 # deterministic | agentic | shell | split
  rule: 3
  reason: >-
    Deterministic code handled 4 of 6 examples (invoices in the standard template) and deferred the other 2
    (free-form letters). The deferred inputs go to extract_invoice_fields_agentic via extract.error.
  classification:             # from the decide call
    - {example: 1, needs: pure, why: "fixed 'Invoice No:' / 'Total:' template"}
    - {example: 5, needs: judgement, why: "free-form letter, amount written in words"}
  attempts:
    - {kind: deterministic, n: 1, handled: 3, deferred: 2, wrong: 1}
    - {kind: deterministic, n: 2, handled: 4, deferred: 2, wrong: 0}
    - {kind: agentic, tier: cheap, n: 1, handled: 6, deferred: 0, wrong: 0}
  tier: cheap                 # agentic only
```

---

## 9. Generated artefacts

### 9.1 Step package layout (P4; the M1 hand-written steps follow it too)

```
processes/process_supplier_invoice/
  proto/extract_invoice_fields.yaml
  steps/extract_invoice_fields/              # package dir name == proto name
    pyproject.toml
    step.lock.yaml
    extract_invoice_fields.py                # the step module: exactly one Step subclass
    test_extract_invoice_fields.py           # generated tests
    cassettes/                               # agentic only; runtime cassette format; git-lfs
  tests/                                     # process-level integration tests (11)
    test_process.py
    cassettes/
```

- This is 08's flat layout. `entrypoint: extract_invoice_fields:ExtractInvoiceFields` is **relative to the package
  directory**, which works with 02's loader: it mounts the directory as `wynd_steps.<step_package_name(key)>` and
  imports `<that>.extract_invoice_fields`. Two packages with the same module name therefore never collide in a venv.
  01's example `extract_invoice_fields.step:…` and 02's `step.py` are the alternatives; see 19.
- Class name = PascalCase of the package name (`extract_invoice_fields` → `ExtractInvoiceFields`). A split's
  agentic half is `extract_invoice_fields_agentic` / `ExtractInvoiceFieldsAgentic`.
- Generated tests load the class with `load_step(Path(__file__).parent)` (02 §4.5), not by top-level import.
- `pyproject.toml` (08's template, verified with `uv build --wheel`: the wheel ships only the module):

```toml
# Generated by wynd compile. Edit freely: the compiler rewrites this file only when the proto-step changes.
[project]
name = "process-supplier-invoice-extract-invoice-fields"
version = "0.1.0"
description = "Wynd step extract_invoice_fields (process_supplier_invoice)"
requires-python = ">=3.12"
dependencies = []          # third-party only; wynd-spec/wynd-runtime are installed into every venv by the builder

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
only-include = ["extract_invoice_fields.py"]
```

The project name is `<process-id or root alias + path, slugged>-<package>`, which is unique per workspace.

### 9.2 `step.lock.yaml` (01's `StepLock`, written by the compiler)

Agentic example:

```yaml
# Generated by wynd compile. This is a review surface: hand edits are kept until the proto-step changes.
# Tier-to-model mapping lives in the user registry, never here.
wynd: 1
name: extract_invoice_fields
kind: agentic
entrypoint: extract_invoice_fields:ExtractInvoiceFields
proto_hash: sha256:9f2b6c0e5a71d3c4b8e0f1a2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f6
tier: cheap                      # provider omitted = process default (wynd compile never sets it, spec 3.9)
thinking: low
retries: { run: 1, validation: 2, tool: 1 }       # DEFAULT_RETRIES["agentic"] (01)
effects: []
tools: []
mcp: []
context: [process.goal]
fragment:
  deps: []
  system: []
  vars: []
locked_deps: []
interface:
  input: { type: object, properties: { invoice_text: { type: string } }, required: [invoice_text] }
  outputs:
    done:
      type: object
      properties:
        exit: { const: done }
        supplier: { type: string }
        invoice_number: { type: string }
        total: { type: number }
        currency: { type: string }
        due_date: { type: string, format: date }
      required: [supplier, invoice_number, total, currency, due_date]
    not_an_invoice: { type: object, properties: { exit: { const: not_an_invoice } } }
compiled:                        # 9.2.1 (compiler provenance; REQUIREMENT C-S5)
  session: job_01j9z6q4m8x2
  job: job_01j9z9a2k7t3
  compiler: wynd-compiler 0.1.0
  model: claude-code/strong
  decision: { kind: agentic, rule: 2, reason: "All 4 examples need reading comprehension of free-form invoice text." }
  inferred_interface: false
  free_text: []
  guidance: []
  rejected: ["What should happen if the total is written in words?"]
  split: null
```

Deterministic example (the parts that differ):

```yaml
kind: deterministic
entrypoint: read_pdf:ReadPdf
retries: { run: 0, validation: 0, tool: 0 }
effects: [filesystem]
fragment:
  deps: ["pypdf>=6,<7"]
  system: []
  vars:
    - name: PDF_PASSWORD
      description: Password for encrypted supplier PDFs, if any.
      secret: true
      required: false
      used_by: ["step:process_supplier_invoice#read_pdf"]
locked_deps: ["pypdf==6.19.0"]
```

How each field is filled in:

| Field | Source |
|---|---|
| `kind`, `entrypoint` | The decision (8) |
| `interface` | `interface_from_fields` of the (declared or inferred) fields, checked equivalent to what `describe` printed (7.6) |
| `tier`, `thinking` | `cheap` / `low` (`DEFAULT_TIER`/`DEFAULT_THINKING`), unless escalated to `standard` (8.2). Not written for other kinds |
| `retries` | `DEFAULT_RETRIES[kind]` (01): deterministic and shell `{0,0,0}`, agentic `{run: 1, validation: 2, tool: 1}` |
| `effects` | Codegen's declared effects ∪ tool effects ∪ `network` if `mcp` is non-empty ∪ `shell` for ShellStep (01 checks this) |
| `tools`, `mcp`, `context` | AST extraction (9.7) plus the catalog (7.7) |
| `shell` | `ShellLock(exit_codes=proto.exit_codes or {0: done, "*": error})` for ShellStep |
| `fragment.deps` | `proto.env.deps` ∪ codegen `deps` (deduplicated by normalised name; the proto's specifier wins) |
| `fragment.system`, `requires` | proto ∪ codegen `system_packages` / `requires_glibc` |
| `fragment.vars` | AST env scan ∪ codegen `env_vars` ∪ tool env ∪ MCP `auth_env`, merged with `merge_env_vars` (01). Provider vars are never listed; they come from the provider's fragment |
| `locked_deps` | `UvResolver.compile(fragment.deps, universal=True)` (C-P4), minus `wynd-spec`/`wynd-runtime`; `[]` when there are no deps |

Test results are never written here (spec 6.3). They go to the RunRegistry through `run_tests(commit=…)` (C-P6).

#### 9.2.1 `compiled` (requested addition to 01's `StepLock`)

```python
class CompiledSplit(SpecModel):
    role: Literal["deterministic", "agentic"]
    node: str                        # this package's node in process.yaml
    partner: str                     # the other half's package dir name
    partner_node: str
    handled: list[int] = []          # example numbers (deterministic half)
    deferred: list[int] = []

class CompiledInfo(SpecModel):
    session: str
    job: str
    compiler: str                    # "wynd-compiler <version>"
    model: str                       # "<provider>/<tier>" used by the compiler for codegen
    decision: dict                   # {kind, rule, reason}
    inferred_interface: bool = False
    free_text: list[str] = []        # output fields tested for presence only
    guidance: list[str] = []         # clarification answers (I-9)
    rejected: list[str] = []         # rejected edge-case questions (7.5)
    split: CompiledSplit | None = None
```

Only the compiler reads `compiled`. Other packages ignore it, and `wheel_metadata()` drops it.

### 9.3 Generated test files (P5)

#### 9.3.1 Step test file (`testgen.step_tests`)

The output is deterministic (the same examples give the same bytes), and it is generated **before** any code
exists.

```python
# Generated by wynd compile from proto/extract_invoice_fields.yaml (sha256:9f2b6c0e…).
# One test per example. Change the proto-step's examples and recompile instead of editing this file.
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step, sub_tmp

ExtractInvoiceFields = load_step(Path(__file__).parent)
BASE = Path(__file__).resolve().parents[2]   # relative example paths resolve against the process directory


def test_example_1(tmp_path):
    result = run_step(
        ExtractInvoiceFields,
        {"invoice_text": "ACME Supplies Ltd\nInvoice No: INV-1042\nTotal: £1,200.50\nDue: 1 October 2026"},
        workspace=tmp_path,
    )
    expect(
        result,
        exit="done",
        outputs={"supplier": "ACME Supplies Ltd", "invoice_number": "INV-1042", "total": 1200.5,
                 "currency": "GBP", "due_date": "2026-10-01"},
    )


def test_example_2(tmp_path):
    result = run_step(ExtractInvoiceFields, {"invoice_text": "Dear customer, your order has shipped"}, workspace=tmp_path)
    expect(result, exit="not_an_invoice")


def test_example_3(tmp_path):
    """What should happen if the invoice has no due date?"""
    result = run_step(ExtractInvoiceFields, {"invoice_text": "INVOICE INV-2001\nTotal due: £80.00"}, workspace=tmp_path)
    expect(result, exit="not_an_invoice")
```

A path-typed step (from `read_pdf`) and a `{tmp}` example (from `save_record`):

```python
def test_example_1(tmp_path):
    result = run_step(ReadPdf, {"pdf_path": str(BASE / "examples/acme_inv_1042.pdf")}, workspace=tmp_path)
    expect(result, exit="done", outputs={"pages": 1}, present=["text"])


def test_example_1(tmp_path):
    inputs = sub_tmp({"record": {...}, "dest": "{tmp}/records"}, tmp_path)
    result = run_step(SaveRecord, inputs, workspace=tmp_path / "ws")
    expect(result, exit="done", outputs=sub_tmp({"record": {...}, "path": "{tmp}/records/acme-supplies-ltd__INV-1042.json"}, tmp_path))
```

Generation rules:

- One function per example: `test_example_<n>`, where n is the 1-based position in the (appended) proto example
  list (08's naming; the compiler maps failures back to examples by n). An example's `description` becomes the test's docstring.
- Values are canonical JSON (7.4), emitted with `pprint.pformat(width=100)`, so the result is valid Python.
- Path-typed inputs with relative values are emitted as `str(BASE / "<value>")`. `BASE` is `parents[2]` for
  process-local steps (the process dir) and `parents[0]` for step-root steps (the package dir; 01 §6.3).
- Any value containing the literal prefix `{tmp}` makes the test wrap that example's inputs and expected outputs in
  `sub_tmp(…, tmp_path)` (08's convention). When `{tmp}` is used, the run workspace moves to `tmp_path / "ws"` so the
  step's own workspace does not collide with the example's paths.
- An example `env:` mapping (08's extension, C-S1 note) becomes `monkeypatch.setenv` calls, after `sub_tmp`.
- `present=[...]` lists the `compiled.free_text` fields that the example gives (their value is only checked to be
  present). It is omitted when empty.
- Split, deterministic half: deferred examples are emitted as `expect(result, exit="error")`, with the comment
  `# handled by extract_agentic (split): this step must hand off, not guess`. Split, agentic half: every example.
- Examples with `exit: error` become `expect(result, exit="error")`.

#### 9.3.2 Comparison semantics (`wynd.runtime.testing.match_outputs`, REQUIREMENT C-R3)

| Case | Rule |
|---|---|
| exit | Exact string equality. On a mismatch with actual `error`, the message includes the error payload (`cause`, `message`, `error_type`) |
| outputs | **Subset match.** Only the keys the example gives are checked, so partial examples are allowed. `outputs=None` checks nothing beyond the exit |
| nested mapping | Recursive subset match (`record: {}` matches any object) |
| list | Same length, element-wise match, order matters |
| bool | Exact (checked before number, since `bool` is an `int`) |
| number (int or float) | `math.isclose(a, e, rel_tol=1e-9, abs_tol=1e-9)`, so `1200.5 == 1200.50` and `1200 == 1200.0` |
| string | Exact. Dates and datetimes compare as the ISO strings pydantic emits in JSON mode, since the compiler canonicalised the expected side the same way |
| `present` field | The key exists, and the value is not `None` and not `""` |
| `None` expected | Actual is `None` or absent |

The actual side is `result.output.model_dump(mode="json", exclude={"exit"})` for steps, and `result.outputs` for
processes. 04's process-example runner should call the same `match_outputs`, so step tests and process tests share
one semantics.

#### 9.3.3 `wynd.runtime.testing` additions (REQUIREMENT C-R3)

```python
def expect(result, *, exit: str, outputs: Mapping[str, Any] | None = None, present: Sequence[str] = ()) -> None:
    """Raise AssertionError whose message starts with one line:
       'WYND-EXPECT ' + json.dumps({"expected_exit": …, "actual_exit": …,
                                    "mismatches": [{"field": "a.b[2]", "expected": …, "actual": …}],
                                    "error": {"cause": …, "message": …, "error_type": …} | null})
       followed by a human-readable diff."""

def match_outputs(expected: Mapping[str, Any], actual: Mapping[str, Any], *, present: Sequence[str] = ()) -> list[Mismatch]

def sub_tmp(value: Any, tmp: Path) -> Any:
    """Recursively replace a leading '{tmp}' in every string with str(tmp)."""
```

`run_step` and `load_step` are used as 02 defines them. A replay miss surfaces as `exit == "error"` with
`cause == "transport"` and the explicit "no recording for this request; re-record with `wynd test --live`"
message (02 §4.5). `expect` shows it verbatim, so the test fails loudly.

### 9.4 Cassettes: recorded live, promoted, verified

1. **Recording.** For every agentic attempt, `run_attempt` empties `pkg/cassettes/` and runs the tests with
   `WYND_CASSETTE_MODE=record`. Afterwards the dir holds exactly that attempt's recordings (C-R4). The compiler
   snapshots the dir to `scratch/attempts/<node>/<n>/cassettes`.
2. **Promotion.** When an attempt is accepted (after MCP narrowing), its snapshot is copied back into
   `pkg/cassettes/`. This is a no-op when it was the last attempt run. Stale entries disappear because the whole
   directory is replaced.
3. **Replay check.** The tests are re-run once with `WYND_CASSETTE_MODE=replay`. A failure here, usually a
   normalisation bug in cassette keys, fails the step with the runtime's cassette-miss message. It is reported as
   a runtime problem, not asked as a clarification.
4. **Size check.** If `sum(size(pkg/cassettes/**)) > ws.config.cassette_warn_mb`, the report warns: "cassettes for
   extract_invoice_fields are 7.2 MB (limit 5 MB); they are tracked with git-lfs; consider fewer or smaller
   examples".

Deterministic and shell steps have no cassettes. If a recompile changes a step's kind away from agentic, its
`cassettes/` dir is removed.

### 9.5 Models block (`schemas.render_models_block`)

The Python rendering mirrors 01's `build_models` mapping (01 §5.2): `string→str`, `number→float`, `integer→int`,
`boolean→bool`, `date→date`, `datetime→datetime`, `path→Path`, `object→dict[str, Any]`, `list[T]→list[T]`,
nested mapping → nested model `<Parent><Field>`, one-item sequence `[T]` of a mapping → `<Parent><Field>Item`, and
`T?` → `T | None = None`. Every model sets `ConfigDict(extra="forbid")`. The block is indented four spaces for the
class body, and codegen must include it verbatim (the interface check enforces this):

```python
    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")
        invoice_text: str

    class Done(BaseModel):
        model_config = ConfigDict(extra="forbid")
        exit: Literal["done"] = "done"
        supplier: str
        invoice_number: str
        total: float
        currency: str
        due_date: date

    class NotAnInvoice(BaseModel):
        model_config = ConfigDict(extra="forbid")
        exit: Literal["not_an_invoice"] = "not_an_invoice"

    Output = Done | NotAnInvoice
```

The exit class name is PascalCase(exit), with `Exit` appended if that collides with `Input` or `Output`. A done-only
step renders `Output = Done`. The module source is never executed in the compiler's own process. The interface is
checked in the step venv through `wynd.runtime.describe` (C-R7) and compared with `interfaces_equivalent` (C-S3),
which ignores titles and defaults.

### 9.6 Appending confirmed examples to the proto (`examples.append_examples`, via `yamledit.py`)

```python
def append_block_items(text: str, key: str, items: list[Any], *, comment: str) -> str
def append_mapping_entries(text: str, key: str, entries: dict[str, Any], *, marker: str) -> str
def remove_marked(text: str, marker: str) -> str
```

This is surgical editing, 01 §13 option (b), so comments survive. The algorithm for
`append_block_items(text, "examples", items, comment)`:

1. Find the top-level line matching `^examples:\s*(#.*)?$`.
   - Found, with a block list below: the block ends at the first following line that is non-blank, not a comment
     and at indentation 0 (or at EOF). The item indent is taken from the block's first `-` line (`"  - "` or
     `"- "`). New lines go after the block's last content line.
   - Found as `examples: []`: that line is replaced by `examples:` followed by the items.
   - Missing: `examples:` and the items are appended at EOF.
2. Each item is rendered with `yaml.safe_dump([item], sort_keys=False, allow_unicode=True, width=100,
   default_flow_style=None)` and re-indented. Key order is `inputs, outputs, exit, description`. Values of date
   fields are converted to `datetime.date` so they dump unquoted, like hand-written ones.
3. A comment line is prepended: `  # confirmed during wynd compile (session job_01j9z6q4m8x2)`.
4. **Verify** that `load_proto_step(new)` equals the old model with the examples extended. If it does not, fall
   back to `dump_yaml(proto.to_authoring())` (C-S6), which loses comments, and warn in the report: "proto-step YAML
   was reformatted (comments lost)".

The same functions drive the `process.yaml` edits (10.2). The proto hash is computed from the new text after the
append.

### 9.7 Static checks (`astcheck.py`, after 02's `check_step_module`)

```python
@dataclass
class StaticReport:
    errors: list[str]
    env_vars: set[str]
    tools: list[str]                          # names in `tools = [...]`
    mcp: list[tuple[str, list[str]]]          # McpServer("x", allow=[...]) literals
    context: list[str]
    tool_methods: list[ToolMethodInfo]        # name, effects, idempotent, env from @tool(...)

def check(source: str, *, class_name: str, base: Literal["DeterministicStep", "AgenticStep", "ShellStep"],
          catalog: ToolCatalog, upstream_nodes: list[str]) -> StaticReport
```

02's `check_step_module` covers the generic rules (module-level state and so on). Compiler-specific errors, which
are fed back into revision:

- There is not exactly one class named `class_name` deriving from `base`.
- An agentic `tools`, `mcp` or `context` attribute that is not a literal list of names, `McpServer(...)` calls with
  literal `allow`, or strings. An `AgenticStep.run` body other than `...`.
- An unknown builtin tool, unknown MCP server or unknown MCP tool. A `context` entry that 01's `parse_context_entry`
  rejects, or that names a node which is not upstream.
- `subprocess` or `os.system` in a non-shell step without `shell` in its declared effects.

The env var scan collects string-literal first arguments of `self.runtime.env(…)`, `env(…)`, `os.environ[…]`,
`os.environ.get(…)` and `os.getenv(…)`, plus `env=[...]` in `@tool(...)` decorators.

### 9.8 Agentic module rendering (`codegen.render_agentic`)

Agentic modules are **rendered by the compiler** from the `write_agentic` response (docstring, context, tools, mcp,
tool methods). The LLM never writes the scaffolding, so it cannot get it wrong:

```python
"""Generated by wynd compile from proto/extract_invoice_fields.yaml. Hand edits are kept until the proto-step changes."""
from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wynd.runtime import AgenticStep


class ExtractInvoiceFields(AgenticStep):
    """Extract the key payment fields from the text of a supplier invoice.

    Choose `done` when the text is an invoice requesting payment: it has an invoice number, an amount due and a
    due date. Choose `not_an_invoice` for anything else, including quotes, receipts, delivery notes, order
    confirmations and invoices with no due date.

    - supplier: the name of the company that issued the invoice, as printed in its letterhead.
    - invoice_number: the supplier's invoice reference exactly as printed (keep prefixes such as "INV-").
    - total: the final amount due including tax, as a plain number without currency symbols or thousands separators.
    - currency: the ISO 4217 code (£ is GBP, € is EUR, $ is USD unless another dollar is named).
    - due_date: the payment due date as YYYY-MM-DD. If only payment terms are given (e.g. "30 days"), add them to
      the invoice date.
    """

    # models block, verbatim (9.5)

    context = ["process.goal"]
    tools = []
    mcp = []

    def run(self, input: Input) -> Output: ...
```

Imports of tools (`from wynd.runtime.tools import web_search`), `McpServer` and `tool` are added only when they are
used. Tool methods from the response are inserted verbatim between `mcp` and `run`, and the result goes through the
static checks. Deterministic and shell modules are written in full by the LLM, since their logic is the point, and
then checked.

---

## 10. Two-step split: rewriting `process.yaml` (rule 3)

### 10.1 What gets produced

For node `extract` using `./steps/extract_invoice_fields`:

| Artefact | Content |
|---|---|
| `steps/extract_invoice_fields/` | The `DeterministicStep` (keeps the node name, the incoming edges and the entry role). Its tests assert handled examples and `exit="error"` for deferred ones |
| `steps/extract_invoice_fields_agentic/` | An `AgenticStep` with the **same Input and Output**, docstring written with the split note (14.8), and tests covering all examples, with cassettes |
| `process.yaml` | New node `extract_agentic`, an edge `extract.error → extract_agentic`, and a copy of every edge `from: extract.<exit>` as `from: extract_agentic.<exit>` with references renamed |
| Both locks | `compiled.split` sections (10.4) and the same `proto_hash` |

### 10.2 The rewrite (`split.py`)

```python
@dataclass
class SplitPlan:
    node: str                    # extract
    agentic_node: str            # extract_agentic
    agentic_use: str             # ./steps/extract_invoice_fields_agentic
    input_fields: list[str]      # the Input field names

def apply_split(text: str, spec: ProcessSpec, plan: SplitPlan) -> str
def remove_split(text: str, spec: ProcessSpec, node: str) -> str
def rename_step_refs(expr: str, old: str, new: str) -> str       # via wynd.spec.expr.references spans (C-S5)
```

`apply_split` algorithm:

1. `remove_split(text, node)` first, so a re-split replaces the old one.
2. Append to the top-level `steps:` mapping (`append_mapping_entries`, same block-end detection as 9.6):

```yaml
  # wynd:split extract begin (added by wynd compile; recompiling extract_invoice_fields may remove it)
  extract_agentic: { use: ./steps/extract_invoice_fields_agentic }
  # wynd:split extract end
```

3. Build the new edges, in this order, and append them to the top-level `edges:` list inside the same markers:
   - `{from: extract.error, to: extract_agentic, with: {<f>: "steps.extract.outputs.inputs.<f>" for f in input_fields}}` (the error payload carries the bound inputs, C-S9)`
   - For every edge `E` with `E.from == "extract.<x>"`: a deep copy with `from: extract_agentic.<x>`, and every
     expression string in `to` / `with` / `when` / `limits` passed through
     `rename_step_refs(expr, "extract", "extract_agentic")`, which locates each step-name token from the `Ref` line/column that `references()` returns (C-S7) and re-checks the result with `references()`. This renames `steps.extract.*` and
     `edges["extract.*"]`. `previous.*` needs no change. Branch `name:`s are kept, so counters stay addressable.
4. Items are dumped with `yaml.safe_dump(..., sort_keys=False, default_flow_style=None, width=100)` and
   re-indented to the list's indent.
5. **Verify:** re-parse, and check that the parsed document equals the old one plus exactly the planned node and
   edges. Then run `validate_process(types=True)`. If validation fails, the split is abandoned: the step becomes a
   plain agentic step (whose attempt already passed), with the validator message in the report.

Result on the dogfood (the edges from `extract` before the split are `extract.done → validate` and
`extract.not_an_invoice → $exit.not_an_invoice`):

```yaml
edges:
  # ... existing edges unchanged ...
  - from: escalate.done
    to: $exit.needs_review
  # wynd:split extract begin (added by wynd compile; recompiling extract_invoice_fields may remove it)
  - from: extract.error
    to: extract_agentic
    with:
      invoice_text: steps.extract.outputs.inputs.invoice_text
  - from: extract_agentic.done
    to: validate
    with:
      fields: steps.extract_agentic.outputs
  - from: extract_agentic.not_an_invoice
    to: $exit.not_an_invoice
  # wynd:split extract end
```

`extract_agentic`'s own `error` exit has no edge, so it goes to the process error handler (spec 3.5). If either
node lies on a cycle, the validator fills in `max_traversals` on the new branches as usual.

`remove_split(text, spec, node)`: delete every line between `# wynd:split <node> begin` and `… end` (inclusive) in
both blocks. If the markers are gone (the graph editor dropped comments), remove structurally instead: the node
named in the deterministic lock's `compiled.split.partner_node`, every edge `from: <agentic_node>.*`, and the edge
`from: <node>.error` whose `to` is the agentic node. Then do a full re-dump (comments lost; the report warns). The
agentic package dir is deleted in the same commit.

### 10.3 Why only "own edges" may reference the split step

After a split, a path through `extract_agentic` has `steps.extract.exit == "error"`, with no `done` fields. Any
reference to `steps.extract.outputs.<f>` outside the edges leaving `extract` would be invalid on that path, and the
exit-aware validator would (rightly) reject it. Instead of generating guarded conditional expressions that the
validator would have to understand, the compiler makes the split applicable only when no such external reference
exists (8.3). Otherwise it compiles a plain AgenticStep and says why.

### 10.4 Lock `compiled.split` sections

```yaml
# steps/extract_invoice_fields/step.lock.yaml
compiled:
  # ...
  split:
    role: deterministic
    node: extract
    partner: extract_invoice_fields_agentic
    partner_node: extract_agentic
    handled: [1, 2, 3, 6]          # example numbers
    deferred: [4, 5]

# steps/extract_invoice_fields_agentic/step.lock.yaml
proto_hash: sha256:9f2b…           # same hash as the deterministic half (informational: this package has no proto of its own)
compiled:
  # ...
  split:
    role: agentic
    node: extract_agentic
    partner: extract_invoice_fields
    partner_node: extract
```

The agentic half has no `proto/extract_invoice_fields_agentic.yaml`. 04's loader accepts a compiled package without
a proto (C-P1). Staleness is detected through the deterministic half, whose proto changes whenever the shared proto
does. That puts the whole process back into design, and the next compile handles both halves together.

### 10.5 Reporting

- Event `split` in the session: "extract_invoice_fields was split in two. Deterministic code handles 4 of 6
  examples (the standard invoice template) and hands the other 2 (free-form letters) to a new agentic step,
  extract_invoice_fields_agentic, through extract.error. process.yaml gained 1 step and 3 edges."
- Report `process_changes[]` entry: `{"type": "split", "process": …, "node": "extract", "added_node":
  "extract_agentic", "edges_added": ["extract.error", "extract_agentic.done", "extract_agentic.not_an_invoice"],
  "handled": [1,2,3,6], "deferred": [4,5]}`.
- The CLI prints process changes in their own section, above the per-step table. The commit message has a
  "Process changes" paragraph.

---

## 11. Process-level examples as integration tests

### 11.1 Generated file

Once every node of a process has compiled or been skipped (none awaiting), `testgen.process_tests(lp)` writes
`<process_dir>/tests/test_process.py` (08's layout and names). If the process has no examples, the file and
`tests/cassettes/` are removed.

```python
# Generated by wynd compile from process.yaml examples. Runs the whole graph with the local executor.
from pathlib import Path

from wynd.process.testing import run_process
from wynd.runtime.testing import expect, sub_tmp

PROCESS_DIR = Path(__file__).resolve().parents[1]


def test_example_1(tmp_path):
    env = sub_tmp({"RECORDS_DIR": "{tmp}/records", "REVIEW_DIR": "{tmp}/review", "ESCALATIONS_DIR": "{tmp}/escalations"}, tmp_path)
    result = run_process(PROCESS_DIR, {"pdf_path": str(PROCESS_DIR / "examples/acme_inv_1042.pdf")},
                         env=env, run_id="run-example-1")
    expect(result, exit="done",
           outputs={"record": {"key": "acme-supplies-ltd__INV-1042", "supplier": "ACME Supplies Ltd",
                               "invoice_number": "INV-1042", "total": 1200.5, "currency": "GBP",
                               "due_date": "2026-10-01"}})
```

- `run_process(process_dir, inputs, *, env=None, cassettes=None, run_id=None) -> ProcessResult(exit, outputs,
  run_id, events)` is 08's C13, from `wynd.process.testing`. It runs the local executor (venv workers). Its
  cassettes default to `<process_dir>/tests/cassettes`, and its mode comes from `WYND_CASSETTE_MODE`.
- A fixed `run_id` per example (`run-example-<n>`) keeps outputs that embed `run.id` deterministic. Cassette keys
  normalise `run.id` in any case (spec 7).
- Relative path inputs become `str(PROCESS_DIR / …)`. An example's `env:` and `{tmp}` values go through `sub_tmp`
  (08's convention). Expected outputs are canonicalised (YAML dates to ISO strings) before emission.

### 11.2 Process-level phase of the compile job

After every step of the process is final:

1. Write `tests/test_process.py` (11.1).
2. From M5: `changed = sync_edge_lock(process_dir, process)` (08 C14). If `changed`, write `edges.lock.yaml`.
3. Empty `<process_dir>/tests/cassettes/` and run `deps.run_process_suite(process_dir, mode="record")`. That is
   pytest on `tests/test_process.py` **in the compile job's own interpreter**, since the tests need
   `wynd.process`. It runs with `WYND_CASSETTE_MODE=record` and the same `-p no:cacheprovider`/`--basetemp`
   hygiene. Recordings land in `tests/cassettes/` (C-R4). The process-level cassettes are recorded live by the
   compile job, as 08 C14 requires.
4. Run the same suite in replay to verify the cassettes, as in 9.4.
5. A failing process example does not trigger revision, because the failure cannot be reliably attributed to one
   step. The job ends `failed`, and the branch is still published with the report, which lists each failing
   example with its trace pointer and the mismatches. The user decides what to change: examples, instructions or
   the graph.

Alternative (reconciliation point, 19): 04 runs process examples directly from `process.yaml` in its test runner,
with no generated file and cassettes at `<process_dir>/cassettes/`. Both work. The generated file is the literal
reading of spec 9 ("process-level examples become integration tests") and is what 08's sample assumes.

---

## 12. Test running, venvs and the `wynd test --live` job

### 12.1 How the compiler runs tests

| When | Call | Mode |
|---|---|---|
| Each attempt (7.6) | `run_step_suite(pkg_dir, …)` (C-P5) in the step's venv | `record` for agentic, `replay` for deterministic and shell (neither makes model calls) |
| After an agentic attempt is accepted | `run_step_suite` again | `replay` (the cassette check, 9.4) |
| Process-level phase (11.2) | `run_process_suite` | `record`, then `replay` |
| On the final commit (5.2) | `run_tests(ws, pid, mode="replay", commit=sha, registry=ctx.run_registry)` (C-P6) | `replay`. It records results keyed by commit and step hash, so `wynd build` can reuse them |

Every pytest run uses `-q -p no:cacheprovider --import-mode=importlib --basetemp=<scratch>/… --junitxml=<scratch>/…`
with `PYTHONDONTWRITEBYTECODE=1`, following 04 §7. Nothing lands in the checkout that `ctx.commit` would pick up.

### 12.2 Venvs

`deps.step_python(requirements)` (C-P3) returns the interpreter of a cached local venv under `ctx.state_dir/venvs`
(04 §6.4: spec + runtime + pytest + the requirements). The requirements are the attempt's `fragment.deps` plus, for
agentic steps, the provider fragment's deps (`claude-agent-sdk` for claude-code). Two attempts, or two steps, with
the same requirement set share a venv, as the builder's venv-per-dependency-set does. Venv creation is the slowest
part of a first compile, and it happens once per distinct set.

### 12.3 The `wynd test --live` job (behaviour; implemented as 04's `run_test_live_job`)

06 and 04 put the `test_live` handler in `wynd.process.testing`. The compiler does not duplicate it. It re-records,
while `wynd compile` records while generating, so these are the compiler-side requirements (P8) the handler should
meet, since both must produce identical cassette states:

```
run_test_live_job(ctx):
  lp = closure at ctx.ref (checkout)
  for each agentic package in the closure (split halves included), sorted by id:
      snapshot pkg/cassettes -> scratch; empty pkg/cassettes
      run_step_suite(mode="record")
      passed → replay check (9.4); keep the new recordings; outcome "re-recorded"
      failed → restore the snapshot (keep the old cassettes); outcome "failed live"
  deterministic and shell packages: run_step_suite(mode="replay") for completeness
  process tests: the same record → verify → keep-or-restore, on <process>/tests/cassettes
  cassette size warnings as in 9.4
  sha = ctx.commit("wynd test --live: re-record cassettes for <pid>")   # only changed cassette files differ
  status: succeeded if every live run passed, else failed
  report: {"steps": {node: {"outcome": "re-recorded" | "failed live" | "replayed", "cases": …, "usage": …}},
           "process": {...}, "cassette_bytes": {...}, "warnings": [...]}
```

The harness publishes `wynd/test-live/<pid>/<job-id>` (06 `BRANCH_PREFIX`) and the CLI integrates it like a compile
branch. Recommended difference from 04 §7, which makes no commit on failure: commit the re-recordings of the steps
that did pass, but report `failed`. The user then keeps the good recordings and sees exactly which steps no longer
match their examples.

---

## 13. The compiler's own LLM use

### 13.1 Provider and tiers

- The compiler's provider is `claude-code` (the user requirement: "use the Claude Code subscription (via the dev
  key)"). It is overridable by `WYND_COMPILER_PROVIDER=<registered provider name>`, which is a backend selected by
  env var, not a code branch. Auth is the provider's concern: the logged-in `claude` CLI locally,
  `CLAUDE_CODE_OAUTH_TOKEN` (from `claude setup-token`) in containers and CI, or `ANTHROPIC_API_KEY`. The compile
  job therefore needs these in its environment, and they appear in the controller's job env documentation.
- Tiers resolve through the user registry (C-R9). With the default seed that means `strong` → opus and
  `standard` → sonnet. The step tests the compiler runs use each step's own provider and tier (process default
  provider, usually `cheap` → haiku).

| Call kind | Tier | Thinking | Response model |
|---|---|---|---|
| `infer_schema` | standard | low | `InferSchemaResponse` |
| `propose_examples` | strong | medium | `ProposeExamplesResponse` |
| `revise_example` | standard | low | `ReviseExampleResponse` |
| `decide` | strong | medium | `DecideResponse` |
| `write_deterministic` | strong | medium | `WriteCodeResponse` |
| `write_shell` | strong | medium | `WriteCodeResponse` |
| `write_agentic` | strong | medium | `WriteAgenticResponse` |
| `revise_code` | strong | high | `ReviseCodeResponse` |
| `revise_agentic` | strong | medium | `ReviseAgenticResponse` |

Structure: every call is a **single structured-output request with no tools**. It is one system prompt (preamble +
call prompt) and one user message (sections, 13.4), and the response is validated against the pydantic model.
Code comes back as strings in the JSON. The compiler deliberately does not let Claude Code edit files or run tests
itself:

1. The generate-test-revise loop, the decision and the report stay in the compiler, where they are deterministic
   and inspectable.
2. Every call is memoisable and scriptable (the offline tests and cheap resumption depend on this).
3. Nothing the model does has side effects outside the returned JSON.

### 13.2 `llm.py`

```python
class Tier(StrEnum): CHEAP = "cheap"; STANDARD = "standard"; STRONG = "strong"
Thinking = Literal["low", "medium", "high"]

@dataclass
class LLMResult(Generic[T]):
    value: T
    usage: Usage              # input_tokens, output_tokens, cost_usd | None, latency_ms
    memo_hit: bool

class CompilerLLM(Protocol):
    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]: ...

class ProviderLLM:                           # real provider behind the runtime protocols
    def __init__(self, provider: ModelProvider | AgentProvider, tiers: dict[str, str], workdir: Path,
                 timeout_s: int = 900): ...
    # ModelProvider: GenerateRequest(system=system, messages=[user(prompt)], tools=[], output_schema=strict(schema),
    #                                thinking=thinking, model_id=tiers[tier])
    # AgentProvider: AgentRequest(instruction=system, context={}, input={}, prompt=prompt, output_schema=strict(schema),
    #                             tools=[], mcp=[], workspace=workdir, model_id=tiers[tier], thinking=thinking)
    # validate structured_output with response_model; on ValidationError re-ask once with the error appended to
    # the prompt ("Your previous answer did not match the schema: …"); transport errors retry twice with backoff 5s, 20s.

class MemoLLM:                               # wraps any CompilerLLM with the session memo (6.6)
    def __init__(self, inner: CompilerLLM, memo: Memo, on_usage: Callable[[CallKind, str, Usage], None]): ...

def strict(schema: dict) -> dict:
    """Make a pydantic JSON schema acceptable to strict structured outputs: set additionalProperties: false and a
    full `required` list on every object, drop unsupported keywords (minLength, maxLength, minimum, maximum,
    multipleOf, pattern, default, title). $defs/$ref are kept (supported). Raises if the schema is recursive."""

def default_llm(registry: Registry, workdir: Path) -> CompilerLLM:
    name = os.environ.get("WYND_COMPILER_PROVIDER", "claude-code")
    return ProviderLLM(get_provider(name), registry.provider_tiers(name), workdir)
```

Strict structured outputs require `additionalProperties: false` on every object, so no call schema has a free-form
object. Example data travels as **JSON-encoded strings** (`inputs_json`, `outputs_json`) and is parsed and
validated by the compiler.

### 13.3 Response models (`calls.py`)

```python
class Field(BaseModel):
    name: str
    type: str                  # proto type grammar, validated with spec.parse_type
    description: str
    free_text: bool

class ExitFields(BaseModel):
    exit: str
    fields: list[Field]

class InferSchemaResponse(BaseModel):
    inputs: list[Field]
    exits: list[ExitFields]
    problems: list[str]        # contradictions the model found; empty if none

class Proposal(BaseModel):
    question: str
    rationale: str
    exit: str
    inputs_json: str
    outputs_json: str

class ProposeExamplesResponse(BaseModel):
    proposals: list[Proposal]

class ReviseExampleResponse(BaseModel):
    drop: bool
    exit: str
    inputs_json: str
    outputs_json: str
    understood: str

class ExampleNeeds(BaseModel):
    index: int                 # 1-based example number
    needs: Literal["pure", "judgement", "world_knowledge", "external_data"]
    why: str

class DecideResponse(BaseModel):
    examples: list[ExampleNeeds]
    command: bool
    program: str               # "" when command is false
    summary: str

class EnvVar(BaseModel):
    name: str
    description: str

class WriteCodeResponse(BaseModel):          # deterministic and shell
    module_source: str
    deps: list[str]
    system_packages: list[str]
    requires_glibc: bool
    effects: list[Literal["network", "filesystem", "shell"]]
    env_vars: list[EnvVar]
    notes: str

class Suspect(BaseModel):
    example: int
    why: str

class ReviseCodeResponse(WriteCodeResponse):
    diagnosis: str
    verdict: Literal["fixable", "needs_judgement", "examples_inconsistent"]
    suspects: list[Suspect]

class McpUse(BaseModel):
    server: str
    allow: list[str]

class WriteAgenticResponse(BaseModel):
    docstring: str
    context: list[str]
    tools: list[str]           # builtin tool names
    mcp: list[McpUse]
    tool_methods: str          # "" or method source indented 4 spaces
    deps: list[str]            # for tool methods only
    env_vars: list[EnvVar]
    notes: str

class ReviseAgenticResponse(WriteAgenticResponse):
    diagnosis: str
    verdict: Literal["fixable", "examples_inconsistent", "needs_stronger_model"]
    suspects: list[Suspect]
```

`test_llm.py` asserts that `strict(M.model_json_schema())` is strict-valid for every model: all objects closed, no
recursion, no unsupported keywords.

### 13.4 User message rendering

`prompts.render(sections: list[tuple[str, str | dict | list]]) -> str`. Each section is `## <Title>` followed by
plain text, or by a fenced `yaml` block (for data) or `python` block (for code). The output is deterministic (sorted
keys where order does not matter), so memo keys are stable. Sections per call:

| Call | Sections (in order) |
|---|---|
| `infer_schema` | Step (name, instruction) · Exits · Examples (numbered, YAML) · Constraints from the process graph · Given schema (if half given) · Previous interface (if any) · User corrections (if any) · Problems with your previous answer (retry only) |
| `propose_examples` | Step · Schema (types + plain language) · Examples (numbered) · Fixture files · Rejected questions · Process context (goal; upstream and downstream nodes with instructions) · Maximum proposals |
| `revise_example` | Schema · Proposed example · The user's answer |
| `decide` | Step · Schema · Examples (numbered) · Available tools (builtin names, MCP servers with descriptions) · Process context · User guidance |
| `write_deterministic` | Runtime API reference · Step contract (instruction, exits, plain-language schema) · Class name and base · Models block (include verbatim) · Test file · Fixture files · Previous implementation (if recompiling) · User guidance |
| `write_shell` | as `write_deterministic` + Exit codes · Program |
| `write_agentic` | Runtime API reference (agentic part) · Step contract · Examples (numbered, for deriving rules only) · Built-in tools (signature, effects) · MCP servers (tool names + descriptions) · Upstream nodes (for context) · Process goal · Model tier (plain words: "a small, fast model") · Split note (agentic half only) · Previous docstring (if recompiling) · User guidance |
| `revise_code` | Current module · Test file · Failures · Earlier diagnoses · Attempt `i` of `N+1` |
| `revise_agentic` | Current docstring and capabilities · Failures · Earlier diagnoses · Attempt · Model tier |

**Failures section**: for each failing case, the test name, example number, expected exit and outputs, actual exit
and outputs (from `WYND-EXPECT`), or the error type and message plus the last 30 traceback lines. Each case is capped
at 4 KB and the whole section at 30 KB. Deferred cases are labelled "deferred (raised NotImplementedError: …)".

### 13.5 Scripted and recorded LLMs (`testing.py`)

```python
class ScriptedLLM:                       # implements CompilerLLM; offline tests
    def __init__(self, script: Path | dict): ...
    # Matches calls on (kind, node) in order: the n-th call with that key gets the n-th entry. A miss raises
    # ScriptMiss(kind, node, n, prompt_head) so a test failure shows exactly which response is missing.

class RecordingLLM:                      # wraps a real LLM; used by live tests to write a script for offline reuse
    def __init__(self, inner: CompilerLLM, out: Path): ...
```

Script format:

```yaml
# packages/compiler/tests/fixtures/scripts/ws_mini.yaml
calls:
  - kind: infer_schema
    node: parse
    response:
      inputs: [{name: text, type: string, description: "amount as written", free_text: false}]
      exits: [{exit: done, fields: [{name: amount, type: number, description: "the amount", free_text: false}]}]
      problems: []
  - kind: propose_examples
    node: parse
    response: {proposals: []}
  - kind: decide
    node: parse
    response:
      examples: [{index: 1, needs: pure, why: "digits"}, {index: 2, needs: pure, why: "digits"},
                 {index: 3, needs: judgement, why: "words"}]
      command: false
      program: ""
      summary: "Mostly numeric formats; one amount in words."
  - kind: write_deterministic
    node: parse
    response:
      module_source_file: ../code/parse_amount_v1.py      # special key, resolved relative to this script
      deps: []
      system_packages: []
      requires_glibc: false
      effects: []
      env_vars: []
      notes: "Parses numeric amounts; defers amounts written in words."
```

The compiler's own calls are therefore **not** cassette-recorded by request hash. Offline tests use scripts keyed by
`(kind, node, n)`, which survive prompt wording changes. They only break when the pipeline's call sequence changes,
which is exactly when a test should notice. Within a session, the memo (6.6) acts as a per-session cassette.

### 13.6 Usage accounting

Every `LLMResult.usage` is added to `session.usage` (totals), to `report.steps[node].usage.compiler`, and to
`report.usage.by_kind[kind]` and `by_tier[tier]`. Test-recording usage (the steps' own model calls during record
runs) is summed from the `model.call` events in `WYND_EVENTS_FILE` (C-R5) into `report.steps[node].usage.recording`. Latency is the sum
of per-call latency. `cost_usd` is `null` when the provider does not report it. Claude Code reports a notional
`total_cost_usd` even on a subscription; it is recorded as reported.

---

## 14. Prompts (actual text)

Stored as `src/wynd/compiler/prompts/<name>.md`. The system prompt for every call is `preamble.md` + `"\n\n"` +
`<call>.md`, with `{placeholders}` filled by `str.format_map`. Braces in the prompt text are doubled. Prompts are
data: a change to them changes memo keys, but not offline tests (scripts are keyed by call kind).

### 14.1 `preamble.md`

```text
You are the Wynd compiler. Wynd turns a step-by-step business process into tested, reproducible automation. The
process is described by someone who knows it well but may not be a programmer, as a graph of steps. Each step has
typed inputs, one or more named exits with typed outputs, and worked examples. You compile one step at a time into
one of three kinds:

- DeterministicStep: plain Python that is a pure function of its inputs. This is always preferred when it can be
  done reliably, because it is free and instant to run.
- AgenticStep: at run time a small, inexpensive language model completes the step from an instruction you write,
  and its output is validated against the step's schema.
- ShellStep: runs a command-line program.

The examples are the specification. They become the step's tests, and a step is accepted only when its tests pass.
Examples show a sample of the inputs the step will meet in production, not all of them, so the implementation must
be general. Wherever your output includes an explanation, write it in plain language for the process owner, in one
or two sentences. Answer only with the JSON object required by the output schema.
```

### 14.2 `infer_schema.md`

```text
Task: infer the input and output schema of one step from its instruction and examples. The schema will be shown to
the process owner in plain language. A baseline inferred mechanically from the example values is given: start from
it, and correct what values alone cannot show (paths, identifiers made of digits, optional fields, fields of exits
without examples).

Types. Use only: string, number, integer, boolean, date, datetime, path, object, list[<type>]. Append "?" to a type
when the field may be missing or null (for example "string?").
- number is for amounts, measurements and anything that may have a fractional part. integer is only for counts and
  whole-number quantities. An identifier made of digits (an invoice number, a postcode) is a string.
- date is for calendar dates, datetime only when a time of day appears. path is for values that name files or
  directories. object is for a nested record whose inner structure is not the point of this step. list[<type>] is
  for repeated values.

Rules.
- Declare exactly the exits listed, in the same order. An exit that returns nothing has an empty field list.
- Every input field must appear in at least one example's inputs. Every output field of an exit must appear in at
  least one example ending in that exit, unless a constraint below names it.
- The constraints from the process graph are fixed. Field names that edges already use must appear with exactly
  those names. The entry step's inputs must match the process inputs by name.
- If a previous interface is given, keep its field names and types wherever the examples still agree with them, so
  that the rest of the process keeps working. Change only what the examples now contradict.
- If the user has corrected a previous inference, apply the correction exactly.
- Mark an output field free_text only when its value is prose that a correct implementation could word differently
  (a note, a summary, a reason). Identifiers, codes, amounts, dates, names and categories are never free text.
- Give every field a short description in plain language, as the process owner would say it.
- If the examples contradict each other, or no single schema fits them, explain each problem in plain language in
  "problems" instead of guessing. Otherwise leave "problems" empty.
```

### 14.3 `propose_examples.md`

```text
Task: propose edge-case examples for one step. The process owner will confirm, correct or reject each one, and
confirmed examples become permanent tests. The owner's examples usually show the common case. Your proposals should
probe the boundaries where an implementation is most likely to be wrong, or to overfit to the few examples it has.

Rules.
- Propose at most {max_proposals}, most valuable first. Propose fewer, or none, if the existing examples already
  cover the boundaries that matter.
- Each proposal has: a single question in plain language, as you would ask the owner ("What should happen if the
  invoice has no due date?"); the complete example you think is the most likely correct answer; and a one-sentence
  rationale.
- Good edge cases: a missing or empty value; boundary numbers (zero, negative, very large); the same thing written
  in a different format (dates, thousands separators, currency symbols, upper and lower case); an input that
  belongs to a different exit; a near-miss that looks valid but is not.
- Every value must match the schema. The outputs must contain every field of the chosen exit; an exit with no
  fields has empty outputs. Use the exit "error" only for inputs where the step cannot produce any declared exit,
  such as a file that does not exist.
- For path inputs you cannot create files. Use only the fixture files listed, or a path that does not exist when
  the point of the example is a missing file.
- Do not repeat an existing example, and do not propose anything the owner has already rejected (listed below).
- Write inputs_json and outputs_json as JSON objects serialised to strings, for example "{{\"total\": 0}}".
```

### 14.4 `revise_example.md`

```text
Task: you proposed an edge-case example and the process owner answered in their own words. Turn their answer into
the corrected example.

Rules.
- Keep the proposal's inputs unless the answer changes them.
- Apply the answer exactly. Do not add assumptions of your own.
- If the answer says the case does not matter or should not be a test, set drop to true.
- Values must match the schema. Write inputs_json and outputs_json as JSON objects serialised to strings.
- In "understood", restate in one plain sentence what you took the answer to mean, so the owner can check it.
```

### 14.5 `decide.md`

```text
Task: for each example of one step, decide what an implementation needs in order to produce the expected result.
Also decide whether the step is a shell command.

Classify each example as exactly one of:
- pure: the result follows from the inputs by rules a programmer could write down and trust on unseen inputs of
  the same kind (parsing a fixed format, arithmetic, lookups in fixed tables, formatting, validation, fixed decision
  rules), using the Python standard library or a well-known package.
- judgement: it requires reading comprehension, classifying free-form text, or tolerating phrasing that written
  rules would not reliably capture.
- world_knowledge: it requires facts that are not in the inputs.
- external_data: it requires fetching information at run time (a web page, an API, a database).

Be realistic rather than optimistic. Choose pure only when you could write the rule and expect it to generalise
beyond these examples. Free-form text written by different people usually needs judgement. Machine-generated or
templated text (logs, CSV, exports, PDFs produced by one system, fixed forms) is usually pure.

Set command to true only when the instruction clearly asks to run a named command-line program, or clearly
describes the step as running a command. Then put the program's name in "program". Otherwise set command to false
and program to "".

In "summary", explain the classification to the process owner in two or three plain sentences.
```

### 14.6 `write_deterministic.md`

```text
Task: write the complete Python module for a DeterministicStep named {class_name} that makes the given test file
pass. The tests were generated from the examples before any code existed and will not change.

Rules.
- Define exactly one step class, {class_name}(DeterministicStep), containing the models block copied verbatim and a
  method run(self, input: Input) -> Output that returns one of the exit models. Import what the models block needs.
- Write a general implementation of the instruction. Never special-case example values, embed the examples, read
  the test file, or branch on anything that exists only in the tests. The tests are a sample; production inputs
  will differ.
- When an input falls outside what your code can handle with confidence, raise NotImplementedError with a short
  reason instead of guessing. The runtime routes such a failure to a fallback. A wrong answer would go unnoticed.
- Prefer the standard library. If a third-party package is clearly the right tool (for example pypdf to read PDF
  files), import it and put a requirement such as "pypdf>=5" in deps. Never list wynd packages.
- Module level may contain only imports, constants named in UPPER_CASE bound to immutable values (str, int, float,
  bool, None, tuples, frozensets, re.compile(...)), functions and classes. Keep no state between runs; use
  self.runtime.cache when a cache is genuinely needed.
- Read configuration and secrets only with self.runtime.env("NAME"). List every such name in env_vars with a plain
  description. Never hard-code secrets.
- Write files only under self.runtime.workspace, unless the instruction says to write elsewhere. Declare
  "filesystem" in effects if the code reads or writes files named by its inputs or outside the workspace, "network"
  if it makes network calls, and "shell" if it runs subprocesses.
- Use pre() and post() only for per-run setup and teardown of things the runtime cannot see (temporary files, open
  sessions, subprocesses).
- Keep the code small, explicit and readable. A reviewer will read it in a git diff. Add a short comment only where
  the logic is not obvious.
- If a previous implementation is given, it may contain hand edits by a reviewer. Keep them wherever they are still
  consistent with the tests.
- If user guidance is given, follow it.
- In notes, explain in one or two plain sentences how the step works.
- Set system_packages to [] and requires_glibc to false unless a dependency genuinely needs them.
```

### 14.7 `write_shell.md`

```text
Task: write the complete Python module for a ShellStep named {class_name} that runs the program "{program}" and
makes the given test file pass.

Rules.
- Define exactly one step class, {class_name}(ShellStep), containing the models block copied verbatim.
- Implement command(self, input: Input) -> list[str], returning the argument vector. Never build a shell string,
  never invoke a shell, and never let an input value become more than one argument.
- Set the class attribute exit_codes to exactly {exit_codes}. Keys are process exit codes and "*" is the fallback.
- Implement outputs(self, exit: str, result: ShellResult) -> Output, which turns result.stdout, result.stderr and
  result.returncode into the model for that exit.
- List the Debian packages that provide the program in system_packages (for example "poppler-utils" for pdftotext).
  Always include "shell" in effects, and add "filesystem" or "network" if the command touches files named by the
  inputs or the network.
- The rules on module-level state, environment variables and readability from deterministic steps apply here too.
- In notes, explain in one or two plain sentences what the command does.
```

### 14.8 `write_agentic.md`

```text
Task: write the instruction (the class docstring) and choose the capabilities for an AgenticStep. At run time
{tier_words} receives your docstring as its only instructions, the step's input as JSON, and any context you
declare, and must answer with output that validates against one of the exit models. It never sees the examples,
this conversation or the process owner.

The docstring.
- Write it for a capable but literal reader who knows nothing about this business. Start with one sentence stating
  the goal. Then say when to choose each exit, using the exit names exactly. Then say how to fill each output field:
  formats, units, normalisation (ISO dates, currency codes, number formats). Then say what to do when information is
  missing, ambiguous or contradictory.
- Derive general rules from the examples. Never copy example inputs or outputs into the docstring, and never
  mention examples or tests.
- Aim for fewer than 250 words: plain text, short paragraphs and hyphen lists.
{split_note}
Capabilities. Most steps need none. When everything needed is in the input, leave tools, mcp and tool_methods empty.
- When a capability is needed, prefer the built-in tools listed. Write a bespoke @tool method only for logic
  specific to this step. Use an MCP server from the registry only when neither covers the need, and list in allow
  only the tool names you need.
- A bespoke tool is an ordinary method with type hints and a one-line docstring, decorated
  @tool(effects=[...], idempotent=..., env=[...]). Put its source, indented by four spaces as it will appear in the
  class body, in tool_methods. idempotent is true only if calling it twice is harmless.
- context lists extra trace context the model should see. Choose only from: "process.goal", "previous.summary",
  "previous.outputs", and "steps.<node>.outputs" for the upstream nodes listed. Use ["process.goal"] when a process
  goal exists, and request more only when the instruction needs information that is not in the input.
- List any environment variables the tools read in env_vars, and any packages the tool methods import in deps.

If a previous docstring is given, keep what is still right. If user guidance is given, follow it.
In notes, explain in one or two plain sentences why this step needs a model.
```

`{split_note}` for the agentic half of a split:

```text
- This step is a fallback. A deterministic step handles common inputs, and this step only receives inputs that the
  deterministic step could not handle (for example: {deferred_summary}). Do not assume the input is typical.
```

`{tier_words}`: cheap → "a small, fast language model", standard → "a mid-sized language model", strong → "a large
language model".

### 14.9 `revise_code.md`

```text
Task: the step's tests failed. Diagnose why in general terms, then return a corrected, complete module.

You are given the current module, the test file, each failing test (expected exit and outputs, and what actually
happened, or the exception with the end of its traceback), and the diagnoses from earlier attempts. Do not repeat
an approach that an earlier diagnosis shows has already failed.

Rules.
- Write the diagnosis first: what the code does wrong in general terms, not example by example.
- Fix the general behaviour. Never special-case example values, never read the test file, never hard-code an
  expected output.
- Tests marked "deferred" failed because the code raised NotImplementedError. Handle those inputs if a reliable rule
  exists. If an input really needs judgement, world knowledge or data from outside, leave it deferred and set
  verdict to "needs_judgement".
- If two examples contradict each other, or an expected output cannot follow from its inputs under any reasonable
  reading of the instruction, set verdict to "examples_inconsistent" and list each suspect example number with a
  plain-language reason. Still return your best module.
- Otherwise set verdict to "fixable".
- Every rule of the original task still applies: the models block verbatim, no module-level state, environment only
  through self.runtime.env, deps, effects and env_vars declared.
```

### 14.10 `revise_agentic.md`

```text
Task: the AgenticStep's tests failed when the run-time model followed your docstring. Diagnose why in general terms
and return a revised docstring and capabilities.

You are given the current docstring and capabilities, the model tier, each failing test (the input, the expected
exit and outputs, and what the model actually returned, or the validation or tool error), and the diagnoses from
earlier attempts.

Rules.
- Diagnose first: which rule was missing, unclear or wrong, or which capability was missing.
- Revise the rules, not the examples. Never copy example values into the docstring.
- Keep the docstring short. Make each rule clearer instead of adding many special cases.
- If an expected output cannot follow from its input under any reasonable reading, set verdict to
  "examples_inconsistent" and list the suspect example numbers with reasons.
- If the docstring is already clear and correct and the model still fails, set verdict to "needs_stronger_model".
- Otherwise set verdict to "fixable".
- Every rule of the original task still applies.
```

### 14.11 `runtime_api.md` (reference excerpt, sketch)

This is maintained by CMP-D in lockstep with the runtime draft. `test_prompts.py` parses every
`wynd.runtime…` symbol in it and imports it, so the file cannot silently drift.

~~~text
# Wynd runtime API (for step authors)

from wynd.runtime import DeterministicStep, AgenticStep, ShellStep, ShellResult, McpServer, tool
from wynd.runtime.tools import http_get, web_search, workspace_read, workspace_write, shell, now

A step class nests its models: `class Input(BaseModel)`, one model per exit, each with
`exit: Literal["<name>"] = "<name>"`, and `Output = A | B` (or `Output = Done` for a done-only step).
run(self, input: Input) -> Output returns exactly one exit model. Raising any exception sends the run to the
step's implicit `error` exit. The step never decides where the process goes next.

self.runtime (available in pre, run, post and tool methods):
  self.runtime.env(name: str) -> str          # required env var; raises if unset
  self.runtime.workspace: Path                # per-run directory; files here are cleaned up after the run
  self.runtime.cache                          # .get(key) / .set(key, value): the only allowed cross-run state
  self.runtime.log(message: str, **fields)    # structured log line into the trace
  self.runtime.http.get(url, **kw) / .post(url, **kw)   # declared "network" effect required

Hooks: pre(self) -> None and post(self) -> None run once per step run. Use them only for state the runtime
cannot see.

DeterministicStep: no model, no network unless "network" is declared.
ShellStep: exit_codes = {0: "done", "*": "error"}; command(self, input) -> list[str];
           outputs(self, exit: str, result: ShellResult) -> Output   # ShellResult(returncode, stdout, stderr)
AgenticStep: the class docstring is the instruction. run(self, input: Input) -> Output: ...  (body is literally ...)
           context = [...]; tools = [web_search, ...]; mcp = [McpServer("github", allow=["list_issues"])]
@tool(effects=["network"], idempotent=True, env=["API_KEY"]) marks a method as callable by the model. Its type
hints and docstring are the tool schema.

Built-in tools (can also be called directly from deterministic code):
{builtin_catalog}        # generated at prompt-render time from wynd.runtime.tools.BUILTINS
~~~

---

## 15. Compile report (`report.py`, P4)

Attached to the job record (`record.report`), updated at each checkpoint, and final when the job ends. It is not
committed. Its summary goes into the commit message.

```json
{
  "report_version": 1,
  "process": "process_supplier_invoice",
  "session": "j_01JB8Z6Q4M",
  "jobs": ["j_01JB8Z6Q4M", "j_01JB9A2K7T"],
  "base_commit": "4f1c2e9d0b7a…",
  "commit": "b7d93e10a4c2…",
  "branch": "wynd/compile/process_supplier_invoice/j_01JB8Z6Q4M",
  "status": "succeeded",
  "summary": "Compiled 6 steps: 4 deterministic, 1 agentic, 1 split into deterministic + agentic. All 23 step tests and 2 process tests pass.",
  "steps": [
    {
      "process": "process_supplier_invoice", "node": "read", "step": "read_pdf",
      "package": "processes/process_supplier_invoice/steps/read_pdf",
      "action": "compiled",
      "reason": "not compiled yet",
      "decision": {"kind": "deterministic", "rule": 1,
                   "why": "Extracting the text layer of a PDF is a pure function of the file.",
                   "classification": [{"example": 1, "needs": "pure", "why": "text PDF"}],
                   "tier": null},
      "schema": {"inferred": false, "plain": "read_pdf takes:\n  - pdf_path (file path) ..."},
      "examples": {"original": 2, "proposed": 2, "confirmed": 1, "corrected": 0, "rejected": 1},
      "attempts": [{"kind": "deterministic", "tier": null, "n": 1, "handled": 3, "deferred": 0, "wrong": 0,
                    "duration_ms": 4120, "diagnosis": null}],
      "tests": {"passed": 3, "failed": 0},
      "tools": [], "mcp": [], "effects": ["filesystem"], "env": [],
      "deps": {"declared": ["pypdf>=5"], "locked": ["pypdf==6.19.0"]},
      "cassette_bytes": 0,
      "usage": {"compiler": {"calls": 4, "input_tokens": 18200, "output_tokens": 3900, "cost_usd": 0.41, "latency_ms": 71000},
                "recording": {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "latency_ms": 0}},
      "warnings": []
    },
    {
      "process": "process_supplier_invoice", "node": "save", "step": "save_record",
      "action": "skipped", "reason": "unchanged since session j_01J9…",
      "tests": {"passed": 2, "failed": 0}
    }
  ],
  "process_changes": [
    {"type": "split", "process": "process_supplier_invoice", "node": "extract", "added_node": "extract_agentic",
     "edges_added": ["extract.error", "extract_agentic.done", "extract_agentic.not_an_invoice"],
     "handled": [1, 2, 3, 6], "deferred": [4, 5]}
  ],
  "proto_changes": [
    {"proto": "processes/process_supplier_invoice/proto/extract_invoice_fields.yaml", "examples_added": 2}
  ],
  "integration_tests": {"passed": 2, "failed": 0, "cases": []},
  "validation": {"errors": [], "warnings": []},
  "questions": {"asked": 7, "answered_by_user": 3, "answered_by_accept_proposals": 4},
  "usage": {
    "total": {"calls": 31, "input_tokens": 402100, "output_tokens": 88100, "cost_usd": 9.62, "latency_ms": 1620000},
    "by_kind": {"write_deterministic": {"calls": 6, "cost_usd": 3.1}},
    "by_tier": {"strong": {"calls": 24, "cost_usd": 9.01}, "standard": {"calls": 7, "cost_usd": 0.61}},
    "recording": {"calls": 44, "cost_usd": 0.38}
  },
  "warnings": ["cassettes for extract_invoice_fields_agentic are 5.4 MB (limit 5 MB)"],
  "error": null
}
```

Commit message (`render_commit_message(report)`):

```
wynd compile: process_supplier_invoice

Compiled 6 steps: 4 deterministic, 1 agentic, 1 split into deterministic + agentic.

- read (read_pdf): deterministic, rule 1. Extracting the text layer of a PDF is a pure function of the file.
- extract (extract_invoice_fields): split, rule 3. Deterministic code handles 4/6 examples; the rest go to
  extract_invoice_fields_agentic (cheap tier) via extract.error.
- validate (validate_fields): deterministic, rule 1.
- fix (fix_fields): agentic, rule 2 (cheap tier). Correcting mis-read fields needs reading the context.
- save (save_record): skipped, unchanged.
- escalate (escalate_to_human): deterministic, rule 1.

Process changes: added node extract_agentic and 3 edges (split of extract).
Proto-steps: 2 confirmed edge-case examples added to extract_invoice_fields.

Wynd-Session: j_01JB8Z6Q4M
Wynd-Process: process_supplier_invoice
Wynd-Base: 4f1c2e9d0b7a…
Wynd-Job: j_01JB9A2K7T          (appended by the harness's ctx.commit)
```

---

## 16. Tests

All offline tests are deterministic and need no network: no LLM, no Docker. Venvs are built from the uv cache with
`UV_OFFLINE=1`, which requires the root `pyproject.toml` dev group to include `pypdf`, so that `uv sync` caches it
for the dogfood steps. Markers: `live` (skipped unless `WYND_LIVE=1`) and `docker` (not used here).

### 16.1 Unit tests (per module)

| File | Covers |
|---|---|
| `test_session.py` | State transitions (every legal and illegal call). JSON round trip (`from_json(to_json(s)) == s`). The JSON shape matches 07's `CompileSession` fixture (steps, questions, inferred_schemas, events). `ask()` idempotence, stale fingerprints, pre-answers, `accept_proposals`. The answer interpretation table (6.4). Clarify id numbering. Memo cap and eviction |
| `test_schemas.py` | `render_models_block` agrees with 01's `build_models`: render, `exec` into a scratch module *in the test only*, and compare with `interfaces_equivalent`. Canonicalisation (dates, numbers, partial examples, `{tmp}` untouched). Plain-language lines |
| `test_examples.py` | Contradiction and unknown-exit checks. Proposal validation (bad JSON, schema mismatch, missing fixture). The correction flow with a scripted `revise_example`. `description` on appended examples |
| `test_yamledit.py` | Append to a block list with comments preserved (golden files). Flow `examples: []`. Missing key. Both indentation styles. Marker removal. The fallback path with its warning |
| `test_testgen.py` | Golden step test files (deterministic, agentic, split deterministic half with deferred examples, shell, path input, `{tmp}` example, `env:` example). Golden `test_process.py` for the dogfood examples. `BASE` depth for local and root steps. `present` lists |
| `test_astcheck.py` | Class shape. Extraction of `tools`/`mcp`/`context`. The env var scan (all forms, plus `@tool(env=…)`). The agentic `run` body must be `...`. Unknown tool, server or context entry |
| `test_lockfile.py` | Golden `step.lock.yaml` for each kind, validated by 01's `StepLock` (including `compiled`). `DEFAULT_RETRIES`. The effects union satisfies 01's `E-LOCK` rules. `fragment.vars` merge. Deps merge (the proto wins). `pyproject.toml` golden |
| `test_tools.py` | The catalog from fake builtins and a fake `list_tools`. A discovery failure is dropped with a warning. `used_tools` from an events JSONL fixture. `narrow_allow`. Snapshot hashes |
| `test_llm.py` | `ProviderLLM` against fake ModelProvider and AgentProvider objects (request shape, one schema-repair retry, transport retries). `MemoLLM` hit, miss and usage. `strict()` on every response model |
| `test_prompts.py` | Every prompt renders with its placeholders, with no stray braces. Every `wynd.runtime` symbol named in `runtime_api.md` imports. Section rendering is deterministic |
| `test_attempts.py` | `run_attempt` with fake `step_python`/`describe`/`run_step_suite`: lint short-circuit, interface mismatch, WYND-EXPECT classification (handled, deferred, wrong), memo of failing attempts, cassette snapshot and restore |
| `test_decision.py` | The rule matrix, with a `ScriptedLLM` and a fake `run_attempt`: exit_codes → shell; command → shell; at most half pure → agentic without a deterministic attempt; all pure and all passing → deterministic; 4/6 handled with 0 wrong → split; 4/6 handled with 1 wrong → agentic; split eligible but not applicable (step root, external reference, existing error edge, name taken) → agentic with the reason; agentic fails at cheap and passes at standard → `tier: standard`; both fail → clarification with the rendered text; `examples_inconsistent` stops the loop; `needs_judgement` stops early; attempt scoring |
| `test_split.py` | `apply_split` on the real dogfood `process.yaml` (golden file). `rename_step_refs` on tricky expressions (`steps.extractor` untouched, `edges["extract.done"]` renamed, string literals untouched). `remove_split` with and without markers. The rewritten graph passes 04's `validate` |

### 16.2 Offline end-to-end (`test_pipeline_offline.py`, `test_jobs.py`, `test_dogfood_offline.py`)

The fixture workspace `tests/fixtures/ws_mini/` has a process `mini` with `provider: fake` (C-R10) and only
stdlib steps:

| Node | Proto | Scripted outcome |
|---|---|---|
| `normalise` | `normalise_name` (strip, title case) | deterministic, rule 1; passes on attempt 2 after one revision |
| `classify` | `classify_ticket` (message → `done{category}` \| `spam`) | agentic, rule 2; the fake provider script answers per message |
| `parse` | `parse_amount` (`"£12.50"`, `"12.50 GBP"`, `"EUR 3"`, `"twelve pounds fifty"`) | split, rule 3: the deterministic code defers amounts in words, and the agentic half is answered by the fake provider |
| `shout` | `shout`, with `exit_codes: {0: done, "*": error}` | shell (`tr a-z A-Z`), rule 4 |
| `store` | `store_note` (writes to `dest: "{tmp}/notes"`) | deterministic, with a `{tmp}` example |

There are also two process examples, which give the integration tests. Script: `fixtures/scripts/ws_mini.yaml`.
Code: `fixtures/code/*.py`.

`FakeJobContext` (in `wynd.compiler.testing`) implements 06's `JobContext` over a temporary git repo. It makes a
real `git worktree` under `<tmp>/.wynd/jobs/<id>`, checks out the result branch, and gives `commit` the harness's
semantics (06 §6.4).

1. **Straight through.** With `accept_proposals=True`, the job succeeds. There is exactly one commit on the
   branch, with the base as its parent. The expected files exist. The lock kinds are deterministic, agentic, split
   and shell. Cassettes exist only for `classify`, `parse_agentic` and `tests/`. `process.yaml` has the split
   markers. 04's `run_tests(mode="replay")` passes on the result commit. The report matches a golden file (sha,
   timings and cost masked).
2. **Questions and resume.** Without `accept_proposals`, the first job ends `awaiting_input` with the expected
   question ids, and the WIP commit contains only completed steps. After answers and a resubmit (`resume_from`,
   cumulative answers), the second job succeeds. The `ScriptedLLM` records **no repeated calls** for memoised work.
   The final commit is a single squash on the base.
3. **Clarification.** The script makes `normalise` fail every attempt. The job asks `normalise.clarify1`. The
   answer appears in the next job's prompts (asserted on the ScriptedLLM's captured prompts) and is persisted to
   `compiled.guidance`.
4. **Skip and preserve.** After a successful compile, hand-edit `normalise_name.py`, commit and compile again: all
   steps are skipped, no commit is made and the hand edit survives. Change `classify`'s proto: only `classify` is
   recompiled, and its prompt contains the previous module. Script an all-passing deterministic result for `parse`:
   the split markers, node and edges are gone, and the agentic package is deleted.
5. **Working tree untouched.** `git status --porcelain` and a hash of the user's working tree are unchanged by every
   job.
6. **Integration failure.** A failing process example gives status `failed`, a published branch, and the failing
   cases in the report.

`test_dogfood_offline.py` copies `examples/invoices`, removes `processes/process_supplier_invoice/steps/` and
`tests/`, and compiles with a ScriptedLLM whose `write_*` responses are **08's hand-written M1 modules**. Its agentic
steps run on the `fake` provider, scripted from the M1 examples' expected outputs. This proves the compiler
reproduces the M1 package layout, locks and tests, and that they pass, with no LLM.

### 16.3 Live tests (`@pytest.mark.live`, `WYND_LIVE=1`)

- `live/test_llm_calls_live.py`: one real call per call kind against `claude-code`, with tiny payloads. It asserts
  that the response validates against the response model, that usage has tokens and latency, and that the strict
  schema was accepted. This catches structured-output incompatibilities early, for about nine strong-tier calls.
- `live/test_compile_live.py`: compiles a copy of `ws_mini` with `provider: claude-code` and the real compiler LLM
  (`accept_proposals=True`). It asserts success and passing replay tests. It wraps the LLM in `RecordingLLM`, writing
  `recorded_script.yaml` so a developer can refresh the offline scripts.

### 16.4 Acceptance test (live): `wynd compile process_supplier_invoice` in a temp copy of the examples workspace

08 owns the file, `examples/invoices/tests/test_compile_from_proto.py`. This area supplies its required assertions
and helps maintain it:

```
1. Copy examples/invoices to tmp_path/ws; `git init`. Remove processes/process_supplier_invoice/steps/ and
   processes/process_supplier_invoice/tests/ (keep proto/, process.yaml, examples/). Commit.
2. env: WYND_HOME=tmp_path/home (fresh user registry with the claude-code tiers seeded), plus the caller's claude
   auth (logged-in CLI, CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY).
3. `uv run wynd validate process_supplier_invoice` exits 0.
4. `uv run wynd compile process_supplier_invoice --accept-proposals` (cwd ws, timeout 90 min) exits 0 and names
   the branch wynd/compile/process_supplier_invoice/<id>. HEAD equals the branch commit (the CLI fast-forwarded)
   and the tree is clean.
5. Every node has step.lock.yaml with a `compiled` section and proto_hash == proto_hash(proto). read_pdf,
   validate_fields, save_record and escalate_to_human are deterministic. fix_fields is agentic.
   extract_invoice_fields is agentic or split (both are faithful outcomes). Every agentic package has a non-empty
   cassettes/. tests/test_process.py and tests/cassettes/ exist.
6. `uv run wynd test process_supplier_invoice` exits 0 (replay: step tests and process integration tests), with
   the claude credentials removed from the environment.
7. `uv run wynd compile process_supplier_invoice` again exits 0. The report says every step was skipped, and no
   branch is created.
8. Append one example to proto/validate_fields.yaml, commit and compile: only validate_fields is recompiled, and
   its test file gains one test.
```

---

## 17. Interpretations

| Id | Ambiguity | Interpretation (simplest faithful) |
|---|---|---|
| I-1 | Schema inference: ask or present? Where do inferred schemas live? | Present, never ask ("present in plain language; the user edits examples, not schemas"). They are not written to the proto. The lock records them as `interface` with `compiled.inferred_interface: true`, and a recompile uses them as a stability hint |
| I-2 | "Passes most but not all" (rule 3) | Strictly more than half handled, **zero wrong answers**, and at least one deferred (`NotImplementedError`) |
| I-3 | Overlap between rules 2 and 3 | Precedence is 4, then (1, 3), then 2. The deterministic attempt runs when the LLM classifies more than half of the examples as pure. Otherwise rule 2 applies directly. It is also the fallback |
| I-4 | "Default tier cheap" when cheap cannot pass | One escalation to `standard` (two attempts), recorded with its reason. Then a clarification. Never `strong` automatically |
| I-5 | Stop at the first question, or ask everything? | Every node runs up to its question point in one pass. All questions are asked at once, and nodes without questions complete |
| I-6 | Resubmission and branch identity | A new job per resume (06). The root job id is the session id and names the branch. WIP commits hold completed steps between resumes (C-P8). The final output is squashed into one commit on the base |
| I-7 | "If still failing, raise a clarification question": for which kinds? | Shell and agentic failures ask. A failing deterministic attempt first falls back to agentic, because rule 1's premise has been shown false |
| I-8 | Splitting shared step-root steps, or steps with external references | Not done. They become plain AgenticSteps, and the report says why. The agentic half binds its inputs from `steps.<n>.outputs.inputs.<f>` (C-S9) |
| I-9 | Persistence of clarification answers | Persisted to `compiled.guidance` and shown to later compiles as "earlier guidance (may be outdated)" |
| I-10 | "In v1 `wynd compile` only ever sets the process default" | The compiler never writes `provider`, neither per step nor in `process.yaml`. The spec's default `claude-code` applies when it is omitted |
| I-11 | Hand-written steps | Spec-literal: re-touched only when their proto hash changes, with the old source as the baseline in the prompt. A step without a proto is never touched |
| I-12 | `exit: error` in examples | Allowed. It tests that the step fails loudly (for example, a missing file) |
| I-13 | Env and temp paths in tests | 08's conventions: relative path values resolve against the process dir (01: the package dir for root steps), `{tmp}` is a per-example temp dir, and examples may carry `env:` |
| I-14 | "Edges are compiled like steps" (3.4) | v1 edges are expressions run by the spec's evaluator, the safe equivalent of emitted code. The compiler compiles edges by supplying interfaces for M3 type checks, rewriting them in splits, and (M5) syncing `edges.lock.yaml` |
| I-15 | Compiling the reference closure | Child processes compile first, in the same job and session. Step-root steps are compiled in place when their proto changed. Child questions are prefixed `<child-id>:` |
| I-16 | Proto with no examples | Proposals are still made. If there are still no examples, a clarification with `expects: examples` is asked (no default): a step without tests is never compiled |
| I-17 | Integration test failures | The job fails and the branch is published with the report. There is no automatic revision |
| I-18 | `next()` name | `CompileSession.run(env)`: it needs the job environment, and `next` shadows a builtin. The semantics are the spec's `next()` |

---

## 18. Risks and open points for the lead

- **R-1: pytest in the driver environment.** Generated process tests (11) run under pytest in the compile job's
  interpreter, and 04's `wynd test` runs pytest too. So `pytest` must be a **runtime** dependency of
  `wynd-process` (or `wynd-compiler`), not only a dev dependency. It is on the approved list; this moves its scope.
  Step venvs get pytest through 04's `ensure_local_venv`.
- **R-2: Running model-written code.** Compile jobs execute generated code, as tests, with the job's permissions.
  The in-process and subprocess runners give no isolation; the Kubernetes runner does. This is inherent to "run
  examples as tests" and must be documented. It must never be switched by environment in code (spec 15).
- **R-3: Cost and latency.** Worst case per step is about 11 strong-tier calls plus up to 6 live test passes. A
  sequential compile of the dogfood's 6 steps is estimated at 20–40 minutes through Claude Code, and first-time venv
  creation adds minutes. Nodes are independent until the process-level phase, so compiling them in parallel would be
  a later, local change inside `compile_closure`. It is not designed in now.
- **R-4: Cross-area requirements** (REQUIREMENT items in 3): C-S5 (`StepLock.compiled`), C-S8, C-S9 (`inputs` on the
  error payload), C-R1 (`AgentRequest.prompt`/`thinking`), C-R2 (claude-code isolation), C-R3 (`expect`,
  `match_outputs`, `sub_tmp`), C-R5 (`WYND_EVENTS_FILE`), C-R10 (`fake` provider), C-P3 (`step_python`), C-P5
  (`run_step_suite`), C-P8 (resumed checkout on the existing branch).
- **R-5: Web editor and split markers.** If the graph editor re-serialises `process.yaml` without comments, split
  removal falls back to structural matching (10.2). This works but makes a noisy diff.
- **R-6: Recording is nondeterministic.** A live agentic test can pass once and fail on re-record. Tests replay by
  default, and the live job keeps old cassettes for steps that fail live.

---

## 19. Reconciliation with the parallel drafts

| Topic | Drafts | This design's choice | Why |
|---|---|---|---|
| Step module and entrypoint | 01: `pkg.step:Class`; 02: `step.py`, `step:Class`, mounted as `wynd_steps.<pkg>`; 08: flat `<name>.py`, `entry: <name>:<Class>` | Flat `<name>.py`, `entrypoint: <name>:<Class>` relative to the package, loaded by 02's mount | Matches spec 6.3 ("the step module", `test_<step>.py`), 08's sample and 02's collision-free loader. Only the module file name differs from 02 |
| Lock schema | 01 `StepLock`; 08 assumes `version/entry/env/lock/retries: 0` and defers to 01 | 01's `StepLock`, plus `compiled` (C-S5) | 01 owns the model and rejects unknown keys |
| `retries` | 01 `RetryPolicy(run, validation, tool)`, agentic `{1, 2, 1}` | 01 | Spec 3.5 defaults are covered. `run: 1` for agentic is 01's interpretation |
| Error-exit payload | 01 `StepError` (no inputs); 02 `ErrorOutput(inputs=…)`; 04 validator uses `ProcessError` fields incl. `inputs` | `inputs` is required (C-S9) | The split binds the agentic half's inputs from it |
| Proto hash input | 01: normalised `ProtoStep`; 04: raw mapping | 01's function, called by everyone | One definition prevents false "design" status |
| Step hash | 01: file walk; 04: git tree over blob ids | Whatever 04's `run_tests` uses (the compiler never computes it) | Test-result keys are 04's |
| Test runner | 04 owns `run_tests` and `run_test_live_job`; 06 lists them in `wynd.process.testing` | Consume them. Request `run_step_suite`. Specify live-job behaviour (12.3) | Avoids two test runners |
| Process examples | 04: run directly, cassettes `<process>/cassettes/`; 08: generated `tests/test_process.py`, cassettes `tests/cassettes/` | Generated file (08) | Spec 9 says process examples "become integration tests". The sample expects the file |
| Comparison semantics | 04: deep-equal subset; 08: exact `==`; spec: unspecified | `match_outputs` in `wynd.runtime.testing` (subset, float tolerance, `present`), used by both | One semantics for step and process tests |
| `{tmp}`, example `env:` | 08 | Adopted (I-13); 01's `Example` needs `env: dict[str, str] = {}` | The sample needs it |
| Session JSON | 06: `transcript` + `pending_questions`, kinds incl. `schema_confirmation`; 07: `steps`, `questions`, `inferred_schemas`, `events`, kinds `clarification`/`example_proposal` | 07's shape, with 06's pending questions in `record.questions` | The web is the view. Schema confirmation is not a question (I-1) |
| Question ids | 06: `q_<sha8>`; 07 example: `q1` | Readable deterministic ids (`extract.example1`) plus a fingerprint | They meet 06's determinism requirement and are typeable on the CLI |
| Handler signature | 06: `run_compile_job(ctx) -> JobOutcome`, `ctx.commit`, `ctx.save_session`, `inputs.accept_proposals` | Adopted | 06 owns the harness |
| CLI | 06: exit code 4 for awaiting input, `--answer`, `--answers-file`, `--accept-proposals` | Adopted (6.7) | 06 owns the CLI |
| Acceptance test | 08 owns `examples/invoices/tests/test_compile_from_proto.py` and expects `extract` to be agentic | 08 owns it with 16.4's assertions; `extract` may be agentic **or split** | Rule 3 can legitimately fire on templated invoices |
| M5 edge locks | 08: the compile job calls `sync_edge_lock` and records process cassettes | Adopted (11.2) | Compile commits all "compiled" artefacts |
| Id renumbering | 03 cites this draft's **first** ids (C-R1..C-R14, C-P5) | Old → new: C-R4 (`WYND_DEFAULT_PROVIDER`) → C-R3 note; C-R5 (`promote`) → C-R4; C-R6 (trace events) → C-R5; C-R7 (error `input`) → C-S9 (`inputs`); C-R8/C-R9 (step API, builtins) → C-R8; C-R10 (`step_interface`) → C-R7 (`describe`); C-R11/C-R12 (MCP, registry) → C-R9; C-R13 (`fake`) → C-R10; C-R14 (RunRegistry) → C-P6; old C-P5 (`run_process`) → 08's C13 (11.1) | 03 already provides `AgentRequest.prompt`/`thinking`, claude-code isolation (`setting_sources=[]`, `tools=[]`), the `fake` provider and `promote` |
