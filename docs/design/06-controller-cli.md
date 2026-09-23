# 06 — `wynd.controller` + `wynd.cli`: API surface, jobs, releases, triggers, chats, CLI

This is a draft design for the controller library, dist `wynd-controller`, and the `wynd` CLI, dist `wynd-cli`. Both are AGPL-3.0-or-later.

The source of truth is `docs/SPEC.md`, and "§x.y" refers to a spec section. "02/04/05/07/08" refer to the parallel drafts in this scratchpad: runtime, process, compiler, web and M5/kube. I read those drafts while writing this one and aligned names with them where I could. Every remaining disagreement is listed in §13.

Contents

0. Summary
1. Spec clauses this area owns (coverage audit)
2. Package layout, modules, owners, milestones
3. Interfaces CONSUMED
4. Interfaces PROVIDED
5. Controller library API (service by service, with algorithms)
6. JobRunner implementations, job harness, Kubernetes seam
7. Chats
8. `wynd serve-api`: the HTTP API
9. CLI
10. Record formats and on-disk layout
11. Test plan
12. Interpretations
13. Requests to other areas and reconciliation items

---

## 0. Summary

**Library and clients.**
- `wynd.controller` is a library. `Controller.open(root)` picks every backend by env var or entry point:
  - storage, through `stores_from_env` (02)
  - build artefacts (04)
  - the job runner
  - the serving backend
  - the trigger backend
- It exposes these services: `ctl.processes`, `ctl.design`, `ctl.jobs`, `ctl.runs`, `ctl.env`, `ctl.serve`, `ctl.uploads`, `ctl.registries`, `ctl.releases`, `ctl.chats`.
- The CLI calls the library in-process. `wynd serve-api` serves the same object through FastAPI.
- Neither client imports Docker, the executor or the compiler.

**HTTP contract.** Web 07 §12 is adopted as the normative API: routes, DTOs, save semantics, SSE and error codes. The controller adds routes for CLI parity, webhooks, OAuth and registries (§8).

**Jobs.**
- The five job kinds are compile, test_live, build, bake and optimise. All of them go through the `JobRunner` interface, which lives in `wynd.process.jobs`.
- The controller ships two runners: `inprocess` (M1) and `subprocess` (M2, the default).
- Both runners use one harness:
  1. check out a detached `git worktree` under `.wynd/jobs/<id>/`;
  2. call the handler `(ctx, inputs)`, which belongs to the compiler or process area;
  3. fold the handler's result into the job record.
- Handlers publish their result branches with `ctx.push_branch`.
- Answering compile questions uses the compiler's `apply_answers`. When no questions are pending, **the same job is re-queued** with `resume_request(session)`.
- `wynd.kube` plugs in through the `wynd.job_runners`, `wynd.serving_backends` and `wynd.trigger_backends` entry points.

**Integration.**
- `ctl.jobs.integrate(job_id)` fast-forwards, auto-rebases, or leaves a PR branch. It is idempotent.
- The decision uses the union of the reference closure at the base, target and branch commits.
- Rebases run in a scratch worktree, and test results are re-keyed afterwards. The user's checkout is only ever fast-forwarded.

**Status** is derived at the process HEAD, meaning the last commit touching the process's closure. It is computed from `compile_state`, recorded tests, build artefacts and releases, with `behind` counts. It is never stored.

**Design edits.**
- All design edits go through one path, `ctl.design.save` (07 §12.3). It gives scope checks, revision checks, atomic writes, YAML generated from JSON docs, and a design lock for the duration of a chat turn.
- Commits happen at boundaries and use `git commit --only -- <that process's design paths>`, so a commit never spans two processes.

**Releases.**
- A release is a document holding the image, a trigger (manual, schedule, or webhook with `secret_env`) and env bindings.
- It runs on a `ServingBackend` (default `docker`) and is fired by a `TriggerBackend`. The default trigger backend is an in-controller scheduler: a stdlib 5-field cron parser with time zones and at-most-once firing.
- Webhooks arrive at `/hooks/releases/{id}`.
- The controller mirrors the traces of runs it submits into its own `TraceSink`.

**Chats.**
- A chat is a document that is not tied to any process.
- Each turn is one `claude-code` `AgentProvider.run`, with the controller's tools served as in-process MCP tools.
- Read tools work on any process. The write tools (`edit_design`, `edit_proto`, `start_compile`, `start_build`) are closures bound to the message's `acting_on` process.
- A turn that edited something ends with a commit.
- Items and deltas stream over a per-chat SSE log.
- The Compile and Build buttons create chats bound to their job, and the compile session is projected into the web's `CompileSession` shape.

**CLI.**
- It has every command in §10, plus `status`, `search`, `jobs …`, `release …`, `bake` and `optimise` (optimise is owned by 08).
- Exit codes: 0 ok, 1 failed, 2 usage, 3 precondition, 4 awaiting input, 5 left for review, 130 interrupted.
- Every command that returns data accepts `--json`.

---

## 1. Spec clauses this area owns

(C) marks a clause this area only consumes; another area implements it.

| § | Clause | Where |
|---|---|---|
| 3.8 | MCP servers registered once, globally: `wynd mcp add … --url … --auth-env …`, or "the equivalent OAuth flow in the web UI" | `registries.py`, `oauth.py`, CLI `mcp`, `/api/registries/mcp*`, `/api/oauth/callback` |
| 3.9 | Tier→model mapping in the user registry; `wynd provider add\|list\|remove` | `registries.py`, CLI `provider`, `GET/POST/DELETE /api/providers` |
| 3.9 | claude-code AgentProvider (C) | chat engine (§7) |
| 4 | `wynd env check` gate; `.env` secrets locally; warm pools ("second run should not touch Docker") | `envcheck.py`, `envfile.py`, `serving.py` (HTTP-only health probe) |
| 4.1 | Run API contract (C); the controller's trigger scheduler | `runs/runapi.py`, `runs/mirror.py`, `releases/scheduler.py` |
| 5 | The controller is a library: the CLI runs it in-process, `serve-api` runs it as a service; clients never import Docker or the executor | whole package; `api/`; `test_cli_imports.py` |
| 5.1 | `wynd.yaml`, roots, `.wynd/` ignored, ids are relative paths, build outputs never tracked, subdirectory workspaces | `workspace.py`, `git.py` (prefix), §10.8 |
| 6.5 | Compile is a job whose output is a branch; integrate by ff / auto-rebase / PR; build refuses a dirty tree; `test --live` is a job with a branch output; tests pass before build (C, handler) | `jobs/service.py`, `jobs/integrate.py`, CLI `compile/test/build` |
| 6.6 | JobRunner in-process (M1) and subprocess (M2), K8s seam; jobs run on a git checkout at a ref (worktree under `.wynd/jobs/<id>/`); submit and poll; serialisable session, `awaiting_input`, answer = resubmit; chat is a view | `jobs/*`, `compile_view.py` |
| 7.1 | Storage behind interfaces (C); `RunRegistry` holds jobs and sessions | `jobs/records.py`, `store.py` |
| 9 | CLI drives the session non-interactively and fails on unanswered questions; the web chat is a view | CLI `compile`, `compile_view.py` |
| 10 | Every command | `wynd.cli` |
| 11 | Selector, decoupled chats + "acting on", write tools only on the open process, auto-commit, Compile button + session chat + integration, Build button, status derived per commit, search with flags, Release + triggers, run panel | `api/*`, `chat/*`, `design.py`, `status.py`, `search.py`, `releases/*`, `runs/*`, `uploads.py` |
| 12 | M1–M5 controller parts; post-M5 K8s runner and trigger backends (seam) | §2 milestone column, §6.7 |
| 13 | Decisions: jobs vs runs, controller library, derived status, `Release`, design auto-commits, no environment branches | throughout |
| 15 | No privileged code paths (backends by env var or entry point); usage observable (job duration/CPU, chat usage); cluster deployability | §5.1, §6.7, §7.7, `test_no_env_branches.py` |
| 17 | Tests with every package | §11 |

---

## 2. Package layout, modules, owners, milestones

Each module has exactly one owner:

| Owner | Covers |
|---|---|
| CORE | wiring, git, workspace, processes, status, search, design, doc store |
| JOBS | runners, harness, integration, base |
| RUNS | runs, traces, env, serving, docker, uploads |
| REL | registries, OAuth, releases, cron, scheduler, backends |
| CHAT | chats, compile view |
| API | FastAPI app |
| CLI | `wynd.cli` |
| A8-opt | optimise modules, from draft 08 |

### 2.1 `packages/controller` (dist `wynd-controller`, AGPL-3.0-or-later)

```
packages/controller/
  pyproject.toml   LICENSE (AGPL-3.0-or-later)
  src/wynd/controller/                     # NO src/wynd/__init__.py (PEP 420)
    __init__.py            CORE  M1  Controller, errors, __version__
    controller.py          CORE  M1  Controller, ControllerContext, open(), meta()
    errors.py              CORE  M1  WyndError hierarchy (codes per 07 §12.0) + HTTP/exit maps
    models.py              CORE  M1  pydantic models: 07 §12.2 mirrors + §10.4 controller models
    git.py                 CORE  M1  Git, GitLock (thin over wynd.process.git primitives, §13 R3)
    store.py               CORE  M4  DocStore protocol + FileDocStore (§10.2)
    workspace.py           CORE  M1  init_workspace, new_process_files (templates)
    processes.py           CORE  M1  ProcessService
    status.py              CORE  M1  derive_status (M1 design/compiled; M2 built; M4 released)
    search.py              CORE  M4  search
    design.py              CORE  M4  DesignService (get/save/commit/scope/lock)
    envfile.py             RUNS  M1  parse_env_file, load_into_environ
    envcheck.py            RUNS  M1  EnvService
    uploads.py             RUNS  M4  UploadService
    docker.py              RUNS  M2  docker CLI helper (subprocess only)
    serving.py             RUNS  M2  ServeService (warm containers: serve/acquire/stop/list)
    runs/__init__.py       RUNS  M1
    runs/service.py        RUNS  M1  RunService
    runs/runapi.py         RUNS  M2  RunApiClient (httpx)
    runs/mirror.py         RUNS  M2  mirror_run
    runs/tree.py           RUNS  M1  render_tree (over runtime build_tree)
    jobs/__init__.py       JOBS  M1
    jobs/records.py        JOBS  M1  JobRecord <-> RunRecord mapping, to_dto
    jobs/handlers.py       JOBS  M1  DEFAULT_HANDLERS, resolve_handler
    jobs/checkout.py       JOBS  M1  CheckoutBackend, WorktreeCheckout, CloneCheckout (post-M5 use)
    jobs/harness.py        JOBS  M1  execute_job, JobCancelled
    jobs/inprocess.py      JOBS  M1  InProcessJobRunner
    jobs/subproc.py        JOBS  M2  SubprocessJobRunner
    jobs/worker.py         JOBS  M2  python -m wynd.controller.jobs.worker
    jobs/runners.py        JOBS  M1  open_job_runner, pid_alive
    jobs/service.py        JOBS  M2  JobService
    jobs/integrate.py      JOBS  M3  integrate
    base.py                JOBS  M2  build_base / publish_base wrappers
    registries.py          REL   M1  RegistryService (image registries M2)
    oauth.py               REL   M4  MCP OAuth
    releases/__init__.py   REL   M4
    releases/store.py      REL   M4  release + fire documents
    releases/cron.py       REL   M4  CronExpr
    releases/service.py    REL   M4  ReleaseService
    releases/scheduler.py  REL   M4  Scheduler
    releases/serving.py    REL   M4  ServingBackend protocol, DockerServing, open_serving_backend
    releases/triggers.py   REL   M4  TriggerBackend protocol, SchedulerTriggers, open_trigger_backend
    chat/__init__.py       CHAT  M4
    chat/store.py          CHAT  M4  chat documents
    chat/events.py         CHAT  M4  per-chat ring buffer (item/delta/turn/reset)
    chat/prompt.py         CHAT  M4  SYSTEM_INSTRUCTION, CHAT_REPLY_SCHEMA
    chat/tools.py          CHAT  M4  build_tools
    chat/engine.py         CHAT  M4  ChatService
    compile_view.py        CHAT  M3  session_dto, answer_text
    optimise.py            A8-opt M5 optimise_report, submit_optimise, run_optimise_job (08 P2)
    api/__init__.py        API   M4  create_app, serve
    api/app.py             API   M4  factory: auth, CORS, errors, lifespan
    api/sse.py             API   M4  poll_stream, frames
    api/routes_meta.py     API   M4  health, meta, steps, providers, expressions, uploads
    api/routes_processes.py API  M4  processes, design, builds, interface, (+) status/validate/test/history/files/env
    api/routes_jobs.py     API   M4  compile/build/test-live/bake/optimise submit, jobs/*
    api/routes_runs.py     API   M4  runs/*
    api/routes_chats.py    API   M4  chats/*
    api/routes_releases.py API   M4  releases/*, /hooks/releases/{id}
    api/routes_registries.py API M4  registries/*, oauth
    api/static.py          API   M4  web_dist_dir, mount_web (07 C-CTL-STATIC)
    web_dist/              (build output of packages/web; git-ignored; shipped as wheel artifacts)
  tests/                   (§11)
```

`pyproject.toml`. JOBS owns the entry-point tables; CORE owns the rest.

```toml
[project]
name = "wynd-controller"
version = "0.1.0"
description = "Wynd controller: API surface, job runners, releases, triggers, chats"
requires-python = ">=3.12"
license = "AGPL-3.0-or-later"
dependencies = [
  "wynd-spec", "wynd-runtime", "wynd-process", "wynd-compiler",
  "fastapi>=0.115", "uvicorn>=0.30", "httpx>=0.27", "pyyaml>=6",
  "claude-agent-sdk>=0.2,<0.3",   # §13 R1: default provider for chat (and compile) must be importable here
]

[project.entry-points."wynd.job_runners"]
inprocess  = "wynd.controller.jobs.inprocess:InProcessJobRunner"
subprocess = "wynd.controller.jobs.subproc:SubprocessJobRunner"

[project.entry-points."wynd.serving_backends"]
docker = "wynd.controller.releases.serving:DockerServing"

[project.entry-points."wynd.trigger_backends"]
scheduler = "wynd.controller.releases.triggers:SchedulerTriggers"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/wynd"]
artifacts = ["src/wynd/controller/web_dist/**"]

[tool.uv.sources]
wynd-spec = { workspace = true }
wynd-runtime = { workspace = true }
wynd-process = { workspace = true }
wynd-compiler = { workspace = true }
```

### 2.2 `packages/cli` (dist `wynd-cli`, AGPL-3.0-or-later). Owner: CLI unless noted

```
packages/cli/
  pyproject.toml   deps: wynd-controller, typer>=0.12, pyyaml>=6 ; [project.scripts] wynd = "wynd.cli.main:main"
  LICENSE
  src/wynd/cli/
    __init__.py
    main.py                 M1  typer app, sub-apps, @command, main()
    context.py              M1  CliState, get_controller (lazy)
    output.py               M1  echo/err/print_json/table/fmt_*
    inputs.py               M1  parse_inputs (path absolutising), parse_answers
    commands/workspace.py   M1  init, new, validate, status, search (M4)
    commands/test.py        M1  test (replay; --live M3)
    commands/run.py         M1  run (--local M1, --image M2), trace
    commands/env.py         M1  env check
    commands/registries.py  M1  mcp, provider (M1), registry (M2)
    commands/build.py       M2  build, bake, base build|publish
    commands/serve.py       M2  serve (M2), serve-api (M4)
    commands/jobs.py        M2  jobs list|show|logs|answer|cancel|integrate
    commands/compile.py     M3  compile
    commands/release.py     M4  release create|list|show|run|enable|disable|delete|fires
    commands/optimise.py    M5  optimise   (owner A8-opt, per 08 §3.8; registered by main.py)
  tests/
```
The CLI may import only:
- `wynd.controller`, including `wynd.controller.api.serve`, `wynd.controller.base` and `wynd.controller.docker.logs_follow`;
- `typer`;
- `yaml`.

It must never import `wynd.process`, `wynd.runtime` or `wynd.compiler`, and it must not shell out to `docker` itself. `test_cli_imports.py` enforces this.

---

## 3. Interfaces CONSUMED

Names follow the drafts wherever they already define them. Each entry lists what the controller relies on. The §13 row named in each heading gives the reconciliation.

### 3.1 From `wynd.spec` (§13 R16)
- `PROCESS_ID_PATTERN`, including the reserved final segments from R8.
- `ProcessSpec`, `ProtoStep`, `StepLock`.
- `EnvManifest`, where `vars` is `[EnvVar{name, description, secret, required, default?, used_by}]`.
- `wynd.spec.yaml.load/dump`, which must round-trip without loss (07 C-SPEC-1).
- `fields_to_json_schema` (C-SPEC-2).
- The `Meta` vocabularies (C-SPEC-5/6).

### 3.2 From `wynd.runtime` (02)
- **Storage.**
  - `stores_from_env(env, *, data_dir) -> Stores(workspaces, traces, runs, registry)` and `registry_from_env(env)`.
  - `TraceSink.write(event)` and `.read(run_id)`.
  - `RunRegistry.create/update/get/list/put_test_result/get_test_result`, with `RunRecord` and `TestResult` as in 02 §9.2. `RunKind` gains `bake` and `optimise` (R11).
  - `Registry.list/get/put/remove` over the sections `mcp`, `providers` and `registries`, plus `put_secret`/`secrets()` for OAuth (R6).
- **Ids and traces.**
  - `new_id(prefix)` and `valid_id`.
  - `build_tree(events) -> TraceTree`, and the trace event types in 02 §8.2.
- **Providers.**
  - `load_provider(name)`, `available_providers()`, `DEFAULT_PROVIDER` and `check_provider(name)` (R5).
  - `AgentRequest` with three additions: `builtin_tools`, `on_event` and `cancel` (R1).
  - `AgentResponse(structured_output, usage, transcript)`.
  - `wynd.runtime.tools.tool`, usable as a decorator on closures.
- **Other.** `__version__`, the supervisor run API and the image labels (R14).

### 3.3 From `wynd.process` (04; §13 R3, R7, R9, R15)
- **Workspace and loading.** `find_workspace_root`, and `load_workspace(root, tree=None)` with `CommitTree(rev)`. From `Workspace`: `process_ids()`, `process_dir()` and `process_root_of()`. Also `LoadedProcess` and `ResolvedStep`, whose names 04 adopted from 06.
- **Validation.** `validate_process(ws, pid, *, stats=None) -> ValidationReport`, where each `Issue` is `{severity, code, message, file, loc, span}` (07 C-PROC-2).
- **Closure and compile state.** `reference_closure(ws, pid)`, which includes `wynd.yaml`, and `compile_state(ws, pid)`, which includes `process_hash`.
- **Descriptions and expressions.** `describe_steps(ws, pid) -> dict[str, StepInfo]`, `step_catalog(ws)`, `process_interface(ws, pid)`, and `check_expr`/`expr_scope` (C-PROC-1/3).
- **Tests.** `run_tests(root, pid, *, mode, commit, runs)` and `tests_status(runs, ws, pid, commit)`. Whether process or compiler owns these is open (R7).
- **Local runs.** `run_local(ws, pid, inputs, *, run_id, env, stores, on_event)`.
- **Env and artefacts.**
  - `assemble_env_manifest(ws, pid, registry)`.
  - `open_artefact_store(state_dir)`, whose `get_build`/`list_builds` return `BuildInfo{process, commit, image, image_digest, manifest, created_at, dir, job_id}`.
- **Jobs.** `wynd.process.jobs`: `JobKind`, `JobState`, `JobRecord`, `JobContext`, `JobHandler` and `JobRunner`, with the shapes in §6.1 (R9, R12).
- **Handlers and base images.** `wynd.process.build:run_build_job`, `wynd.process.bake:run_bake_job`, `build_base` and `publish_base`.
- **Git.** The primitives in `wynd.process.git`.

### 3.4 From `wynd.compiler` (05)
- `wynd.compiler.job:run_compile_job(ctx, inputs) -> JobResult` and `run_live_test_job(ctx, inputs)`.
- `apply_answers(session, answers, *, by)`.
- `resume_request(session) -> (kind, ref, inputs)`.
- `SessionData` JSON (05 §6.5). Its `report` must give each node a decision, tests, attempts and `skipped_reason` (R13).
- `CompileOptions{accept_defaults, max_revisions}`.

### 3.5 From `web` (07)
- The normative HTTP contract (§12).
- The bundle in `src/wynd/controller/web_dist/` (P-BUNDLE).
- The fixtures in `packages/web/src/api/fixtures/` (P-FIXTURES).

### 3.6 From M5/kube (08)
- `wynd.controller.optimise` (owner A8-opt).
- `wynd.process.optimise.collect_stats`, used by `validate`.
- The entry points `wynd.job_runners: kube`, `wynd.serving_backends: kube` and `wynd.trigger_backends: kube`, using the factory convention in §5.14.
- `wynd-kube fire`, which calls `POST /api/releases/{id}/trigger` with body `{"source":"schedule"}`.

---

## 4. Interfaces PROVIDED

