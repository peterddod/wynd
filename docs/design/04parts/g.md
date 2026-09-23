## 15. Interfaces CONSUMED (names to reconcile)

### 15.1 From `wynd.spec`

| Name | Shape this design codes against |
|---|---|
| `WorkspaceConfig`, `RootSpec` | §3.1 |
| `ProcessSpec` | `kind`, `name`, `goal?`, `latency: Literal["fast"] \| None`, `provider: str \| None`, `env.base: Literal["debian-slim-python","alpine-python"] = "debian-slim-python"`, `entry`, `inputs: dict[str, str]`, `outputs: dict[exit, dict[str, str]]` (flat form normalised to `done`), `examples: list[{inputs, outputs?, exit="done"}]`, `steps: dict[alias, StepRef(use: str)]`, `edges: list[Edge]`, `on_error: str \| None`, `finally_: list[str]` (alias `finally`). `Edge(from_: str (alias "from"), to: str \| list[Branch], with_?, limits?, kind: Literal["deterministic","agentic"])`. `Branch(step: str, when: str \| bool \| None, with_: dict (alias "with"), limits: Limits \| None, name: str \| None)`. `Limits(max_traversals: int \| None, timeout?, retries?)`. The validator re-derives shorthand/list locations from `LoadedProcess.raw`, so either model normalisation is fine. |
| `ProtoStep` | `name`, `instruction`, `inputs: dict[str,str] \| None`, `outputs` (nested by exit, or flat = done) `\| None`, `exits: list[str]` (default `[done]`), `examples`, `env: EnvFragment` |
| `StepLock` | §3.8 fields: `kind`, `entrypoint`, `proto_hash`, `provider`, `tier`, `thinking`, `retries`, `effects`, `tools`, `mcp[{server, allow, tools, auth_env?}]`, `env_vars[{name, description, secret, used_by}]`, `env: EnvFragment` (`deps` = pinned set), `interface: {input, exits}` |
| `EnvFragment` | `deps: list[str]`, `system: list[str]`, `requires: Literal["glibc"] \| None`, `env: list[EnvVar]` (providers), `env_groups: dict[str, EnvGroup]` (providers) |
| `ProcessError` | pydantic model; `.model_json_schema()` is the `error` exit schema and the `on_error` input |
| `DEFAULT_PROVIDER = "claude-code"`, `DEFAULT_MAX_TRAVERSALS = 10` | constants |
| `wynd.spec.types.type_to_schema(t: str) -> dict`, `fields_to_json_schema(fields: dict[str,str]) -> dict` (06 name) | proto/process type strings → JSON Schema (`path` → `{type: string, format: path}`, `date` → `format: date`, `object` → open object) |
| `wynd.spec.expr.parse(text) -> Expr` | Raises `ExprSyntaxError(message, line, column)`. Nodes (frozen dataclasses): `Literal(value)`, `ListExpr(items)`, `ObjectExpr(items: list[tuple[str, Expr]])`, `Ref(root: str, path: tuple[str \| int, ...])` (e.g. `Ref("steps", ("validate","outputs","fields","total"))`, `Ref("edges", ("validate.done", 0, "taken"))`, `Ref("edges", ("validate.done", "retry", "taken"))`), `BinOp(op, left, right)` for `== != < <= > >= and or in + - * / %`, `UnaryOp(op: "not" \| "-", operand)`, `IfExpr(branches: list[tuple[Expr, Expr]], orelse: Expr)`, `Call(name, args)`. `BUILTINS: dict[str, tuple[int, int \| None]]` (min, max arity). If spec's AST differs, only `validate/exprcheck.py` and `validate/dataflow.py` (atom recognition) adapt. |
| Evaluator semantics (spec/runtime) | (a) soft positions: direct `Ref` args of `default` (arg 0) and `coalesce` (all but last) evaluate unavailable refs to `null`; (b) `steps.x.exit` is `null` before `x` runs; (c) `and`/`or`/`not` return booleans; (d) YAML string values in `with:`/`when:`/`limits:` are expressions, other YAML scalars are literals. |

