# 04 — `wynd.process`: workspace loader, graph validator, local venvs, builder, base images, JobRunner, git

Draft architecture for the `wynd-process` distribution (AGPL-3.0-or-later) plus the `wynd-base` image family. Source of truth: `docs/SPEC.md`; `§x.y` means a spec section. Where the parallel controller/CLI draft (06, seen partially at `design/parts/p1.md`) already named something I provide, I adopted its name and mark it "(06)". Everything listed under "Interfaces consumed" is a proposal that the synthesizer should reconcile.

Contents

0. Summary
1. Spec clauses owned (coverage audit)
2. Package layout, modules, owners, milestones
3. Workspace discovery and reference resolution (§5.1)
4. Graph validator (§8, §3.3–3.5, §3.4.1, §6.2)
5. Hashing (§5.1, §6.3, §6.5)
6. Env fragments, venv planning, local venvs, `ExecutionPlan` (§4, §8)
7. Test runner and test records (§6.5, §7, §9)
8. Builder (§4, §6.4, §6.5, §8) and `bake`
9. `wynd-base` image family (§4, §10)
10. Env manifest and `wynd env check` (§4, §4.1)
11. Artefact store (§5.1, §6.4)
12. Jobs: `JobRunner` interface, records, handlers, session hook (§6.6)
13. Git utilities: closure, HEAD, dirty checks, worktrees, integration (§6.5, §6.6, §11)
14. Compile state (inputs to derived status, §11)
15. Interfaces CONSUMED
16. Interfaces PROVIDED
17. Interpretations
18. Test plan
19. Risks and open items for the synthesizer

---

## 0. Summary