| To | Interface | § |
|---|---|---|
| CLI | `Controller` and its services, `errors`, `models`, `render_tree`, `base`, `api.serve`, `docker.logs_follow` | §5, §8 |
| web | 07 §12 implemented exactly, plus the (+) routes; `/openapi.json` | §8 |
| process / compiler / 08 handlers | `JobContext` filled in by the harness (§6.1); handler signature `(ctx, inputs)`; result normalisation; `requeue` semantics; `wynd.controller.jobs.worker` | §6 |
| kube (08) | Factory contracts for runners, serving and triggers; `CheckoutBackend`/`CloneCheckout`; `execute_job`; `WYND_GIT_REMOTE` push/fetch; `/api/releases/{id}/trigger` | §5.14, §6.7 |
| 08 optimise | `ctl.jobs.submit("optimise", pid, inputs)`, `ctl.jobs.wait`, `ctl.jobs.integrate`, and the job kind `optimise` in the handler table | §5.8, §6.2 |
| everyone | Controller env vars: `WYND_WORKSPACE`, `WYND_JOB_RUNNER` (`subprocess`), `WYND_SERVING_BACKEND` (`docker`), `WYND_TRIGGER_BACKEND` (`scheduler`), `WYND_GIT_REMOTE`, `WYND_API_TOKEN`, `WYND_CORS_ORIGINS`, `WYND_WEB_DIST`, `WYND_CHAT_PROVIDER` (`claude-code`), `WYND_CHAT_TIER` (`standard`) | §5.1 |

---

## 5. Controller library API

> **Alignment note.** While I was drafting, the parallel drafts 02 (runtime), 04 (process), 05 (compiler) and 07 (web) appeared in the scratchpad. This section is written against their names wherever they already exist:
> - runtime `stores_from_env`, `RunRecord`, `TestResult`, `Registry`, `new_id`, `build_tree`
> - compiler `run_compile_job(ctx, inputs)`, `apply_answers`, `resume_request`
> - **web §12 as the normative HTTP contract**
>
> Where the drafts disagree with each other, §13 lists the conflict and my recommendation.

Every public method is synchronous. Long work either blocks, which is what the CLI wants, or has a `start…` variant that runs a background thread and returns an id, which is what the API wants. Model names in `models.py` mirror web 07 §12.2 one-to-one: `Meta`, `ProcessSummary`, `ProcessStatus`, `DesignDoc`, `SaveRequest`, `SaveResult`, `StepInfo`, `Interface`, `ProcessInterface`, `ValidationReport`, `Issue`, `ExprCheck*`, `StepCatalogEntry`, `ProviderEntry`, `Job`, `CompileSession`, `CompileStep`, `Question`, `AnswerRequest`, `BuildResult`, `Integration`, `ChatSummary`, `ChatItem`, `ChatSnapshot`, `SendMessageRequest`, `Run`, `RunTarget`, `CreateRunRequest`, `Build`, `Release`, `Trigger`, `EnvBinding`, `CreateReleaseRequest`, `EnvCheck`. Formats that only the controller and CLI use are defined in §10.

### 5.1 Construction and wiring (`controller.py`, CORE)

```python
@dataclass(frozen=True)
class ControllerContext:
    root: Path                       # workspace root (dir containing wynd.yaml)
    state_dir: Path                  # root / ".wynd"  (== WYND_DATA_DIR for local backends)
    subdir: str                      # workspace path inside the git repo: "" or "examples/invoices"
    git: Git                         # Git(root, lock=GitLock(state_dir / "locks" / "git.lock"))
    stores: Stores                   # runtime: workspaces, traces, runs (RunRegistry), registry (user Registry)
    artefacts: ArtefactStore         # process: .wynd/build/<process>/<commit>/ or env-selected
    docs: DocStore                   # releases, fires, chats: FileDocStore(state_dir / "controller") (§10.2, §13 R2)
    runner: JobRunner
    serving: ServingBackend           # where releases run (docker | kube)
    triggers: TriggerBackend          # what fires schedules (scheduler | kube)
    load_provider: Callable[[str], object]        # wynd.runtime.providers.load_provider
    clock: Callable[[], datetime]                 # tz-aware UTC

    def workspace(self, tree: "Tree | None" = None) -> Workspace:
        return load_workspace(self.root, tree)    # reloaded on every call (autosave changes files)


class Controller:
    def __init__(self, ctx: ControllerContext) -> None:
        self.ctx = ctx
        self.processes = ProcessService(ctx, self)   # CORE
        self.design = DesignService(ctx, self)       # CORE
        self.jobs = JobService(ctx, self)            # JOBS
        self.runs = RunService(ctx, self)            # RUNS
        self.env = EnvService(ctx, self)             # RUNS
        self.serve = ServeService(ctx, self)         # RUNS (warm containers for `wynd serve` / image runs)
        self.uploads = UploadService(ctx, self)      # RUNS
        self.registries = RegistryService(ctx, self) # REL
        self.releases = ReleaseService(ctx, self)    # REL
        self.chats = ChatService(ctx, self)          # CHAT

    @classmethod
    def open(cls, root: Path | None = None, *, environ: Mapping[str, str] | None = None,
             load_dotenv: bool = True) -> "Controller": ...
    def meta(self) -> Meta: ...
    def close(self) -> None: ...      # stops threads started by services (API lifespan calls it)
```

`Controller.open`:
1. Set `env = environ or os.environ`.
2. Find the root: `root = find_workspace_root(root or Path(env.get("WYND_WORKSPACE") or Path.cwd()))`. If it is `None`, raise `NotAWorkspace`.
3. Set `state_dir = root/".wynd"` and create it with the subdirectories `locks/`, `jobs/`, `serve/`, `uploads/`, `chats/`.
4. If `load_dotenv`, call `envfile.load_into_environ(root)`. It sets only keys that are not already set, from `root/.env`. The in-process runner and the claude-code provider read `os.environ`. Tests pass `load_dotenv=False`.
5. Open storage with `stores = stores_from_env(env, data_dir=state_dir)`. This is runtime 02 §9.4. Backends are chosen by `WYND_RUN_REGISTRY`, `WYND_TRACE_SINK`, `WYND_WORKSPACE_STORE` and `WYND_REGISTRY`.
6. Open build artefacts with `artefacts = open_artefact_store(state_dir)`. This is process 04; the backend is chosen by `WYND_ARTEFACT_STORE`.
7. Open the job runner with `runner = open_job_runner(env.get("WYND_JOB_RUNNER", "subprocess"), env=env, workspace_root=root, state_dir=state_dir, stores=stores, handlers=DEFAULT_HANDLERS)`.
8. Open the release backends with the same keyword arguments, `(env=env, workspace_root=root, state_dir=state_dir, stores=stores)`:
   - `serving = open_serving_backend(env.get("WYND_SERVING_BACKEND", "docker"), **kw)`
   - `triggers = open_trigger_backend(env.get("WYND_TRIGGER_BACKEND", "scheduler"), **kw)`
9. Record `remote = env.get("WYND_GIT_REMOTE")`. When it is set, job submission pushes the target branch to that remote before submitting, and integration fetches the result branch from it and pushes the target back after advancing it (08 C15). This is configuration data, not an environment branch.

`meta()` returns web `Meta`:
- `version`: `wynd.controller.__version__`.
- `workspace`: `root`, `branch`, `head`, `process_roots`, `step_roots`.
- Constants from spec (C-SPEC-2/5/6): `proto_types`, `bases`, `latency`, `edge_kinds`, `expr_functions`, `limit_fields`, `default_max_traversals`.
- `default_provider`: `wynd.runtime.providers.DEFAULT_PROVIDER` (`"claude-code"`).
- `llm`: `{provider: WYND_CHAT_PROVIDER, ready, detail}`, where `ready, detail = check_provider(name)` (§13 R5). If the runtime does not expose `check_provider`, `ready` is `true` and `detail` is `"not checked"`.

### 5.2 Errors (`errors.py`, CORE)

```python
class WyndError(Exception):
    code: ClassVar[str] = "error"; http: ClassVar[int] = 500; exit: ClassVar[int] = 1
    def __init__(self, message: str, *, details: Any = None, hint: str | None = None): ...

class Invalid(WyndError):            code = "invalid";            http = 422; exit = 2
class ValidationFailed(WyndError):   code = "validation_failed";  http = 422; exit = 1   # details = ValidationReport
class Unauthorized(WyndError):       code = "unauthorized";       http = 401; exit = 3
class OutOfScope(WyndError):         code = "out_of_scope";       http = 403; exit = 3
class NotFound(WyndError):           code = "not_found";          http = 404; exit = 3
class NotAWorkspace(NotFound):       code = "not_a_workspace"
class Conflict(WyndError):           code = "conflict";           http = 409; exit = 3
class DirtyTree(Conflict):           code = "dirty_tree"          # details = {"paths": [...]}
class RevisionConflict(Conflict):    code = "revision_conflict"   # details = {"path", "current_revision"}
class NotBuilt(Conflict):            code = "not_built"
class EnvMissing(Conflict):          code = "env_missing"         # details = {"missing": [...]}
class EnvUnbound(Conflict):          code = "env_unbound"         # details = {"unbound": [...]}
class JobState(Conflict):            code = "job_state"
class DetachedHead(Conflict):        code = "detached_head"
class TurnInProgress(Conflict):      code = "turn_in_progress"
class VersionMismatch(Conflict):     code = "version_mismatch"
class DesignLocked(WyndError):       code = "design_locked";      http = 423; exit = 3  # details = {"chat_id", "turn_id"}
class Unavailable(WyndError):        code = "unavailable";        http = 503; exit = 3  # docker / provider / run API unreachable
```
The HTTP body follows web 07 §12.0: `{"error": {"code", "message", "details"}}`. The controller also adds `"hint"` when it has one. The CLI prints `error: <message>` and, when present, `hint: <hint>` to stderr, then exits with `exc.exit`.

### 5.3 Git (`git.py`, CORE)

The git **primitives** needed by process, compiler and controller should live in exactly one place. Process 04 proposes `wynd.process.git` for closure, closure_head, dirty_paths, worktrees and branches. If the synthesizer keeps that, `wynd.controller.git` becomes a thin wrapper: it adds `GitLock` and the three main-worktree mutations the controller alone performs (`commit_paths`, `merge_ff_only`, `update_ref`). The shape below is what the controller calls either way.

- Every call is `subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True)`. Nothing goes through a shell.
- The workspace may be a subdirectory of the repo (the `examples/invoices` case). So pathspecs are **workspace-relative**, commands run with `-C <workspace root>`, and diffs use `--relative`.
- Commits made when the repo has no configured identity run with `-c user.name=Wynd -c user.email=wynd@localhost`. This is a data condition, not an environment check.
- Every mutation of the main worktree, and every worktree add/remove, holds `git.lock`. The lock is a `threading.RLock` plus `fcntl.flock(LOCK_EX)` on `.wynd/locks/git.lock`, so the CLI and `serve-api` serialise even when they are separate processes.

```python
class GitLock:
    def __init__(self, path: Path) -> None: ...
    def __enter__(self) -> "GitLock": ...
    def __exit__(self, *exc) -> None: ...

class Git:
    def __init__(self, root: Path, lock: GitLock) -> None: ...
    lock: GitLock
    def run(self, *args: str, check: bool = True, env: Mapping[str, str] | None = None) -> str: ...
    def toplevel(self) -> Path: ...                          # rev-parse --show-toplevel
    def prefix(self) -> str: ...                             # rev-parse --show-prefix  ("" | "examples/invoices/")
    def head(self) -> str | None: ...
    def current_branch(self) -> str | None: ...              # None when detached
    def rev_parse(self, rev: str) -> str: ...
    def branch_exists(self, name: str) -> bool: ...
    def last_commit_touching(self, paths: Sequence[str], rev: str = "HEAD") -> str | None: ...
    def count_touching(self, a: str, b: str, paths: Sequence[str]) -> int: ...   # rev-list --count a..b -- paths
    def dirty_paths(self, paths: Sequence[str] | None = None) -> list[str]: ...  # status --porcelain=v1 -z -uall -- paths
    def commit_paths(self, paths: Sequence[str], message: str) -> str | None: ...
        # add -A -- paths ; commit --only -m message -- paths ; None if no staged diff for those paths
    def log(self, paths: Sequence[str], limit: int) -> list[CommitInfo]: ...
    def diff_names(self, a: str, b: str) -> list[str]: ...   # diff --name-only --relative a b
    def merge_base(self, a: str, b: str) -> str: ...
    def is_ancestor(self, a: str, b: str) -> bool: ...
    def tree_entry(self, rev: str, path: str) -> str | None: ...
    def worktree_add_detached(self, path: Path, rev: str) -> None: ...
    def worktree_remove(self, path: Path) -> None: ...       # worktree remove --force ; worktree prune
    def merge_ff_only(self, rev: str) -> None: ...           # main worktree; GitError carries overwritten paths
    def update_ref(self, ref: str, new: str, old: str | None) -> None: ...
    def delete_branch(self, name: str) -> None: ...
    def has_identity(self) -> bool: ...
```

### 5.4 Processes: listing, reading, catalog, interface, tests (`processes.py`, `workspace.py`, CORE)

```python
def init_workspace(path: Path, *, commit: bool = True) -> InitResult: ...     # module-level; no Controller needed
def new_process_files(pid: str, *, goal: str | None) -> dict[str, str]: ...   # relpath -> content

class ProcessService:
    def list(self, q: str = "", flags: Sequence[StatusFlag] = ()) -> list[ProcessSummary]: ...   # search (§5.6)
    def get(self, pid: str) -> ProcessSummary: ...
    def new(self, pid: str, *, goal: str | None = None, root: str | None = None, commit: bool = True) -> ProcessSummary: ...
    def status(self, pid: str) -> ProcessStatus: ...                               # §5.5
    def validate(self, pid: str) -> ValidationReport: ...
    def validate_all(self) -> dict[str, ValidationReport]: ...
    def steps(self, pid: str) -> dict[str, StepInfo]: ...
    def step_catalog(self) -> list[StepCatalogEntry]: ...                          # all step-root steps
    def interface(self, pid: str, commit: str | None = None) -> ProcessInterface: ...
    def builds(self, pid: str) -> list[Build]: ...
    def read_file(self, pid: str, path: str) -> FileContent: ...                  # any file in the closure (chat, CLI)
    def step_source(self, pid: str, step: str) -> StepSource: ...                  # compiled package files (chat)
    def history(self, pid: str, limit: int = 50) -> list[CommitInfo]: ...
    def test(self, pid: str, *, log: Callable[[str], None] | None = None) -> TestReport: ...
    def check_expr(self, req: ExprCheckRequest) -> ExprCheck: ...
```

**`init_workspace(path)`**
1. If `path/wynd.yaml` already exists, raise `Conflict`.
2. Write the scaffold:
   - `wynd.yaml`, containing `process_roots: [processes]` and `step_roots: {shared: shared/steps}`.
   - `processes/.gitkeep` and `shared/steps/.gitkeep`.
   - `.gitignore`: append whichever of `.wynd/`, `.env`, `processes/**/build/`, `__pycache__/`, `.pytest_cache/` are missing.
   - `.gitattributes`: append `**/cassettes/** filter=lfs diff=lfs merge=lfs -text` if it is missing.
3. If `path` is not inside a git repo, run `git init -b main`.
4. If `commit` is set, run `commit_paths(created, "chore(wynd): init workspace")`.

**`new(pid)`**
1. `pid` must match `PROCESS_ID_PATTERN`, otherwise `Invalid`.
2. `root` must be one of the configured process roots. The default is the first one.
3. The directory `root/pid` must not exist, and no ancestor or descendant of it may contain a `process.yaml`. Otherwise raise `Conflict`.
4. Write the templates below and run `validate(pid)`. A test asserts the templates validate.
5. If `commit` is set, run `commit_paths([dir], f"design({pid}): new process")`. The message format is the one web 07 §12.1 row 4 expects.

Templates (`name` is the last id segment, I-18):

```yaml
# processes/<pid>/process.yaml
kind: process
name: <last segment>
goal: <goal or "TODO: describe the outcome of this process">
provider: claude-code
env:
  base: debian-slim-python
entry: first
inputs:
  text: string
outputs:
  done:
    result: string
steps:
  first: { use: ./steps/first }
edges:
  - from: first.done
    to: $exit.done
    with: { result: steps.first.outputs.result }
```
```yaml
# processes/<pid>/proto/first.yaml   (local proto path convention per compiler C-P1: <process>/proto/<s>.yaml)
kind: proto_step
name: first
instruction: |
  TODO: describe what this step does.
inputs:
  text: string
outputs:
  result: string
examples: []
env:
  deps: []
```

**`steps(pid)` / `step_catalog()`** delegate to the process area's step description (C-PROC-1, consumed as `describe_steps(ws, pid) -> dict[str, StepInfo]` and `step_catalog(ws) -> list[StepCatalogEntry]`). The controller adds nothing to them.

**`interface(pid, commit)`**
- With no `commit`, it loads the working tree.
- With a `commit`, it loads `load_workspace(root, CommitTree(commit))`.
- It returns `process_interface(ws, pid)` (process / C-SPEC-2).

**`builds(pid)`**
- For each `b` in `artefacts.list_builds(pid)` (newest first), it returns `Build{process_id, commit, short, image, built_at, job_id, at_head: b.commit == head, behind: git.count_touching(b.commit, head, closure), env: b.manifest.vars}`.
- `head` is the process HEAD (§5.5) and `closure` is the process's reference closure.

**`read_file(pid, path)`**
- Normalise `path` with `PurePosixPath`.
- Reject `..`, absolute paths, and anything outside the reference closure with `NotFound`. Git-ignored files and `.wynd/` are excluded.
- Files over 256 KiB are truncated and flagged `truncated=true`.
- `revision` is `"sha256:<hex of file bytes>"`, the same format as web 07 §12.0.

**`test(pid)`** runs replay tests directly; it is not a job.
1. `dirty = git.dirty_paths(closure)`.
2. `commit = None if dirty else closure_head`.
3. `report = run_tests(root, pid, mode="replay", commit=commit, runs=stores.runs)`. This is compiler P5, or process `run_replay_tests`, whichever the synthesizer keeps (§13 R7).
4. Set `report.recorded = commit is not None` and return it. When the closure is dirty, results are not recorded, because they belong to no commit.

**`check_expr(req)`** delegates to the process area's expression checks (C-PROC-3): `check_expr(process_doc, loc, expr)` and `expr_scope(process_doc, loc)`. It runs them on the in-flight document the web sends, not on disk.

### 5.5 Derived status (`status.py`, CORE)

```python
def derive_status(ctx: ControllerContext, ws: Workspace, pid: str) -> ProcessStatus: ...
```
The result has the web `ProcessStatus` shape. Every call recomputes it; nothing is stored.

1. `closure = reference_closure(ws, pid)`.
2. `head = git.last_commit_touching(closure)`. This is the **process HEAD** (§6.5, §11). Also record `subject`, `at` and `short`.
3. `cs = compile_state(ws, pid)`. Then `design = cs.design`, and `design_steps` lists the step keys whose proto hash does not match their lock.
4. `tests = tests_status(stores.runs, ws, pid, head)`. This gives `"passed" | "failed" | "missing"`; the API maps `missing` to `"unknown"`.
5. `compiled = (not design) and tests == "passed"`.
6. `built = artefacts.get_build(pid, head) is not None`.
7. For each release of `pid`:
   - `rel = {id, commit, short, behind: git.count_touching(r.commit, head, closure), trigger: r.trigger.kind, state}`.
   - `released = any(r.enabled for r in releases)`.
   - Each release carries its own commit, so "released at X" and "design at HEAD" show side by side.
8. `dirty_paths = git.dirty_paths(closure)` is returned as an extra field (`dirty`), which the CLI shows.

`ProcessService.list()` catches per-process load errors and puts them in `ProcessSummary.error`, so one broken YAML never hides the other processes.

### 5.6 Search (`search.py`, CORE)

```python
def search(docs: list[SearchDoc], statuses: dict[str, ProcessStatus], q: str,
           flags: Sequence[StatusFlag], limit: int = 200) -> list[tuple[str, int, list[Match]]]: ...
# SearchDoc = {"id", "name", "goal", "steps": [(step_key, instruction or "")]}
```
1. `tokens = q.lower().split()`. With no tokens every process matches.
2. The searchable fields are `id` and `name` (weight 3), `goal` (weight 2), and each step's `instruction` (weight 1). The step name also counts, at weight 1.
3. A token matches a field when it is a substring of the lower-cased field. **Every token must match somewhere (AND).**
4. `score` is the sum over tokens of the highest weight that token matched.
5. `flags` are ANDed: every requested flag must be true on the process's derived status.
6. Sort by `(-score, id)`.
7. Each hit gets up to 3 `matches` entries of the form `{field: "id"|"name"|"goal"|"instruction", step, snippet}`. A snippet is 40 characters either side of the first match, marked with `…`.

### 5.7 Design edits and auto-commit (`design.py`, CORE)

The web contract is 07 §12.3. The chat uses the same service, so there is exactly one code path for writing design files.

```python
class DesignService:
    def get(self, pid: str) -> DesignDoc: ...
    def save(self, pid: str, req: SaveRequest, *, origin: Literal["web", "chat", "cli"] = "web",
             lock_owner: tuple[str, str] | None = None) -> SaveResult: ...
    def commit(self, pid: str, *, reason: str, summary: str, origin: str = "web",
               extra_trailers: Mapping[str, str] | None = None) -> CommitInfo | None: ...
    def scope(self, pid: str) -> list[str]: ...           # the design scope (C-PROC-1)
    def lock(self, pid: str, chat_id: str, turn_id: str) -> None: ...
    def unlock(self, pid: str, turn_id: str) -> None: ...
    def locked_by(self, pid: str) -> tuple[str, str] | None: ...
```

**Design scope** of `pid` is:
- its `process.yaml`,
- any proto-step YAML under its own directory, whether it exists or is being created, and
- the proto YAML of every step-root step its `steps:` reference.

Compiled source and other processes' own files are never in scope.