### 15.2 From `wynd.runtime`

| Name | Shape |
|---|---|
| `wynd.runtime.__version__` | == base version |
| `provider_info(name) -> ProviderInfo` | `ProviderInfo(name, kind: Literal["model","agent"], fragment: EnvFragment)`. Loads entry point group `wynd.providers` and reads a class attribute `env_fragment`. `KeyError` if unknown. The claude-code fragment is expected to declare `deps: ["claude-agent-sdk>=0.2,<0.3"]` (plus anything else its bundled CLI needs), `env` = `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY` (secret, group `claude-code-auth`), and `env_groups: {claude-code-auth: {min: 1, modes: [image]}}`. |
| `wynd.runtime.executor.run_process(plan: ExecutionPlan, inputs: dict, *, run_id: str \| None = None, env: Mapping[str,str] \| None = None, traces: TraceSink \| None = None, registry: RunRegistry \| None = None, cassette_mode: Literal["live","replay","record"] = "live", cassette_dir: Path \| None = None) -> RunResult` | `RunResult(run_id, exit, outputs, error)` |
| WorkerPool | consumes `ExecutionPlan.venvs` per §6.5 (python rule, `PYTHONPATH` = `sys_path`, pre-import `steps`); worker module `wynd.runtime.worker` |
| `python -m wynd.runtime.describe <module:Class>` | prints `{"input": <schema>, "exits": {name: <schema without exit>}}` (pydantic `model_json_schema`) |
| `wynd-supervisor` console script (in `wynd-runtime`) | `serve` (default CMD, env `WYND_PLAN`, `WYND_ENV_MANIFEST`, `WYND_VENV_ROOT`, `WYND_PORT`); `check-plan <lock path>` (spawns each venv's worker, imports every entrypoint, exits non-zero on failure); runs `check_env(..., mode="image")` at startup |
| `wynd.runtime.bake.main(plan_path: Path, site: Path, argv: list[str]) -> int` | §8.9 |
| Cassette env contract | `WYND_CASSETTE_MODE ∈ {replay, record}`, `WYND_STEP_DIR` → replay reads `<dir>/cassettes/`, record promotes there; process-level via `run_process(cassette_dir=…)` |
| `wynd.runtime.storage` | `RunRegistry` with the shape in 06 §3.2 (`put/get/update/list/delete(kind, …)`, `put_test_result(commit, key, result)`, `get_test_result(commit, key)`, `list_test_results(commit)`); `TraceSink`; `UserRegistry.get(section, name)` for `mcp` and `image_registries`; `storage_env_vars() -> list[EnvVar]` (backend selector vars with defaults) |

### 15.3 From `wynd.compiler`

- Handlers `wynd.compiler.jobs:run_compile_job(ctx) -> JobOutcome` and `wynd.compiler.optimise:run_optimise_job(ctx) -> JobOutcome`. They use `ctx.inputs`, `ctx.session`, `ctx.commit`, `ctx.save_session`, and return `awaiting_input` with a session and questions.
- The compiled step layout in §3.8: `wynd_steps.<slug>` package, `entrypoint`, `interface` snapshot, pinned `env.deps`, `test_<name>.py` at the package root, `cassettes/`. The compiler writes `proto_hash = wynd.process.hashing.proto_hash(raw proto)` and records replay test results through `wynd.process.testing.run_replay_tests(…, commit=<its commit>)`.
- Process-level examples are run by `testing.py` from `process.yaml` (§7), so the compiler does not need to generate a process-level pytest file. If it does generate extra ones, they need pytest in the driver: see §19 R3.

### 15.4 From `wynd.controller` (06)

- The job harness `execute_job` honours §12. It uses the worktree checkout at `.wynd/jobs/<id>/checkout`, the job log file `.wynd/jobs/<id>/job.log`, the commit trailer `Wynd-Job: <id>`, and `git.result_branch` naming.
- `jobs/integrate.py` calls `wynd.process.git.integrate`.
- Before submitting `build`, `bake` or `compile`: `git.dirty_paths(ws.root, git.reference_closure(ws, pid))` must be empty.
- `wynd test` passes `commit = closure_head` only when the closure is clean.
- `.env` loading and the `wynd env check` command. This workstream only provides `assemble_env_manifest` + `check_env`.
- CLI flag requests: `wynd validate <id> --sync-interfaces`, `wynd validate -v` (show info), `wynd build <id> --push <registry> [--platform P]`, `wynd bake <id>`.

---

## 16. Interfaces PROVIDED

| Consumer | Interface |
|---|---|
| controller, compiler, web (via controller) | `find_workspace_root`, `load_workspace`, `Workspace` (`process_ids`, `process_dir`, `process_root_of`, `load_process`), `LoadedProcess`, `ResolvedStep`, `StepPackage`, `StepInterface`, `WorkingTree`, `CommitTree` (§3) |
| controller, compiler | `validate`, `validate_process`, `ValidationReport`, `Diagnostic`, `format_diagnostic`, `CODES` (§4) |
| compiler, controller | `hashing.proto_hash`, `step_hash`, `process_hash`, `tree_hash`, `canonical_json` (§5) |
| controller, compiler | `plan_local`, `execution_plan`, `run_local`, `merge_fragments`, `venv_groups`, `detect_runtime_source`, `ensure_local_venv`, `sync_interfaces` (§6) |
| runtime (data) | `wynd.spec.plan.ExecutionPlan` / `ProcessLock` (§6.5, §8.7), `wynd.spec.envmanifest` (§10) |
| controller, compiler | `testing.run_tests`, `run_replay_tests`, `tests_status`, `TestReport`; handler `run_test_live_job` (§7) |
| controller | handlers `build.run_build_job`, `bake.run_bake_job`; `base.build_base`, `publish_base`, `base_image_ref`, `ensure_base`; `artefacts.open_artefact_store`, `ArtefactStore`, `BuildInfo`; `envmanifest.assemble_env_manifest` (§8–§11) |
| controller, kube | `jobs.*` (§12): `JobKind`, `JobState`, `JobRecord`, `JobContext`, `JobOutcome`, `JobHandler`, `JobRunner`, `Artefact`, `new_job_id`, `new_job_record`, `wait_for`, `answers_inputs` |
| controller, kube | `git.*` (§13): `reference_closure`, `closure_head`, `dirty_paths`, `add_worktree`, `remove_worktree`, `worktree_for_branch`, `commit_paths`, `result_branch`, `set_branch`, `delete_branch`, `commits_touching`, `integrate`, `IntegrationResult` |
| controller | `compile.compile_state`, `CompileState` (§14) |
| kube (post-M5) | extension points by entry point + env var: `wynd.image_builders` (`WYND_IMAGE_BUILDER`, e.g. `buildctl` for rootless BuildKit), `wynd.artefact_stores` (`WYND_ARTEFACT_STORE`, e.g. object storage) |
| env vars read here | `WYND_WORKSPACE`, `WYND_RUNTIME_SOURCE`, `WYND_UV`, `WYND_IMAGE_BUILDER`, `WYND_ARTEFACT_STORE`, `WYND_BASE_REPO`, `WYND_HOME` (bake cache only) |

---

## 17. Interpretations (spec ambiguities and the simplest faithful choice)

- **I-1 Explicit ignore.** Written `to: $ignore`. At run time it behaves like an unrouted `error` exit: the process error handler runs, with cause `ignored_exit`. `$exit.error` is not a routable target.
- **I-2 Folder name == process id.** `process.yaml` `name:` equals the last segment of the id (which is the folder name). Protos follow the same rule.
- **I-3 Proto location.** Process-local protos are `<process>/proto/<name>.yaml` (§5.1 layout). Step-root protos are `<step dir>/proto.yaml`: the "proto-step YAML" marker, sitting beside the compiled files once they exist.
- **I-4 Local step id.** `<process-id>#<name>`. The spec names only root-step ids.
- **I-5 Closure includes `wynd.yaml`.** A root remap changes what a process resolves to.
- **I-6 "Refuses a dirty tree".** Enforced on the process's reference closure, not the whole repo. Only closure content enters the build, and design auto-commits (§11) mean unrelated processes may legitimately be mid-edit.
- **I-7 Commit keys.** Test results and builds are keyed by the **closure HEAD** commit, not by `HEAD`. "HEAD of the process" and "this commit" both mean the closure HEAD (§6.5, §11). A later unrelated commit therefore doesn't invalidate a passing test or an existing build.
- **I-8 Compile/test-live job base.** The job is based on the tip of the current branch, which has the same closure content as the closure HEAD. So "fast-forward if nothing moved" is the common path. Resumed compiles reuse the parent's base.
- **I-9 `finally` steps.** Bound from `process.inputs` by field name, like `entry`. Their exits are not routed. `on_error` and `finally` steps may not appear elsewhere in the graph.
- **I-10 Soft references.** `default()`/`coalesce()` make direct reference arguments soft, both for the validator and for the evaluator. Typos are still errors.
- **I-11 Provisional interfaces.** Protos without schemas get field names from examples. Field-level findings are warnings until compile. Exits are always exact.
- **I-12 `max_traversals` fill.** In-memory normalisation, carried into the plan and lock. Never written to YAML. Reported as info.
- **I-13 Type checking staging.** Always on, and naturally lenient where schemas are `any`. No M1/M3 flag.
- **I-14 bake.** Stdlib `zipapp` with an extract-once bootstrap, not pex/shiv. Offered as a separate `bake` job kind, requiring one resolvable environment, no `system:` packages and no shell steps.
- **I-15 BuildKit vs "never a Docker socket".** Per the lead, the local builder is `docker buildx build` (BuildKit through the docker driver, which is what makes local base tags work without a registry). In clusters, the kube package supplies a socket-less `ImageBuilder` (rootless `buildctl`) selected by `WYND_IMAGE_BUILDER`, which satisfies §6.6 there.
- **I-16 `system:` packages.** Passed verbatim to `apt-get` (slim) or `apk` (alpine). Alpine fallback is triggered by `requires: glibc` or a failing musllinux binary-only resolve, not by package-name differences. An alpine `apk` failure fails the build with apk's message.
- **I-17 Provider-auth group enforced only in `image` mode.** Locally, the logged-in `claude` CLI subscription is used, and nothing in the environment can prove that. Containers and CI must provide `CLAUDE_CODE_OAUTH_TOKEN` (the dev key) or `ANTHROPIC_API_KEY`. This is data in the provider fragment, not an environment branch.
- **I-18 Host-side resolution.** Venvs are resolved on the host with `uv pip compile --universal` and pinned into `process.lock.yaml`. The image installs exactly those pins with `--no-deps`.
- **I-19 Local venvs include pytest** and editable spec/runtime when running from source. Image venvs contain neither.
- **I-20 Process-level examples** are run directly by the test runner through the executor (data-driven), not as generated pytest files. This avoids pytest as a dependency of the driver.
- **I-21 Result branches** are deleted after a successful fast-forward or rebase integration (the job record keeps `result_sha`) and kept on `needs_review`.
- **I-22 A compiled step without tests** fails `wynd test` ("no tests collected"). §6.3 says compiled packages contain generated tests.
- **I-23 Agentic edges (M5).** Accepted structurally by the same validator rules. Their extra fields belong to the M5 edge schema in spec.
- **I-24 Workspaces must be git repositories** (E101). Discovery reads through git so that ignore rules and commit views are exact.

---

## 18. Test plan

The default run is offline and deterministic. Two markers:
- `@pytest.mark.docker`: skipped unless `WYND_DOCKER=1`.
- `@pytest.mark.live`: skipped unless `WYND_LIVE=1`. None in this package: live behaviour belongs to the compiler and providers.

Tests that run `uv` set `UV_OFFLINE=1`. They rely on the uv cache warmed by the monorepo's own `uv sync` (hatchling and the runtime's dependencies are cached by it).