- **Workspace model.** `wynd.yaml` declares process roots and aliased step roots. Discovery is by marker over a `Tree`, which is either the working tree (`git ls-files -co --exclude-standard`) or a commit (`git ls-tree`). The same loader therefore answers questions about HEAD, about any historical commit (used by integration and per-commit status) and about the working tree. Process ids are paths relative to their root. Step ids are `alias:path` for root steps and `<process-id>#<name>` for process-local steps. A reference resolves to a compiled package (`pyproject.toml` + `step.lock.yaml`), a proto-step (design phase), or a child process (`process:<id>`), with cycle detection.
- **Validator.** It enforces every graph rule in §3–§8 with stable codes (`E1xx` loader, `E2xx` graph, `E3xx` references, `E4xx` types, `W`/`I` for warnings and info). Reference checks are **exit-aware** through a forward dataflow analysis. The lattice is "set of possible last exits per step, plus *not-run*", joined at merge points and iterated to a fixpoint, so loops are handled. It is path-sensitive for a small, sound set of guard atoms (`steps.x.exit == "a"`, `in [...]`, `!=`, `steps.x.runs > 0`) in `when:`, `and`, `or` and inline `if`. `default()` and `coalesce()` make their non-final reference arguments "soft". `max_traversals` is filled on every branch whose endpoints share a strongly connected component (Tarjan), which does not depend on DFS order. The M3 type checker runs over the same walk, using JSON-Schema-derived types.
- **Explicit ignore.** An exit is explicitly ignored with `- from: x.e` / `to: $ignore`. At run time that exit routes to the process error handler, the same as an unrouted `error` exit.
- **Venvs.** Every compiled step in the closure, including those of `ProcessStep` children, gets an effective requirement set: its locked deps, plus the provider fragment's deps if it is agentic. Steps are grouped by identical sets, giving **one venv per distinct set**. Local mode materialises each group as a `uv` venv under `.wynd/venvs/<hash>/`, installing `wynd-spec` + `wynd-runtime` (editable from the monorepo when running from source, otherwise pinned) plus the group's requirements plus pytest. Step modules are imported from their source directories. Image mode builds the same groups under `/opt/wynd/venvs/<key>/`. Both produce one `ExecutionPlan`, which is what runtime's executor and `WorkerPool` consume. The mode, the `venv_root`, and whether source paths are present are the only differences.
- **Builder.** The build job validates, requires compiled steps, and checks for a passing replay-test record for (closure-HEAD commit, process hash), running the tests if none exists. It then resolves each venv host-side with `uv pip compile --universal` and builds step wheels with `uv build`, adding `wynd-step.json` metadata and no tier. It picks the base variant: alpine is used only if no fragment `requires: glibc` and a musllinux binary-only resolve succeeds. Otherwise it falls back to slim and records the reason. It writes `Dockerfile`, `process.lock.yaml`, `process.env.yaml`, `dist/` and `venvs/*/requirements.txt` into a staging dir, builds through the `ImageBuilder` interface (default `docker buildx build`, docker driver, so local `wynd-base:<ver>-<variant>` tags work without a registry), optionally pushes to a user-registry entry, and commits the result to the `ArtefactStore` at `.wynd/build/<process>/<commit>/`. `bake` produces a stdlib `zipapp` with a self-extracting bootstrap, because pydantic-core is a compiled extension that cannot load from a zip.
- **Base images.** Templated Dockerfiles for `<ver>-slim` (`python:3.12-slim-trixie`) and `<ver>-alpine` (`python:3.12-alpine3.22`). Each has pinned `uv`, the spec and runtime wheels vendored in `/opt/wynd/wheels`, a supervisor venv, a non-root user, and `ENTRYPOINT wynd-supervisor`. `build_base` and `publish_base` go through the same `ImageBuilder`.
- **Jobs.** `wynd.process.jobs` defines `JobKind` (`compile`, `test_live`, `build`, `bake`, `optimise`), `JobState` (including `awaiting_input`), `JobRecord` (stored in `RunRegistry` kind `"job"`), `JobContext`, `JobOutcome`, `JobHandler` and the `JobRunner` protocol (`submit`, `status`, `logs`, `artefacts`). Resuming after a question means submitting a new job with `parent_job` and `answers`; the harness hands that job the parent's serialised session.
- **Git.** Provides: the reference closure (own dir + referenced step dirs + child closures, transitively, + `wynd.yaml`); closure HEAD (`git log -1 -- <closure>`); dirty checks restricted to the closure; worktrees; result branches `wynd/<kind>/<process>/<job-id>`; and `integrate()`. `integrate()` fast-forwards if the target has not moved. If it has moved but no intervening commit touches the union of the closures at base and tip, it rebases in a scratch worktree. Otherwise it leaves the branch for review. The user's checkout is only ever fast-forwarded.
- **Env manifest.** Assembled from step locks (`env_vars`, MCP auth env), provider fragments, `env.*` references in edge expressions and runtime storage selectors. `check_env()` is a pure function in `wynd.spec.envmanifest` so the in-image supervisor can run it as a startup gate. Provider-auth alternatives (`CLAUDE_CODE_OAUTH_TOKEN` | `ANTHROPIC_API_KEY`) form a group that is enforced in `image` mode only, because locally a logged-in `claude` CLI is used.

---

## 1. Spec clauses owned (coverage audit)

C = consumed only (implemented elsewhere, called or checked here).