**`get(pid)`** returns `DesignDoc`:
- `process_file` and `protos`. Each is loaded with the spec YAML loader (C-SPEC-1), a SafeLoader without timestamps, so the JSON round trip is lossless. Each carries `revision = "sha256:…"` and the original `yaml` text. A YAML parse error sets `parse_error` and `doc = null`.
- `steps`, `interface`, `available_local`, `conventions`, `validation` (§5.4).
- `head`.
- `locked_by`.

**`save(pid, req)`** runs these steps in order and is atomic from the client's view:
1. **Scope.** Every `write.path` must be in `scope(pid)`. If any path is outside it, raise `OutOfScope` and write nothing.
2. **Lock.** If `locked_by(pid)` is set and is not `lock_owner`, raise `DesignLocked(details={chat_id, turn_id})`.
3. **Revision.** Every `write.base_revision` must equal the current revision, where `null` means the file must not exist. Otherwise raise `RevisionConflict(details={path, current_revision})` and write nothing. Steps 3 and 4 hold a per-process `threading.Lock`.
4. **Write.** Dump each `doc` with `wynd.spec.yaml.dump(doc)` (C-SPEC-1), write it to `path.tmp`, then `os.replace` it into place. `delete: true` unlinks the file. `doc` must be a JSON object, otherwise raise `Invalid`. **Validation errors never block a save**, because the design may be mid-edit.
5. **Recompute** `validation`, `steps` and `interface` from the working tree.
6. **Commit.** If `req.commit` is set, call `commit(pid, reason=req.commit.reason, summary=req.commit.summary, origin=origin)`.
7. Return `SaveResult{files: [{path, revision, yaml}], validation, steps, interface, commit, head}`.

**`commit(pid, reason, summary, origin)`** makes one commit on a boundary:
1. `paths = [p for p in scope(pid) if p in git.dirty_paths(scope(pid))]`. Deletions are included. If no path is dirty, return `None`.
2. Build the message: `f"design({pid}): {summary or 'update ' + ', '.join(basename(p) for p in paths[:3])}"`, a blank line, then `Wynd-Origin: <origin>`, `Wynd-Reason: <reason>` and any extra trailers (the chat adds `Wynd-Chat: <chat_id>`).
3. Under `git.lock`, run `git.commit_paths(paths, msg)`. That is `git commit --only -- <paths>`, so dirty files belonging to another process, or a hand edit elsewhere, are never swept in. **A commit therefore never spans two processes.** A shared step-root proto is in the scope of every process that references it, and it is committed under whichever process's boundary comes first.
4. Return `CommitInfo{sha, short, message}`.

The boundaries:

| Boundary | Who calls `commit` |
|---|---|
| Web blur, hidden tab, process switch, before job, before chat, before integrate, unload | The web, via `SaveRequest.commit` |
| End of a chat turn that edited the process | `ChatService`, with `origin="chat"` |
| `wynd new` / `POST /api/processes` | `ProcessService.new` |

The CLI never auto-commits hand edits. Its job commands refuse a dirty closure instead (§5.8).

### 5.8 Jobs (`jobs/service.py`, JOBS)

```python
class JobService:
    def submit_compile(self, pid: str, *, answers: Mapping[str, str] | None = None,
                       accept_defaults: bool = False, max_revisions: int | None = None) -> Job: ...
    def submit_test_live(self, pid: str, *, steps: Sequence[str] | None = None) -> Job: ...
    def submit_build(self, pid: str, *, registry: str | None = None, push: bool | None = None) -> Job: ...
    def submit_bake(self, pid: str) -> Job: ...
    def submit(self, kind: JobKind, pid: str, inputs: Mapping[str, Any]) -> Job: ...
        # generic: runs the preconditions below for `kind`, adds "process" (+ "target_branch" for commit kinds),
        # picks the ref, calls runner.submit. 08's wynd.controller.optimise.submit_optimise calls this with kind="optimise".
    def answer(self, job_id: str, answers: Sequence[AnswerRequest] | Mapping[str, str], *,
               accept_defaults: bool = False) -> Job: ...
    def get(self, job_id: str) -> Job: ...
    def list(self, *, process_id: str | None = None, kind: str | None = None, status: str | None = None,
             active: bool = False, limit: int = 50) -> list[Job]: ...
    def logs(self, job_id: str, offset: int = 0) -> LogChunk: ...           # {text, offset (next), done}
    def cancel(self, job_id: str) -> Job: ...
    def wait(self, job_id: str, *, timeout: float | None = None, poll: float = 0.5,
             on_log: Callable[[str], None] | None = None,
             on_event: Callable[[dict], None] | None = None) -> Job: ...   # returns on succeeded/failed/cancelled/awaiting_input
    def integrate(self, job_id: str) -> Job: ...                             # §5.9; idempotent
```

**Preconditions for every job submit.** The web commits its own edits before calling (reason `before_job`). The CLI commits nothing.
1. The process loads (`NotFound`), and `validate(pid)` reports no errors (`ValidationFailed`, with the report in `details`).
2. `dirty = git.dirty_paths(reference_closure(ws, pid))`. If it is non-empty, raise `DirtyTree(details={"paths": dirty})`. Jobs only see committed state.
3. Commit-producing kinds (`compile`, `test_live`, `optimise`):
   - `target = git.current_branch()`; if it is `None`, raise `DetachedHead`.
   - `ref = git.head()`. At the branch tip the closure content is identical to the closure at the process HEAD (I-1), and forking from the tip lets an unchanged target fast-forward.
4. `build` and `bake`: `ref = git.last_commit_touching(closure)`. The process HEAD is the artefact key (§6.4).

**Submitted inputs.** Every kind carries `process`. Commit kinds also carry `target_branch`.

| kind | ref | inputs |
|---|---|---|
| compile | branch tip | `{"process", "target_branch", "options": {"accept_defaults", "max_revisions"?}, "answers": {...}}`. This is compiler `CompileJobInputs` plus `target_branch`. |
| test_live | branch tip | `{"process", "target_branch", "steps": [...] \| null}` |
| build | process HEAD | `{"process", "registry": name \| null, "push": bool}`. `registry` defaults to the user registry's default image registry. `push` defaults to `registry is not None`. |
| bake | process HEAD | `{"process"}` |
| optimise | branch tip | whatever 08 §3.7 defines (`OptimiseJobInput`), plus `process` and `target_branch` |

**`answer(job_id, answers)`** is compiler C-C2 plus web C-CTL-ANSWERS.
1. The job must be a `compile` job in `awaiting_input` (`JobState` otherwise).
2. Normalise each answer to text with `compile_view.answer_text(q, a)`:
   - `{text}` → `text`
   - `{decision: "confirm"}` → `"accept"`, or `"yes"` for `confirm_schema`
   - `{decision: "reject", text?}` → `"reject"`
   - `{decision: "correct", example}` → `yaml.safe_dump(example)`. The compiler treats a mapping with `exit` as a replacement example.
   - `{decision: "correct", text}` → `text`
3. If `accept_defaults` is set, add `q.default` for every pending question that has a default and no answer yet.
4. Apply: `session = apply_answers(job.session, answers)` (compiler P3), then `stores.runs.update(job_id, {"session": session})`.
5. If `session["state"] == "ready"`, meaning no pending questions remain:
   - `kind, ref, inputs = resume_request(session)`
   - `runner.requeue(job_id, ref, {**inputs, "target_branch": job.inputs["target_branch"]})`.

   **The same job id** goes back to `queued` (I-5). Otherwise the job stays in `awaiting_input` with fewer pending questions, so answers can arrive one at a time.
6. Return the refreshed `Job`.

**`list(active=True)`** returns jobs in `queued | running | awaiting_input`, plus jobs in `succeeded` with `integration is None` whose kind is `compile`, `test_live` or `optimise`. These are the ones web 07 §9.9 integrates.

**`wait`** polls `runner.status` every `poll` seconds.
- It tails `runner.logs(job_id, offset)` into `on_log`.
- For compile jobs it also sends new `session.events` (by `seq`) to `on_event`, which is what compiler 6.7 streams.
- On timeout it raises `Unavailable("timed out waiting for job …")`, and the job keeps running.

**`Job` DTO** is the web shape, built by `jobs/records.py:to_dto(record)` from the stored `JobRecord`:
- `branch` and `result_commit` come from the artefacts.
- `session` is `compile_view.session_dto(record.session)` (§7.8).
- `build` is `BuildResult` from the build artefacts.
- `integration`, `error` (as `{message, detail}`), `usage` and `chat_id` come across as stored.

### 5.9 Compile-branch integration (`jobs/integrate.py`, JOBS)

```python
def integrate(ctx: ControllerContext, job: JobRecord) -> Integration: ...
```
Integration applies to `compile`, `test_live` and `optimise` jobs whose status is `succeeded`. It is **idempotent**: if `job.integration` is already set, that stored result comes back unchanged. Process 04 also sketches this algorithm in `wynd.process.git`. There must be exactly one implementation (§13 R3), and `ctl.jobs.integrate` is the only operation surface.

1. `B = job.artefacts.branch`. If it is `None`, the result is `mode="noop"`.
2. If `job.artefacts.remote` is set, run `git fetch <remote> <B>:<B>`. Kube jobs push to a remote; local worktree jobs share refs and have no `remote`.
3. `T = job.inputs["target_branch"]`. Everything from here runs under `git.lock`.
4. `b = rev_parse(B)`, `t = rev_parse("refs/heads/" + T)`, `base = job.artefacts.base_commit or merge_base(t, b)`.
5. If `is_ancestor(b, t)`, the result is `mode="noop"`.
6. **Fast-forward**, when `merge_base(t, b) == t`:
   - Set `new = b`, `mode = "fast_forward"`, `skipped = 0`.
7. **Moved target**, otherwise:
   1. Take the closure union: `closure = reference_closure(ws_at(base), pid) ∪ reference_closure(ws_at(t), pid) ∪ reference_closure(ws_at(b), pid)`, where `ws_at(r) = load_workspace(root, CommitTree(r))`. Process 04 also uses the union.
   2. `changed = diff_names(base, t)`.
   3. `conflicts = [p for p in changed if under(p, closure)]`, where `under(p, c)` means `p == c` or `p` starts with `c.rstrip('/') + '/'`.
   4. If `conflicts` is non-empty, the result is `mode="pr_branch"` with those conflicts.
   5. Otherwise rebase in a **scratch worktree**:
      - `wt = state_dir/"integrate"/job.id`
      - `worktree_add_detached(wt, b)`
      - `git -C wt rebase --onto t base`
      - If the rebase fails, run `rebase --abort`, remove `wt`, and return `pr_branch` with `conflicts=["<rebase conflict>"]`.
      - On success, `new = rev_parse("HEAD")` in `wt`. Remove `wt`, then `update_ref("refs/heads/"+B, new, b)`.
      - `mode = "rebased"`, `skipped = rev_count(base..t)`.
   6. **Re-key test results.** For each `key` in `compile_state(ws, pid).steps[*].step_hash + [process_hash]`: if `r = runs.get_test_result(b, key)` exists, call `runs.put_test_result(r.model_copy(update={"commit": new}))`. The keys are git tree hashes and the closure content is unchanged, so the results stay valid (I-22).
8. **Advance T to `new`.**
   - If `git.current_branch() == T`, run `merge_ff_only(new)` in the main worktree.
     - If git refuses because local changes would be overwritten, raise `DirtyTree(details={"paths": overwritten})` and record nothing. The web then commits (`before_integrate`) and retries once (07 §9.9).
   - Otherwise, `update_ref("refs/heads/" + T, new, t)`.
   - Then `delete_branch(B)`, because its commits are now on T.
9. Build the result: `Integration{mode, branch: B, target: T, head: new or None, skipped_commits, conflicts, pr_url: None, at: now}`. Store it with `runs.update(job.id, {"integration": …})`. The job's `JobRecord.integration` holds the same value.

After `pr_branch` the branch stays. The CLI prints `git push origin <B> && gh pr create --head <B>`. The controller never opens a PR itself, so `pr_url` stays `None` in v1.

### 5.10 Runs, traces and uploads (`runs/service.py`, `uploads.py`, RUNS)

```python
class RunService:
    def run(self, pid: str, inputs: dict[str, Any], *, target: RunTarget = LocalTarget(),
            on_event: Callable[[dict], None] | None = None, trigger: str = "api") -> Run: ...   # blocking
    def start(self, req: CreateRunRequest, *, trigger: str = "api") -> Run: ...               # background
    def get(self, run_id: str) -> Run: ...
    def list(self, *, process_id: str | None = None, release_id: str | None = None, limit: int = 50) -> list[Run]: ...
    def events(self, run_id: str, since: int = 0) -> list[dict]: ...    # normalised for the web (below)
    def follow(self, run_id: str, *, since: int = 0, on_event: Callable[[dict], None],
               poll: float = 0.2, timeout: float | None = None) -> Run: ...

class UploadService:
    def put(self, filename: str, data: bytes) -> Path: ...    # .wynd/uploads/<sha256>/<basename(filename)>
```

**Local target** (`{kind: "local"}`):
1. `run_id = new_id("run")`.
2. `env = ctl.env.resolve()`.
3. Call `wynd.process.local.run_local(ws, pid, inputs, run_id=run_id, env=env, stores=ctx.stores, on_event=on_event)`. This is process 04, and it wraps the runtime `Executor` in local mode with venv workers under `.wynd/venvs/`.
   - `run` calls it directly.
   - `start` calls it in a daemon thread and returns `Run(status="running")` right away.
   - The controller does no venv planning of its own.

**Image target** (`{kind: "image", commit}`):
1. `build = artefacts.get_build(pid, commit)`. If there is none, raise `NotBuilt`.
2. `url, ephemeral = ctl.serve.acquire(build.image)`. `acquire` reuses a registered warm container whose `GET /readyz` returns 200, using HTTP only with no Docker call. If none is ready it starts an ephemeral container (§5.12).
3. Rewrite upload paths (below).
4. `run_id = new_id("run")`, then `RunApiClient(url).submit(inputs, run_id=run_id)`.
5. Call `mirror_run(ctx, url, run_id, extra={"process": pid, "mode": "image", "trigger": trigger, "commit": commit})`. It streams `GET /runs/{id}/events` into `stores.traces.write(ev)` and `on_event`. At `end` it fetches `GET /runs/{id}` and creates or updates the local `RunRecord`.
6. If the container was ephemeral, stop it.

**Release target** (`{kind: "release", release_id}`) goes through `ctl.releases.trigger` (§5.14).

**Upload paths.**
- `UploadService.put` stores the file at `<state_dir>/uploads/<sha256>/<name>` and returns that absolute path. Local runs read it as is.
- Docker containers started by the controller (serve, ephemeral, release) mount `<state_dir>/uploads` read-only at `/wynd/uploads`.
- Before an image or release submit, every input field whose declared type is `path` and whose value starts with `<state_dir>/uploads/` is rewritten to `/wynd/uploads/<rest>`.
- A target that cannot mount uploads raises `Invalid("uploads are not supported by serving backend <name>")`. That is the kube target in v1.

**Event normalisation for the web** (`events()`, and the run SSE). Each runtime event is passed through with two fields added:
- `mode`, taken from the run record.
- `path`, set to `ev.get("path") or ev.get("step")` for step-scoped events.

The runtime (02 §8.2) and the web (07 C-RT-TRACE) disagree on event type names (`run.start` vs `run_started`). The controller passes the runtime names through unchanged; §13 R4 records this.

**`Run` DTO** is built from the `RunRecord`:
- `target` is `{kind: local}` or `{kind: image, commit}`; for release runs it is `{kind: release, release_id}`.
- `trigger` is `api`, `manual`, `schedule` or `webhook`.
- `status` maps runtime `running` → `running`, `succeeded`/`failed` → `finished`. If the run failed to start, it is `failed_to_start`.

**CLI rendering** (`runs/tree.py`) uses runtime `build_tree(events) -> TraceTree` (02 §8.4). The controller only renders:

```python
def render_tree(tree: TraceTree, *, full: bool = False) -> str: ...
```
```
run run_20260922T215001100_a1b2c3  process_supplier_invoice  local  exit=done  1.84s  $0.0011
├─ read            done   14ms
├─ extract         done   1.21s   haiku 812→64 tok $0.0011
├─ validate        done   3ms     valid=false fixable=true
│  → validate.done[1] retry → fix
├─ fix             done   402ms
├─ validate #2     done   2ms     valid=true
└─ archive         done   9ms
   └─ write        done   5ms
```
Each node line shows the step (`#n` when `run > 1`), exit, `timings.duration_ms`, usage (`model in→out tok $cost` if present), and `summary.key_outputs` as `k=v`, truncated to 80 chars.
- `edge.taken` events whose branch index is greater than 0, or that are named, print as `→ from[branch] name → to`.
- An `error` exit adds a `cause: <cause>: <message>` line.
- `full` adds `in:` and `out:` JSON, each truncated to 400 chars.

### 5.11 Env (`envcheck.py`, `envfile.py`, RUNS)

```python
def parse_env_file(text: str) -> dict[str, str]: ...
    # KEY=VALUE; optional "export "; "#" comments; blank lines; '…'/"…" quotes (double honours \n \" \\); no interpolation
def load_into_environ(root: Path) -> None: ...

class EnvService:
    def resolve(self, extra_files: Sequence[Path] = ()) -> dict[str, str]: ...
        # highest first: os.environ, extra_files (in order), root/.env
    def manifest(self, pid: str, commit: str | None = None) -> tuple[EnvManifest, Literal["build", "assembled"]]: ...
        # the build's manifest for (pid, commit or process HEAD) if built, else assemble_env_manifest(ws, pid, registry)
    def check(self, pid: str, *, extra_files: Sequence[Path] = ()) -> EnvCheckReport: ...
    def check_release(self, release: Release) -> EnvCheck: ...     # web {ok, missing, unbound}
```

`check` produces one row per manifest variable: `{name, required, secret, description, used_by, set, source}`, where `source` is `env`, `file`, `dotenv` or `missing`. `ok` is true when every required variable is set. Values are never returned.

`check_release` computes:
- `unbound`: required variables that have no binding.
- `missing`: variables bound with `from_env` whose source is unset in the controller's env.

### 5.12 Serving warm containers (`serving.py`, `docker.py`, RUNS)

```python
# docker.py: every call is subprocess ["docker", ...]; missing binary or daemon -> Unavailable
def image_labels(image: str) -> dict[str, str]: ...
def run_detached(image: str, *, name: str, env: Mapping[str, str], host_port: int | None, container_port: int,
                 labels: Mapping[str, str], mounts: Sequence[tuple[Path, str]], restart: str | None) -> str: ...
def host_port(name: str, container_port: int) -> int: ...
def rm(name: str) -> None: ...                           # docker rm -f (idempotent)
def logs_follow(name: str) -> subprocess.Popen: ...
def logs_tail(name: str, n: int = 50) -> str: ...

class ServeService:
    def serve(self, image: str, *, port: int | None = None, extra_env_files: Sequence[Path] = (),
              timeout: float = 120.0, register: bool = True, name: str | None = None) -> ServedContainer: ...
    def acquire(self, image: str) -> tuple[str, bool]: ...   # (url, ephemeral)
    def stop(self, name_or_image: str) -> list[str]: ...
    def list(self) -> list[ServedContainer]: ...             # from .wynd/serve/*.json, no docker call
```

`serve(image)`:
1. `labels = image_labels(image)`. The manifest is `EnvManifest.model_validate_json(labels["dev.wynd.env-manifest"])`. If the label is missing, raise `Invalid("not a wynd process image")`.
2. `resolved = ctl.env.resolve(extra_env_files)`. `missing = [required vars not in resolved]`. If any are missing, raise `EnvMissing`.
3. Set up the container:
   - `pass_env` is the resolved values for the manifest's variables.
   - `name` defaults to `wynd-<slug(process)>-<commit[:7]>`.
   - `container_port = int(labels.get("dev.wynd.run-api-port", "8080"))`.
4. Run `docker run -d --name <name> -p 127.0.0.1:<port or ''>:<container_port> -v <state_dir>/uploads:/wynd/uploads:ro -e VAR … <image>`.
   - Each `-e VAR` is passed **without** a value. The values go through the child process `env=`, so **secrets never appear in argv**.
   - Add the label `dev.wynd.served=1`.
5. `url = http://127.0.0.1:<host_port>`. Poll `/healthz` and then `/readyz` every 0.25 s until both return 200. On timeout, raise `Unavailable` with the last 50 log lines.
6. If `register` is set, write `.wynd/serve/<name>.json` (§10.8).

`acquire(image)`:
- Probe every registered container for `image` with `GET url/readyz` (1 s timeout).
- The first healthy one returns `(url, False)`. Delete the records of containers that fail the probe.
- If none is healthy, call `serve(image, register=False, name="wynd-run-<8 hex>")` and return `(url, True)`.

### 5.13 Registries (`registries.py`, REL) and MCP OAuth (`oauth.py`, REL)

Sections follow runtime 02 §9.1: `"mcp"`, `"providers"`, `"registries"`. `"registries"` holds image registries. The entry schemas are in §10.5.

```python
class RegistryService:
    def list_mcp(self) -> list[McpServerEntry]: ...
    def add_mcp(self, entry: McpServerEntry) -> McpServerEntry: ...                  # upsert
    def remove_mcp(self, name: str) -> RemoveResult: ...                             # {removed, referenced_by}
    def list_providers(self) -> list[ProviderEntry]: ...                             # web ProviderEntry {name, kind, tiers}
    def add_provider(self, name: str, *, tiers: Mapping[str, str] | None = None, env: Sequence[str] | None = None) -> ProviderEntry: ...
    def remove_provider(self, name: str) -> RemoveResult: ...
    def list_image_registries(self) -> list[ImageRegistryEntry]: ...
    def add_image_registry(self, entry: ImageRegistryEntry) -> ImageRegistryEntry: ...
    def remove_image_registry(self, name: str) -> RemoveResult: ...
    def default_image_registry(self) -> ImageRegistryEntry | None: ...
    def oauth_start(self, name: str, *, redirect_uri: str, return_to: str | None) -> str: ...
    def oauth_complete(self, state: str, code: str) -> tuple[McpServerEntry, str | None]: ...
    def refresh_tokens(self) -> list[str]: ...
```