### 18.1 Fixtures and helpers (`packages/process/tests/conftest.py`, WS)

- `fixtures/workspaces/<name>/` holds plain directory trees. `make_repo(tmp_path, name) -> Path` copies one, runs `git init -b main`, sets a local identity, and commits it. `commit(repo, msg, **files)` writes files and commits.
- `examples/invoices/` (repo root) is used read-only through `make_repo(…, source=REPO/"examples/invoices")`.
- `FakeProviders` is a `ProviderCatalog` with `claude-code` (agent, deps `["claude-agent-sdk>=0.2,<0.3"]`, auth group) and `anthropic` (model).
- `FakeResolver` records calls. It returns canned pins for `compile`, raises `ResolutionError` for configured musl inputs, and writes a dummy wheel for `build_wheel`.
- `FakeImageBuilder` records `ImageBuildRequest`s, has a configurable `exists`, and returns a fixed digest.
- `MemRegistry` is an in-memory `RunRegistry` with the 06 shape.

### 18.2 Workspace and loader (WS): `test_workspace.py`, `test_refs.py`, `test_loader.py`, `test_hashing.py`

- Flat and hierarchical roots give the ids `a`, `finance/invoices`, `finance:extract/invoice`, `p#name`. Default roots apply when `wynd.yaml` is empty.
- Every loader code E100–E127, W128, W129 and I130 has a fixture that triggers it. Assertions are on `(code, file, path)`.
- `parse_use` table test with more than 30 cases: valid forms, `../`, absolute, `~`, backslash, `./steps/a/b`, `./proto/x`, `process:` with a bad id, alias `process`, uppercase alias, empty path.
- Proto-only vs compiled vs stale resolution, and the interface choice table rows 1–4.
- Cycles: self-reference, a 3-cycle, and a diamond without a cycle (the child is loaded once, checked by object identity).
- `WorkingTree` vs `CommitTree`: the same file set and blob ids after a commit. Untracked non-ignored files appear only in `WorkingTree`. `.wynd/` is excluded even with no `.gitignore`.
- `step_hash` is equal from both trees, changes when a cassette changes, and ignores `__pycache__` (gitignored). `process_hash` changes when a child's step file changes (parent hash includes children, §5.1). `proto_hash` is invariant to comments, key order and quoting.