| § | Clause | Where |
|---|---|---|
| 3.2 | `ProcessStep`: child runs in same container, builder merges child fragments recursively; child `env.base`/`provider`/`latency` ignored and validator warns when they differ; child error exit propagates (C runtime) | `fragments.py` (closure merge), `validate/rules.py` W203–W205, interface of `process:` refs includes `error` |
| 3.3 | Process has `entry:`; entry `Input` bound from `process.inputs` by field name; mismatch is a validator error; no `$entry` edge | `validate/bindings.py` E210 |
| 3.4 | One edge per exit; `to:` list if/elif/else; first bare branch is else, later branches ignored + warning; all-conditioned & none true → error handler (C runtime); `with:`/`limits:` on branch or shorthand edge; `max_traversals` auto-filled (default 10) on **every branch on a cycle**; `kind: deterministic|agentic` | `validate/structure.py` E202–E206, E218, E222, W201; `validate/cycles.py` I201; agentic edges accepted structurally (M5 fields validated by spec model) |
| 3.4.1 | Reference validity in M1 (`steps.x.outputs.y` exists, `to` is a real step); full type checking in M3; `steps.<name>` in loops = latest run; counters `steps.x.runs`, `edges["a.b"][i].taken`, named branches | `validate/dataflow.py`, `validate/exprcheck.py`, `validate/types.py` |
| 3.5 | Implicit `error` exit, exempt from routing; unrouted exit / traversal limit → process error handler (C runtime); `on_error` step receives `ProcessError`, its exits map to `$exit.<name>`; `finally:` steps; `ProcessStep` propagates `error` | `validate/structure.py` E208 exemption, E213–E215, E219–E221, E404, E406 |
| 3.8 | Tools/MCP declare env vars → build knows what a step requires; secrets are env references resolved at run time, nothing in image/lockfile | `envmanifest.py` (collect), builder never copies `.env`; Dockerfile sets only non-secret `WYND_*` |
| 3.9 | Providers contribute env fragments merged by the same builder; tier→model mapping never in a lockfile; validator warns AgentProvider under `latency: fast`; provider per-step lockfile field with process default | `fragments.py`, `validate/rules.py` W206/W207; `process.lock.yaml` carries tier *names* only |
| 4 | Process = image boundary; exactly one Dockerfile; default slim, alpine opt-in, musl fallback recorded; fragments (deps, `system:`, `requires: glibc`); one venv per distinct dep set built with `uv`; builder owns venv assignment; `spec`+`runtime` in every venv, no system-site-packages; one worker per venv (C runtime); local mode same scheme under `.wynd/`; versioned `wynd-base:<ver>-<variant>`; runtime version == base version; nothing undeclared installed; env manifest; `wynd env check`; secrets never baked | `fragments.py`, `venvs.py`, `plan.py`, `build/*`, `base.py`, `envmanifest.py`, `wynd.spec.envmanifest` |
| 4.1 | Image runs unchanged with supervisor entrypoint; env manifest maps to Secrets/ConfigMaps; `env check` usable as startup probe | base `ENTRYPOINT`, `check_env` in spec, image label with manifest (06) |
| 5 | `process` depends only on `spec` + `runtime` (+pyyaml); never inside an image | `pyproject.toml` |
| 5.1 | `wynd.yaml` roots (flat or hierarchical), discovery by marker, identity by relative path, leaves don't nest, processes only under process roots / steps only under step roots, roots committed, `url:` reserved; three `use:` forms, reject everything else incl. `../`; `process:<id>` child; parent compiled hash includes children's; reference cycles are load-time errors; build outputs never tracked; folder name == process id | `workspace.py`, `refs.py`, `loader.py`, `hashing.py`, E100–E127 |
| 6.1 | Proto-step shape (inputs/outputs nested by exit, flat = done-only, `exits`, `env.deps`/`requires`) | `interface.py` (proto → interface), `loader.py` |
| 6.2 | `$exit.<name>` must be declared under `outputs`; process outputs bound by terminating edge's `with:` | E207, E211, E405 |
| 6.3 | Compiled step = source package with `pyproject.toml`, module, tests, cassettes, `step.lock.yaml`; test results in RunRegistry keyed by commit + step hash, not in lockfile; wheel contains module, pinned dep set, metadata (proto hash, kind, tool snapshot), **no tier** | `loader.py`, `testing.py`, `build/wheels.py` |
| 6.4 | `.wynd/build/<process>/<commit>/`: `Dockerfile`, `process.env.yaml`, `process.lock.yaml` (resolved step locks, venv plan, base chosen + why, `wynd-base` version, commit), `dist/` | `build/*`, `artefacts.py` |
| 6.5 | Build: reproducible from a compile commit without the compiler; refuses dirty tree; records commit; tests must pass (replay run or recorded pass on same commit); "HEAD of the process" = last commit touching closure; compile output = commit on `wynd/compile/<process>/<job-id>`; integrate: ff / auto-rebase if intervening commits outside closure / else PR; `test --live` is a job producing a branch | `build/job.py`, `git.py`, `testing.py` |
| 6.6 | `JobRunner` interface in `process`: `submit(kind, ref, inputs) -> job_id`, `status`, `logs`, `artefacts`; implementations in controller (C); jobs on a git checkout at a ref; worktree under `.wynd/jobs/<job-id>/`; push result branch back into same repo; BuildKit, never a Docker socket (see Interpretation I-15); push to registry from user registry; `awaiting_input` + resubmit with answer | `jobs.py`, `git.py`, `build/imagebuilder.py` |
| 7 | Replay by default, live re-records; cassettes promoted into `steps/<name>/cassettes/` (C runtime mechanics) | `testing.py` (sets env contract, commits re-recorded cassettes) |
| 7.1 | RunRegistry holds jobs + test results + sessions (C); no code assumes a home dir | `jobs.py`, `testing.py`; all paths are workspace-relative or `WYND_HOME` |
| 8 | Loader; graph validation list (all items); builder pipeline; `bake` (pex/shiv → zipapp, see I-14) | `loader.py`, `validate/*`, `build/*`, `bake.py` |
| 10 | Backing functions for `validate`, `test`, `build`, `env check`, `base build\|publish` (CLI in 06) | §16 |
| 11 | Reference closure definition; "this commit" for build's tests-passed check; status derived per commit (C controller, fed by `compile_state`, `tests_status`, artefact store) | `git.py`, `compile.py`, `testing.py`, `artefacts.py` |
| 12 | M1 loader/validator/local venvs/test; M2 builder/base/bake; M3 type checking + integration; post-M5 kube runner consumes `JobRunner`/`ImageBuilder`/`ArtefactStore` seams | §2 milestone column |
| 13 | Decisions: routing on edges, exits in `Output`, builder owns venvs, one Dockerfile, slim default, reference checks exit-aware, closure-based HEAD, jobs on checkouts, build refuses dirty tree | throughout |
| 14 | Open questions: auto-rebase rule implemented as stated; `max_traversals` fill reported as *info* not warning to limit noise | §13.8, §4.6 |
| 15 | AGPL for `process`; environment differences as backends by env var (`WYND_IMAGE_BUILDER`, `WYND_ARTEFACT_STORE`, job checkout backends), never `if <environment>`; job usage recorded | `pyproject.toml`, `imagebuilder.py`, `artefacts.py`, `JobRecord.usage` |