Boundary validation:
- **`add_mcp`**:
  - `name` must match `[a-z0-9][a-z0-9_-]*`.
  - `url` must be http(s).
  - `transport` must be `http` or `sse`.
  - Env var names in `auth_env` and `headers_env` must match `[A-Z_][A-Z0-9_]*`.
  - `allow` lists are per step (§3.8), never in the registry.
- **`add_provider`**:
  - `name` must be in `available_providers()`, otherwise `Invalid` listing the ones that are.
  - The merge is existing entry → then `tiers` → then the provider defaults. The result must have exactly `cheap`, `standard` and `strong`, each with a non-empty model id.
  - `kind` comes from the provider's entry point.
- **`remove_*`** lists the processes whose `step.lock.yaml` names the entry. It removes the entry anyway; the CLI warns.

**OAuth for MCP** (M4, httpx, pending state kept in memory for 10 minutes):
1. **`oauth_start`**:
   1. Discover the issuer: `GET {origin(url)}/.well-known/oauth-protected-resource` (RFC 9728) and take `authorization_servers[0]`. If that fails, fall back to `origin(url)`.
   2. Fetch server metadata: `GET {issuer}/.well-known/oauth-authorization-server` (RFC 8414).
   3. If the server has a `registration_endpoint` and no client is stored, register one (RFC 7591): `{"client_name":"Wynd","redirect_uris":[redirect_uri],"grant_types":["authorization_code","refresh_token"],"response_types":["code"],"token_endpoint_auth_method":"none"}`.
   4. Create a PKCE S256 verifier and a random `state`.
   5. Return the authorize URL with `response_type=code, client_id, redirect_uri, code_challenge, code_challenge_method=S256, state, resource=<url>`.
2. **`oauth_complete`**:
   - Exchange the code at the token endpoint.
   - Store `WYND_MCP_<NAME>_TOKEN`, and `_REFRESH_TOKEN` when one is returned, through `registry.put_secret` (§13 R6).
   - Set `auth_env` and `oauth = {client_id, token_endpoint, expires_at, refresh_env}` on the entry.
3. **`refresh_tokens`** refreshes any token that expires within 60 s. It runs before runs, serve, env check and every scheduler tick. Failures are logged and skipped.

### 5.14 Releases, triggers, scheduler (`releases/*`, REL)

```python
class ReleaseService:
    def create(self, req: CreateReleaseRequest) -> Release: ...
    def list(self, *, process_id: str | None = None) -> list[Release]: ...
    def get(self, release_id: str) -> Release: ...
    def update(self, release_id: str, patch: ReleasePatch) -> Release: ...     # trigger?, env?, enabled?
    def delete(self, release_id: str) -> None: ...
    def trigger(self, release_id: str, inputs: dict[str, Any] | None, *,
                source: Literal["manual", "schedule", "webhook"]) -> Run: ...
    def env_check(self, release_id: str) -> EnvCheck: ...
    def verify_webhook(self, release_id: str, presented: str | None) -> Release: ...
    def fires(self, release_id: str, limit: int = 50) -> list[TriggerFire]: ...
```

**`create(req)`**. Only built processes can be released.
1. The process must exist.
2. `build = artefacts.get_build(req.process_id, req.commit)`. If it is `None`, raise `NotBuilt`.
3. Validate the trigger:
   - `{kind: manual}` has no fields.
   - `{kind: schedule, cron, timezone, inputs}`: `cron` must pass `CronExpr.parse`; `timezone` must be `null` (meaning UTC) or a valid `ZoneInfo`; the keys of `inputs` must equal the process input field names.
   - `{kind: webhook, secret_env}`: `secret_env`, when set, must be a valid env var name.
   - Every release can also be run manually, whatever its trigger.
4. Validate the env binding against `build.manifest`:
   - Unknown names raise `Invalid`.
   - Required variables with no binding raise `EnvUnbound`.
   - Each binding must be exactly `{value}` or `{from_env}` and supported by `serving.supported_bindings`.
5. Store the record with `id = new_id("rel")`, `image = build.image_digest or build.image`, `target = serving.name`, `enabled`, `created_at`.
6. If `enabled`, call `serving.ensure(release, resolved_env)` in a background thread so the release starts warm. The `state` field reports progress.
7. Return the `Release` DTO. It is computed on read with:
   - `short`
   - `behind`, which is `git.count_touching(commit, head, closure)`
   - `state` and `state_detail`, from `serving.status(release)`
   - `next_fire_at`, from `cron.next_after(now)`
   - `webhook_url`, which is `"/hooks/releases/<id>"` for webhooks

**`update`** revalidates exactly as `create` does. Changing `env`, or setting `enabled=false`, calls `serving.remove(release.id)`. Re-enabling calls `ensure` in the background.

**`delete`** calls `serving.remove(id)` and `triggers.remove(id)`, then deletes the record. The fire history is kept.

**`trigger(release_id, inputs, source)`**:
1. The release must be enabled, otherwise `Conflict("release disabled")`. `inputs` defaults to `release.trigger.inputs`, or `{}`.
2. Resolve the bindings: `value` is used literally, `from_env` reads `ctl.env.resolve()[name]`, and a missing name raises `EnvMissing`.
3. Rewrite upload paths (§5.10). `url = serving.ensure(release, env)`.
4. `run_id = new_id("run")`. Call `RunApiClient(url).submit(inputs, run_id=run_id)`. Start `mirror_run(ctx, url, run_id, extra={"process", "mode": "image", "release_id", "trigger": source, "commit"})` in a daemon thread.
5. Record the fire as `{release_id, source, at, run_id, ok}`. If an exception occurs, record `ok: false, error` and re-raise.
6. Return the `Run` in state `running`.

**`verify_webhook(release_id, presented)`**. The presented secret is read from `Authorization: Bearer <s>` or `X-Wynd-Token: <s>`.
- The release must exist (`NotFound`), have a `webhook` trigger (`NotFound`), and be enabled (`Conflict`).
- If `trigger.secret_env` is set, compare with `hmac.compare_digest(presented, resolve()[secret_env])`. A mismatch, or an unset env var, raises `Unauthorized`.
- If `secret_env` is `null`, the hook falls back to the API bearer token when `WYND_API_TOKEN` is set. When no token is set it is open; `create` warns about this (I-11).

**Cron** (`releases/cron.py`, stdlib only):

```python
@dataclass(frozen=True)
class CronExpr:
    minutes: frozenset[int]; hours: frozenset[int]; days: frozenset[int]; months: frozenset[int]
    weekdays: frozenset[int]; dom_star: bool; dow_star: bool; source: str
    @classmethod
    def parse(cls, text: str) -> "CronExpr": ...          # Invalid names the bad field
    def matches(self, dt: datetime) -> bool: ...          # local wall time, seconds ignored
    def next_after(self, dt: datetime) -> datetime: ...   # strictly after dt; tz of dt
```
Grammar:
- Five fields: minute 0–59, hour 0–23, day of month 1–31, month 1–12 or `JAN`–`DEC`, day of week 0–7 or `SUN`–`SAT` (7 is Sunday).
- Each field is a comma list of `*`, `*/n`, `a`, `a-b` or `a-b/n`.
- Macros: `@yearly`/`@annually`, `@monthly`, `@weekly`, `@daily`/`@midnight`, `@hourly`.
- Day matching follows Vixie cron: when both day of month and day of week are restricted, a day matches if **either** matches.

`next_after(dt)`:
1. Start from `dt` floored to the minute, plus one minute. Allow at most 5 years of iterations.
2. If the month does not match, jump to the first day of the next month at 00:00.
3. Else if the day does not match, jump to the next day at 00:00.
4. Else if the hour does not match, jump to the next hour at :00.
5. Else if the minute does not match, add one minute.
6. Else return. The arithmetic runs on naive wall time, localised with `ZoneInfo`.
7. DST: a wall time inside a gap is skipped, and an ambiguous (fold) time fires once, with `fold=0`.
8. If the search runs out, raise `Invalid("schedule never fires")`, for example for `0 0 31 2 *`.

**Scheduler** (`releases/scheduler.py`):

```python
class Scheduler:
    def __init__(self, ctl: Controller, *, clock: Callable[[], datetime] | None = None,
                 max_catch_up_minutes: int = 5, workers: int = 4) -> None: ...
    def start(self) -> bool: ...     # False if .wynd/locks/scheduler.lock is held by another process (flock LOCK_NB)
    def stop(self) -> None: ...
    def tick(self, now: datetime) -> list[str]: ...   # testable core; returns fired release ids
```
The loop:
1. `last = floor_minute(clock())`.
2. Until stopped: `stop_event.wait(until next minute + 0.5 s)`, then `tick(floor_minute(clock()))`.

`tick(now)`:
1. Call `ctl.registries.refresh_tokens()`.
2. Collect every minute in `(last, now]`, capped at `max_catch_up_minutes`, then set `last = now`. Downtime is not caught up (I-10).
3. For each enabled release with a schedule trigger, check each of those minutes in order:
   1. Compute `key = m.isoformat()` in UTC.
   2. If `cron.matches(m.astimezone(tz))` and `release.last_fire_key != key`:
      - **Before** firing, call `ctx.docs.update("releases", id, {...})` to set `last_fire_key` and `last_fired_at`. This makes firing at-most-once.
      - Submit `ctl.releases.trigger(id, None, source="schedule")` to a `ThreadPoolExecutor(workers)`.
      - Stop checking this release; each release fires at most once per tick.

The `Scheduler` is the default **trigger backend**, `scheduler`. Only `serve-api` runs it, from the app lifespan, and the flock means at most one scheduler runs per workspace.

**Backends** (`releases/serving.py`, `releases/triggers.py`). The M5/kube draft (08 C15) splits "where a release runs" from "what fires it". I adopt that split and its env var names:

```python
class ServingBackend(Protocol):                      # WYND_SERVING_BACKEND (default "docker"); entry points "wynd.serving_backends"
    name: str
    supported_bindings: frozenset[str]
    mounts_uploads: bool
    def ensure(self, release: Release, env: Mapping[str, str]) -> str: ...   # ready run-API base URL
    def status(self, release: Release) -> tuple[Literal["starting", "serving", "stopped", "error"], str | None]: ...
    def remove(self, release_id: str) -> None: ...

class TriggerBackend(Protocol):                      # WYND_TRIGGER_BACKEND (default "scheduler"); entry points "wynd.trigger_backends"
    name: str
    def install(self, release: Release) -> None: ...          # scheduler: no-op (it reads records every tick); kube: CronJob
    def remove(self, release_id: str) -> None: ...
    def reconcile(self, releases: Sequence[Release]) -> None: ...   # called at serve-api startup and after every release change
    def start(self, ctl: "Controller") -> None: ...           # serve-api lifespan; scheduler: starts the Scheduler thread; kube: no-op
    def stop(self) -> None: ...

# Entry-point factory convention (both groups, and wynd.job_runners):
#   factory(*, env: Mapping[str, str], workspace_root: Path, state_dir: Path, stores: Stores) -> Backend
# 08 proposes `from_env(env, registry)`. The kwargs superset lets a kube factory ignore what it doesn't need (§13 R9).
def open_serving_backend(name: str, **kw: Any) -> ServingBackend: ...
def open_trigger_backend(name: str, **kw: Any) -> TriggerBackend: ...

class DockerServing:
    name = "docker"; supported_bindings = frozenset({"value", "from_env"}); mounts_uploads = True

class SchedulerTriggers:                              # install/remove/reconcile are no-ops; start() runs Scheduler(ctl).start()
    name = "scheduler"
```

`ReleaseService` calls the trigger backend at these points:
- `create`, and `update` of the trigger or `enabled`: `triggers.install(release)` or `triggers.remove(id)`.
- `delete`: `triggers.remove(id)`.
- serve-api startup: `triggers.reconcile(list())`.

With the `kube` trigger backend, CronJobs fire through `POST /api/releases/{id}/trigger` with `{"source": "schedule"}`, authenticated by the API bearer token. That is 08's `wynd-kube fire`. Webhooks always arrive at the controller's `/hooks/releases/{id}`.

`DockerServing.ensure`:
1. `cname = f"wynd-rel-{release.id}"`. `rec` is the record in `.wynd/serve/<cname>.json`. `h = sha256(image + sorted env items)`.
2. If `rec.env_hash == h` and `GET rec.url/readyz` returns 200, return `rec.url`.
3. Otherwise:
   - `rm(cname)`.
   - `run_detached(…, restart="unless-stopped", labels={"dev.wynd.release": id}, mounts=[(uploads, "/wynd/uploads:ro")])`.
   - Wait until the container is ready (up to 120 s). Starting and error states are recorded in `rec.state`.
   - Write `rec` with `env_hash = h`. It stores the hash, never the values.

`status` works like this:
- no record → `stopped`
- `rec.state == "error"` → `error`
- `readyz` returns 200 within 1 s → `serving`
- otherwise → `starting`, or `error` with the last log line after 120 s

### 5.15 Base images (`base.py`, JOBS)

```python
def build_base(version: str, variants: Sequence[Literal["slim", "alpine"]], *, log: Callable[[str], None]) -> list[str]: ...
def publish_base(version: str, variants: Sequence[str], registry_name: str, *, log: Callable[[str], None]) -> list[str]: ...
```
- If `version != wynd.runtime.__version__`, both raise `VersionMismatch`, because §4 requires the runtime and the base to share a version number.
- Both delegate to `wynd.process.base.build_base/publish_base`, which go through process's `ImageBuilder`.
- They are plain functions, **not jobs**: a base image has no git ref in the workspace (I-7). They work without a workspace, and `publish_base` reads the registry entry through `registry_from_env()`.

---

## 6. JobRunner implementations, job harness, Kubernetes seam

### 6.1 Types in `wynd.process.jobs` (process owns the file; this is the shape the controller implements against)

This section merges three drafts:
- my first draft;
- compiler 05 C-P4 (`JobContext` field names, handler `(ctx, inputs)`);
- process 04 §0 (`JobKind`/`JobState`/`JobRecord`/`JobRunner` in `wynd.process.jobs`).

Handlers in `process` and `compiler` import these types. The controller implements the runners and the harness.

```python
JobKind = Literal["compile", "test_live", "build", "bake", "optimise"]
JobState = Literal["queued", "running", "awaiting_input", "succeeded", "failed", "cancelled"]
TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})

class JobRecord(BaseModel):
    id: str                                  # new_id("job") e.g. job_20260922T215001100_a1b2c3
    kind: JobKind
    process_id: str
    ref: str                                 # full sha the checkout is at (changes on requeue: resume ref)
    inputs: dict[str, Any]
    status: JobState
    runner: str                              # "inprocess" | "subprocess" | "kube"
    handler: str                             # "module:function", fixed at submit (lets tests inject handlers)
    attempt: int = 1                         # +1 per requeue (answering questions)
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int | None = None           # summed over attempts
    cpu_ms: int | None = None                # subprocess worker: getrusage(SELF)+getrusage(CHILDREN); in-process: None
    pid: int | None = None                   # OS pid hosting the current attempt
    host: str | None = None
    artefacts: dict[str, Any] = {}           # handler result extras + harness keys: branch, commit, base_commit, remote
    report: dict[str, Any] | None = None
    session: dict[str, Any] | None = None    # compiler SessionData JSON (compile only)
    usage: dict[str, Any] = {}
    error: dict[str, Any] | None = None      # {"message", "detail"}
    integration: dict[str, Any] | None = None  # web Integration, written by ctl.jobs.integrate
    chat_id: str | None = None               # chat bound to this job (web compile/build buttons)

class JobContext(Protocol):                  # compiler 05 C-P4 names + three additions (marked +)
    job_id: str
    kind: str
    worktree: Path                           # clean DETACHED checkout of `commit` (git top level)
    workspace: Path                          # + workspace root inside the worktree (= worktree / subdir)
    commit: str                              # resolved sha of the submitted ref
    scratch: Path                            # job-private dir outside the worktree: .wynd/jobs/<id>/scratch
    state_dir: Path                          # + the user's <workspace>/.wynd (artefact store, venv cache)
    registry: Registry                       # user-level registry
    runs: RunRegistry
    log_path: Path                           # + file the job log is appended to (for streaming subprocess output)
    def log(self, line: str) -> None: ...
    def update(self, **fields: Any) -> None: ...          # merge into the job record (session=, report=, usage=, …)
    def push_branch(self, branch: str, commit: str) -> None: ...   # force-publish; local: update-ref in the shared repo

JobHandler = Callable[[JobContext, dict[str, Any]], Any]   # returns a pydantic model or dataclass with at least `status`

class JobRunner(Protocol):
    name: str
    def submit(self, kind: JobKind, ref: str, inputs: dict[str, Any]) -> str: ...
    def status(self, job_id: str) -> JobRecord: ...
    def logs(self, job_id: str, offset: int = 0) -> tuple[str, int, bool]: ...   # (text, next_offset, done)
    def artefacts(self, job_id: str) -> dict[str, Any]: ...
    def cancel(self, job_id: str) -> None: ...                                   # + (not in §6.6)
    def requeue(self, job_id: str, ref: str, inputs: dict[str, Any]) -> None: ...  # + answering = resubmit the same job
```

`submit`, `status`, `logs` and `artefacts` come from §6.6. `cancel` and `requeue` are additions. `requeue` implements "answering resubmits the job with the answer appended" for one job id, so the web's job-bound chat keeps following the same job (I-5).

**Mapping a handler result into the record.** The harness normalises whatever the handler returns: `out = result.model_dump(mode="json")` if it is pydantic, else `dataclasses.asdict(result)`.
- `status` must be one of `succeeded | failed | awaiting_input`.
- `session`, `report`, `usage` and `error` go to the fields of the same name. A string `error` becomes `{"message": s, "detail": None}`.
- Every other key goes into `artefacts`: for example compile's `branch`, `commit` and `base_commit`, and build's `image`, `image_digest`, `artefact_dir`, `tests`.
- If the handler called `push_branch` but did not report `branch` or `commit`, the harness fills them in from the last push.

### 6.2 Handler table (`jobs/handlers.py`)

```python
DEFAULT_HANDLERS: dict[str, str] = {
    "compile":   "wynd.compiler.job:run_compile_job",        # compiler 05 P2
    "test_live": "wynd.compiler.job:run_live_test_job",      # compiler 05 P2 (process 04 also lists one; §13 R7)
    "build":     "wynd.process.build:run_build_job",         # process 04
    "bake":      "wynd.process.bake:run_bake_job",           # process 04
    "optimise":  "wynd.controller.optimise:run_optimise_job",  # module owned by A8-opt (08 P2); lives in this package
}
def resolve_handler(spec: str) -> JobHandler: ...            # importlib; lazy, so M1 works before the compiler exists
```

Handlers name their own branches. The convention, taken from process 04, is `wynd/<kind>/<process>/<job-id>`, for example `wynd/compile/process_supplier_invoice/job_…`. The job id is stable across requeues, so a resumed compile keeps using the same branch.

### 6.3 Checkout backends (`jobs/checkout.py`)

```python
@dataclass
class Checkout:
    worktree: Path; workspace: Path; commit: str; remote: str | None = None

class CheckoutBackend(Protocol):
    def prepare(self, job: JobRecord) -> Checkout: ...
    def push_branch(self, co: Checkout, branch: str, commit: str) -> None: ...
    def cleanup(self, co: Checkout, *, keep: bool) -> None: ...

class WorktreeCheckout:        # local runners
    def __init__(self, git: Git, state_dir: Path, subdir: str) -> None: ...

class CloneCheckout:           # kube pods (post-M5)
    def __init__(self, remote_url: str, workdir: Path, subdir: str) -> None: ...
```

**`WorktreeCheckout`**
- `prepare(job)`:
  - `path = state_dir/"jobs"/job.id/f"checkout-{job.attempt}"`.
  - Under `git.lock`, call `git.worktree_add_detached(path, job.ref)`.
  - Return `Checkout(path, path/subdir, rev_parse(job.ref))`.
- `push_branch(co, branch, commit)` runs `git update-ref refs/heads/<branch> <commit>`. The worktree shares the object store with the repo, so no push is needed.
- `cleanup(keep)` calls `worktree_remove(path)` unless `keep` is set. Failed jobs keep their worktree for inspection (I-23).

**`CloneCheckout`**
- `prepare` clones: `git clone --filter=blob:none --no-checkout <remote> <workdir>`, then `git -C <workdir> checkout --detach <ref>`.
- `push_branch` runs `git push -f origin <commit>:refs/heads/<branch>` and sets `co.remote = "origin"`.
- The harness then records `artefacts.remote = "origin"`, which tells `integrate` to fetch the branch first.

### 6.4 Harness (`jobs/harness.py`)