### 18.3 Validator (VAL): golden tests plus unit tests

- `tests/validator/cases/<case>/` contains a minimal workspace plus `expected.json`: a list of `{code, severity, process, path}`, compared order-insensitively. One case per code E201–E222, E301–E307, E401–E406, W201–W209, I201. Plus the clean dogfood case (exactly two I201) and a "kitchen sink" case.
- `test_dataflow.py` covers synthetic graphs:
  - linear;
  - diamond (`x.outputs.f` valid only if both arms leave `x` with exit `done`);
  - loop back-edge (`fix` may be `NOT_RUN` at `validate`);
  - an `error` edge;
  - guard refinement: `when: steps.x.exit == "done"` makes `with:` valid; the else branch after a single-atom guard is negatively refined; a conjunction guard gives no negative refinement;
  - `and`/`or`/inline-`if` refinement inside one expression;
  - `default`/`coalesce` soft positions (a typo is still E302);
  - `previous`;
  - the fixpoint terminates on a graph with 3 nested cycles.
- `test_cycles.py`: `max_traversals` is filled on exactly the in-SCC branches. Property test: permuting the order of `steps:` and `edges:` in 50 shuffles gives an identical normalised output (DFS-order independence). Self-loop. An explicit `max_traversals: 3` is kept.
- `test_types.py`: `from_schema` for pydantic-generated schemas with `$defs`, `Optional`, `list[Model]`, `Literal`; the assignability table; operator and builtin typing.
- `test_codes.py`: every emitted code is in `CODES`, and every `CODES` entry appears in some golden `expected.json`.