---

## 2. Package layout, modules, owners, milestones

Sub-owners inside this workstream, so each module has exactly one owner:

- **WS**: workspace, refs, loader, interfaces, YAML locations, hashing, errors
- **VAL**: validator
- **ENV**: fragments, venvs, plan, local run, testing, env manifest (+ two spec modules)
- **BLD**: build, bake, base, image builder, artefact store
- **GIT**: git, jobs, compile state

```
packages/process/
  pyproject.toml                         BLD   M1
  LICENSE                                BLD   M1   AGPL-3.0-or-later full text
  src/wynd/process/                      (no src/wynd/__init__.py — PEP 420)
    __init__.py                          WS    M1   re-exports the public API in §16
    errors.py                            WS    M1   Diagnostic, Severity, CODES table, WyndProcessError + subclasses
    _proc.py                             WS    M1   run(args, *, cwd, env, log, check) streaming subprocess helper; require_tool()
    yamlloc.py                           WS    M1   build_locmap(text) -> dict[loc, (line, col)]
    workspace.py                         WS    M1   Tree protocol, WorkingTree, CommitTree, find_workspace_root, load_workspace, Workspace
    refs.py                              WS    M1   parse_use, LocalUse/RootUse/ProcessUse, step ids, slugs
    interface.py                         WS    M1   StepInterface, proto→interface, examples→provisional, process→interface, schema navigation
    loader.py                            WS    M1   LoadedProcess, ResolvedStep, StepPackage, load_process
    hashing.py                           WS    M1   canonical_json, git_blob_id, tree_hash, proto_hash, step_hash, process_hash
    validate/__init__.py                 VAL   M1   validate(), validate_process(), ValidationReport
    validate/structure.py                VAL   M1   E201–E222 structural rules, branch normalisation, W201
    validate/reach.py                    VAL   M1   reachability E209
    validate/cycles.py                   VAL   M1   Tarjan SCC, max_traversals fill I201
    validate/bindings.py                 VAL   M1   E210–E212, E215, E219 (+ M3 type parts E401/E404–E406)
    validate/dataflow.py                 VAL   M1   exit lattice, guard atoms, fixpoint
    validate/exprcheck.py                VAL   M1   reference validity + exit-aware walk (E301–E307); M3 typing hooks
    validate/types.py                    VAL   M3   Type lattice, from_schema, assignable, operator/function typing
    validate/rules.py                    VAL   M1   W203–W207, E126 closure-wide rules
    fragments.py                         ENV   M1   merge_fragments → MergedEnv, normalize_requirement
    venvs.py                             ENV   M1   RuntimeSource, VenvGroup, venv_groups, ensure_local_venv, sync_interfaces
    plan.py                              ENV   M1   execution_plan(), plan_local()
    local.py                             ENV   M1   run_local() (06 name)
    testing.py                           ENV   M1   run_tests, run_replay_tests, tests_status, run_test_live_job (M3)
    envmanifest.py                       ENV   M2   assemble_env_manifest (M1: used by `wynd env check`, see §10)
    compile.py                           GIT   M1   compile_state() (06 name)
    git.py                               GIT   M1   closure, closure_head, dirty_paths, worktrees, branches, integrate (M3)
    jobs.py                              GIT   M1   JobKind, JobState, JobRecord, JobContext, JobOutcome, JobHandler, JobRunner, helpers
    artefacts.py                         BLD   M2   BuildInfo, ArtefactStore, LocalArtefactStore, open_artefact_store
    build/__init__.py                    BLD   M2   re-exports run_build_job, build_process
    build/job.py                         BLD   M2   run_build_job(ctx), prepare_build(), build_process()
    build/resolve.py                     BLD   M2   Resolver protocol, UvResolver, ResolutionError
    build/wheels.py                      BLD   M2   stage_step(), build_step_wheel()
    build/variant.py                     BLD   M2   choose_base() (alpine/musl fallback)
    build/dockerfile.py                  BLD   M2   render_dockerfile(lock) (process image)
    build/lockfile.py                    BLD   M2   make_process_lock(), write/read process.lock.yaml
    build/imagebuilder.py                BLD   M2   ImageBuilder protocol, BuildxImageBuilder, open_image_builder
    bake.py                              BLD   M2   run_bake_job(ctx), bake_process()
    base.py                              BLD   M2   base_image_ref, render_base_dockerfile, build_base, publish_base, ensure_base
    templates/base.Dockerfile            BLD   M2   single template, variant blocks selected in Python
    templates/bake_main.py               BLD   M2   zipapp bootstrap (stdlib only)
  tests/                                 (each test file owned by the owner of the module under test; §18)

packages/spec/src/wynd/spec/             (authored by this workstream, physically in spec because the
                                          in-image supervisor needs them; flag for the spec owner)
    plan.py                              ENV   M1   ExecutionPlan, PlanVenv, PlanStep, PlanProcess, PlanBinding,
                                                    ProcessLock, BaseChoiceRecord, LockedVenv, LockedStep, FragmentRecord
    envmanifest.py                       ENV   M1   EnvVar, EnvGroup, EnvManifest, EnvProblem, check_env
```

`packages/process/pyproject.toml`:

```toml
[project]
name = "wynd-process"
version = "0.1.0"
description = "Wynd process package: workspace loader, graph validation, venv planning, builder, jobs"
requires-python = ">=3.12"
license = "AGPL-3.0-or-later"
dependencies = ["wynd-spec", "wynd-runtime", "pyyaml>=6"]

[project.entry-points."wynd.image_builders"]
buildx = "wynd.process.build.imagebuilder:BuildxImageBuilder"

[project.entry-points."wynd.artefact_stores"]
local = "wynd.process.artefacts:LocalArtefactStore"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/wynd"]          # ships wynd/process/** incl. templates/ (PEP 420: no wynd/__init__.py)

[tool.uv.sources]
wynd-spec = { workspace = true }
wynd-runtime = { workspace = true }
```

External executables, each checked at the point of use by `_proc.require_tool(name)`, which raises a `ToolMissing` error with an install hint: `git` everywhere, `uv` for venvs/build/bake (the `WYND_UV` env var overrides the path), `docker` (buildx) only in `BuildxImageBuilder`.

No new Python dependencies. `pytest` is installed into **local step venvs** by `uv`; it is not a dependency of `wynd-process` (§7).

---