```python
class JobCancelled(Exception): ...

def execute_job(*, job_id: str, workspace_root: Path, state_dir: Path, stores: Stores,
                checkout: CheckoutBackend) -> JobRecord: ...
```
1. Load the record: `rec = JobRecord(**stores.runs.get(job_id))` (via `records.load`). If `rec.status != "queued"`, return it unchanged.
2. Mark it running: `records.update(job_id, status="running", started_at=now, pid=os.getpid(), host=gethostname())`.
3. Open the log: `log = JobLog(state_dir/"jobs"/job_id/"job.log")`. It appends line-buffered, with `HH:MM:SS ` prefixes. When `attempt > 1`, first write a `---- attempt N (resumed at <ref7>) ----` separator.
4. Inside `try`:
   1. `co = checkout.prepare(rec)`.
   2. `(state_dir/"jobs"/job_id/"scratch").mkdir(exist_ok=True)`.
   3. Build `ctx = _Ctx(job_id, rec.kind, co.worktree, co.workspace, co.commit, scratch, state_dir, stores.registry, stores.runs, log.path, log.line, partial(records.update, stores.runs, job_id), partial(_push, co))`.
   4. `result = resolve_handler(rec.handler)(ctx, rec.inputs)`.
   5. `fields = normalise(result)` (§6.1).
5. On exceptions:
   - `except JobCancelled`: `fields = {"status": "cancelled"}`.
   - `except Exception as e`: `log.line(traceback)`, then `fields = {"status": "failed", "error": {"message": f"{type(e).__name__}: {e}", "detail": tb}}`.
6. In `finally`:
   1. `checkout.cleanup(co, keep=(fields["status"] == "failed"))`.
   2. Write `records.update(job_id, **fields, finished_at=now, duration_ms=prev + elapsed, cpu_ms=…)`.
   3. `artefacts` are merged with the previous attempt's, so a resumed compile keeps its `base_commit`.

`_push(co, branch, commit)` calls `checkout.push_branch(co, branch, commit)` and remembers `(branch, commit)` so the result can be filled in afterwards.

### 6.5 Runners

```python
class InProcessJobRunner:                                    # M1
    name = "inprocess"
    def __init__(self, *, env: Mapping[str, str], workspace_root: Path, state_dir: Path, stores: Stores,
                 handlers: Mapping[str, str] = DEFAULT_HANDLERS) -> None: ...

class SubprocessJobRunner:                                   # M2, default
    name = "subprocess"
    def __init__(self, *, env: Mapping[str, str], workspace_root: Path, state_dir: Path, stores: Stores,
                 handlers: Mapping[str, str] = DEFAULT_HANDLERS, python: str = sys.executable) -> None: ...

def open_job_runner(name: str, **kwargs: Any) -> JobRunner: ...   # importlib.metadata.entry_points(group="wynd.job_runners")
def pid_alive(pid: int) -> bool: ...                              # os.kill(pid, 0): ProcessLookupError -> False, PermissionError -> True
```

Both runners share these behaviours:
- **Records.** `submit` creates the record through `records.create(stores.runs, JobRecord(id=new_id("job"), kind, process_id=inputs["process"], ref, inputs, status="queued", runner=self.name, handler=handlers[kind], created_at=now))`, then starts the job (below).
- **Logs.** `logs(id, offset)` reads `.wynd/jobs/<id>/job.log` from the byte `offset` and decodes it as UTF-8 with `errors="replace"`. It returns `(text, offset + len(bytes), done)`, where `done = status in TERMINAL or status == "awaiting_input"`.
- **Liveness.** If `status` sees a non-terminal record whose `pid` is not this process and not alive (after reloading once), it marks the job `failed` with the message "worker exited unexpectedly (see log)".
- **Requeue.** `requeue(id, ref, inputs)` requires `status == "awaiting_input"`. It calls `records.update(id, status="queued", ref=ref, inputs=inputs, attempt=attempt+1, pid=None)` and starts the job again.
- **Cancel.**
  - A `queued` or `awaiting_input` job is updated straight to `cancelled`.
  - A `running` job is stopped runner-specifically (below).

Starting and cancelling differ per runner:

| | start | cancel a running job |
|---|---|---|
| inprocess | `threading.Thread(target=execute_job, daemon=True, name=f"wynd-job-{id}").start()` | raises `JobState("in-process jobs cannot be cancelled while running")` |
| subprocess | spawns the worker below; keeps `self._procs[id] = p` and calls `p.poll()` in `status` to reap zombies in long-lived hosts | `os.killpg(rec.pid, SIGTERM)`; the worker turns SIGTERM into `JobCancelled` |

The subprocess runner spawns the worker with:

```python
subprocess.Popen([python, "-m", "wynd.controller.jobs.worker", "--workspace", str(workspace_root),
                  "--job", id, "--checkout", "worktree"],
                 stdin=DEVNULL, stdout=log_fh, stderr=STDOUT, cwd=workspace_root,
                 start_new_session=True, env=os.environ.copy())
```

**Worker** (`jobs/worker.py`): `python -m wynd.controller.jobs.worker --workspace PATH --job ID [--checkout worktree|clone] [--remote URL]`.
1. Install a SIGTERM handler that raises `JobCancelled`.
2. Call `load_into_environ(root)`.
3. Open the stores with `stores_from_env(os.environ, data_dir=root/".wynd")`.
4. Build the checkout backend, call `execute_job(...)`, and exit 0.
5. Exit 70 only for infrastructure errors that happen before the record can be updated.

The default runner is `WYND_JOB_RUNNER=subprocess`. Tests construct `InProcessJobRunner` directly with a custom handler table.

### 6.6 Kind-specific artefacts (what the harness records; web `Job` fields in brackets)

| kind | handler result keys → `artefacts` | web `Job` |
|---|---|---|
| compile | `branch`, `commit`, `base_commit` (05 `JobResult`); `session` and `report` go to their own fields | `branch`, `result_commit`, `session` (§7.8 projection) |
| test_live | `branch`, `commit`, `base_commit`, `recorded_steps` | `branch`, `result_commit` |
| build | `commit`, `image`, `image_digest`, `artefact_dir`, `pushed`, `tests: {passed, failed, total, source: "ran" \| "registry"}` | `build: BuildResult` |
| bake | `path` | – |
| optimise | `branch?`, `commit?`, `base_commit?`, `report` | `branch`, `result_commit` |

### 6.7 Kubernetes seam (post-M5, implemented in `wynd.kube`, per 08 §5)

- **Entry points.** `wynd.job_runners: kube`, `wynd.serving_backends: kube` and `wynd.trigger_backends: kube`. They are selected by `WYND_JOB_RUNNER`, `WYND_SERVING_BACKEND` and `WYND_TRIGGER_BACKEND`. Every factory is called with the keyword arguments `(env, workspace_root, state_dir, stores)`, plus `handlers` for runners (§5.14; 08 proposes `from_env(env, registry)`, reconciled in §13 R9).
- **Runner.**
  - `submit` creates the record, then a Kubernetes `Job` running the controller image. Its command is the job worker: `python -m wynd.controller.jobs.worker --workspace /work/<subdir> --job <id> --checkout clone --remote $WYND_GIT_REMOTE_URL`. 08 names this entrypoint `wynd.controller.jobmain` with `--phase prepare|finalize` for BuildKit init containers. The synthesizer picks one module path, and the worker must accept `--phase` if the kube build flow needs it (§13 R9).
  - `logs` reads the pod logs.
  - `cancel` deletes the Job.
  - `requeue` updates the record and creates a new Job.
  - The runner needs a `RunRegistry` shared across pods. 08 uses the `file` backend on an RWX PVC; the local file backend is already multi-process safe.
- **Checkout.** Pods use `CloneCheckout`, which pushes result branches to the remote. `integrate()` fetches them (§5.9 step 2). With `WYND_GIT_REMOTE` set, the controller pushes the target branch before submitting, so the pod can fetch the ref.
- **Serving** (`KubeServingBackend`): a Deployment and Service per release. `supported_bindings = {"value", "from_env"}` and `mounts_uploads = False`.
- **Triggers** (`KubeTriggerBackend`): `install` renders a CronJob that runs `wynd-kube fire <release_id>`, which calls `POST /api/releases/{id}/trigger {"source": "schedule"}` with the API token. `remove` deletes it, and `reconcile` diffs the CronJobs against the records. `KubeTriggerBackend.start` does nothing, so no in-controller `Scheduler` runs.
- No code in `wynd.controller` imports `wynd.kube`. There are no environment branches: everything goes through entry points and env vars.

---

## 7. Chats (§11)

### 7.1 Model

- A chat is a record with **no process field**. A chat that the Compile or Build button created carries `job_id`.
- Every user message carries the `acting_on` pointer that was sent with it (`SendMessageRequest.acting_on`, the open process or null). This follows §11: "a pointer sent with each chat message, not a property of the chat".
- **Read tools** accept any process id.
- **Write tools** exist only when `acting_on` is set. They are closures bound to it, take **no process argument**, and their descriptions say "acts on <pid>". Anything outside the scope is refused by `DesignService` (`out_of_scope`).
- A turn is one `AgentProvider.run` call.
  - Provider: `WYND_CHAT_PROVIDER`, default `claude-code`.
  - Model: the provider registry's tier `WYND_CHAT_TIER`, default `standard`, which maps to `sonnet`. A ModelProvider fails the turn with `Unavailable("chat requires an AgentProvider")`.
  - Tools: the claude-code provider exposes `AgentRequest.tools` as one in-process MCP server. Harness built-ins are disabled with `builtin_tools=[]` (§13 R1), so model-written shell never runs on the controller host.
- Continuity: each turn sends the persisted history as `context` (the last 30 user/assistant items, compacted). Claude Code sessions are **not** resumed (I-12).

### 7.2 Storage and the event log (`chat/store.py`, `chat/engine.py`, CHAT)

- **Chat record** (`chat/store.py`):
  - Fields: `{id, title, created_at, updated_at, job_id, items: [ChatItem], next_seq, usage_totals}`.
  - It is stored through the release/chat document store (§13 R2).
  - Items are the web `ChatItem` union: user, assistant, tool, commit, job, notice.
  - Deltas are never persisted. An assistant item stores its final `text` and `status`.
- **Event log** (in memory, per chat): a ring buffer of the last 1000 events `(id, event, data)`, where `id` is a per-chat counter that only goes up.
  - Event types are `item`, `delta`, `turn` and `reset`, as in web 07 §12.4.
  - `ChatSnapshot.cursor` is the last event id the snapshot reflects.
  - A reader whose `since` has fallen out of the buffer gets `reset`.

```python
class ChatService:
    def list(self, limit: int = 100) -> list[ChatSummary]: ...
    def create(self, title: str | None = None, *, job_id: str | None = None) -> ChatSummary: ...
    def snapshot(self, chat_id: str) -> ChatSnapshot: ...
    def rename(self, chat_id: str, title: str) -> ChatSummary: ...
    def delete(self, chat_id: str) -> None: ...
    def send(self, chat_id: str, req: SendMessageRequest) -> tuple[str, ChatItem]: ...  # (turn_id, user item); starts a thread
    def cancel(self, chat_id: str) -> None: ...
    def events(self, chat_id: str, since: int) -> tuple[list[ChatEvent], bool]: ...     # (events, reset_needed)
    def run_turn(self, chat_id: str, turn_id: str, text: str, acting_on: str | None) -> None: ...  # blocking core
    def add_job_item(self, chat_id: str, job: Job) -> None: ...
```
`ChatSummary` comes from the record:
- `running_turn` is the active turn id, if any.
- `job` is `{id, kind, process_id, status, pending_questions}` when `job_id` is set. `pending_questions` counts the session's pending questions.

### 7.3 A turn (`run_turn`)

`send(chat_id, req)` does the setup:
1. The chat must exist and `text` must be non-empty.
2. If `acting_on` is set, the process must exist (`NotFound`).
3. If a turn is already running in this chat, raise `TurnInProgress`.
4. Append the user item `{type: "user", text, acting_on}`. `client_id` is echoed for idempotency: a repeated `client_id` returns the existing turn.
5. If this is the chat's first user message, retitle the chat to its first 60 characters.
6. If `acting_on` is set, call `ctl.design.lock(acting_on, chat_id, turn_id)`.
7. Emit `item`, then `turn {status: "running", acting_on, edited: [], error: null}`, then start the thread.

`run_turn` then does the work:
1. Build the tools: `tools = build_tools(ctl, acting_on, turn)`. `turn = TurnState(chat_id, turn_id, edited=set(), cancelled=Event())`.
2. Emit an empty assistant item `{type: "assistant", status: "streaming", text: ""}`.
3. Call the provider:
   ```python
   req = AgentRequest(
       instruction=SYSTEM_INSTRUCTION,
       context={
           "acting_on": acting_on,
           "acting_on_design": ctl.design.get(acting_on).process_file.yaml if acting_on else None,
           "bound_job": ctl.jobs.get(chat.job_id).model_dump() if chat.job_id else None,   # web 07 §9.6
           "history": compact(items[-30:]),       # [{"role", "text", "acting_on", "tools": [names]}]
       },
       input={"message": text},
       output_schema=CHAT_REPLY_SCHEMA,           # {"type":"object","properties":{"reply":{"type":"string"}},"required":["reply"]}
       tools=tools, mcp=[],
       workspace=state_dir / "chats" / chat_id,   # scratch cwd, never the repo
       model_id=registry tiers[WYND_CHAT_TIER],
       builtin_tools=[],
       on_event=turn.on_agent_event,              # text → delta; tool_use/tool_result → tool items
       cancel=turn.cancelled,                     # §13 R1 (optional; see cancel below)
   )
   resp = provider.run(req)
   ```
   The provider's events are handled like this:
   - A `text` event appends to the assistant item and emits `delta {item_id, text}`.
   - A `tool_use` event emits a tool item with `status: "running"` and `write`/`acting_on` from the tool's metadata.
   - A `tool_result` event updates that item to `ok` or `error`, with `summary` (the first 200 characters of the result) and `duration_ms`.
4. Finalise the assistant item:
   - `text = resp.structured_output["reply"]` if it is present, otherwise the accumulated text.
   - `status` is `done`. It is `error` (with `error` set) if the provider raised, and `cancelled` if the turn was cancelled.
   - Add `usage` to `usage_totals`.
5. **Commit boundary.** If `acting_on` is in `turn.edited`:
   - `c = ctl.design.commit(acting_on, reason="chat_turn", summary=first_line(text)[:60], origin="chat", extra_trailers={"Wynd-Chat": chat_id})`.
   - If `c` is set, emit a `commit` item `{process_id, sha, message}`.
   - This runs even when the provider failed after writing.
6. In `finally`:
   - `ctl.design.unlock(acting_on, turn_id)`.
   - Emit `turn {status: "done" | "error" | "cancelled", acting_on, edited: sorted(turn.edited), error}`.
   - Persist the chat.

**`cancel(chat_id)`**:
- It sets `turn.cancelled`. From then on, every tool call returns `{"error": {"code": "cancelled"}}` without doing anything, so no further writes happen, and later deltas are dropped.
- If the provider honours `AgentRequest.cancel` (§13 R1), the run stops early. If not, the result is discarded when it returns.
- Either way the turn ends with status `cancelled`, and the commit boundary still runs for edits made before the cancel.

### 7.4 Tools (`chat/tools.py`, CHAT)

Each tool is a plain function decorated with `wynd.runtime.tools.tool`, and returns a JSON-serialisable dict.
- A wrapper catches `WyndError` and returns `{"error": {"code", "message", "details"}}`, so the model can recover.
- The wrapper checks `turn.cancelled` first.
- It marks write tools with `write=True`, which is metadata the tool items use.

The names match web 07 §9.5.

**Read tools** are always available, take any process, and have `effects=()`.

| tool | signature | returns |
|---|---|---|
| `list_processes` | `() -> dict` | `{"processes": [ProcessSummary]}` |
| `search_processes` | `(query: str, flags: list[str] = []) -> dict` | `{"processes": [ProcessSummary]}` |
| `read_design` | `(process: str) -> dict` | `{"path", "yaml", "doc", "validation"}` for `process.yaml` |
| `read_proto` | `(process: str, step: str) -> dict` | `{"path", "yaml", "doc"}` of that step's proto |
| `read_step_source` | `(process: str, step: str) -> dict` | `StepSource{files: [{path, content}], lock_yaml}` (compiled package; cassettes listed, not read) |
| `get_status` | `(process: str) -> dict` | `ProcessStatus` |
| `validate_process` | `(process: str) -> dict` | `ValidationReport` |
| `list_runs` | `(process: str, limit: int = 10) -> dict` | `{"runs": [Run]}` (outputs truncated to 2 KB) |
| `read_trace` | `(run_id: str) -> dict` | the `render_tree` text plus `run.end` (outputs and error), truncated to 16 KB |
| `list_jobs` | `(process: str \| None = None, limit: int = 10) -> dict` | `{"jobs": [Job]}` |
| `read_job` | `(job_id: str) -> dict` | `Job` (with its session projection) |

**Write tools** are available only when `acting_on` is set, are bound to it, and have `effects=("filesystem",)`.

| tool | signature | behaviour |
|---|---|---|
| `edit_design` | `(doc: dict) -> dict` | `ctl.design.save(acting_on, SaveRequest(writes=[{path: <process.yaml>, base_revision: <current>, doc}], commit=None), origin="chat", lock_owner=(chat_id, turn_id))`; adds `acting_on` to `turn.edited`; returns `{"revision", "validation"}` |
| `edit_proto` | `(step: str, doc: dict) -> dict` | the same, for `<process>/proto/<step>.yaml` or the referenced step-root proto (in scope only); creates the file if needed |
| `start_compile` | `(accept_defaults: bool = False) -> dict` | commits pending edits first (`design.commit(reason="before_job", origin="chat")`), then `ctl.jobs.submit_compile(acting_on, accept_defaults=…)` with `job.chat_id = chat_id`; `add_job_item` |
| `start_build` | `() -> dict` | the same pattern with `submit_build` |

The chat reads the current revision immediately before writing, so a design lock taken for this turn never conflicts with the chat's own writes. A 409 happens only if someone else wrote in between, and the web cannot, because the design is locked while the turn runs.

### 7.5 System instruction (`chat/prompt.py`, CHAT): outline

`SYSTEM_INSTRUCTION` is one constant string of about 120 lines:
1. **Role.** Wynd's design assistant. It helps a user design processes as graphs of steps, starts compiles and builds, and explains status, traces and compile questions.
2. **Scope.**
   - It can read any process.
   - It can change only `context.acting_on`, and only with the write tools.
   - If `acting_on` is null it cannot change anything; it says so and suggests opening a process.
   - It never claims to have edited something unless a write tool returned without an error.
3. **Format primer**, condensed from spec §6.1, §6.2, §3.4 and §3.4.1:
   - process and proto-step YAML fields;
   - edges, `to:` branches, `when:`/`with:`/`limits:`;
   - the expression language and its builtins;
   - `$exit.<name>` and the `use:` forms;
   - "examples live on proto-steps and become tests".
4. **Editing discipline.**
   - Read before writing, and write whole documents.
   - Call `validate_process` after edits and fix any errors before replying.
   - Prefer adding examples over writing schemas.
5. **Reply.** Put the final answer in `reply` as concise Markdown that says which files changed and which jobs started. When `bound_job` is set, answer questions about that job from its session and report.

### 7.6 Compile and build buttons (job-bound chats)

`POST /api/processes/{pid}/compile` and `POST /api/processes/{pid}/build` (web 07 §9.6) do three things:
1. `job = ctl.jobs.submit_…(pid)`.
2. `chat = ctl.chats.create(f"Compile {pid}" or f"Build {pid}", job_id=job.id)`, with `job.chat_id = chat.id` stored on the job record.
3. `add_job_item(chat, job)`, which adds the first item `{type: "job", job_id, kind, process_id}`.

They return `{job, chat}`. From then on the chat's composer talks to the assistant, and the assistant's context includes `bound_job`.

### 7.7 Usage accounting (§15)

- Assistant items carry `usage`. `ChatSummary` does not show it, but `snapshot.chat.usage_totals` holds the totals.
- Jobs store `duration_ms`, `cpu_ms` and `usage`.
- Runs are accounted for by their traces.

### 7.8 Compile-session view (`compile_view.py`, CHAT)

The web renders `Job.session` in its own `CompileSession` shape (07 §12.2). The compiler stores `SessionData` (05 §6.5). The controller projects the second into the first with pure functions:

```python
def session_dto(s: dict | None) -> CompileSession | None: ...
def answer_text(q: dict, a: AnswerRequest) -> str: ...     # used by JobService.answer (§5.8)
```

**State**

| compiler `state` | web `state` |
|---|---|
| `new`, `running`, `ready` | `"compiling"` |
| `awaiting_input` | `"awaiting_input"` |
| `done` | `"done"` |
| `failed`, `cancelled` | `"failed"` |

**`steps[]`** come from `s["steps"]` (`StepProgress`) joined with `s["report"]` for each step:
- `phase` maps as follows:
  - `pending`, `schema`, `proposals`, `awaiting_input` and `deciding` → `pending`
  - `generating` → `generating`
  - `testing` → `testing`
  - `done` → `done`
  - `skipped` → `skipped`
  - `failed` → `failed`
- `decision` is `{kind, tier, thinking, reason, split}`, taken from the report's decision for that node.
- `tests`, `attempts` and `skipped_reason` come from the report.

**`questions[]`**

| compiler kind | web kind | fields |
|---|---|---|
| `confirm_example` | `"example_proposal"` | `proposed = proposal` |
| `confirm_schema`, `clarify`, `need_examples` | `"clarification"` | `text = text + "\n\n" + detail` |

On both: `step = q["step"]`, `status = "answered" if q["status"] == "answered" else "pending"`, `answer = {"text": q["answer"]}` when answered, and `asked_at` is the time of the matching `question` event.

**`inferred_schemas`**: for each `confirm_schema` question, `{step: Interface(inputs, exits, source="inferred")}` built from `q["proposal"]`.

**`events`**: `[{at, type, step: node, text}]`, taken from `s["events"]`.