### 18.4 Env, venvs, plan, testing (ENV)

- `test_fragments.py`: grouping for the dogfood gives 3 groups, keys stable across runs and orderings. Provider deps are added only to agentic steps. Child steps are included, and the root provider applies to them. `glibc_required_by` provenance. `normalize_requirement` table.
- `test_runtime_source.py`: fake distributions with and without editable `direct_url.json`, and the `WYND_RUNTIME_SOURCE` directory and version forms.
- `test_venvs.py`:
  - `ensure_local_venv` with a fake `_proc.run` checks the exact `uv` command lines and the marker file, and that a concurrent winner gives `rmtree(tmp)`.
  - Real `uv` (offline): create a venv for an empty group with editable spec/runtime, then import `wynd.runtime` in it.
- `test_plan.py`: golden `ExecutionPlan` YAML for the dogfood in local mode (paths templated) and in image mode. `mode`, `venv_root` and source paths are the only differences.
- `test_testing.py`: a fixture step package with a passing test and a failing test in a real local venv (offline). JUnit parsing, "no tests collected", interface drift detection (through a stubbed `describe`), registry keys `step:…`/`process:…` written only when `commit` is given, `match_outputs` subset semantics, relative `path` inputs resolved against the process dir.
- `test_envmanifest.py`: golden `process.env.yaml` for the dogfood. Merging rules (union `used_by`, `secret` OR). `check_env` table: missing, empty string, defaults, group min, group modes (`local` passes with no token, `image` fails).

### 18.5 Builder, bake, base (BLD)

- `test_dockerfile.py`: golden files `golden/Dockerfile.dogfood-slim`, `Dockerfile.system-slim`, `Dockerfile.system-alpine`, rendered from fixed `ProcessLock` fixtures. Byte-exact.
- `test_base_dockerfile.py`: golden `base-slim.Dockerfile`, `base-alpine.Dockerfile`.
- `test_variant.py`: slim requested gives slim. Alpine plus `requires: glibc` gives slim with reason. Alpine plus `FakeResolver` musl failure gives slim with the reason naming the venv and steps. Alpine clean gives alpine.
- `test_wheels.py`: offline `uv build` of a fixture step. The wheel contains `wynd_steps/<mod>/__init__.py` and `wynd-step.json` and **no** `tier`/`thinking`/`provider` keys. Bad entrypoint gives an error.
- `test_build_job.py` (fakes for resolver, image builder, registry):
  - design phase fails;
  - validation errors fail;
  - a recorded passing test result skips the test run;
  - no record runs the tests and records;
  - failing tests fail the build;
  - the build dir layout is exact (`Dockerfile`, `process.lock.yaml`, `process.env.yaml`, `dist/`, `venvs/*/requirements.txt`, `build.json`);
  - the lock records the commit (closure HEAD), `source_sha` and the base choice, and contains no timestamps (two builds of the same commit give byte-identical locks);
  - push with a registry entry performs login, tag and push in that order;
  - an unknown registry fails before building;
  - nothing under the tracked tree changes (`git status --porcelain` is empty afterwards).
- `test_bake.py` (offline, real `uv`): a pure-Python fixture process with one deterministic step gives a `.pyz`. `python3.12 app.pyz '{"x": 1}'` prints the expected outputs, using a stubbed `wynd.runtime.bake.main` if the runtime is not ready. A second run skips extraction. A process with `system:` or a shell step is not bakeable, with the expected message.
- `@docker` `test_docker_build.py`:
  - `build_base("0.1.0", ["slim"])` builds `wynd-base:0.1.0-slim` locally;
  - `ensure_base` finds it;
  - a full `run_build_job` of the dogfood with `FakeRegistry` holding a passing test record produces an image;
  - `docker run --rm <image> check-plan /opt/wynd/process/process.lock.yaml` exits 0;
  - the image user is `wynd`;
  - labels are present;
  - there is no `.env` in the image (`docker run … test ! -e /work/.env`).

  The alpine base build is also `@docker`. Trace parity between local and image runs is an M2 acceptance test owned by runtime/controller.