`answer_text` is the inverse used for answers (§5.8 step 2). With it, web answers, CLI answer files and compiler answer strings all meet in `apply_answers`.

---

## 8. `wynd serve-api`: the HTTP API (API owner)

**Normative contract.** Web 07 §12 is adopted as written: 39 routes, the DTOs in §12.2, save semantics in §12.3, chat SSE in §12.4, run SSE in §12.5, and the error codes in §12.0. The controller adds the routes marked **(+)** below. The web doesn't need them; the CLI parity, webhooks, OAuth and registries do. Pydantic response models in `models.py` mirror 07 §12.2. `tests/test_web_contract.py` checks every web fixture against its model (07 P-FIXTURES).

### 8.1 App factory

```python
def create_app(ctl: Controller, *, web_dist: Path | None = None, api_token: str | None = None,
               cors_origins: Sequence[str] = ("http://localhost:5173", "http://127.0.0.1:5173"),
               scheduler: bool = True) -> FastAPI: ...

def serve(ctl: Controller, *, host: str = "127.0.0.1", port: int = 8780, web_dist: Path | None = None,
          api_token: str | None = None, cors_origins: Sequence[str] | None = None, scheduler: bool = True,
          log_level: str = "info") -> None: ...      # uvicorn.run(create_app(...), host=host, port=port) — one worker
```

- **Lifespan.**
  - Startup: when `scheduler` is set, `ctl.ctx.triggers.start(ctl)`. For the default `scheduler` backend this starts the `Scheduler` thread; if another process holds the scheduler lock, it logs a warning. Then `triggers.reconcile(ctl.releases.list())`.
  - Startup: re-`ensure` every enabled release in a background thread, so a restarted controller serves its releases warm.
  - Shutdown: `triggers.stop()`, then `ctl.close()`.
- **Auth.** When `api_token` (or `WYND_API_TOKEN`) is set, every `/api/*` route except `/api/health` and `/api/oauth/callback` requires `Authorization: Bearer <token>`. Streams may pass `?access_token=<token>` instead. Anything else gets 401 `unauthorized`. `/hooks/*` uses release secrets (§5.14). Static files are public.
- **CORS.** `CORSMiddleware(allow_origins=cors_origins or env WYND_CORS_ORIGINS.split(","), allow_methods=["*"], allow_headers=["*"], allow_credentials=False)`. The Vite dev server runs on 5173.
- **Errors.** One handler maps `WyndError` to `{"error": {"code", "message", "details", "hint"}}` with `exc.http`. FastAPI's own request-validation errors are re-shaped to `{"error": {"code": "invalid", …}}` with status 422 (07 §12.0).
- **Process ids** contain `/`. Following 07 §12.0, routes use `{process_id:path}`, and the suffixed routes (`/design`, `/compile`, `/build`, `/builds`, `/interface`, and the (+) ones: `/status`, `/validate`, `/test`, `/test-live`, `/bake`, `/optimise`, `/history`, `/env`, `/files/…`) are registered **before** `GET /api/processes/{process_id:path}`.
  - This is ambiguous when a process id's last segment equals one of those words. The spec area should reserve those words as final id segments (§13 R8). My earlier `/-/` separator scheme would also remove the ambiguity; I drop it here in favour of the web contract.
- **Sync handlers** run in the threadpool. Long work runs in background threads or job runners, and clients observe it by polling or SSE.

### 8.2 SSE (`api/sse.py`)

- Headers: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`.
- The first line is `retry: 2000`. Each frame is `id: <n>\nevent: <type>\ndata: <single-line json>\n\n`. A `: ping\n\n` comment is sent every 15 s.
- Resume uses `Last-Event-ID` or `?since=`.
  - Run streams use `seq` as the id.
  - Chat streams use the per-chat event counter.
  - The (+) job stream uses the byte offset.
- Implementation: one generic `poll_stream(fetch: Callable[[int], tuple[list[tuple[int, str, dict]], bool]], since)`. It is an async generator:
  1. Call `await run_in_threadpool(fetch, cursor)`.
  2. Yield the frames.
  3. If `done`, yield `end` and stop.
  4. Otherwise `await asyncio.sleep(0.25)`.

  It exits when `await request.is_disconnected()`. Every stream polls the underlying store, so there is no pub/sub layer.

### 8.3 Routes

**Web contract (07 §12.1).** Controller call per route:

| # | Route | Controller call |
|---|---|---|
| 1 | `GET /api/health` | `{"ok": true}` |
| 2 | `GET /api/meta` | `ctl.meta()` |
| 3 | `GET /api/processes?q=&flags=` | `{"processes": ctl.processes.list(q, flags.split(","))}` |
| 4 | `POST /api/processes` | `ctl.processes.new(id, goal=, root=)` → 201 |
| 5 | `GET /api/processes/{pid}` | `ctl.processes.get(pid)` |
| 6 | `GET /api/processes/{pid}/design` | `ctl.design.get(pid)` |
| 7 | `POST /api/processes/{pid}/design` | `ctl.design.save(pid, SaveRequest, origin="web")`; 403 `out_of_scope`, 409 `revision_conflict`, 423 `design_locked` |
| 8 | `POST /api/expressions/validate` | `ctl.processes.check_expr(req)` |
| 9 | `GET /api/steps` | `{"steps": ctl.processes.step_catalog()}` |
| 10 | `GET /api/providers` | `{"providers": ctl.registries.list_providers()}` |
| 11 | `POST /api/processes/{pid}/compile` | §7.6 → 201 `{job, chat}` |
| 12 | `POST /api/processes/{pid}/build` | §7.6 → 201 `{job, chat}`; 409 `dirty_tree` |
| 13 | `GET /api/processes/{pid}/builds` | `{"builds": ctl.processes.builds(pid)}` |
| 14 | `GET /api/processes/{pid}/interface?commit=` | `ctl.processes.interface(pid, commit)` |
| 15 | `GET /api/jobs?process_id=&active=` | `{"jobs": ctl.jobs.list(process_id=, active=)}` |
| 16 | `GET /api/jobs/{job_id}` | `ctl.jobs.get(id)` |
| 17 | `GET /api/jobs/{job_id}/logs?offset=` | `ctl.jobs.logs(id, offset)` → `{text, offset, done}` |
| 18 | `POST /api/jobs/{job_id}/answers` | `ctl.jobs.answer(id, [AnswerRequest])` → `Job` |
| 19 | `POST /api/jobs/{job_id}/integrate` | `ctl.jobs.integrate(id)` → `Job` (idempotent; 409 `dirty_tree`) |
| 20 | `POST /api/jobs/{job_id}/cancel` | `ctl.jobs.cancel(id)` |
| 21 | `GET /api/chats` | `{"chats": ctl.chats.list()}` |
| 22 | `POST /api/chats` | `ctl.chats.create(title)` → 201 |
| 23 | `GET /api/chats/{chat_id}` | `ctl.chats.snapshot(id)` |
| 24 | `PATCH /api/chats/{chat_id}` | `ctl.chats.rename(id, title)` |
| 25 | `DELETE /api/chats/{chat_id}` | `ctl.chats.delete(id)` → 204 |
| 26 | `POST /api/chats/{chat_id}/messages` | `ctl.chats.send(id, req)` → 202 `{turn_id, item}`; 409 `turn_in_progress` |
| 27 | `POST /api/chats/{chat_id}/cancel` | `ctl.chats.cancel(id)` → `{"ok": true}` |
| 28 | `GET /api/chats/{chat_id}/events?since=` | SSE from `ctl.chats.events` (`item`/`delta`/`turn`/`reset`) |
| 29 | `POST /api/runs` | `ctl.runs.start(CreateRunRequest)` → 201 `Run` |
| 30 | `GET /api/runs?process_id=&release_id=&limit=` | `{"runs": ctl.runs.list(...)}` |
| 31 | `GET /api/runs/{run_id}` | `ctl.runs.get(id)` |
| 32 | `GET /api/runs/{run_id}/events?since=` | SSE: `trace` per normalised event (§5.10), then `end {}` once `run.end` has been sent and the record is terminal. If no event appears within 10 s, it sends `end {"error": "not_found"}` |
| 33 | `POST /api/uploads` | raw body, header `X-Wynd-Filename` → `ctl.uploads.put` → 201 `{"path"}`; the body is capped at 100 MB (413 beyond that) |
| 34 | `GET /api/releases?process_id=` | `{"releases": ctl.releases.list(process_id=)}` |
| 35 | `POST /api/releases` | `ctl.releases.create(req)` → 201; 409 `not_built`/`env_unbound` |
| 36 | `PATCH /api/releases/{id}` | `ctl.releases.update(id, patch)` |
| 37 | `DELETE /api/releases/{id}` | `ctl.releases.delete(id)` → 204 |
| 38 | `POST /api/releases/{id}/trigger` | `ctl.releases.trigger(id, inputs, source="manual")` → 201 `Run` |
| 39 | `GET /api/releases/{id}/env-check` | `ctl.releases.env_check(id)` |

**(+) Controller additions.** Same conventions. The CLI calls the matching library methods in-process; these routes exist for remote use and for completeness.

| Route | Request | Response |
|---|---|---|
| `GET /api/processes/{pid}/status` | – | `ProcessStatus` (+ `dirty`) |
| `POST /api/processes/{pid}/validate` | – | `ValidationReport` |
| `POST /api/workspace/validate` | – | `{"reports": {pid: ValidationReport}}` |
| `GET /api/processes/{pid}/history?limit=50` | – | `{"commits": [CommitInfo]}` |
| `GET /api/processes/{pid}/files/{path:path}` | – | `FileContent` (any closure file, read-only) |
| `GET /api/processes/{pid}/env` | – | `EnvCheckReport` (§10.7) |
| `POST /api/processes/{pid}/test` | – | `TestReport` (replay, synchronous) |
| `POST /api/processes/{pid}/test-live` | `{"steps"?: [str]}` | 201 `{job, chat}` |
| `POST /api/processes/{pid}/bake` | – | 201 `Job` |
| `GET/POST /api/processes/{pid}/optimise` | as in 08 §3.8 (`GET` → `OptimiseReport`; `POST {"units","max_runs","min_runs"}` → `{"job_id","changes"}`) | routes live in `wynd.controller.optimise` (A8-opt) and are mounted by `api/app.py` |
| `GET /api/jobs/{job_id}/events?offset=` | – | SSE: `log {text, offset}`, `status {status}` on each change, `end {job}` when the job is terminal or `awaiting_input` |
| `GET /api/jobs/{job_id}/artefacts` | – | `{"artefacts": {...}}` (the JobRunner `artefacts()`) |
| `GET /api/releases/{id}/fires?limit=50` | – | `{"fires": [TriggerFire]}` |
| `POST /hooks/releases/{id}` | body = process inputs (JSON object); `Authorization: Bearer <secret>` or `X-Wynd-Token` | 201 `Run`; 401; 404; 409 disabled; 415 when the body is not JSON |
| `GET /api/registries/mcp` | – | `{"items": [McpServerEntry]}` |
| `POST /api/registries/mcp` | `McpServerEntry` | 201 `McpServerEntry` (upsert) |
| `DELETE /api/registries/mcp/{name}` | – | `RemoveResult` |
| `POST /api/registries/mcp/{name}/oauth/start` | `{"return_to"?: str}` | `{"authorize_url"}`; `redirect_uri = <request base>/api/oauth/callback` |
| `GET /api/oauth/callback?code&state` | (no bearer; the state is the credential) | 302 to `return_to?mcp=<name>&ok=1`, or a small HTML page |
| `POST /api/providers` | `{"name", "tiers"?, "env"?}` | 201 `ProviderEntry` |
| `DELETE /api/providers/{name}` | – | `RemoveResult` |
| `GET /api/registries/images` | – | `{"items": [ImageRegistryEntry]}` |
| `POST /api/registries/images` | `ImageRegistryEntry` | 201 |
| `DELETE /api/registries/images/{name}` | – | `RemoveResult` |

**Static web** (`api/static.py`, web 07 C-CTL-STATIC):

```python
def web_dist_dir(explicit: Path | None = None) -> Path | None: ...
    # explicit arg, else $WYND_WEB_DIST, else importlib.resources.files("wynd.controller") / "web_dist";
    # the first that contains index.html
def mount_web(app: FastAPI, dist: Path | None) -> None: ...
    # registered AFTER all /api and /hooks routes: StaticFiles(directory=dist, html=True) at "/";
    # dist None: GET "/" returns the "Web UI not built: run `npm --prefix packages/web ci && npm --prefix packages/web run build`" page
```

`packages/controller/pyproject.toml` adds `[tool.hatch.build.targets.wheel] artifacts = ["src/wynd/controller/web_dist/**"]`. The directory is git-ignored build output that `npm --prefix packages/web run build` writes (07 P-BUNDLE).

### 8.4 Examples

**Compile button**

```http
POST /api/processes/process_supplier_invoice/compile
{}

201
{"job": {"id":"job_20260922T100000123_4f1c2e","kind":"compile","process_id":"process_supplier_invoice",
         "ref":"3f9c1e2b…","status":"queued","created_at":"2026-09-22T10:00:00Z","started_at":null,"finished_at":null,
         "branch":null,"result_commit":null,"session":null,"build":null,"integration":null,"error":null,
         "usage":null,"chat_id":"chat_20260922T100000130_a9b8c7"},
 "chat": {"id":"chat_20260922T100000130_a9b8c7","title":"Compile process_supplier_invoice",
          "created_at":"2026-09-22T10:00:00Z","updated_at":"2026-09-22T10:00:00Z","running_turn":null,
          "job":{"id":"job_20260922T100000123_4f1c2e","kind":"compile","process_id":"process_supplier_invoice",
                 "status":"queued","pending_questions":0}}}
```

**Answering one question.** The job stays in `awaiting_input` while other questions are still pending.

```http
POST /api/jobs/job_20260922T100000123_4f1c2e/answers
{"question_id":"extract.example1","decision":"confirm"}

200  { …Job…, "status":"awaiting_input", "session": {"state":"awaiting_input", "questions":[{"id":"extract.example1","status":"answered",…}, {"id":"extract.schema","status":"pending",…}], …} }
```

**Integration**

```http
POST /api/jobs/job_20260922T100000123_4f1c2e/integrate
{}

200 { …Job…, "status":"succeeded",
      "integration":{"mode":"rebased","branch":"wynd/compile/process_supplier_invoice/job_20260922T100000123_4f1c2e",
                     "target":"main","head":"9a1b2c3…","skipped_commits":2,"conflicts":[],"pr_url":null,
                     "at":"2026-09-22T10:07:12Z"} }
```

**Webhook**

```http
POST /hooks/releases/rel_20260922T110000000_77aa01
Authorization: Bearer s3cr3t-from-INVOICE_HOOK_SECRET
{"pdf_path":"/wynd/uploads/9f…/inv1042.pdf"}

201 {"id":"run_20260922T110501000_1c2d3e","process_id":"process_supplier_invoice","commit":"71aa3c0…","mode":"image",
     "target":{"kind":"release","release_id":"rel_20260922T110000000_77aa01"},"release_id":"rel_20260922T110000000_77aa01",
     "trigger":"webhook","status":"running","inputs":{"pdf_path":"/wynd/uploads/9f…/inv1042.pdf"},"exit":null,
     "outputs":null,"error":null,"started_at":"2026-09-22T11:05:01Z","finished_at":null,"duration_ms":null,"usage":null}
```

---

## 9. CLI (`wynd.cli`, CLI owner)

### 9.1 Structure and conventions

```python
# main.py
app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)
for name, sub in [("env", env_app), ("mcp", mcp_app), ("provider", provider_app), ("registry", registry_app),
                  ("base", base_app), ("jobs", jobs_app), ("release", release_app)]:
    app.add_typer(sub, name=name)

@app.callback()
def root(ctx: typer.Context,
         workspace: Path | None = typer.Option(None, "--workspace", "-C", envvar="WYND_WORKSPACE"),
         version: bool = typer.Option(False, "--version", is_eager=True, callback=_print_version)) -> None:
    ctx.obj = CliState(workspace=workspace)      # the Controller is built lazily by context.get_controller(ctx)

def command(target: typer.Typer, name: str | None = None, **kw):   # decorator: registers the command and maps errors
    # wraps fn:
    #   except WyndError as e: err(f"error: {e.message}"); e.hint and err(f"hint: {e.hint}")
    #                          (with --json, also print {"error": {...}} to stdout); raise typer.Exit(e.exit)
    #   except KeyboardInterrupt: raise typer.Exit(130)

def main() -> None:
    app()
```

**Output.**
- Human-readable output goes to stdout. Progress, streamed job logs and compile session events go to stderr.
- With `--json`, stdout carries exactly one JSON document: the model's `model_dump(mode="json")`, or `{"items": [...]}` for lists.
- Tables are plain columns separated by two spaces (`output.table()`), with no colour dependency.
- Short shas are 7 characters. Durations print as `14ms`, `1.21s` or `2m03s`. Usage prints as `haiku 812→64 tok $0.0011`.

**Exit codes.**

| code | meaning |
|---|---|
| 0 | Success. This includes a run that ends in a declared non-error exit such as `not_an_invoice`. |
| 1 | The operation ran and failed: validation errors, test failures, a failed job, a run ending in `$exit.error`, or `env check` finding missing required variables. |
| 2 | Usage error (click) or `Invalid` input. |
| 3 | Precondition, lookup or conflict: not a workspace, unknown process or job, dirty closure, detached HEAD, not built, env missing for serve/run, Docker or provider unavailable, version mismatch. |
| 4 | Compile stopped at `awaiting_input` with unanswered questions. Compiler 05 §6.7 proposes 3 here, which clashes with preconditions (§13 R10). |
| 5 | The job succeeded but its branch was left for review (`pr_branch`) and not integrated. |
| 130 | Interrupted. Subprocess jobs keep running; the CLI prints `job continues: wynd jobs logs <id> -f`. |

**Inputs** (`inputs.py`), used by `run`, `release create` and `release run`:
- `--input KEY=VALUE` can repeat. The value is parsed with `yaml.safe_load`, so `3` becomes an int and `true` a bool.
- `--inputs '<json object>'`.
- `--inputs-file PATH|-`, in JSON or YAML.
- They merge in this order: file, then `--inputs`, then `--input`. The result must be a mapping.
- **`path`-typed fields are made absolute against the current directory** (08 C16). The type comes from `ctl.processes.interface(pid).inputs` (`x-wynd-type: path`).

### 9.2 Commands

Every command is wrapped with `@command`. `<id>` is a process id: §10 uses ids, not paths.

**`wynd init [PATH=.] [--no-commit] [--json]`** (M1)
Runs `init_workspace(PATH, commit=not no_commit)` and prints the created files and the commit. It works outside a workspace. Exit 3 if `PATH` is already a workspace.

**`wynd new <id> [--root ROOT] [--goal TEXT] [--no-commit] [--json]`** (M1)
Runs `ctl.processes.new(...)`. Prints the created paths, then `next: edit processes/<id>/proto/first.yaml, then wynd validate <id>`.

**`wynd validate [<id>] [--json]`** (M1)
Validates one process, or every process when `<id>` is omitted. Trace statistics for latency warnings are collected by the controller (08 C16).
- Each diagnostic prints as `ERROR  <code>  <file>:<line> <loc>  <message>`, or `WARN  …`.
- A summary follows: `<n> errors, <m> warnings`.
- Exit 1 if there is any error.

**`wynd status [<id>] [--json]`** (M1; all flags from M4)
- Without `<id>`, prints a table: `PROCESS  HEAD  DIRTY  DESIGN  COMPILED  BUILT  RELEASED  TESTS`. `RELEASED` shows `yes (71aa3c0, 2 behind)` or `-`.
- With `<id>`, prints:
  - the head commit and its subject,
  - the four flags,
  - `design_steps`, with the tests status,
  - the build at HEAD,
  - each release as `<id> <trigger> @<short> behind=<n> <state>`.

**`wynd search <query…> [--flag design|compiled|built|released]… [--json]`** (M4)
Prints `id  flags  first-match`, sorted by score.

**`wynd compile <id> [--answers FILE] [--answer QID=TEXT]… [--accept-defaults] [--resume JOB_ID] [--max-revisions N] [--no-wait] [--no-integrate] [--timeout SECONDS] [--quiet] [--json]`** (M3; flags follow compiler 05 §6.7)
1. Build the answers. The answers file is a YAML mapping of question id to answer text, in the format of compiler 05 §6.7. `--answer` pairs override it.
2. Submit or resume:
   - With `--resume`: `job = ctl.jobs.answer(JOB_ID, answers, accept_defaults=…)`.
   - Otherwise: `job = ctl.jobs.submit_compile(id, answers=answers, accept_defaults=…, max_revisions=…)`. Pre-answers go in with the submit.
   - A precondition error exits 3.
3. Print `submitted <job> (compile) at <ref7> on <branch>`. With `--no-wait`, print the job and exit 0.
4. Wait with `ctl.jobs.wait(job.id, on_event=print_session_event, on_log=(stderr if --verbose))`. Session events print as `[<node>] <text>`.
5. Handle the outcome:
   - **`awaiting_input`**: print the questions block (below) and **exit 4**. It never prompts; this is §9's "fail on unanswered questions".
   - **`failed`** or **`cancelled`**: print the report summary, the error, and the branch if there is one. Exit 1.
   - **`succeeded`**: print the report summary (per step: decision, tier, reason, tests `passed/total`).
6. Integrate, unless `--no-integrate`: `job = ctl.jobs.integrate(job.id)`. Print one of:
   - `fast-forwarded main 3f9c1e2..8d0e4aa`
   - `rebased onto main past 2 unrelated commits → 9a1b2c3`
   - `nothing to integrate`
   - `left for review: <branch> (conflicts: <paths>)`, followed by `hint: git push origin <branch> && gh pr create --head <branch>`. This case exits **5**.

   If integration raises `dirty_tree`, the CLI exits 3 and lists the paths. The job stays integrable later with `wynd jobs integrate <job>`.

Questions block (same content as compiler 05 §6.7):
```
Compile of process_supplier_invoice needs input (job job_20260922T100000123_4f1c2e, 2 questions):