### 18.6 Git and jobs (GIT)

- `test_git_closure.py`: the closure for local-only, root-step and child-process processes, including `wynd.yaml`. Dogfood equals `["processes/process_supplier_invoice", "wynd.yaml"]`.
- `test_git_head.py`: `closure_head` does not move when another process commits, moves when a referenced root step or a child changes, and returns `None` for an uncommitted process.
- `test_git_dirty.py`: untracked, modified and staged files inside the closure are dirty. Files outside the closure and ignored files are not.
- `test_integrate.py` (temp repos):
  1. The target has not moved: `fast_forward`. The target is checked out in the main worktree and the files update. The branch is deleted.
  2. The target moved with commits outside the closure: `rebased`. The target is now a linear descendant and the rebased commits keep their messages.
  3. The target moved with a commit touching the closure (including via `wynd.yaml`, and via a child process dir): `needs_review` and the branch is kept.
  4. The target is checked out with conflicting local changes: `needs_review` with the ff refusal message, and the user's files are untouched.
  5. The target is not checked out anywhere: `set_branch` CAS. A CAS race gives `needs_review`.
  6. Already integrated: `noop`.
  7. A commit that touches the closure and a later revert still give `needs_review` (per-commit rule).
- `test_jobs.py`:
  - `new_job_id` format and sortability;
  - `new_job_record` resolves `base_sha` and `target_branch` for `HEAD`, a branch, and a detached sha;
  - `answers_inputs` merges answers;
  - `JobRecord` round-trips through `MemRegistry`;
  - a reference harness (a 40-line test double of 06's `execute_job` in `tests/harness.py`) proves the contract: success with a commit gives branch `wynd/compile/<p>/<id>`; `awaiting_input` stores the session and the resumed job receives it; an exception gives `failed` and the checkout is kept; the worktree is removed on success; a process id with slashes gives nested branch names.

### 18.7 Coverage gate

`tests/test_spec_coverage.py` holds a table mapping each clause in §1 to at least one test id. It fails if a clause row has no test. This is a cheap audit that the synthesizer can extend.

---

## 19. Risks and open items for the synthesizer

- **R1 Ownership overlap with 06.** 06 places the job harness, checkout backends and `jobs/integrate.py` in the controller, and has its own `controller/git.py`. Recommendation:
  - keep the harness and checkout backends in the controller (06);
  - keep **all git algorithms** (closure, closure HEAD, dirty, integrate, result-branch naming) in `wynd.process.git`, which the controller calls, so there is exactly one implementation;
  - have `controller/git.py` wrap only design-commit helpers.
- **R2 `ExecutionPlan`/`ProcessLock` ownership.** Proposed in `wynd.spec.plan`, authored here. If the runtime draft defines a `VenvPlan`, adopt one model. The required fields are in §6.5.
- **R3 Process-level pytest files.** If the compiler insists on generating `processes/<p>/tests/test_*.py`, running them needs pytest in the driver environment. Either add `pytest` as a dependency of `wynd-process` (flagged: new runtime dependency) or run them in a dedicated local "driver venv" (spec + runtime + process + pytest). The design avoids both by running process examples directly (I-20).
- **R4 claude-agent-sdk platform wheels.** Its bundled CLI binary determines the musl viability and bake portability of agentic processes. The musl check (§8.3) handles the fallback automatically. Bake extraction restores exec bits.
- **R5 Uncommitted design edits and compile.** Compile jobs see only commits. Web auto-commit (§11) keeps this invisible there. The CLI must refuse `compile` on a dirty closure (06).
- **R6 `uv` offline tests.** These depend on a warm uv cache. CI must run `uv sync` first, which it does anyway.