[extract.schema] I read your examples as describing this step. Is this right?   (default: yes)
    extract_invoice_fields takes:
      - invoice_text (text): the full text of the invoice
    ...
[extract.example1] What should happen if the invoice has no due date?   (default: accept)
    Proposed example: ...

Answer in a file (question id -> answer) and resume:
    wynd compile process_supplier_invoice --resume job_20260922T100000123_4f1c2e --answers answers.yaml
Or accept every default:
    wynd compile process_supplier_invoice --resume job_20260922T100000123_4f1c2e --accept-defaults
```

**`wynd test <id> [--live] [--step NAME]… [--no-wait] [--no-integrate] [--quiet] [--json]`** (replay M1, `--live` M3)
- **Replay:** `report = ctl.processes.test(id, log=stderr)`.
  - Prints one line per step: `name  <passed>/<total>  <dur>`.
  - Then `process (integration)  …`.
  - Then either `recorded for <commit7> (process HEAD)` or `not recorded: uncommitted changes in <paths>`.
  - Exit 1 on any failure.
- **`--live`:** `job = ctl.jobs.submit_test_live(id, steps=…)`, then wait and integrate as `compile` does. It uses the same exit codes: 1, 3 and 5.

**`wynd run <id> [--local | --image] [--commit SHA] [--input K=V]… [--inputs JSON] [--inputs-file PATH|-] [--full] [--json]`** (`--local` M1, `--image` M2)
- `--local` is the default.
- `--image` runs the build at process HEAD, or at `--commit`.
- Trace events stream through `on_event`. Each step is printed when its `step.end` arrives, as a tree line in the §5.10 format, indented by path depth.
- At the end it prints `exit: <name>` and the outputs as pretty JSON. An `error` exit also prints the `ProcessError` (step, cause, message) and `trace: wynd trace <run_id>`.
- `--json` prints only the `Run`.
- Exit codes: 1 for the `error` exit, otherwise 0. `NotBuilt`, `EnvMissing` and `Unavailable` exit 3.

**`wynd trace <run_id> [--full] [--follow] [--json]`** (M1)
- Prints `render_tree(build_tree(ctl.runs.events(run_id)), full=…)`.
- `--follow` tails the run until it ends, then prints the tree.
- `--json` prints the raw events.
- Agentic edge verdicts (M5) render as in 08 §4.7: `validate.done → save  check: NOT TAKEN — "…" (claude-code/cheap, 2.9s, $0.0011)`.

**`wynd build <id> [--registry NAME] [--push/--no-push] [--no-wait] [--quiet] [--json]`** (M2)
1. `job = ctl.jobs.submit_build(...)`. On a dirty closure this exits 3 with `error: build refuses a dirty tree: <paths>`.
2. Stream the log and wait.
3. On success, print `built <id>@<commit7>`, then:
   - `image <ref>`,
   - `digest <sha256:…>` if the image was pushed,
   - `tests <passed>/<total> (<ran|reused recorded result>)`,
   - `artefacts .wynd/build/<id>/<commit>/`.
4. Exit 1 on failure.

**`wynd bake <id> [--output PATH] [--no-wait] [--json]`** (M2)
Submits a bake job and waits. On success it copies `artefacts.path` to `--output` when one is given.

**`wynd env check <id> [--env-file PATH]… [--json]`** (M1)
Prints a table: `VAR  REQUIRED  SET  SOURCE  USED BY  DESCRIPTION`. Below it, one of `manifest: build @<commit7>` or `manifest: assembled from step locks`. Exit 1 if a required variable is missing.

**`wynd serve <image> [--port N] [--env-file PATH]… [--detach/-d] [--timeout 120] [--name NAME] [--stop] [--json]`** (M2)
- With `--stop`: `ctl.serve.stop(image_or_name)`. Exit 3 if nothing matched.
- Otherwise: `sc = ctl.serve.serve(image, …)`, then print `serving <process>@<commit7> at <url> (container <name>)`.
  - With `--detach`, exit 0 and leave the container registered as warm.
  - Without it, follow the container's logs (`wynd.controller.docker.logs_follow`) until Ctrl-C, then stop the container and exit 0.
- Works outside a workspace. In that case nothing is registered and no `.env` is read.

**`wynd serve-api [--host 127.0.0.1] [--port 8780] [--web-dist PATH] [--no-scheduler] [--token TOKEN] [--cors-origin ORIGIN]… [--log-level info]`** (M4; port per web 07 C-CLI-1)
- Runs `wynd.controller.api.serve(ctl, …)`.
- `--token` falls back to `WYND_API_TOKEN`.
- A non-loopback `--host` without a token prints a warning, but the command still runs.

**`wynd mcp add <name> --url URL [--transport http|sse] [--auth-env VAR] [--header NAME=ENVVAR]…`**, **`wynd mcp list [--json]`**, **`wynd mcp remove <name>`** (M1)
- `list` prints `NAME  TRANSPORT  URL  AUTH`. `AUTH` is `env:GITHUB_TOKEN`, `oauth` or `-`.
- `remove` warns `referenced by: …` when compiled steps still reference the server.

**`wynd provider add <name> [--tier cheap=MODEL]… [--env VAR]…`**, **`wynd provider list [--json]`**, **`wynd provider remove <name>`** (M1)
- `list` prints `NAME  KIND  CHEAP  STANDARD  STRONG  ENV`.
- `add` on an existing name updates only the tiers you pass.
- Changing a mapping changes cassette keys, so the command prints a reminder to `wynd test --live` (08 docs).

**`wynd registry add <name> <url> [--username-env VAR] [--password-env VAR] [--insecure] [--default]`**, **`wynd registry list`**, **`wynd registry remove <name>`** (M2)
These manage image registries; `<url>` is positional, per 08 C16.

**`wynd base build <ver> [--variant slim|alpine|all]`**, **`wynd base publish <ver> --registry NAME [--variant …]`** (M2)
- Call `wynd.controller.base` and log to stderr.
- `VersionMismatch` exits 3.
- No workspace is needed.

**`wynd jobs list [--process ID] [--kind K] [--status S] [--active] [--limit N] [--json]`**, **`wynd jobs show <job>`**, **`wynd jobs logs <job> [--follow/-f]`**, **`wynd jobs answer <job> [--answers FILE] [--answer QID=TEXT]… [--accept-defaults]`**, **`wynd jobs cancel <job>`**, **`wynd jobs integrate <job>`** (M2; `answer` and `integrate` M3)
- `list` prints `JOB  KIND  PROCESS  STATUS  REF  AGE  DURATION  INTEGRATION`.
- `answer` applies the answers and prints the resulting status without waiting: still `awaiting_input` (with the remaining questions) or `queued`.
- `integrate` uses the same exit codes as `compile` (0 or 5, and 3 on `dirty_tree`).

**`wynd release create <id> --commit SHA|HEAD --trigger manual|schedule|webhook [--cron EXPR] [--tz ZONE] [--secret-env VAR] [--input K=V]… [--inputs …] [--inputs-file …] [--env VAR=VALUE]… [--env-from VAR=HOSTVAR]… [--disabled] [--json]`** (M4, CLI parity decided **yes**)
- Runs `ctl.releases.create(CreateReleaseRequest(...))`.
- `HEAD` resolves to the process HEAD.
- Prints the release, the webhook path `POST <base>/hooks/releases/<id>` for webhook releases, and `next fire: …` for schedules.
- Always adds `note: schedules fire while 'wynd serve-api' is running (trigger backend: scheduler)`.

**`wynd release list [<id>] [--json]`**, **`wynd release show <release>`**, **`wynd release run <release> [--input …] [--follow]`**, **`wynd release enable|disable <release>`**, **`wynd release delete <release>`**, **`wynd release fires <release>`** (M4)
`run` is the manual trigger. With `--follow` it prints the mirrored trace the way `wynd run` does.

**`wynd optimise <id> [--max-runs 200] [--min-runs 20] [--apply] [--unit NAME]… [--no-wait] [--json]`** (M5)
- The module is `packages/cli/src/wynd/cli/commands/optimise.py`, **owned by A8-opt** (08 §3.8). CLI registers it in `main.py`.
- Without `--apply` it prints the report. No job runs.
- With `--apply` it submits the job, waits, and integrates like `compile`.

---

## 10. Record formats and on-disk layout

Web DTOs are defined in web 07 §12.2, and the controller's pydantic models mirror them. This section covers only the formats the controller itself owns.

### 10.1 Job record (stored in the `RunRegistry` as a runtime `RunRecord`; mapping in `jobs/records.py`)

The runtime `RunRecord` (02 §9.2) has fixed fields plus `meta`. A `JobRecord` maps onto it like this:

| `JobRecord` field | `RunRecord` field |
|---|---|
| `kind` | `kind`, via `compile→compile`, `test_live→test`, `build→build`. `bake` and `optimise` need to be added to runtime `RunKind` (§13 R11). The exact job kind is always kept in `meta.job_kind`. |
| `process_id` | `process` |
| `ref`, `inputs`, `status`, `created_at`, `started_at` | same names |
| `finished_at` | `ended_at` |
| `duration_ms`, `usage`, `session` | same names |
| `error` | `error` |
| `artefacts` (dict) | `meta.artefacts`. `RunRecord.artefacts` holds the pointer `.wynd/jobs/<id>/` |
| `runner`, `handler`, `attempt`, `pid`, `host`, `cpu_ms`, `report`, `integration`, `chat_id` | `meta.*` |

```json
{"id":"job_20260922T100000123_4f1c2e","kind":"compile","process":"process_supplier_invoice","status":"succeeded",
 "created_at":"2026-09-22T10:00:00.123Z","updated_at":"2026-09-22T10:07:12.400Z",
 "started_at":"2026-09-22T10:05:01.002Z","ended_at":"2026-09-22T10:06:58.990Z",
 "ref":"9ab01d77c1e0e5a4b3f2d1c0b9a8f7e6d5c4b3a2",
 "inputs":{"process":"process_supplier_invoice","target_branch":"main",
           "options":{"accept_defaults":false,"max_revisions":3},"answers":{},"session":{"…":"on attempt 2 (resume_request)"}},
 "error":null,"artefacts":".wynd/jobs/job_20260922T100000123_4f1c2e/","duration_ms":307100,
 "usage":{"input_tokens":61022,"output_tokens":17310,"cost_usd":2.61,"latency_ms":402000},
 "session":{"session_version":1,"id":"job_20260922T100000123_4f1c2e","state":"done","…":"compiler 05 §6.5"},
 "meta":{"job_kind":"compile","runner":"subprocess","handler":"wynd.compiler.job:run_compile_job","attempt":2,
         "pid":48213,"host":"peters-mbp","cpu_ms":88210,
         "artefacts":{"branch":"wynd/compile/process_supplier_invoice/job_20260922T100000123_4f1c2e",
                      "commit":"c41e0a9…","base_commit":"3f9c1e2…"},
         "report":{"…":"CompileReport (05 §15)"},
         "integration":{"mode":"rebased","branch":"wynd/compile/process_supplier_invoice/job_20260922T100000123_4f1c2e",
                        "target":"main","head":"9a1b2c3…","skipped_commits":2,"conflicts":[],"pr_url":null,"at":"2026-09-22T10:07:12Z"},
         "chat_id":"chat_20260922T100000130_a9b8c7"}}
```

### 10.2 Controller document store (`store.py`, CORE): releases, trigger fires, chats

§5 lists "process/release registries" under the controller. These records are neither runs nor jobs, so they live in a small document store. It sits behind one Protocol with one file backend. If the runtime adds generic documents to `RunRegistry` (§13 R2), the protocol is re-pointed there instead.

```python
class DocStore(Protocol):
    def put(self, collection: str, id: str, doc: dict) -> None: ...
    def get(self, collection: str, id: str) -> dict | None: ...
    def update(self, collection: str, id: str, patch: dict) -> dict: ...     # atomic shallow merge
    def list(self, collection: str, where: Mapping[str, Any] | None = None, limit: int | None = None) -> list[dict]: ...
    def delete(self, collection: str, id: str) -> None: ...

class FileDocStore:     # <state_dir>/controller/<collection>/<id>.json ; tmp + os.replace ; flock(<state_dir>/controller/.lock)
    def __init__(self, root: Path) -> None: ...
```
The file backend is multi-process safe: one file per document, temp file plus rename, and no index. That makes it usable on the RWX volume that 08 uses in a cluster.

**Release** (`controller/releases/<id>.json`). Stored fields are shown here. The DTO in 07 §12.2 adds the computed `short`, `behind`, `state`, `state_detail`, `next_fire_at` and `webhook_url`.
```json
{"id":"rel_20260922T110000000_77aa01","process_id":"process_supplier_invoice",
 "commit":"71aa3c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b",
 "image":"localhost:5001/wynd/process_supplier_invoice@sha256:5e1d…",
 "trigger":{"kind":"schedule","cron":"0 9 * * 1-5","timezone":"Europe/London",
            "inputs":{"pdf_path":"/data/inbox/today.pdf"}},
 "env":{"RECORDS_DIR":{"value":"/data/records"},"REVIEW_DIR":{"value":"/data/review"},
        "ESCALATIONS_DIR":{"value":"/data/escalations"},
        "CLAUDE_CODE_OAUTH_TOKEN":{"from_env":"CLAUDE_CODE_OAUTH_TOKEN"}},
 "enabled":true,"serving":"docker","triggers":"scheduler",
 "created_at":"2026-09-22T11:00:00Z","updated_at":"2026-09-22T11:00:00Z",
 "last_fire_key":"2026-09-23T08:00:00+00:00","last_fired_at":"2026-09-23T08:00:00.512Z"}
```
A webhook trigger looks like `{"kind":"webhook","secret_env":"INVOICE_HOOK_SECRET"}`. A manual trigger is `{"kind":"manual"}`.

**TriggerFire** (`controller/fires/<id>.json`):
```json
{"id":"fire_20260923T080000512_0b1c2d","release_id":"rel_20260922T110000000_77aa01","source":"schedule",
 "at":"2026-09-23T08:00:00.512Z","run_id":"run_20260923T080000530_9d8e7f","ok":true,"error":null}
```

**Chat** (`controller/chats/<id>.json`). Deltas are not stored.
```json
{"id":"chat_20260922T100000130_a9b8c7","title":"Add finance notification","created_at":"…","updated_at":"…",
 "job_id":null,"next_seq":11,
 "usage_totals":{"input_tokens":18220,"output_tokens":1432,"cost_usd":0.071},
 "items":[
  {"id":"it_7","seq":7,"type":"user","created_at":"…","text":"Add a step that emails finance when escalating",
   "acting_on":"process_supplier_invoice"},
  {"id":"it_8","seq":8,"type":"assistant","created_at":"…","turn_id":"turn_20260922T100500000_11aa22",
   "text":"Added `notify_finance` …","status":"done","error":null,
   "usage":{"input_tokens":9120,"output_tokens":702,"cost_usd":0.034,"latency_ms":8120,"provider":"claude-code","model":"sonnet"}},
  {"id":"it_9","seq":9,"type":"tool","created_at":"…","turn_id":"turn_…","tool":"edit_design","args":{"doc":"<process.yaml doc>"},
   "write":true,"acting_on":"process_supplier_invoice","status":"ok","summary":"saved; validation ok","duration_ms":38},
  {"id":"it_10","seq":10,"type":"commit","created_at":"…","turn_id":"turn_…","process_id":"process_supplier_invoice",
   "sha":"b4d2e19…","message":"design(process_supplier_invoice): add step notify_finance"}]}
```

### 10.3 User-registry entries (sections `mcp`, `providers`, `registries`; runtime `Registry` file layout, 02 §9.3)

```json
// $WYND_HOME/mcp.json
{"version":1,"entries":{
  "github":{"name":"github","url":"https://api.githubcopilot.com/mcp/","transport":"http",
            "auth_env":"GITHUB_TOKEN","headers_env":{},"oauth":null},
  "linear":{"name":"linear","url":"https://mcp.linear.app/mcp","transport":"http",
            "auth_env":"WYND_MCP_LINEAR_TOKEN","headers_env":{},
            "oauth":{"client_id":"wynd-8f2…","token_endpoint":"https://mcp.linear.app/token",
                     "expires_at":"2026-09-22T12:00:00Z","refresh_env":"WYND_MCP_LINEAR_REFRESH_TOKEN"}}}}
// $WYND_HOME/providers.json   (kind comes from the provider's entry point)
{"version":1,"entries":{
  "claude-code":{"kind":"agent","tiers":{"cheap":"haiku","standard":"sonnet","strong":"opus"},
                 "env":["CLAUDE_CODE_OAUTH_TOKEN","ANTHROPIC_API_KEY"]},
  "anthropic":{"kind":"model","tiers":{"cheap":"<model id>","standard":"<model id>","strong":"<model id>"},
               "env":["ANTHROPIC_API_KEY"]}}}
// $WYND_HOME/registries.json  (image registries)
{"version":1,"entries":{
  "local":{"name":"local","url":"localhost:5001/wynd","username_env":null,"password_env":null,"insecure":true,"default":true}}}
```
The default model ids for `anthropic` belong to the runtime providers area.

### 10.4 Small controller/CLI models (`models.py`)

```python
class CommitInfo(BaseModel):      sha: str; short: str; subject: str; author: str; at: datetime; message: str | None = None
class FileContent(BaseModel):     path: str; revision: str; content: str; size: int; truncated: bool = False
class StepSource(BaseModel):      step: str; package_dir: str; lock_yaml: str | None; files: list[FileContent]; cassettes: list[dict]
class LogChunk(BaseModel):        text: str; offset: int; done: bool
class RemoveResult(BaseModel):    removed: bool; referenced_by: list[str] = []
class InitResult(BaseModel):      root: str; created: list[str]; commit: str | None
class ServedContainer(BaseModel): name: str; container_id: str; image: str; url: str; process: str; commit: str; started_at: datetime
class EnvCheckRow(BaseModel):     name: str; required: bool; secret: bool; description: str; used_by: list[str]; set: bool; source: str
class EnvCheckReport(BaseModel):  process: str; manifest: Literal["build", "assembled"]; commit: str | None; ok: bool; vars: list[EnvCheckRow]
class TriggerFire(BaseModel):     id: str; release_id: str; source: str; at: datetime; run_id: str | None; ok: bool; error: str | None
class McpServerEntry(BaseModel):  name: str; url: str; transport: Literal["http", "sse"] = "http"; auth_env: str | None = None
                                  headers_env: dict[str, str] = {}; oauth: dict | None = None
class ImageRegistryEntry(BaseModel): name: str; url: str; username_env: str | None = None; password_env: str | None = None
                                  insecure: bool = False; default: bool = False
```

`EnvCheckReport` example (`wynd env check process_supplier_invoice --json`):
```json
{"process":"process_supplier_invoice","manifest":"build","commit":"71aa3c0…","ok":false,
 "vars":[{"name":"RECORDS_DIR","required":true,"secret":false,"description":"Directory for saved records",
          "used_by":["process:env.RECORDS_DIR"],"set":true,"source":"dotenv"},
         {"name":"CLAUDE_CODE_OAUTH_TOKEN","required":false,"secret":true,"description":"Claude Code dev key (claude setup-token)",
          "used_by":["provider:claude-code"],"set":false,"source":"missing"},
         {"name":"ESCALATIONS_DIR","required":true,"secret":false,"description":"Queue for human review",
          "used_by":["process:env.ESCALATIONS_DIR"],"set":false,"source":"missing"}]}
```

### 10.5 `.wynd/serve/<name>.json` (warm and release containers)
```json
{"name":"wynd-process_supplier_invoice-71aa3c0","container_id":"e3b0c442…","image":"localhost:5001/wynd/process_supplier_invoice:71aa3c0",
 "url":"http://127.0.0.1:53817","process":"process_supplier_invoice","commit":"71aa3c0…","release_id":null,
 "env_hash":"sha256:9c1f…","state":"serving","started_at":"2026-09-22T11:02:10Z"}
```

### 10.6 CLI input files
Answers file for `wynd compile --answers`: a YAML mapping of question id to answer text, exactly as compiler 05 §6.7 specifies.
```yaml
extract.schema: yes
extract.example1: accept
extract.example2: |
  It should be not_an_invoice: we never pay anything without a due date.
extract.example3: reject
```
Inputs file for `wynd run --inputs-file`, as JSON or YAML:
```yaml
pdf_path: processes/process_supplier_invoice/examples/acme_inv_1042.pdf   # path-typed: made absolute by the CLI
```

### 10.7 Commit messages written by the controller

```
chore(wynd): init workspace

design(finance/invoices): new process

design(process_supplier_invoice): tighten validate loop

Wynd-Origin: web
Wynd-Reason: blur

design(process_supplier_invoice): add step notify_finance

Wynd-Origin: chat
Wynd-Reason: chat_turn
Wynd-Chat: chat_20260922T100000130_a9b8c7
```
Job commits (compile, test_live, optimise) are written by their handlers, which own their trailers (05 §5.3). Integration never writes a commit of its own: a fast-forward and a rebase both keep the handler's commits.

### 10.8 `.wynd/` layout (controller-owned entries; others noted)
```
.wynd/
  locks/git.lock, locks/scheduler.lock        controller
  jobs/<job-id>/job.log                       controller (runner logs; appended across attempts)
  jobs/<job-id>/scratch/                      controller (JobContext.scratch)
  jobs/<job-id>/checkout-<attempt>/           controller (git worktree; removed unless the attempt failed)
  integrate/<job-id>/                         controller (temporary rebase worktree)
  serve/<container>.json                      controller (warm + release containers)
  uploads/<sha256>/<filename>                 controller
  chats/<chat-id>/                            controller (empty cwd for chat turns)
  controller/{releases,fires,chats}/<id>.json controller (FileDocStore, §10.2)
  workspaces/ traces/ registry/               runtime (02 §9.3; WYND_DATA_DIR = .wynd)
  venvs/ build/                               process (04)
```

---

## 11. Test plan

The default run is offline and deterministic. `@pytest.mark.docker` tests are skipped unless `WYND_DOCKER=1`, and `@pytest.mark.live` tests are skipped unless `WYND_LIVE=1`. Each test file is owned by the owner of the module it tests.

### 11.1 Fixtures (`packages/controller/tests/conftest.py`, CORE)
- **`git_repo(tmp_path)`**: `git init -b main`, a local `user.name`/`user.email`, and one initial commit.
- **`workspace(git_repo)`**: copies `tests/fixtures/ws_basic/` and commits it. The fixture contains:
  - `wynd.yaml`;
  - `processes/p1`, with two hand-written deterministic steps (`upper`, `count`) whose packages carry locks, plus a proto for a third, design-phase step;
  - `processes/p2`, which uses `shared:normalise`;
  - `processes/parent`, which uses `process:p1`;
  - `shared/steps/normalise`.
- **`subdir_workspace(git_repo)`**: the same files under `examples/ws/`, to exercise `--show-prefix` handling.
- **`fakes`**:
  - `FakeRunner`, an `InProcessJobRunner` with a handler table of test functions from `tests/support/handlers.py` (`commit_file`, `ask_then_finish`, `fail`, `build_ok`);
  - `FakeServing` and `FakeTriggers`;
  - `FakeProvider`, a scripted AgentProvider that calls the provided tools in order and returns `{"reply": …}`;
  - `httpx.MockTransport` for the run API, OAuth and docker-less serving;
  - `FakeDocker`, a monkeypatch of `wynd.controller.docker` functions;
  - `FakeClock`.
- **`controller(workspace, fakes)`**: `Controller(ControllerContext(...))`. It uses the real runtime file stores under `tmp/.wynd`, which are stdlib-only and fast, and fakes for everything else.

### 11.2 Controller unit tests
| file | owner | asserts |
|---|---|---|
| `test_git.py` | CORE | `last_commit_touching` ignores unrelated commits; `dirty_paths` scoping; `commit_paths` commits only its paths while other files are dirty (and staged); worktree add/remove; `prefix` in a subdir workspace; `count_touching` |
| `test_workspace.py` | CORE | `init` creates files, `.gitignore`/`.gitattributes` lines are idempotent, `git init` when needed, the commit; `init` inside an existing repo subdir; `new` validates (with the real process validator) and commits `design(<id>): new process`; rejects a bad id, a nested process or an existing dir |
| `test_design.py` | CORE | scope: own `process.yaml`/protos allowed, another process's files → `out_of_scope`, compiled source → `out_of_scope`, shared proto referenced → allowed; `revision_conflict` on a stale base and nothing written; atomic multi-write; `design_locked` while a turn lock is held, and the lock owner can still write; the commit includes only this process's dirty scope paths while another process's files are dirty; deletions are committed; message and trailers; `commit` returns `None` when clean |
| `test_design_roundtrip.py` | CORE | load → JSON → save → reload of the dogfood `process.yaml` and protos is lossless: dates and `exit_codes` int keys survive (07 C-SPEC-1) |
| `test_status.py` | CORE | design flag from a proto change; compiled needs a recorded passing result at the process HEAD; an unrelated commit leaves HEAD and flags unchanged; a child edit makes the parent design; built comes from a fake artefact store; released with `behind` counts; `dirty` reported |
| `test_search.py` | CORE | token AND, weights and order, snippet, ANDed flag filters |
| `test_jobs_inprocess.py` | JOBS | submit → queued → running → succeeded; the handler pushes a branch → `artefacts.branch/commit`; the worktree is removed on success and kept on failure; an exception → `failed` with the traceback in the log; log offsets and `done`; `requeue` increments `attempt` and appends a separator to the log; cancel of queued/awaiting; a dead host pid → `failed` |
| `test_jobs_subprocess.py` | JOBS | the same lifecycle with `PYTHONPATH` pointing at `tests/support`; SIGTERM cancel → `cancelled`; zombies reaped; a worker crash → `failed` |
| `test_job_service.py` | JOBS | submit preconditions (`validation_failed`, `dirty_tree` scoped to the closure while unrelated dirt is allowed, `detached_head`); build ref = process HEAD; answers: normalisation of every `AnswerRequest` form, partial answer → stays `awaiting_input`, last answer → `requeue` with `resume_request` (using a fake compiler `apply_answers`/`resume_request`); `accept_defaults`; `list(active=True)` semantics |
| `test_integrate.py` | JOBS | fast-forward when the target did not move; rebase when intervening commits sit outside the closure, with test results re-keyed; `pr_branch` when they touch the process dir, a referenced shared step, a child process or `wynd.yaml`; closure union (a reference added only on the branch); `dirty_tree` when the ff would overwrite local changes; target not checked out → `update-ref`; idempotent second call; branch deleted after integration |
| `test_runs.py` | RUNS | local run delegates to `run_local` (monkeypatched) and `start` returns immediately; image run via `FakeDocker` + `MockTransport` run API mirrors events into the TraceSink and writes the run record; ephemeral container stopped; warm container reused without a docker call; upload path rewrite; event normalisation adds `path`/`mode` |
| `test_render_tree.py` | RUNS | golden text for the dogfood-shaped trace (loop, nested child, error exit, `edge.check` line) |
| `test_env.py` | RUNS | `.env` parsing (quotes, export, comments); precedence; manifest source build vs assembled; `check_release` unbound/missing |
| `test_serving.py` | RUNS | `serve` passes `-e VAR` with no value in argv and the value in the child env; missing required → `env_missing`; health timeout → `unavailable` with the log tail |
| `test_registries.py` | REL | provider add validates against fake entry points and requires three tiers; the merge updates only the given tiers; mcp add validation; remove reports `referenced_by`; the image registry default is exclusive |
| `test_oauth.py` | REL | discovery (9728 → 8414), DCR, PKCE challenge, state expiry, token exchange → secrets and entry updated, refresh |
| `test_cron.py` | REL | table of expressions → `matches`/`next_after`, including `*/15`, ranges with steps, names, macros, dom/dow OR, Europe/London DST gap and fold, `0 0 31 2 *` → invalid, bad field messages |
| `test_scheduler.py` | REL | `tick` with `FakeClock`: fires exactly once per matching minute; `last_fire_key` persisted before firing (a crash mid-fire does not double fire); disabled releases are skipped; bounded catch-up; a second scheduler cannot take the lock |
| `test_releases.py` | REL | create requires a build (`not_built`); env coverage (`env_unbound`, unknown var → `invalid`); trigger validation; `behind`; manual trigger → `FakeServing.ensure` + run API submit + fire record; webhook `secret_env` verification and the bearer fallback; disable → `serving.remove`; triggers backend hooks called |
| `test_chat.py` | CHAT | using `FakeProvider`: read tools work on another process; write tools absent when `acting_on` is null; `edit_design` writes the acting-on process and ends in one commit with `Wynd-Origin: chat`; a write to another process is impossible (no argument) and `edit_proto` out of scope → tool error; the design lock is held during the turn and released after (including on provider error); cancel stops further tool writes; item and event sequence (`item`, `delta`, `turn`); ring buffer → `reset`; title from the first message; job-bound chat context includes `bound_job` |
| `test_compile_view.py` | CHAT | projection of the 05 §6.5 example session into the 07 `CompileSession` shape (states, kinds, phases); `answer_text` for every `AnswerRequest` variant |

### 11.3 API and CLI tests
- **`test_api_*.py`** (API), one file per route module, using `fastapi.testclient.TestClient(create_app(controller, scheduler=False))`:
  - every route: happy path, main error codes, and the error body shape;
  - SSE: read `iter_lines()` until `end` for runs, and until the `turn` done event for chats;
  - `Last-Event-ID` resume;
  - auth: missing token → 401, `?access_token=` accepted on streams;
  - CORS preflight from `http://localhost:5173`;
  - `pid` values with `/` on every suffixed route.
- **`test_web_contract.py`** (API): validates every `packages/web/src/api/fixtures/*.json` against its model, as listed in `index.json` (07 P-FIXTURES).
- **`test_static_mount.py`** (API): a temp dist is served at `/`; with no dist, the hint page is served; `/api/health` is unaffected.
- **`packages/cli/tests/test_*.py`** (CLI), using `typer.testing.CliRunner` with `WYND_WORKSPACE` pointing at the fixture workspace. `context.get_controller` is monkeypatched to return the fake-backed controller.
  - Exit-code table: `validate` with errors → 1; `build` on a dirty closure → 3; `compile` awaiting → 4 plus the questions block; `--resume --answers` → requeue → 0 and "fast-forwarded"; `pr_branch` → 5.
  - `--json` emits a single parseable document on stdout.
  - `run` prints tree lines and outputs; `path` inputs are made absolute.
  - `trace` golden output.
  - `env check` table.
  - `mcp`/`provider`/`registry` round trips.
  - `release create` for all three triggers.
  - `init` and `new` outside and inside a repo.
- **`test_cli_imports.py`** (CLI): walks `wynd/cli/**/*.py` with `ast` and asserts it never imports `wynd.process`, `wynd.runtime` or `wynd.compiler`, and never shells out to `docker` (spec §5: clients never touch Docker or the executor).
- **`test_no_env_branches.py`** (CORE): greps the controller sources for `os.environ.get("…ENV")`-style checks outside `controller.py`, `envfile.py`, `api/app.py` and `chat/engine.py`. Backend and env selection happens in exactly those places.

### 11.4 Marked tests
- **`@pytest.mark.docker`** (`test_docker_e2e.py`, RUNS):
  1. Build the dogfood image (process area job) in a temp copy of `examples/invoices`.
  2. `wynd serve --detach` → healthy.
  3. `wynd run --image` → exit `done`.
  4. The mirrored trace has the same step/exit sequence as a local run, modulo `mode`.
  5. The second `run --image` makes no docker call (checked by spying on `docker.run_detached`).
  6. A release with a manual trigger fires, and `/hooks` fires with the secret.
- **`@pytest.mark.live`** (`test_chat_live.py`, CHAT): a real `claude-code` turn on a temp copy of the dogfood workspace. The prompt asks to raise the fix loop limit to 5. It must commit exactly one `design(...)` commit, touching only `process.yaml`, and validation must stay ok.
- **`@pytest.mark.live`** (`test_compile_live.py`, JOBS/CLI): M3 acceptance.
  1. In a temp git copy of `examples/invoices` with `steps/` removed, run `wynd compile process_supplier_invoice --accept-defaults --answers tests/fixtures/dogfood_answers.yaml`.
  2. It exits 0 or 4. If 4, resume once with `--accept-defaults`.
  3. Then `wynd test` passes in replay.
  4. `wynd status` shows `compiled`.

---

## 12. Interpretations

- **I-1** Commit-producing jobs (compile, test_live, optimise) fork from the **tip of the current branch**. Its closure content equals the process HEAD's by definition, and forking there lets an unchanged target fast-forward. Build and bake use the **process HEAD** (the closure head) as both ref and artefact key.
- **I-2** "`wynd build` refuses a dirty tree" is **closure-scoped**, and the same rule applies to compile, test --live and optimise. Unrelated dirt, such as another process's autosave, does not block. Jobs only ever see committed state.
- **I-3** The reference closure includes `wynd.yaml`, because roots affect resolution. Process 04 agrees.
- **I-4** Status is computed at the process HEAD. Releases carry their own commit, which is how "released at X, design at HEAD" is shown. No arbitrary-commit status endpoint exists; the `interface?commit=` route covers the web's per-commit needs.
- **I-5** "Answering resubmits the job with the answer appended" re-queues **the same job id**, using `resume_request(session)` with `attempt+1`. This keeps branch naming `wynd/compile/<process>/<job-id>` literal, and keeps the job-bound chat and web job card pointing at one job. Partial answers are stored without resubmitting.
- **I-6** The integration target is the branch checked out when the job was submitted (`inputs.target_branch`). Detached HEAD refuses to submit.
- **I-7** `wynd base build|publish` are controller functions, not jobs, because a base image has no workspace git ref. They run without a workspace.
- **I-8** Spec §4.1's "`wynd serve` as the entrypoint" of a pod refers to the in-image supervisor (`wynd-supervisor serve`), since the in-image set is `spec + runtime`. The CLI's `wynd serve <image>` is `docker run` of that image plus a health wait.
- **I-9** The default trigger backend (`scheduler`) runs only inside `wynd serve-api`, so CLI-created schedules fire while it runs. The CLI says so.
- **I-10** Scheduled fires are at-most-once. Minutes missed while the controller was down are not caught up. Up to 5 minutes of loop delay are caught up.
- **I-11** Every release can be run manually. The trigger kind adds schedule or webhook firing. A webhook with `secret_env: null` falls back to the API bearer token, and is open when no token is set; creation warns. Secrets stay env references.
- **I-12** Chat continuity replays persisted history as context instead of resuming Claude Code sessions. Those sessions live on one machine's disk, and chats must survive storage backends.
- **I-13** A chat reply is structured output `{reply}`. Deltas stream from provider text events. With no partial-message support, a text block arrives as one delta.
- **I-14** Chat write tools are exactly edit design, edit proto, start compile and start build (§11 + 07 §9.5). Answering compile questions goes through the job card, not the assistant.
- **I-15** `wynd run` defaults to `--local`.
- **I-16** Process-level test results are keyed by the process compiled hash (runtime `TestResult.step_hash`, subject `process:<id>`).
- **I-17** `wynd validate` with no id validates every process.
- **I-18** `wynd new a/b` writes `name: b`, the folder name. The validator's "folder name equals process id" is read per leaf.
- **I-19** Release CLI parity is included, because it is a thin layer over `ReleaseService` and makes releases testable without the web.
- **I-20** The controller never opens PRs. `pr_branch` leaves the branch and prints a `gh pr create` hint, so `pr_url` is always `null` in v1.
- **I-21** Design auto-commit stages every dirty path in the process's design scope (07 §12.3), not only paths the controller wrote. A shared step-root proto is in the scope of every process referencing it, and whichever process commits first takes it.
- **I-22** After an auto-rebase, test results are copied to the rebased commit. They are keyed by commit plus tree-hash step keys, and the closure content is unchanged.
- **I-23** A failed job keeps its worktree (`.wynd/jobs/<id>/checkout-<n>`) for inspection. Successful and awaiting attempts remove theirs.
- **I-24** Uploads reach image and release runs by a read-only bind mount at `/wynd/uploads`, with `path` inputs rewritten. Serving backends that cannot mount (kube, v1) reject upload paths.
- **I-25** The API is single-user. Bearer-token auth is optional (`WYND_API_TOKEN`), and the default bind is loopback.

---

## 13. Requests to other areas and reconciliation items

| # | With | Item | Recommendation |
|---|---|---|---|
| **R1** | runtime (providers) | Chat needs `AgentRequest.builtin_tools: list[str] \| None` (`[]` means no Bash/Read/Edit, while controller tools are still exposed over in-process MCP), `on_event` (`text` / `tool_use` / `tool_result` dicts, MCP prefix stripped), and optional `cancel: threading.Event` (the provider calls `client.interrupt()`). 08 C9's "honours `tools=[]`" covers only the no-tools case. Also: `claude-agent-sdk` must be installed in the controller environment. | Add the three fields. Make `claude-agent-sdk>=0.2,<0.3` a dependency of `wynd-controller` (chat) and of `wynd-compiler` (compile default). It remains only an env-fragment dependency for images. |
| **R2** | runtime (storage) | Releases, fires and chats are neither runs nor jobs. | The controller `DocStore` with a file backend (§10.2). Alternatively, runtime adds generic `put_doc`/`get_doc`/`update_doc`/`list_docs`/`delete_doc(collection, …)` to `RunRegistry`, and `DocStore` delegates to it. |
| **R3** | process | 04 puts `integrate()` in `wynd.process.git`, and 08 calls `integrate_job_branch(workspace, process_id, branch)`. | One implementation. Git **primitives** (closure, closure_head, dirty_paths, worktrees, CommitTree loading) live in `wynd.process.git`. The **operation** is `ctl.jobs.integrate(job_id)` (`jobs/integrate.py`, §5.9), because it needs job records, the RunRegistry re-key and the git lock. 08's optimise CLI calls `ctl.jobs.integrate`. |
| **R4** | runtime ↔ web | Trace event names differ: runtime has `run.start`, `step.start`, `step.end` and `step`; web has `run_started`, `step_started`, `path` and `attempt`. | Runtime owns emission, so keep runtime names. The web adapts its reducer. The controller adds only `path` (= `step`) and `mode` to each streamed event. |
| **R5** | runtime (providers) | `Meta.llm.ready`. | `check_provider(name) -> tuple[bool, str]`. For claude-code: ready if `CLAUDE_CODE_OAUTH_TOKEN` or `ANTHROPIC_API_KEY` is set, or the bundled CLI reports a login. Never makes a model call. |
| **R6** | runtime (storage) | MCP OAuth tokens need somewhere secret to live. | `Registry.put_secret/secrets()`: the local file is `$WYND_HOME/secrets.env` with mode 0600, and the cluster backend is a Secret. Until this exists, MCP OAuth is web-only and disabled with a clear error, and `--auth-env` covers every server. |
| **R7** | process ↔ compiler | Both drafts claim test running: compiler P5 `run_tests(root, pid, *, mode, commit, runs)` and `run_live_test_job`, process `run_replay_tests`, `tests_status` and `run_test_live_job`. | One owner. The controller needs exactly `run_tests(root, pid, mode="replay", commit, runs) -> TestReport`, `tests_status(runs, ws, pid, commit)` and one `test_live` handler. |
| **R8** | spec | 07 routes use `{process_id:path}` with suffixes. | Reserve the final id segments `design, compile, build, builds, interface, status, validate, test, test-live, bake, optimise, history, env, files` in `PROCESS_ID_PATTERN` validation. The alternative is a `/-/` separator, which would change the web client. |
| **R9** | process, compiler, M5/kube | `JobContext`, the handler result and the worker entrypoint differ across 04, 05, 06 and 08. | Use §6.1: 05's fields plus `workspace`, `state_dir` and `log_path`; handler `(ctx, inputs)`; any result with `status`. 08's `ctx.git(...)`/`ctx.run_tests(...)`/`record_test_results` become plain calls in the handler (subprocess git in `ctx.worktree`; `run_tests` imported). The worker module is `wynd.controller.jobs.worker`, which accepts `--phase` if the kube build flow needs it. Backend factories take the kwargs `(env, workspace_root, state_dir, stores[, handlers])`; a `from_env(env, registry)` signature can adapt in one line. |
| **R10** | compiler | Exit code for `awaiting_input`: 05 §6.7 proposes 3. | 4. In the CLI exit-code table (§9.1), 3 means precondition, lookup or conflict. |
| **R11** | runtime / process | Job storage kinds. Runtime `RunKind` = run\|compile\|test\|build; process 04 wants `"job"`. | Keep runtime's shape, add `bake` and `optimise` to `RunKind`, and keep the precise kind in `meta.job_kind` (§10.1). |
| **R12** | process ↔ compiler | Resume after answers. 04 submits a new job with `parent_job`; 05 C-C2 uses `submit(*resume_request(session))` with a `session_id` link; 07 wants the same job to go back to `queued`. | `JobRunner.requeue(job_id, *resume_request(session)[1:])` on the same id (I-5). 05's session id equals that job id. |
| **R13** | compiler | The web `CompileSession` shape differs from 05 `SessionData`. | The controller projects with `compile_view.session_dto` (§7.8). The compiler keeps its format but must give each node a report entry with `decision{kind, tier, thinking, reason, split}`, `tests{passed, failed, total}`, `attempts` and `skipped_reason`. |
| **R14** | runtime (supervisor) | Run API assumed: `POST /runs {inputs, run_id?}`, `GET /runs/{id}`, `GET /runs/{id}/events?after=` (SSE `trace` … `end`), `/healthz`, `/readyz`, port 8080, and image labels `dev.wynd.{process,commit,base-version,run-api-port,env-manifest}`. | The supervisor accepts a caller-supplied `run_id`. If it gains file inputs (08 C10), the upload bind mount (I-24) can go. |
| **R15** | process | Consumed from the process area: `describe_steps` (StepInfo, 07 C-PROC-1), `step_catalog`, `process_interface`, `check_expr`/`expr_scope` (C-PROC-3), `compile_state` (with `process_hash`), `reference_closure`, `load_workspace(root, CommitTree(rev))`, `assemble_env_manifest`, `open_artefact_store` (`get_build`/`list_builds` with `job_id`), `run_local(ws, pid, inputs, *, run_id, env, stores, on_event)`, and `collect_stats` (08 P1) for `validate`. | Names as listed. |
| **R16** | spec | YAML load/dump round trip (07 C-SPEC-1), `PROCESS_ID_PATTERN`, proto-type → JSON Schema (C-SPEC-2), `Meta` vocabularies (C-SPEC-5/6). | Names as listed. |
