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
## 3. Workspace discovery and reference resolution (§5.1)

### 3.1 `wynd.yaml`

```yaml
# wynd.yaml — workspace marker and config (committed)
process_roots:            # default when omitted: [processes]
  - processes
step_roots:               # default when omitted: {}
  shared: shared/steps
  finance: teams/finance/steps
  vendor:                 # long form; `url:` is reserved for git/registry roots and rejected in v1 (E106)
    path: vendor/steps
```

Model consumed from spec (`wynd.spec.WorkspaceConfig`). The fields I need are listed below. Keys owned by other areas, such as the compiler's cassette size limit, are added to the same model, with `extra="forbid"` so typos fail loudly:

```python
class RootSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    path: str
    url: str | None = None                    # reserved (E106 if set)

class WorkspaceConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    process_roots: list[str | RootSpec] = ["processes"]
    step_roots: dict[str, str | RootSpec] = {}
```

Root rules. Each is checked by `load_workspace`, and the diagnostic is attached to `wynd.yaml` at `process_roots[i]` or `step_roots.<alias>`:

| Rule | Code |
|---|---|
| path is relative, POSIX, has no `.`/`..` segments, no leading `/`, no trailing `/` after normalisation, each segment matches `SEG` | E103 |
| root directory exists in the tree (at least one file under it, or it is listed by `git ls-files` as a dir). An empty root is also accepted if it exists on disk, for `wynd init` | E103 |
| no root equals or contains another root (process vs process, step vs step, process vs step) | E104 |
| alias matches `^[a-z][a-z0-9_-]*$` and is not `process` (reserved by the `process:` use form) | E105 |
| `url` unset | E106 |

`SEG = [A-Za-z0-9_][A-Za-z0-9_-]*`. Segments never contain dots, so `.`, `..`, hidden directories and file extensions cannot appear in an id.

### 3.2 Trees

The loader never touches `os.walk`. It reads through a `Tree`, so HEAD, arbitrary commits and the working tree share one code path:

```python
class Tree(Protocol):
    root: Path                                   # workspace root on disk (for CommitTree: where git runs)
    def files(self) -> frozenset[str]: ...       # workspace-relative POSIX file paths
    def read_bytes(self, path: str) -> bytes: ...
    def blob_id(self, path: str) -> str: ...     # git blob sha1 of the content
    def is_dir(self, path: str) -> bool: ...     # derived: any file startswith(path + "/")

class WorkingTree:                               # the checkout on disk, as git would commit it
    def __init__(self, ws_root: Path) -> None: ...
    # files: `git -C ws_root ls-files -z --cached --others --exclude-standard -- . ':(exclude).wynd'`
    #        (paths are relative to ws_root because git runs there), filtered to those that exist on disk
    # read_bytes: Path.read_bytes; blob_id: sha1(b"blob %d\0" % len(data) + data)

class CommitTree:                                # a commit, never checked out
    def __init__(self, ws_root: Path, commit: str) -> None: ...
    # files + blob ids: one `git -C ws_root ls-tree -r -z <commit> -- .` (mode type sha\tpath)
    # read_bytes: `git -C ws_root cat-file blob <blob_id>`
```

The workspace must be inside a git repository (E101). This follows from "one repo per workspace" (§5.1) and is what makes the `.gitignore` handling and `CommitTree` exact. `.wynd/` is always excluded, even if the user's `.gitignore` forgot it.

### 3.3 Discovery algorithm

```text
load_workspace(root, tree=None):
  tree  = tree or WorkingTree(root)
  cfg   = parse wynd.yaml (E117 YAML, E102 schema)
  proc_roots = [norm(r) for r in cfg.process_roots]; step_roots = {a: norm(r) for a, r in cfg.step_roots}
  check root rules (E103–E106)

  # processes
  marker_dirs = sorted(dirname(f) for f in tree.files if basename(f) == "process.yaml")
  for d in marker_dirs:
      pr = the process root r with d startswith r + "/"          (roots are disjoint, so at most one)
      if pr is None:
          if d is under a step root: E114 "process.yaml outside any process root"; continue
          else: ignore (fixtures/docs elsewhere in the repo are not processes)
      pid = d[len(pr)+1:]; every segment must match SEG else E116
      if pid in seen: E112 (duplicate id across roots)
      processes[pid] = ProcessEntry(id=pid, root=pr, dir=d)
  nesting: walk marker_dirs in sorted order with a stack of open dirs; a dir that startswith(top + "/") → E110
           reported on the inner process, naming the outer

  # root step packages
  for alias, sr in step_roots:
      cand = {dirname(f) for f under sr + "/" with basename in {"pyproject.toml", "step.lock.yaml", "proto.yaml"}}
      pkg_dirs = {d in cand where (d/pyproject.toml ∧ d/step.lock.yaml) ∨ d/proto.yaml}
      also report d with exactly one of pyproject.toml/step.lock.yaml and no proto.yaml → E123 (incomplete)
      nesting within pkg_dirs → E111
      id = f"{alias}:{d[len(sr)+1:]}" (segments must match SEG, E116)
      root_steps[id] = StepEntry(id, alias, path, dir=d, proto_path=d/proto.yaml if present)

  # misplaced step packages
  for every dir d containing step.lock.yaml that lies under a process root:
      it must be exactly <process dir>/steps/<SEG>, else E115
  return Workspace(...)
```

Local steps are not discovered globally. They are resolved lazily by `use: ./steps/<name>`, because only the owning process can reference them. The nesting check for `<process>/steps/<a>/<b>` still applies (E111), as does the check for a `process.yaml` below another `process.yaml` (E110). Both rules restrict layout only: they do not restrict what a process may reference (§5.1).

Folder name equals process id (§5.1): `process.yaml` `name:` must equal the **last segment** of the id, for example `processes/finance/invoices/process.yaml` → `name: invoices` (E113). The same rule applies to proto-steps: `proto/<name>.yaml` must declare `name: <name>`, and a step-root `…/<last>/proto.yaml` must declare `name: <last>` (E119).

### 3.4 Identity

| Thing | Id | Example |
|---|---|---|
| process | path relative to its process root | `finance/invoices` |
| root step | `<alias>:<path relative to step root>` | `finance:extract/invoice` |
| process-local step | `<process-id>#<name>` | `process_supplier_invoice#read_pdf` |
| step slug (module/dist names, image names) | `re.sub(r"[^a-z0-9]+", "_", id.lower()).strip("_")` | `process_supplier_invoice_read_pdf`, `finance_extract_invoice` |

`#` and `:` never occur in `SEG`, so step ids are unambiguous. Registries and job records key on these ids. Display names come from YAML `name:`.

### 3.5 `use:` forms

Grammar (anchored regexes in `refs.py`):

```text
SEG   = [A-Za-z0-9_][A-Za-z0-9_-]*
PATH  = SEG(/SEG)*
ALIAS = [a-z][a-z0-9_-]*

"./steps/" SEG              → LocalUse(name)
"process:" PATH             → ProcessUse(process_id)
ALIAS ":" PATH, ALIAS≠process → RootUse(alias, path)
anything else               → E120
```

E120 messages always name the three valid forms and add one hint, chosen in this order:

| Input shape | Hint |
|---|---|
| contains `..` segment | `relative traversal ('..') is not permitted` |
| starts with `/`, `~`, or `X:` drive | `absolute paths are not permitted` |
| contains `\` | `use forward slashes` |
| starts with `./` but is not `./steps/<name>` | `process-local steps are ./steps/<name> (a single segment)` |
| `process:` with an invalid id | `invalid process id` |
| otherwise | none |

```python
@dataclass(frozen=True)
class LocalUse:  name: str
@dataclass(frozen=True)
class RootUse:   alias: str; path: str
@dataclass(frozen=True)
class ProcessUse: process_id: str
Use = LocalUse | RootUse | ProcessUse

class UseError(ValueError):
    code: str; message: str

def parse_use(text: str) -> Use: ...                      # raises UseError (E120)
def local_step_id(process_id: str, name: str) -> str: ... # f"{process_id}#{name}"
def root_step_id(alias: str, path: str) -> str: ...       # f"{alias}:{path}"
def step_slug(step_id: str) -> str: ...
```

### 3.6 Resolution, phases and interfaces

```text
resolve(binding alias → use, in process P):
  LocalUse(n):   pkg = P.dir/steps/n ; proto = P.dir/proto/n.yaml ; id = P.id#n
  RootUse(a, p): a ∉ step_roots → E121 ; pkg = step_roots[a]/p ; proto = pkg/proto.yaml ; id = a:p
  ProcessUse(c): c ∉ processes → E124 ; c on current load stack → E125 (cycle) ; else load child (memoised)

  for a step:
    has_py, has_lock = pkg/pyproject.toml ∈ files, pkg/step.lock.yaml ∈ files
    has_py xor has_lock            → E123 (incomplete package: names which file is missing)
    compiled_pkg = has_py and has_lock
    not compiled_pkg and proto ∉ files → E122 (lists both paths looked at)
    proto parsed (E117/E118/E119) ; lock parsed (E117/E118)
    proto_hash = hashing.proto_hash(proto_raw)                    if proto
    stale      = compiled_pkg and proto and lock.proto_hash != proto_hash
    phase      = "compiled" if compiled_pkg and not stale else "design"
    interface  = choose_interface(...)
```

`choose_interface`, first match wins:

| # | Condition | `source` | `precise` |
|---|---|---|---|
| 1 | compiled, not stale, `lock.interface` present | `lock` | True |
| 2 | proto declares `inputs` and `outputs` | `proto` (types via `wynd.spec.types.type_to_schema`) | True |
| 3 | proto present | `examples` (field names = union of keys across examples per exit; input fields = union of example input keys; all types `any`) | False |
| 4 | compiled but lock has no `interface` | `none` → W129; exits and fields unknown, so checks involving this step are skipped | — |

For a `process:<id>` reference (a `ProcessStep`), the interface is derived from the child's YAML:
- `input` = `fields_to_json_schema(child.inputs)`, with all fields required.
- `exits` = `{exit: fields_to_json_schema(fields) for exit, fields in child.outputs}`.
- `source="process"`, `precise=True`.

Exits of a proto are always known: they come from `exits:`, and a flat `outputs:` means `[done]`. So exit-level rules (E204, E208) are always enforced. Only field-level rules are downgraded when `precise=False`.

Every step implicitly has the `error` exit (§3.5). The interface never stores it. The validator adds it with the schema of `wynd.spec.errors.ProcessError` (fields `step`, `cause`, `inputs`, `partial_outputs`, `trace`; consumed).

```python
class StepInterface(BaseModel):
    input: dict                        # JSON Schema (object) of Input
    exits: dict[str, dict]             # exit name -> JSON Schema (object) of that exit's model, WITHOUT the `exit` discriminator
    source: Literal["lock", "proto", "examples", "process", "none"]
    precise: bool

def exit_names(iface: StepInterface) -> list[str]            # declared exits + "error"
def exit_fields(iface: StepInterface, exit: str) -> dict[str, dict] | None   # None = unknown (source none)
def navigate(schema: dict, path: Sequence[str | int], defs: dict) -> dict | None | Literal["any"]
    # resolves local $ref (#/$defs/X), anyOf/oneOf (field exists if it exists in any non-null branch),
    # properties, additionalProperties (open dict → "any"), items (int index), {} / missing type → "any"
```

Loader output. Names follow (06): `LoadedProcess` and `ResolvedStep`.

```python
@dataclass
class StepPackage:
    id: str                              # step id
    dir: str                             # workspace-relative package dir (may not exist for proto-only local steps)
    proto_path: str | None
    proto: ProtoStep | None
    proto_raw: dict | None
    proto_hash: str | None
    lock: StepLock | None
    phase: Literal["compiled", "design"]
    stale: bool
    interface: StepInterface | None

@dataclass
class ResolvedStep:                      # one entry of process.yaml `steps:`
    name: str                            # alias in this process
    use: str
    ref_kind: Literal["local", "root", "process"]
    package: StepPackage | None          # None for process refs
    child: str | None                    # child process id for process refs
    # convenience (06): .proto, .proto_path, .lock, .package_dir (Path | None)

@dataclass
class LoadedProcess:
    id: str
    dir: str                             # workspace-relative
    path: str                            # dir + "/process.yaml"
    spec: ProcessSpec
    raw: dict                            # parsed YAML (for loc → line)
    locmap: dict[str, tuple[int, int]]   # yamlloc
    steps: dict[str, ResolvedStep]
    children: dict[str, "LoadedProcess"] # direct children by id (shared objects; memoised across the closure)
    diagnostics: list[Diagnostic]        # loader diagnostics for this process only
    def closure_processes(self) -> dict[str, "LoadedProcess"]: ...   # self + all descendants, by id
    def closure_packages(self) -> dict[str, StepPackage]: ...         # all step packages incl. descendants', by step id

class Workspace:
    root: Path; tree: Tree; config: WorkspaceConfig
    processes: dict[str, ProcessEntry]; root_steps: dict[str, StepEntry]
    diagnostics: list[Diagnostic]
    def process_ids(self) -> list[str]: ...                  # sorted (06)
    def process_dir(self, pid: str) -> Path: ...             # absolute (06)
    def process_root_of(self, pid: str) -> str: ...          # (06)
    def load_process(self, pid: str) -> LoadedProcess: ...   # raises ProcessNotFound (E127); memoised per Workspace

def find_workspace_root(start: Path | None = None) -> Path | None: ...
    # WYND_WORKSPACE if set (used by subprocesses and job workers), else walk up from start/cwd for wynd.yaml
def load_workspace(root: Path, tree: Tree | None = None) -> Workspace: ...
def load_process(ws: Workspace, pid: str) -> LoadedProcess: ...           # same as ws.load_process
```

Load failures that prevent building a `ProcessSpec` at all (E117, E118 on `process.yaml`) are raised as `LoadError(diagnostics)`. All other problems are collected as diagnostics so that `wynd validate` can report everything in one pass.

### 3.7 Reference cycles

`load_process` recurses into `process:` references depth-first with an explicit stack of ids. If the target is already on the stack, it emits E125 on the referencing binding with the full cycle (`a -> b -> a`) and does not recurse. A self-reference is a cycle of length 1. Loaded children are memoised per `Workspace`, so diamonds are loaded once.

### 3.8 Compiled step package layout (consumed; contract with compiler and runtime)

```text
<step dir>/                                     # processes/<p>/steps/<name>/  or  <step root>/<path>/
  pyproject.toml
  step.lock.yaml
  wynd_steps/<slug>/__init__.py                 # the step class; may import sibling modules in wynd_steps/<slug>/
  test_<name>.py                                # generated tests (one per example + confirmed edge cases)
  cassettes/                                    # recorded model calls (git-lfs)
  proto.yaml                                    # step-root steps only; process-local protos live in <process>/proto/<name>.yaml
```

- The module lives in the PEP 420 namespace `wynd_steps` (no `wynd_steps/__init__.py`), so the same import path works from source (`sys.path += [step dir]`, merged across all step dirs) and from an installed wheel. Step code can never shadow a PyPI distribution.
- `step.lock.yaml` `entrypoint: "wynd_steps.<slug>:<ClassName>"` is authoritative. The compiler uses `<slug> = step_slug(id)`. The validator enforces only uniqueness of the module within a closure (E126), not the naming convention, so hand-written M1 steps may choose shorter names.
- `pyproject.toml` must build a wheel containing only `wynd_steps/`:

```toml
[project]
name = "wynd-step-process-supplier-invoice-read-pdf"   # "wynd-step-" + slug with "_"→"-"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pypdf>=5,<6"]                         # declared ranges; pins live in step.lock.yaml env.deps

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["wynd_steps"]
```

- `step.lock.yaml`: the fields this workstream reads (model `wynd.spec.StepLock`, owned by spec/compiler):

```yaml
schema: 1
step: read_pdf
kind: deterministic                  # deterministic | agentic | shell
entrypoint: wynd_steps.process_supplier_invoice_read_pdf:ReadPdf
proto_hash: sha256:3f0c…             # hashing.proto_hash of the proto at compile time; null for proto-less hand-written steps
provider: null                       # per-step override (hand-edited only); null → process default
tier: null                           # agentic only: cheap|standard|strong (never a model id)
thinking: null
retries: {validation: 0, tools: 0}
effects: [filesystem]
tools: []                            # tool snapshot (names, schemas, effects, idempotent, env)
mcp: []                              # [{server, allow, tools: [...snapshot], auth_env: GITHUB_TOKEN}]
env_vars:                            # every env var the step's tools need (§4, §9)
  - {name: RECORDS_DIR, description: "Directory for saved records", secret: false, used_by: [workspace_write]}
env:                                 # the step's environment fragment (§4)
  deps: ["pypdf==5.4.0"]             # pinned dependency set = the step's dependency lockfile
  system: []
  requires: null                     # or glibc
interface:                           # snapshot of Input / per-exit Output schemas (JSON Schema, pydantic model_json_schema)
  input:
    type: object
    properties: {pdf_path: {type: string, format: path}}
    required: [pdf_path]
  exits:
    done:
      type: object
      properties: {text: {type: string}, pages: {type: integer}}
      required: [text, pages]
```

`W128` (warning): a `pyproject.toml` `[project].dependencies` distribution name that is absent from `env.deps`. Names are compared after normalising them per PEP 503.

`sync_interfaces(ws, pid, *, venv_root=None, log=None) -> list[str]` (ENV, M1) regenerates `interface:` for the compiled steps in the closure. For each step it runs `python -m wynd.runtime.describe <entrypoint>` in that step's local venv, with the step dir on `PYTHONPATH`, and replaces only the top-level `interface:` block of `step.lock.yaml` by text substitution, so comments and ordering elsewhere survive. It returns the changed paths. It is exposed as `wynd validate <id> --sync-interfaces` (a flag request for the CLI in 06) and is how hand-written M1 steps get their snapshots. `wynd test` fails a step whose snapshot has drifted from its code (§7).

---
## 4. Graph validator (§8, §3.3–3.5, §3.4.1, §6.2)

### 4.1 Diagnostics

```python
Severity = Literal["error", "warning", "info"]

class Diagnostic(BaseModel):
    code: str                       # "E303"
    severity: Severity
    message: str
    process: str | None = None      # process id the diagnostic belongs to (children keep their own id)
    file: str | None = None         # workspace-relative file (process.yaml, wynd.yaml, step.lock.yaml, proto yaml)
    path: str | None = None         # YAML location, e.g. "edges[4].to[1].with.fields" (06 calls this `path`)
    line: int | None = None         # 1-based, from yamlloc
    col: int | None = None          # 1-based; for expressions, column of the scalar start (+ expression column if single-line)

class ValidationReport(BaseModel):
    process: str
    diagnostics: list[Diagnostic]                       # sorted by (process, file, line, code)
    normalized: dict[str, ProcessSpec]                  # per process id in the closure: shorthand expanded,
                                                        # max_traversals filled; this is what goes into ExecutionPlan
    env_refs: dict[str, list[str]]                      # env var name -> ["edge:<from>[i].<field>", ...] (for the manifest)
    @property
    def ok(self) -> bool: ...                           # no severity == "error"

def validate(lp: LoadedProcess, *, providers: ProviderCatalog | None = None) -> ValidationReport: ...
def validate_process(ws: Workspace, pid: str, *, providers: ProviderCatalog | None = None) -> ValidationReport: ...  # (06)
def format_diagnostic(d: Diagnostic) -> str: ...
    # "processes/p/process.yaml:37:9: error E303: …"  or  "[p] warning W206: …" when file is None
```

`ProviderCatalog = Callable[[str], ProviderInfo]`, defaulting to `wynd.runtime.providers.provider_info` (consumed, §15). An unknown provider name raises `KeyError`, which produces W207.

JSON form (`wynd validate --json`, owned by 06):

```json
{"process": "process_supplier_invoice", "ok": false, "diagnostics": [
  {"code": "E303", "severity": "error", "process": "process_supplier_invoice",
   "file": "processes/process_supplier_invoice/process.yaml", "path": "edges[6].to[2].with.note",
   "line": 44, "col": 17,
   "message": "'steps.fix.outputs.note' is not available on every path here: 'fix' may not have run yet. Guard with a when: on steps.fix.runs > 0 or use default()."}]}
```

`yamlloc.build_locmap(text)` uses `yaml.compose()` and walks Mapping and Sequence nodes, building dotted/indexed paths. Each path maps to the value node's `start_mark` as (line + 1, column + 1).

### 4.2 Pass order

`validate(lp)` validates every process in the closure, each with its own interfaces and diagnostics, memoised by id. It then applies the closure-wide rules to the root. Per process:

1. **Normalise edges.** `to: <target>` becomes a single branch carrying the edge-level `with:`/`limits:`, with loc prefix `edges[i]`. The list form keeps loc prefix `edges[i].to[j]`. Every expression string is parsed with `wynd.spec.expr.parse`; a syntax error gives E216, and that expression is then skipped by later passes.
2. **Structure** (§4.3): E201–E207, E213, E214, E217, E218, E220–E222, W201.
3. **Exit coverage** (§4.4): E208.
4. **Reachability** (§4.5): E209.
5. **Cycles and `max_traversals`** (§4.6): I201, and fills `normalized`.
6. **Bindings** (§4.7): E210–E212, E215, E219, plus the M3 type parts.
7. **Dataflow** (§4.8), then an **expression walk** over every `when`/`with`/`limits` under its state (§4.9): E301–E307. The type checker (§4.10) runs inside the same walk: E401–E406, W209.
8. **Child and provider rules** (§4.11): W203–W207.

Closure-wide, on the root: E126 (module collision), and W206 across the closure using the root's `latency` and provider.

Branches after the else (W201) are dropped from `normalized` and from passes 3–7. The runtime ignores them too.

### 4.3 Structural rules

- `entry` must be a key of `steps` (E201).
- `from` must match `^<alias>\.<exit>$` (E202). Aliases may not contain `.`, which the spec model enforces.
- The alias must be declared (E203). The exit must be one of `exit_names(iface)`, which includes `error` (E204); this is skipped when the interface source is `none`.
- At most one edge per `(alias, exit)` (E205).
- A branch target is exactly one of these:
  - a declared alias;
  - `$exit.<name>` where `<name>` is declared in `process.outputs` (E207);
  - `$ignore` (§4.4).
  Anything else gives E206.
- `$exit.error` is reserved (E207, second message). The process `error` exit is produced only by the error handler, never routed to directly.
- `limits.max_traversals`, if given, must be an int ≥ 1 (E217). Other `limits:` fields are type-checked by the spec model.
- Branch `name:` must be unique within its edge, must match `SEG`, and must not be all digits (E222). All-digit names would be ambiguous with indexes in `edges["a.b"].<name>`.
- `on_error` and each `finally` entry must be declared aliases (E213, E214). A handler or finally step must not also be `entry`, a branch target, or the `from` of any edge (E220, E221): its exits map to `$exit` implicitly (§3.5).
- `kind: agentic` edges (M5) go through the same rules. Their agentic-only fields are validated by the spec model (M5 design).

### 4.4 Exits routed or explicitly ignored

For every alias that is not the handler or a finally step, every declared exit except `error` must have an edge (E208). An exit is **explicitly ignored** with:

```yaml
edges:
  - from: extract.not_an_invoice
    to: $ignore          # acknowledged, unhandled: if taken at run time → process error handler (cause "ignored_exit")
```

`$ignore` is allowed only in the shorthand form with no `with:`/`limits:`, or as a single-element list with no `when:` (E218). The implicit `error` exit is exempt: no edge means the error handler. An author may still route `x.error` explicitly. For `on_error` steps, every exit other than `error` must be declared in `process.outputs` (E219), because it becomes `$exit.<name>`.

Runtime contract (consumed): the executor treats a branch target of `$ignore` exactly like an unrouted `error` exit. The `ProcessError.cause` is `"ignored_exit"`.

### 4.5 Reachability

BFS over step aliases. The roots are `entry`, `on_error` and all `finally` steps. The successors of `s` are the step targets of all effective branches of all edges from `s`, regardless of `when:`. Unvisited aliases get E209: `step '<a>' is unreachable from entry '<entry>'`. If the reason is a branch dropped after the else, the message adds `(only targeted by branches ignored after an else; see W201)`.

### 4.6 Cycles and `max_traversals` (SCC-based)

```text
G = directed graph over aliases; edge s→t for every effective branch (s.e → t) whose target is an alias
comp = tarjan_scc(G)              # iterative Tarjan; nodes visited in sorted alias order (membership is order-independent anyway)
for every effective branch b of every edge from s with alias target t:
    if comp[s] == comp[t]:        # s→t is a real edge, so same SCC ⇔ t reaches s ⇔ the branch lies on a cycle (s==t: self-loop)
        if b.limits is None or b.limits.max_traversals is None:
            normalized: b.limits.max_traversals = DEFAULT_MAX_TRAVERSALS  (10, from wynd.spec)
            emit I201 "branch '<s>.<e>' → '<t>' lies on a cycle; max_traversals defaulted to 10"
```

The fill is applied to the in-memory normalised spec, which goes into the `ExecutionPlan` and `process.lock.yaml`. It is never written back to `process.yaml`, so the tree is never dirtied. It is reported as *info*, shown by `wynd validate -v`, to answer §14's over-warning concern. Branches to `$exit`/`$ignore` are never on a cycle. A cycle through a `ProcessStep` is a single node here; cycles inside the child are handled when the child is validated.

Dogfood: SCC `{validate, fix}`. The filled branches are `validate.done` → `fix` (to[1]) and `fix.done` → `validate`. Not filled: `validate.done` → `save` / `escalate` and `extract.done` → `validate`.

### 4.7 Bindings

| Rule | Code | Check |
|---|---|---|
| entry `Input` ↔ `process.inputs` | E210 | Every `process.inputs` name is a property of the entry Input, else `process input(s) {x} are not fields of entry step '{entry}' Input`. Every *required* Input property is in `process.inputs`, else `entry step '{entry}' requires input(s) {x} that process.inputs does not provide`. M3: `type_to_schema(process type)` must be assignable to the property type (E401 at `inputs.<f>`). |
| branch → step `with:` | E212 | Keys must be Input properties (`binds unknown input(s)`). Every required Input property must be bound (`does not bind required input(s)`). For a `ProcessStep` target, the child's inputs. |
| branch → `$exit.<x>` `with:` | E211 | Keys must equal the declared field set of `outputs.<x>` exactly (all declared fields are required): `'$exit.{x}' with: must bind exactly {declared}; missing {m}, unexpected {u}`. |
| `finally` steps | E215 | They take no `with:`. Their required Input properties must be a subset of `process.inputs` (bound by name, like `entry`). |
| `on_error` step | E219, E404, E406 | Exits ⊆ process output exits ∪ {error} (E219). M3: its Input must accept `ProcessError` (`assignable(ProcessError schema → Input)`, E404). Each exit's output type must be assignable to the declared process output fields of that exit (E406). |

When the relevant interface has `precise=False`, E210–E212 are emitted as warnings with the suffix ` [provisional: interface inferred from examples]`. When the source is `none` they are skipped (W129 is already emitted).

### 4.8 Exit-aware dataflow

Goal (§8): `steps.x.outputs.f` is valid at an evaluation point only if, on **every** path from `entry` to that point, `x` has run and its latest exit declares `f`.

**Lattice.** `State = dict[alias, frozenset[str]]`. Each value is the set of possible latest exits of that step, plus the sentinel `NOT_RUN = ""` (`""` is never an exit name). The join is pointwise union. The order is pointwise ⊆. The lattice is finite (subsets of `exit_names ∪ {NOT_RUN}` per alias) and every transfer is monotone, so the worklist terminates.

**Guard atoms.** Parsed from expression ASTs; anything else is "no information":

```text
ExitIs(x, S)     steps.x.exit == "a" | "a" == steps.x.exit | steps.x.exit in ["a", "b"]       (list of string literals)
ExitIsNot(x, S)  steps.x.exit != "a" | not (steps.x.exit == "a") | not (steps.x.exit in [...])
HasRun(x)        steps.x.runs > 0 | steps.x.runs >= 1 | steps.x.runs != 0 | steps.x.exit != null
NotRun(x)        steps.x.runs == 0 | steps.x.exit == null
negate: ExitIs ↔ ExitIsNot (same S), HasRun ↔ NotRun
atoms(e) = conjuncts of the top-level `and` chain of e that parse as atoms (others ignored)
single(e) = the atom if e *is* one atom (no and/or), else None

refine_pos(st, atoms):  ExitIs(x,S): st[x] ∩= S      ExitIsNot(x,S): st[x] −= S
                        HasRun(x):  st[x] −= {NOT_RUN}  NotRun(x):   st[x] ∩= {NOT_RUN}
refine_neg(st, e):      a = single(e); a ? refine_pos(st, [negate(a)]) : st
infeasible(st):         any value is empty
```

Soundness: `ExitIs` removes `NOT_RUN` because `null == "a"` is false. `ExitIsNot` keeps `NOT_RUN` because `null != "a"` is true. Negative refinement is applied only when a whole condition is a single atom, because `not (A and B)` gives no per-atom information. Unknown guards give no refinement, so the analysis only ever over-approximates the reachable exit sets. Errors can therefore be false positives, which the author clears with a guard or `default()`, but never false negatives.

**Transfer and fixpoint.**

```text
INIT = {x: {NOT_RUN} for x in aliases}
IN   = {entry: INIT}                         # absent key = not (yet) reached
work = deque([entry])
while work:
    s = work.popleft()
    for edge in edges_from(s) in declaration order:           # edge.from = s.e ; e may be "error"
        st_e = IN[s] | {s: {e}}                               # s just completed with exit e
        neg  = st_e
        for j, b in enumerate(effective_branches(edge)):
            WHEN_STATE[edge, j] = neg                         # `when` of b is evaluated only if earlier whens were false
            st_b = refine_pos(neg, atoms(b.when))
            WITH_STATE[edge, j] = st_b                        # `with`/`limits` of b are evaluated only if b is taken
            if b targets alias t and not infeasible(st_b):
                new = st_b if t not in IN else join(IN[t], st_b)
                if new != IN.get(t): IN[t] = new; work.append(t)
            neg = refine_neg(neg, b.when)
```

Edges from aliases that never enter `IN` (unreachable, E209) are not checked for references. Handler and finally steps take no expressions and are not part of the flow.

`previous` on an edge `s.e` refers to `(s, e)`, so `previous.outputs.f` is checked against exit `e` of `s` exactly.

### 4.9 Expression walk (reference validity, exit-aware checks, soft positions)

Consumed AST (`wynd.spec.expr`, §15). The walk is `check(node, st, ctx) -> Type`. `ctx` carries: `source=(s, e)`, the loc prefix, `soft: bool`, the process, and the interfaces.

| Node | Rule |
|---|---|
| `Ref steps.x.outputs(.f(.g…)?)?` | `x` unknown → E301. `poss = st[x]`. If `NOT_RUN ∈ poss` and not soft → E303 (`'x' may not have run yet`). With a field `f`: `declaring = {e ∈ exit_names(x) : f ∈ exit_fields(x, e)}`; if empty → E302 (**even when soft**: typo protection); `missing = (poss − {NOT_RUN}) − declaring`; if missing and not soft → E303 listing each exit (`'x' may have last exited via 'e', which has no field 'f'`). Deeper segments are navigated with `navigate`; a missing sub-field → E302 (`'steps.x.outputs.f' has no field 'g'`). With no field (whole `outputs`): only the `NOT_RUN` check. |
| `Ref steps.x.exit` | `x` known (E301). Type `Str`, plus `Null` if `NOT_RUN ∈ poss`. Runtime contract: `steps.x.exit` is `null` before `x` has run. |
| `Ref steps.x.runs` | `x` known. Type `Int`. |
| `Ref steps.x.<other>` | E305 (`unknown member '<other>' of steps.x; expected outputs, exit or runs`). |
| `Ref previous.outputs(.f…)` | Against `(s, e)`: missing field → E303 (`previous ('s') exited via 'e', which has no field 'f'`). `previous.exit` → `Str`. `previous.summary` → `Obj{step, exit, key_outputs, note}`. Other → E305. |
| `Ref process.inputs.f` | `f ∈ process.inputs` else E304. Type from `type_to_schema`. `process.<other>` → E305. |
| `Ref env.NAME` | Always valid. Type `Str`. Records `NAME` in `report.env_refs` with use `edge:<from>[j].<with-key>|when|limits.<k>`. |
| `Ref run.id` | `Str`. `run.<other>` → E305. |
| `Ref edges["k"][i].taken` / `edges["k"].<name>.taken` | `k` must be the `from` of an edge (E306). `i` must be < that edge's effective branch count (E306). `<name>` must be a branch `name:` (E306). Member must be `taken` (E305). Type `Int`. |
| Unknown root | E305. |
| `a and b` | Check `a` under `st`, then `b` under `refine_pos(st, atoms(a))`. Type `Bool`. |
| `a or b` | Check `a` under `st`, then `b` under `refine_neg(st, a)`. Type `Bool`. |
| `not a` | Type `Bool`. |
| `if c1 then v1 elif c2 then v2 else v3` | `c_i` under `neg_{<i}`. `v_i` under `refine_pos(neg_{<i}, atoms(c_i))`. `else` under `neg_all`. Type is the union of the arms. |
| `default(a, b)` | `a` is checked with `soft=True` **iff `a` is directly a `Ref`**; `b` normally. Type `nonnull(a) ∪ b`. |
| `coalesce(a1, …, an)` | `a1…a(n-1)` are soft iff they are directly `Ref`s; `an` is normal. Type `∪ nonnull(ai) ∪ an`. |
| Other `Call` | Name must be in `wynd.spec.expr.BUILTINS` with its arity (E307). Argument types are checked in M3. |
| Operators, literals | See §4.10. |

**Soft-position contract** (consumed; runtime evaluator must agree): in a soft position, an unavailable reference evaluates to `null` rather than raising. Unavailable means: the step has not run, the latest exit lacks the field, or a sub-field is missing. This is what makes `default(steps.extract.outputs.label, "unlabelled")` (§3.4.1) meaningful.

Where to check. For every edge `s.e` and effective branch `j`:
- `when` under `WHEN_STATE[edge, j]`.
- each `with:` value and each expression-valued `limits:` field under `WITH_STATE[edge, j]`.

`with:` values that are YAML mappings or lists are walked recursively. YAML scalars that are not strings are literals. YAML strings are expressions. That convention is owned by spec and runtime; the validator only follows it.

References against a `precise=False` interface downgrade E302 and E303 to warnings with the provisional suffix. References to a step whose interface source is `none` skip field checks.

### 4.10 Type checking (M3)

The type checker runs from M1 as part of the same walk. It is naturally lenient where schemas are `any` (provisional or unknown interfaces), which is how the §3.4.1 staging ("types land in M3") is met without a flag. M3 adds precise schemas from compiled lock snapshots and inferred proto schemas.

```python
Type = Any | Null | Bool | Int | Num | Str(fmt: str | None) | List(item: Type) | Obj(fields: dict[str, Type], required: frozenset[str], open: bool) | Union(members: frozenset[Type])

def from_schema(schema: dict, defs: dict) -> Type
    # string(+format) → Str(fmt); integer → Int; number → Num; boolean → Bool; null → Null
    # array → List(from_schema(items)); object with properties → Obj(..., open = additionalProperties is not False)
    # object without properties / additionalProperties schema → Obj({}, ∅, open=True); anyOf/oneOf → Union
    # enum/const of strings → Str; {} or missing → Any; $ref resolved via defs
def assignable(src: Type, dst: Type) -> bool
    # Any on either side → True; Union src → all members assignable; Union dst → any member accepts
    # Int → Num ok; Str(fmt) → Str(any fmt) ok (pydantic parses "2026-10-01" into date/path)
    # Null → only if dst admits Null; List → items assignable
    # Obj → Obj: every dst.required field present in src (required in src or src open) and assignable;
    #          extra src fields allowed (pydantic ignores extras by default)
```

Operators:

| Operator | Operand types | Result |
|---|---|---|
| `== !=` | any | `Bool` |
| `< <= > >=` | both numeric, or both `Str`, else E402 | `Bool` |
| `in` | right operand `List`, `Str` or `Obj`, else E402 | `Bool` |
| `+` | `Num+Num` → `Num` (`Int` if both `Int`); `Str+Str` → `Str`; `List+List` → `List` | as listed; else E402 |
| `- * %` | numeric | `Num`/`Int` |
| `/` | numeric | `Num` |
| `and or not` | any (truthiness) | `Bool` |

Runtime contract: `and`/`or` return booleans.

Builtins (E403 on argument mismatch):

| Builtin | Signature |
|---|---|
| `len` | `(Str\|List\|Obj) → Int` |
| `lower`, `upper` | `(Str) → Str` |
| `contains` | `(Str\|List, Any) → Bool` |
| `startswith` | `(Str, Str) → Bool` |
| `join` | `(List[Str], Str) → Str` |
| `split` | `(Str, Str) → List[Str]` |
| `default` | `(T, U) → nonnull(T) ∪ U` |
| `coalesce` | `(T…) → ∪` |
| `now` | `() → Str(date-time)` |

Checks:

| Check | Code |
|---|---|
| `with:` value vs target Input property | E401 |
| `$exit` binding value vs declared output type | E405 |
| `when:` whose type is not `Bool` or `Any` | W209 (warning, because truthiness is allowed) |
| on_error input and output | E404, E406 |

### 4.11 Child-process and provider rules

- **W203/W204/W205.** For each `process:` binding whose child **explicitly declares** `env.base`, `provider` or `latency` with a value different from the parent's effective value, warn. The message is: `process step '{alias}' (process:{child}) declares {field} '{child}' but the parent's '{parent}' applies`. A child that omits the field inherits silently.
- **W207.** An effective provider name that `ProviderCatalog` does not know: `provider '{p}' is not installed (entry point group wynd.providers)`.
- **W206** (closure-wide, on the root). If the root has `latency: fast`, then for every step package in the closure with `lock.kind == "agentic"`, compute the effective provider: `lock.provider`, else the **root's** `provider`, else `DEFAULT_PROVIDER` (`"claude-code"`). The root's provider applies to children (§3.2). If `provider_info(p).kind == "agent"`, warn: `process is latency: fast but step '{id}' uses AgentProvider '{p}' (per-call harness startup is recorded in the trace)`.
- **E126** (closure-wide). Two distinct step ids in the closure whose `entrypoint` modules are equal, or whose wheel distribution names are equal: `steps '{a}' and '{b}' both provide module '{m}'; step modules must be unique within a process closure`.

### 4.12 Code table

Severity: E = error, W = warning, I = info. `{…}` are format fields.

| Code | Message template |
|---|---|
| E100 | `no wynd.yaml found in {start} or any parent directory` |
| E101 | `workspace {root} is not inside a git repository` |
| E102 | `wynd.yaml: {loc}: {msg}` |
| E103 | `{kind} root '{path}': {reason}` (must be relative / must not contain '.' or '..' segments / invalid segment '{seg}' / does not exist) |
| E104 | `roots '{a}' and '{b}' overlap; roots must be disjoint` |
| E105 | `step root alias '{alias}' is invalid: {reason}` (must match [a-z][a-z0-9_-]* / 'process' is reserved) |
| E106 | `step root '{alias}': url roots are reserved and not supported in v1` |
| E110 | `process '{inner}' is nested inside process '{outer}'; processes are leaves and must not nest` |
| E111 | `step package '{inner}' is nested inside step package '{outer}'` |
| E112 | `process id '{id}' is defined under both '{a}' and '{b}'` |
| E113 | `process.yaml name '{name}' must equal its folder name '{folder}'` |
| E114 | `'{path}' is a process.yaml under step root '{alias}'; processes live only under process roots` |
| E115 | `step package '{path}' is outside any step root; process-local steps must be at <process>/steps/<name>` |
| E116 | `'{path}': segment '{seg}' is not a valid id segment ([A-Za-z0-9_][A-Za-z0-9_-]*)` |
| E117 | `{file}:{line}:{col}: YAML error: {problem}` |
| E118 | `{file}: {loc}: {msg}` (pydantic validation of process / proto / lock; pyproject.toml unreadable) |
| E119 | `proto-step name '{name}' must equal '{expected}'` |
| E120 | `step '{alias}': use '{use}' is not valid; expected ./steps/<name>, <alias>:<path> or process:<id>{; hint}` |
| E121 | `step '{alias}': unknown step root alias '{root}' (configured: {aliases})` |
| E122 | `step '{alias}': '{use}' does not resolve to a step package or proto-step (looked for {pkg} and {proto})` |
| E123 | `step package '{dir}' has {present} but not {missing}` |
| E124 | `step '{alias}': process '{id}' not found under any process root` |
| E125 | `process reference cycle: {cycle}` |
| E126 | `steps '{a}' and '{b}' both provide module '{module}'; step modules must be unique within a process closure` |
| E127 | `unknown process '{id}'` |
| W128 | `step '{id}': pyproject dependency '{name}' is missing from step.lock.yaml env.deps` |
| W129 | `step '{id}': step.lock.yaml has no interface snapshot; field checks skipped (run wynd validate {pid} --sync-interfaces)` |
| I130 | `step '{alias}': proto-step changed since compile ({old} → {new}); process is in design phase` |
| E201 | `entry '{entry}' is not a declared step` |
| E202 | `edge from '{from}' must be '<step>.<exit>'` |
| E203 | `edge from '{from}': unknown step '{step}'` |
| E204 | `edge from '{from}': step '{step}' has no exit '{exit}' (exits: {exits})` |
| E205 | `exit '{from}' has more than one edge (edges[{i}] and edges[{j}]); one edge per exit` |
| E206 | `branch target '{target}' is not a declared step, $exit.<name> or $ignore` |
| E207 | `'$exit.{name}' is not declared under process outputs (declared: {exits})` / `'$exit.error' is reserved; the error exit is produced by the error handler` |
| E208 | `exit '{step}.{exit}' is not routed; add an edge or route it to $ignore` |
| E209 | `step '{step}' is unreachable from entry '{entry}'{extra}` |
| E210 | see §4.7 |
| E211 | `'$exit.{exit}' with: must bind exactly {declared}; missing {missing}, unexpected {extra}` |
| E212 | `branch to '{target}': with: binds unknown input(s) {extra}` / `branch to '{target}': with: does not bind required input(s) {missing}` |
| E213 | `on_error '{name}' is not a declared step` |
| E214 | `finally step '{name}' is not a declared step` |
| E215 | `finally step '{name}' requires input(s) {missing} that process.inputs does not provide` |
| E216 | `invalid expression: {msg} (column {col})` |
| E217 | `limits.max_traversals must be a positive integer` |
| E218 | `$ignore must be the only, unconditioned target of an edge (to: $ignore)` |
| E219 | `on_error step '{name}' exit '{exit}' is not a declared process output exit` |
| E220 | `step '{name}' is the {role} step and must not also be entry or a branch target` |
| E221 | `edges from {role} step '{name}' are not allowed; its exits map to $exit implicitly` |
| E222 | `branch name '{name}' in edge '{from}' is duplicated or invalid` |
| W201 | `edge '{from}': branch {i} (to '{target}') follows the else branch {j} and is ignored` |
| W202 | `step '{alias}': interface inferred from examples (no declared schemas); field checks are advisory until compile` |
| I201 | `branch '{from}' → '{target}' lies on a cycle; max_traversals defaulted to {n}` |
| W203/W204/W205 | `process step '{alias}' (process:{child}) declares {field} '{child_value}' but the parent's '{parent_value}' applies` |
| W206 | `process is latency: fast but step '{id}' uses AgentProvider '{provider}' (per-call harness startup)` |
| W207 | `provider '{provider}' is not installed (entry point group wynd.providers)` |
| W209 | `when: expression has type {type}; expected a boolean` |
| E301 | `'steps.{step}' is not a declared step` |
| E302 | `step '{step}' declares no output field '{field}' on any exit (exits: {exits})` / `'{prefix}' has no field '{field}'` |
| E303 | `'{ref}' is not available on every path here: {reasons}. Guard with when: (e.g. steps.{step}.exit == "{exit}") or use default()` |
| E304 | `process input '{field}' is not declared (inputs: {inputs})` |
| E305 | `unknown reference '{text}'` |
| E306 | `edges["{key}"]: {reason}` (no such edge / branch index {i} out of range (edge has {n}) / no branch named '{name}') |
| E307 | `unknown function '{name}'` / `{name}() takes {arity} argument(s), got {n}` |
| E401 | `cannot bind {src} to input '{field}' of type {dst}` |
| E402 | `operator '{op}' is not defined for {left} and {right}` |
| E403 | `{name}() argument {i} must be {expected}, got {actual}` |
| E404 | `on_error step '{name}' Input must accept ProcessError ({detail})` |
| E405 | `output '{field}' of $exit.{exit} expects {dst}, got {src}` |
| E406 | `on_error step '{name}' exit '{exit}' output does not match process output '{exit}': {detail}` |

The table lives in `errors.py` as `CODES: dict[str, tuple[Severity, str]]`. A test asserts that every code emitted anywhere is in the table and that every entry is covered by a golden fixture.

### 4.13 Worked example: dogfood

`examples/invoices/processes/process_supplier_invoice/process.yaml` is the §6.2 process with `provider: claude-code`. Expected report: `ok = true`, with two I201 diagnostics (`validate.done[1]` → `fix`, `fix.done[0]` → `validate`) and nothing else. The dataflow gives, among others:

```text
IN[validate] = {read:{done}, extract:{done}, validate:{∅,done}, fix:{∅,done}, save:{∅}, escalate:{∅}}
WITH_STATE[validate.done, 2 (escalate)] has fix:{∅,done}
```

So a hypothetical `with: {note: steps.fix.outputs.note}` on the escalate branch is E303 (`'fix' may not have run yet`). `with: {note: default(steps.fix.outputs.note, "")}` passes, provided `note` exists on some exit of `fix`.

---
## 5. Hashing (§5.1, §6.3, §6.5)

All hashes are `"sha256:" + hex`. Inputs are canonical, so they are identical whether computed from the working tree or from a commit.

```python
def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str).encode()
    # default=str: YAML dates → "2026-10-01"

def git_blob_id(data: bytes) -> str:            # identical to `git hash-object`
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()

def tree_hash(tree: Tree, dir: str) -> str:
    # files = sorted(f for f in tree.files if f.startswith(dir + "/"))
    # h = sha256(); for f in files: h.update(f"{f[len(dir)+1:]}\0{tree.blob_id(f)}\n".encode())
    # WorkingTree computes blob ids from bytes; CommitTree takes them from ls-tree → same value either way

def proto_hash(raw: Mapping[str, Any]) -> str:  # semantic: comments/formatting/key order don't matter
    return "sha256:" + sha256(canonical_json(raw)).hexdigest()

def step_hash(tree: Tree, pkg: StepPackage) -> str:
    return tree_hash(tree, pkg.dir)             # module, tests, cassettes, lock, pyproject (+proto.yaml for root steps)

def process_hash(tree: Tree, lp: LoadedProcess) -> str:
    return "sha256:" + sha256(canonical_json({
        "dir": tree_hash(tree, lp.dir),                                   # process.yaml, proto/, local steps/, cassettes, …
        "steps": {s.name: {"id": s.package.id, "hash": tree_hash(tree, s.package.dir)}
                  for s in lp.steps.values() if s.ref_kind == "root"},    # root-step packages (or proto dirs)
        "children": {cid: process_hash(tree, c) for cid, c in lp.children.items()},
    })).hexdigest()
```

- The **compiled hash of a step** (`step_hash`) keys per-step test results. The **compiled hash of a process** (`process_hash`) includes its children's hashes recursively (§5.1). Editing anything in a child therefore changes the parent's hash. The parent has no passing test record for the new hash, so it is not *compiled* at HEAD until its tests are re-run (§14).
- `proto_hash` is what the compiler writes into `step.lock.yaml` and compares to decide whether a step is stale.

---

## 6. Env fragments, venv planning, local venvs, `ExecutionPlan` (§4, §8)

### 6.1 Fragment merge

```python
@dataclass(frozen=True)
class StepEnv:
    step_id: str
    requirements: tuple[str, ...]      # normalized, sorted, deduped: lock.env.deps ∪ provider.fragment.deps (agentic only)
    provider: str | None               # effective provider (agentic steps only)

@dataclass(frozen=True)
class FragmentRecord:                  # provenance, copied into process.lock.yaml
    source: str                        # "step:<id>" | "provider:<name>"
    deps: tuple[str, ...]; system: tuple[str, ...]; requires: str | None

@dataclass(frozen=True)
class MergedEnv:
    steps: dict[str, StepEnv]          # every compiled step in the closure (children included), by id
    system: tuple[str, ...]            # sorted union of `system:` over all fragments
    glibc_required_by: tuple[str, ...] # sources whose fragment says requires: glibc
    providers: tuple[str, ...]         # effective providers in use (sorted)
    fragments: tuple[FragmentRecord, ...]

def normalize_requirement(req: str) -> str
    # "PyPDF2 >= 3 ; python_version>'3'" → "pypdf2>=3;python_version>'3'"
    # name: re.sub(r"[-_.]+", "-", name).lower(); strip all whitespace outside quoted marker strings

def merge_fragments(lp: LoadedProcess, providers: ProviderCatalog | None = None) -> MergedEnv
    # raises DesignPhase([step ids]) if any closure step has phase "design" (proto-only or stale)
    # effective provider for agentic steps: lock.provider or lp.spec.provider (ROOT's; §3.2) or DEFAULT_PROVIDER
```

The children's `env.base`, `provider` and `latency` are ignored (§3.2). Their steps join the closure, and the root's `env.base` and provider apply.

### 6.2 Venv groups (one venv per distinct dependency set)

```python
@dataclass(frozen=True)
class VenvGroup:
    key: str                           # dep-set hash: sha256(canonical_json(sorted requirements)).hexdigest()[:16]
    requirements: tuple[str, ...]      # the input set (not yet resolved)
    steps: tuple[str, ...]             # sorted step ids

def venv_groups(env: MergedEnv) -> list[VenvGroup]      # group StepEnv by requirements; sorted by key
```

The builder owns assignment (§4). Steps and hooks cannot choose a venv: the only input is the declared dependency set. The empty set is a valid group. Dogfood:

| key (illustrative) | requirements | steps |
|---|---|---|
| `0b4c…` | `[]` | `…#escalate_to_human`, `…#save_record`, `…#validate_fields` |
| `5e1a…` | `["claude-agent-sdk>=0.2,<0.3"]` (claude-code provider fragment) | `…#extract_invoice_fields`, `…#fix_fields` |
| `9d7f…` | `["pypdf==5.4.0"]` | `…#read_pdf` |

### 6.3 Runtime source (how `wynd-spec`/`wynd-runtime` get into every venv)

```python
@dataclass(frozen=True)
class RuntimeSource:
    version: str                                   # wynd-runtime version == wynd-base version (§4)
    editable: tuple[Path, Path] | None             # (packages/spec, packages/runtime) when running from the monorepo
    def install_args(self) -> list[str]:
        # editable → ["-e", str(spec_dir), "-e", str(runtime_dir)]
        # pinned   → [f"wynd-spec=={v}", f"wynd-runtime=={v}"]
    def descriptor(self) -> dict:
        # {"version": v, "editable": [str paths] | None,
        #  "pyprojects": sha256 of both pyproject.toml files | None}  → a runtime dependency change re-keys local venvs

def detect_runtime_source(environ: Mapping[str, str] = os.environ) -> RuntimeSource
    # 1. WYND_RUNTIME_SOURCE: an existing directory → monorepo root (editable packages/spec, packages/runtime);
    #    otherwise a version string → pinned. (Backend selection by env var, §15.)
    # 2. else importlib.metadata.distribution("wynd-runtime"): direct_url.json with dir_info.editable → editable
    #    (path from the file:// url; same for wynd-spec). uv workspace members are installed this way by `uv sync`.
    # 3. else pinned at the installed version.
```

This is **data-driven, not an environment branch**. The same code installs from whatever the running `wynd` itself was installed from.

### 6.4 Local venvs (`.wynd/venvs/<hash>/`)

```text
local_venv_id(group, runtime) = sha256(canonical_json({
    "schema": 1, "python": "3.12", "requirements": list(group.requirements),
    "runtime": runtime.descriptor(), "tools": ["pytest>=8,<10"]})).hexdigest()[:16]

ensure_local_venv(group, *, venv_root, runtime, log) -> str (the id):
    path = venv_root / id
    if (path / "wynd-venv.json").exists(): return id                    # marker written last ⇒ complete
    tmp = venv_root / f".tmp-{id}-{os.getpid()}-{token_hex(3)}"
    uv venv --relocatable --python 3.12 --no-project <tmp>              # uv-managed CPython 3.12; relocatable ⇒ safe rename
    uv pip install --python <tmp>/bin/python <runtime.install_args()> <group.requirements…> "pytest>=8,<10"
    write <tmp>/wynd-venv.json = {"id", "inputs": {…hash input…}, "freeze": `uv pip freeze --python …` lines,
                                  "created_at"}
    os.rename(tmp, path)   # if it fails because path now exists (a concurrent creator won): rmtree(tmp)
    return id
```

- `venv_root` is always the **user's** workspace `.wynd/venvs`, including in jobs, where step sources come from the job checkout. This lets venvs be shared across jobs.
- `pytest` is installed only in local venvs, which run `wynd test`. Image venvs never get it.
- Editable spec and runtime: runtime source edits are live without re-keying. Dependency changes re-key through `pyprojects`.
- There is no GC in v1. `rm -rf .wynd/venvs` is always safe.

### 6.5 `ExecutionPlan` (the contract with runtime's executor and `WorkerPool`)

Defined in `wynd.spec.plan` (authored here, consumed by runtime). It is the entire input of the executor apart from inputs and storage handles. It is built identically for local and image mode.

```python
class PlanVenv(BaseModel):
    id: str                            # directory name under venv_root
    python: str | None = None          # explicit interpreter (bake only); None ⇒ f"{venv_root}/{id}/bin/python"
    sys_path: list[str] = []           # absolute dirs prepended to the worker's sys.path (local: step package dirs)
    steps: list[str]                   # step ids served (entrypoints pre-imported at worker start)

class PlanStep(BaseModel):
    id: str
    venv: str
    entrypoint: str                    # "wynd_steps.<slug>:<Class>"
    kind: Literal["deterministic", "agentic", "shell"]
    package_dir: str | None            # local: absolute source dir (cassette replay reads <dir>/cassettes); image: None
    lock: StepLock                     # retries, provider override, tier, thinking, tools, mcp snapshot, env_vars, effects

class PlanBinding(BaseModel):          # a process.yaml `steps:` entry after resolution
    step: str | None = None            # step id
    process: str | None = None         # child process id (ProcessStep)

class PlanProcess(BaseModel):
    id: str
    dir: str | None                    # local: absolute process dir (process-level cassettes, relative example paths); image: None
    spec: ProcessSpec                  # NORMALIZED (shorthand expanded, max_traversals filled, after-else branches dropped)
    bindings: dict[str, PlanBinding]

class ExecutionPlan(BaseModel):
    schema_version: Literal[1] = 1
    mode: Literal["local", "image"]    # copied into every trace event's `mode` (§8)
    root: str                          # root process id
    provider: str                      # effective process-level default provider (root's; applies to children)
    venv_root: str                     # local: <ws>/.wynd/venvs ; image: /opt/wynd/venvs
    venvs: list[PlanVenv]
    steps: dict[str, PlanStep]
    processes: dict[str, PlanProcess]  # root + every descendant
```

Local example (dogfood, abridged):

```yaml
schema_version: 1
mode: local
root: process_supplier_invoice
provider: claude-code
venv_root: /Users/peter/…/examples/invoices/.wynd/venvs
venvs:
  - id: 1f0e9a7c55d24b10
    sys_path: [/Users/peter/…/processes/process_supplier_invoice/steps/read_pdf]
    steps: ["process_supplier_invoice#read_pdf"]
  - id: 8a31c0d4e2f6b977
    sys_path: [/Users/peter/…/steps/extract_invoice_fields, /Users/peter/…/steps/fix_fields]
    steps: ["process_supplier_invoice#extract_invoice_fields", "process_supplier_invoice#fix_fields"]
  - id: c9b2d1e07a4f3365
    sys_path: [/Users/peter/…/steps/escalate_to_human, /Users/peter/…/steps/save_record, /Users/peter/…/steps/validate_fields]
    steps: ["process_supplier_invoice#escalate_to_human", "process_supplier_invoice#save_record", "process_supplier_invoice#validate_fields"]
steps:
  "process_supplier_invoice#read_pdf":
    id: "process_supplier_invoice#read_pdf"
    venv: 1f0e9a7c55d24b10
    entrypoint: wynd_steps.process_supplier_invoice_read_pdf:ReadPdf
    kind: deterministic
    package_dir: /Users/peter/…/processes/process_supplier_invoice/steps/read_pdf
    lock: {…}
processes:
  process_supplier_invoice:
    id: process_supplier_invoice
    dir: /Users/peter/…/processes/process_supplier_invoice
    spec: {…normalized…}
    bindings: {read: {step: "process_supplier_invoice#read_pdf"}, extract: {step: "…#extract_invoice_fields"}, …}
```

In image mode, the same structure is written into `process.lock.yaml` (§8.7) with these differences:
- `mode: image`
- `venv_root: /opt/wynd/venvs`
- venv ids = group keys
- `sys_path: []`
- `package_dir: null`
- `dir: null`

WorkerPool contract (consumed): one worker per `PlanVenv`, spawned as `[python, "-m", "wynd.runtime.worker"]` with `PYTHONPATH=":".join(sys_path)`. The worker pre-imports the entrypoints of its `steps`.

```python
def execution_plan(lp: LoadedProcess, report: ValidationReport, groups: list[VenvGroup], *,
                   mode: Literal["local", "image"], venv_root: str, venv_ids: Mapping[str, str],
                   ws_root: Path | None) -> ExecutionPlan
    # ws_root given (local) ⇒ absolute sys_path / package_dir / dir; None (image) ⇒ [] / None / None

def plan_local(ws: Workspace, pid: str, *, venv_root: Path | None = None, runtime: RuntimeSource | None = None,
               providers: ProviderCatalog | None = None, log: Callable[[str], None] | None = None) -> ExecutionPlan
    # lp = ws.load_process(pid); report = validate(lp) → raise ValidationFailed(report) if not ok
    # env = merge_fragments(lp) (raises DesignPhase); groups = venv_groups(env)
    # ids = {g.key: ensure_local_venv(g, venv_root=venv_root or ws.root/".wynd/venvs", …)}
    # return execution_plan(…, mode="local", ws_root=ws.root)
```

### 6.6 Local run (06 name)

```python
class RunResult(BaseModel):            # re-exported from runtime if runtime defines one (consumed)
    run_id: str; exit: str; outputs: dict; error: dict | None

def run_local(ws: Workspace, pid: str, inputs: Mapping[str, Any], *, run_id: str, env: Mapping[str, str],
              traces: TraceSink, registry: RunRegistry, cassette_mode: Literal["live", "replay", "record"] = "live",
              venv_root: Path | None = None, log=None) -> RunResult
    # plan = plan_local(ws, pid, venv_root=venv_root, log=log)
    # return wynd.runtime.executor.run_process(plan, dict(inputs), run_id=run_id, env=env, traces=traces,
    #                                          registry=registry, cassette_mode=cassette_mode)
```

`run_local` does not resolve relative paths in `inputs`. The caller, the CLI (06), decides relative to what. The test runner resolves process-example paths against the process dir (§7).

---

## 7. Test runner and test records (§6.5, §7, §9)

`wynd test <id>` (replay) runs in the working tree directly, not as a job. `wynd test --live` is a `test_live` job. The build job calls the same function in its checkout.

```python
class TestCase(BaseModel):
    name: str; outcome: Literal["passed", "failed", "error", "skipped"]; message: str | None = None; duration_ms: int

class SuiteResult(BaseModel):
    subject: str                      # step id, or "process:<id>" for process examples
    hash: str                         # step_hash / process_hash
    passed: bool
    counts: dict[str, int]            # passed / failed / error / skipped
    cases: list[TestCase]
    problem: str | None = None        # "no tests collected", "interface snapshot drift: …", "venv failed: …"

class TestReport(BaseModel):
    process: str; commit: str | None; process_hash: str; mode: Literal["replay", "live"]
    passed: bool; suites: list[SuiteResult]; started_at: datetime; finished_at: datetime

def run_tests(ws: Workspace, pid: str, *, mode: Literal["replay", "live"], commit: str | None,
              registry: RunRegistry | None, venv_root: Path | None = None, log=None) -> TestReport
def run_replay_tests(ws, pid, *, commit, registry, log=None, venv_root=None) -> TestReport   # (06) = run_tests(mode="replay")
def tests_status(registry: RunRegistry, ws: Workspace, pid: str, commit: str) -> dict       # (06)
    # {"status": "passed"|"failed"|"missing", "steps": {step_id: "passed"|"failed"|"missing"}}
def run_test_live_job(ctx: JobContext) -> JobOutcome                                        # (06) handler, §12
```

Algorithm:

1. Validate and plan: `plan = plan_local(ws, pid, venv_root=…)`, which creates venvs as needed. A design-phase process fails fast with `DesignPhase`.
2. **Per compiled step package in the closure** (children's steps included), sorted by id:
   - **Interface drift check.** Run `<venv python> -m wynd.runtime.describe <entrypoint>` with `PYTHONPATH=<pkg dir>`, then compare its JSON to `lock.interface` after canonicalisation. A mismatch fails the suite: `problem = "interface snapshot drift: step.lock.yaml interface differs from <entrypoint>; run wynd validate <pid> --sync-interfaces"`.
   - Run pytest in the step's venv:
     ```text
     cwd = <pkg dir>
     env = os.environ + {PYTHONPATH: <pkg dir>, PYTHONDONTWRITEBYTECODE: "1",
                         WYND_CASSETTE_MODE: "replay" | "record", WYND_STEP_DIR: <pkg dir>, WYND_WORKSPACE: <ws root>}
     <venv python> -m pytest -q -p no:cacheprovider --import-mode=importlib --rootdir <pkg dir>
                   --junitxml <tmp>/junit.xml <pkg dir>
     ```
     `--import-mode=importlib` allows same-named test files across packages. `-p no:cacheprovider` and `PYTHONDONTWRITEBYTECODE` keep the source tree clean. Exit code 5 (no tests collected) fails the suite with `problem = "no tests collected"`, because compiled steps must carry tests (§6.3). Results are parsed from the JUnit XML with `xml.etree`.
3. **Process examples** (integration over the whole graph, §9). For each `process.yaml` example `i`:
   - Inputs whose declared type is `path` and whose value is relative are resolved against the process dir.
   - Run `wynd.runtime.executor.run_process(plan, inputs, cassette_mode=("replay" | "record"), cassette_dir=<process dir>/cassettes, traces=<in-memory sink>, …)`.
   - Case `example[i]` passes iff `result.exit == example.exit` (default `done`) and `match_outputs(example.outputs, result.outputs)` returns no mismatches. Matching is a recursive subset: every key in the example must deep-equal the actual value, and lists must be equal.
   - This runs in the driver interpreter, so no pytest is needed there. Children's process examples are the children's own tests and are not re-run.
4. `passed = all(s.passed)`. If `commit` and `registry` are both given, record:
   - `registry.put_test_result(commit, f"step:{id}:{step_hash}", suite)` for each step suite;
   - `registry.put_test_result(commit, f"process:{pid}:{process_hash}", report)` for the whole report.

   That is "keyed by commit hash + step hash" (§6.3). Nothing is written to the tree.

Which commit is recorded:
- CLI `wynd test`: 06 passes `commit = git.closure_head(...)` only when `git.dirty_paths(closure)` is empty, and `None` otherwise (dirty trees are never recorded as passing).
- Build: the closure HEAD of its checkout.

`tests_status` looks up `(commit, process key)` and the per-step keys.

**Live tests job** (`test_live`, M3). This is `run_tests(mode="live")` in the job checkout, with `WYND_CASSETTE_MODE=record`. The runtime records into run workspaces and promotes into `<WYND_STEP_DIR>/cassettes/`; process-level cassettes go to `<process dir>/cassettes/` (runtime mechanics, consumed).

If the tests pass, the job calls `sha = ctx.commit(f"wynd: re-record cassettes for {pid}")`, which stages the closure paths only. It returns `JobOutcome(status="succeeded", commit=sha, report=report)`. The harness publishes `wynd/test-live/<pid>/<job-id>` and the client integrates (§13.8). If they fail, the status is `failed` and there is no commit.

---
## 8. Builder (§4, §6.4, §6.5, §8)

### 8.1 Build job flow

`wynd build <id>` is `submit("build", "HEAD", {"process": id, "push": <registry name | null>})` plus a wait (§6.6). Before submitting, the CLI/controller (06) refuses if `git.dirty_paths(ws.root, reference_closure(ws, id))` is non-empty. The job itself only ever sees a commit.

```python
def run_build_job(ctx: JobContext) -> JobOutcome                         # (06: wynd.process.build:run_build_job)
def prepare_build(ctx: JobContext) -> Prepared                            # shared by build and bake: steps 1–6
def build_process(p: Prepared, *, image_builder: ImageBuilder, resolver: Resolver, artefacts: ArtefactStore,
                  user_registry: UserRegistry, push: str | None, platform: str | None, log) -> BuildInfo
```

1. `ws = load_workspace(ctx.workspace)`, `lp = ws.load_process(pid)`, `report = validate(lp)`. Errors → `failed` (report attached). If `push` is set, `user_registry.get("image_registries", push)` must exist, else `failed: "unknown image registry '<name>'"` before anything is built.
2. `env = merge_fragments(lp)`. Design phase → `failed: "process is in design phase: steps … are not compiled (run wynd compile …)"`.
3. `closure = reference_closure(ws, pid)`, `C = git.closure_head(ctx.workspace, closure, ref=ctx.job.base_sha)`. This is the **commit being built** and keys everything below. `source_sha = ctx.job.base_sha` (a later commit with identical closure content).
4. `phash = process_hash(ws.tree, lp)`.
5. **Tests gate.** `rec = ctx.registry.get_test_result(C, f"process:{pid}:{phash}")`. If it is absent or not passed, run `run_replay_tests(ws, pid, commit=C, registry=ctx.registry, venv_root=ctx.workspace_root/".wynd/venvs")`. Not passed → `failed: "tests fail on <C>"` (report attached).
6. `Prepared(ws, lp, report, env, groups=venv_groups(env), commit=C, source_sha, process_hash=phash, runtime=detect_runtime_source())`.
7. `stage = <ws_root>/.wynd/tmp/build-<job-id>/` (removed at the end).
8. `platform = platform or image_builder.native_platform()`, for example `linux/arm64`.
9. `base = choose_base(lp.spec.env.base, env, groups, …)` (§8.3).
10. `find_links = runtime_find_links(runtime, stage/"runtime-wheels")` (§8.2).
11. For each group `g`: `pins = resolver.compile([*g.requirements, f"wynd-spec=={V}", f"wynd-runtime=={V}"], universal=True, python_version="3.12", find_links=find_links)`. Write them to `stage/venvs/<g.key>/requirements.txt`. A `ResolutionError` → `failed: "cannot resolve venv <key> (steps …): <uv stderr tail>"`.
12. For each compiled step package in the closure: `build_step_wheel(pkg, out=stage/"dist")` (§8.4).
13. `manifest = assemble_env_manifest(ws, pid, user_registry, commit=C)` → `stage/process.env.yaml` (§10).
14. `lock = make_process_lock(p, base, platform, groups, pins, wheels, plan=execution_plan(…, mode="image", venv_root="/opt/wynd/venvs", venv_ids={k: k}, ws_root=None))` → `stage/process.lock.yaml` (§8.7).
15. `render_dockerfile(lock)` → `stage/Dockerfile` (§8.5).
16. `ensure_base(base.image, V, base.variant, image_builder=…, log=…)` (§9.3).
17. `tag = f"wynd/{step_slug(pid)}:{C[:12]}"`. Build `ImageBuildRequest(context=stage, dockerfile=stage/"Dockerfile", tags=(tag,), labels=labels(lock, manifest), platforms=(platform,))`.
18. Push if requested (§8.8).
19. `info = artefacts.put_build(stage, BuildInfo(...))`. This moves the build files into `.wynd/build/<pid>/<C>/`, replacing any previous build of the same commit.
20. Outcome: `succeeded`. Artefacts: `build_dir`, `image` (and `image` for the pushed ref). Report: `{commit, source_sha, image, image_id, digest, base: {...}, venvs: n, wheels: n, fallback_reason}`.

Build dir contents. The Docker context is exactly this dir; nothing else is ever in it, in particular no `.env`:

```text
.wynd/build/<process-id>/<commit>/
  Dockerfile
  process.lock.yaml
  process.env.yaml
  dist/<wheel>.whl …                       # one wheel per step (§6.4)
  venvs/<key>/requirements.txt             # resolved pins per venv (part of the venv plan)
  build.json                               # BuildInfo (artefact-store metadata, §11)
```

Reproducibility: every input comes from commit `C` plus the pinned base version. The only network-dependent step is resolution, and its full pins are recorded in `process.lock.yaml`. Rebuilding from the lock produces the same venvs. The compiler is never invoked.

### 8.2 Resolver

```python
class ResolutionError(WyndProcessError):
    stderr: str

class Resolver(Protocol):
    def compile(self, requirements: Sequence[str], *, universal: bool, python_version: str = "3.12",
                python_platform: str | None = None, only_binary: bool = False,
                find_links: Sequence[Path] = ()) -> list[str]: ...        # full pinned set, one requirement per line
    def build_wheel(self, src: Path, out_dir: Path) -> Path: ...

class UvResolver:            # subprocess `uv` (WYND_UV overrides the executable)
    # compile: uv pip compile - --no-header --no-annotate [--universal] --python-version 3.12
    #          [--python-platform P] [--only-binary :all:] [--find-links D]…  (stdin = requirements)
    # build_wheel: uv build --wheel --out-dir <out_dir> <src>   → returns the single new .whl
```

`runtime_find_links(runtime, dest) -> list[Path]`:
- **Editable runtime:** `uv build --wheel` of `packages/spec` and `packages/runtime` into `dest`, returning `[dest]`. This makes `wynd-runtime==V` resolvable before it is published. The wheels are used for resolution only; the image installs the base's vendored copies.
- **Pinned runtime:** `[]` (PyPI has `wynd-runtime==V`).

### 8.3 Base variant choice and musl fallback (§4)

```python
@dataclass(frozen=True)
class BaseChoice:
    requested: Literal["debian-slim-python", "alpine-python"]
    variant: Literal["slim", "alpine"]
    version: str                     # == wynd-runtime version
    image: str                       # base_image_ref(version, variant) e.g. "wynd-base:0.1.0-slim"
    reason: str | None               # why alpine was not used (None when requested == used)

def choose_base(requested, env: MergedEnv, groups, *, resolver, platform, version, find_links) -> BaseChoice:
    if requested == "debian-slim-python": return slim(reason=None)
    if env.glibc_required_by:
        return slim(reason=f"alpine-python requested; fell back to debian-slim-python: requires: glibc declared by {', '.join(env.glibc_required_by)}")
    arch = {"linux/amd64": "x86_64", "linux/arm64": "aarch64"}[platform]
    for g in groups:
        try: resolver.compile([*g.requirements, runtime pins], universal=False,
                              python_platform=f"{arch}-unknown-linux-musl", only_binary=True, find_links=find_links)
        except ResolutionError as e:
            return slim(reason=f"alpine-python requested; fell back to debian-slim-python: venv {g.key} "
                               f"(steps {', '.join(g.steps)}) has no musl-compatible wheels: {first_line(e.stderr)}")
    return alpine(reason=None)
```

"Cannot be satisfied on musl" therefore means one of two things: a fragment declares `requires: glibc`, or the binary-only musllinux resolution fails for the target architecture. One Debian slim beats a partial Alpine. `system:` package names are passed verbatim to the variant's package manager (`apt-get` or `apk`); see I-16.

### 8.4 Step wheels (§6.3)

`build_step_wheel(tree, pkg, lock, *, out, resolver, stage_root) -> Path`:

1. Stage: copy the package's tracked files (from `tree.files`) under `pkg.dir` into `stage_root/<slug>/`, excluding `cassettes/` and `test_*.py`. The wheel only includes `wynd_steps/` anyway, but excluding them keeps the build context small.
2. The `entrypoint` must be `wynd_steps.<mod>:<Class>` and `wynd_steps/<mod>/__init__.py` must exist. If not, fail with `"step <id>: entrypoint must be wynd_steps.<module>:<Class> with a package directory"`.
3. Write the wheel metadata to `stage_root/<slug>/wynd_steps/<mod>/wynd-step.json`:

   ```json
   {"schema": 1, "step": "process_supplier_invoice#read_pdf", "entrypoint": "wynd_steps.process_supplier_invoice_read_pdf:ReadPdf",
    "kind": "deterministic", "proto_hash": "sha256:3f0c…", "step_hash": "sha256:a41d…",
    "requirements": ["pypdf==5.4.0"], "effects": ["filesystem"], "tools": [], "mcp": []}
   ```

   It carries **no `tier`, `thinking` or `provider`**, so wheels are reusable across processes at different tiers (§6.3). `requirements` is the step's pinned set (`lock.env.deps`).
4. `resolver.build_wheel(stage, out)`. Two steps in the closure that produce the same wheel filename give E126 at validation, so this cannot happen at build time.

Wheels are installed with `--no-deps`. The venv's pinned `requirements.txt` supplies everything, which guarantees "nothing undeclared is ever installed".

### 8.5 Process Dockerfile (one per process)

`render_dockerfile(lock: ProcessLock) -> str` is a pure function of the lock, which makes golden tests possible. Rules:
- Venvs sorted by id. Wheels sorted by filename. `system` sorted.
- The `RUN apt-get`/`apk` line is emitted only if `system_packages` is non-empty.
- Build inputs are bind-mounted, not copied, so they leave no layer.
- A final `check-plan` import smoke test runs before switching to the non-root user.

Dogfood, slim:

```dockerfile
# syntax=docker/dockerfile:1.7
# Generated by wynd-process 0.1.0 for process process_supplier_invoice at commit 9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f.
# Do not edit: rebuild with `wynd build process_supplier_invoice`.
FROM wynd-base:0.1.0-slim
USER root
RUN --mount=type=cache,target=/var/cache/uv,sharing=locked \
    --mount=type=bind,source=venvs,target=/opt/wynd/build/venvs \
    --mount=type=bind,source=dist,target=/opt/wynd/build/dist \
    uv venv --python /usr/local/bin/python3.12 /opt/wynd/venvs/0b4c7e21d9a86f35 \
 && uv pip install --python /opt/wynd/venvs/0b4c7e21d9a86f35/bin/python --no-deps --find-links /opt/wynd/wheels \
      -r /opt/wynd/build/venvs/0b4c7e21d9a86f35/requirements.txt \
 && uv pip install --python /opt/wynd/venvs/0b4c7e21d9a86f35/bin/python --no-deps \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_escalate_to_human-0.1.0-py3-none-any.whl \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_save_record-0.1.0-py3-none-any.whl \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_validate_fields-0.1.0-py3-none-any.whl \
 && uv venv --python /usr/local/bin/python3.12 /opt/wynd/venvs/5e1a90c3b7f24d18 \
 && uv pip install --python /opt/wynd/venvs/5e1a90c3b7f24d18/bin/python --no-deps --find-links /opt/wynd/wheels \
      -r /opt/wynd/build/venvs/5e1a90c3b7f24d18/requirements.txt \
 && uv pip install --python /opt/wynd/venvs/5e1a90c3b7f24d18/bin/python --no-deps \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_extract_invoice_fields-0.1.0-py3-none-any.whl \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_fix_fields-0.1.0-py3-none-any.whl \
 && uv venv --python /usr/local/bin/python3.12 /opt/wynd/venvs/9d7f2a6b0c1e4f83 \
 && uv pip install --python /opt/wynd/venvs/9d7f2a6b0c1e4f83/bin/python --no-deps --find-links /opt/wynd/wheels \
      -r /opt/wynd/build/venvs/9d7f2a6b0c1e4f83/requirements.txt \
 && uv pip install --python /opt/wynd/venvs/9d7f2a6b0c1e4f83/bin/python --no-deps \
      /opt/wynd/build/dist/wynd_step_process_supplier_invoice_read_pdf-0.1.0-py3-none-any.whl
COPY process.lock.yaml process.env.yaml /opt/wynd/process/
RUN /opt/wynd/supervisor/bin/wynd-supervisor check-plan /opt/wynd/process/process.lock.yaml
USER wynd
LABEL dev.wynd.process="process_supplier_invoice" \
      dev.wynd.commit="9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f" \
      dev.wynd.base-version="0.1.0" \
      dev.wynd.base-variant="slim" \
      dev.wynd.run-api-port="8080" \
      org.opencontainers.image.revision="9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f"
```

When `system_packages` is non-empty, this is inserted after `USER root`:

```dockerfile
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils \
 && rm -rf /var/lib/apt/lists/*                          # slim
RUN apk add --no-cache poppler-utils                     # alpine
```

Notes:
- `ENTRYPOINT`/`CMD` are inherited from the base (supervisor). `WYND_PLAN`, `WYND_ENV_MANIFEST` and `WYND_VENV_ROOT` are also set by the base (§9).
- `spec` + `runtime` are installed into every venv as normal packages from the pinned `requirements.txt`, which contains `wynd-spec==V` and `wynd-runtime==V` resolved from `/opt/wynd/wheels`. There is no `--system-site-packages`, so every venv is hermetic (§4).
- The `dev.wynd.env-manifest` label (06 asks for it: compact JSON manifest) is passed as a build **label** from `ImageBuildRequest.labels`, not written into the Dockerfile text. The manifest holds no secret values.

### 8.6 `ImageBuilder` (BuildKit behind an interface)

```python
@dataclass(frozen=True)
class ImageBuildRequest:
    context: Path
    dockerfile: Path
    tags: tuple[str, ...]
    labels: Mapping[str, str] = field(default_factory=dict)
    platforms: tuple[str, ...] = ()          # () = builder native
    build_args: Mapping[str, str] = field(default_factory=dict)
    push: bool = False                       # push all tags as part of the build (multi-platform publish)

@dataclass(frozen=True)
class ImageBuildResult:
    tags: tuple[str, ...]; image_id: str | None; digest: str | None

class ImageBuilder(Protocol):
    name: str
    def build(self, req: ImageBuildRequest, log: Callable[[str], None]) -> ImageBuildResult: ...
    def exists(self, ref: str) -> bool: ...
    def push(self, ref: str, log: Callable[[str], None]) -> str: ...        # returns repo digest "sha256:…"
    def tag(self, src: str, dst: str) -> None: ...
    def login(self, registry_host: str, username: str, password: str) -> None: ...
    def native_platform(self) -> str: ...                                    # e.g. "linux/arm64"

def open_image_builder(environ: Mapping[str, str] = os.environ) -> ImageBuilder
    # name = environ.get("WYND_IMAGE_BUILDER", "buildx"); load entry point group "wynd.image_builders"
    # (kube package registers e.g. "buildctl" for rootless BuildKit in-cluster — backend by env var, §15)
```

`BuildxImageBuilder` (subprocess `docker`, output streamed to `log`):

| Method | Command |
|---|---|
| build | `docker buildx build --progress=plain --file F [--tag T]… [--label K=V]… [--build-arg K=V]… [--platform P1,P2] (--push \| --load) --metadata-file M CONTEXT`. Digest from `M["containerimage.digest"]`, image id from `M["containerimage.config.digest"]`. |
| exists | `docker image inspect REF` (rc == 0) |
| push | `docker push REF`, digest parsed from `digest: sha256:…` |
| tag | `docker tag SRC DST` |
| login | `docker login HOST -u USER --password-stdin` |
| native_platform | `docker version --format '{{.Server.Os}}/{{.Server.Arch}}'` |

It uses the default buildx builder, the **docker driver** (verified on this machine: `default`/`desktop-linux`, BuildKit v0.27, containerd image store). This is what lets `FROM wynd-base:<ver>-<variant>` resolve a **local** tag with no registry, and it supports multi-platform `--push` for `wynd base publish`.

### 8.7 `process.lock.yaml`

Model: `wynd.spec.plan.ProcessLock`, authored here. It is read by the supervisor, since it contains the `ExecutionPlan`. It carries no timestamps (build time is in the job record and `build.json`), so the same commit produces a byte-identical lock.

```yaml
schema_version: 1
process: process_supplier_invoice
commit: 9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f        # closure HEAD = "git commit hash of the compile tree"
source_sha: 1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e    # commit checked out by the job (same closure content)
process_hash: sha256:77aa…
wynd_version: 0.1.0                                     # wynd-process that produced this build
platform: linux/arm64
base:
  requested: debian-slim-python
  variant: slim
  version: 0.1.0                                        # wynd-base version == wynd-runtime version
  image: wynd-base:0.1.0-slim
  reason: null                                          # e.g. "alpine-python requested; fell back to debian-slim-python: requires: glibc declared by step:shared:ocr"
system_packages: []
fragments:
  - {source: "provider:claude-code", deps: ["claude-agent-sdk>=0.2,<0.3"], system: [], requires: null}
  - {source: "step:process_supplier_invoice#read_pdf", deps: ["pypdf==5.4.0"], system: [], requires: null}
  # … one per step and per provider in use
steps:
  "process_supplier_invoice#read_pdf":
    dir: processes/process_supplier_invoice/steps/read_pdf
    hash: sha256:a41d…
    wheel: dist/wynd_step_process_supplier_invoice_read_pdf-0.1.0-py3-none-any.whl
    lock: {…verbatim step.lock.yaml, including tier NAMES; never a tier→model mapping…}
venvs:
  - id: 9d7f2a6b0c1e4f83
    inputs: ["pypdf==5.4.0"]
    requirements: [annotated-types==0.7.0, lark==1.2.2, pydantic==2.11.9, pydantic-core==2.33.2,
                   pypdf==5.4.0, pyyaml==6.0.2, typing-extensions==4.14.1, typing-inspection==0.4.1,
                   wynd-runtime==0.1.0, wynd-spec==0.1.0]
    steps: ["process_supplier_invoice#read_pdf"]
  # …
plan: {…ExecutionPlan, mode: image, venv_root: /opt/wynd/venvs…}
```

### 8.8 Registry push

`push` names an entry from the user registry section `image_registries` (06 `UserRegistry.get("image_registries", name)`):

```yaml
{name: ghcr, url: ghcr.io/peterddod, username: peterddod, password_env: GHCR_TOKEN}
```

Push steps:
1. `remote = f"{url}/{step_slug(pid)}:{C[:12]}"`.
2. If `password_env` is set and present in the environment: `image_builder.login(host(url), username, os.environ[password_env])`. Otherwise rely on the existing docker credential store.
3. `image_builder.tag(local, remote)`, then `digest = image_builder.push(remote)`.

`BuildInfo.pushed = [f"{remote}@{digest}"]`. An unknown registry name fails the job before anything is built (checked in step 1 of §8.1).

### 8.9 `bake` (single executable; §8, §13)

A job kind of its own (06: `wynd.process.bake:run_bake_job`; CLI `wynd bake <id>`). It shares `prepare_build` (validation, design check, tests gate), so a bake is also commit-pinned and test-gated.

Bakeable iff:
1. `env.system` is empty, and no step has `kind: shell`. "Pure-Python" means the process needs nothing but a Python 3.12 interpreter.
2. The union of all groups' requirements plus the runtime pins resolves as **one** environment for the host platform (`resolver.compile(universal=False)`). If not: `"not bakeable: steps' dependency sets conflict (<uv error>); use wynd build"`.

Steps:

```text
stage/site/         ← uv pip install --target stage/site --python 3.12 --no-deps [--find-links runtime wheels] -r reqs.txt
                    ← uv pip install --target stage/site --python 3.12 --no-deps dist/*.whl
stage/_wynd_bake/plan.json        ExecutionPlan(mode="image", venv_root="", venvs=[PlanVenv(id="bake", steps=[all])], …)
stage/_wynd_bake/process.env.yaml the env manifest
stage/_wynd_bake/meta.json        {"id": sha256(of everything above)[:16], "process", "commit", "platform", "wynd_version"}
stage/__main__.py                 templates/bake_main.py (stdlib only)
zipapp.create_archive(stage, <build dir>/<slug>.pyz, interpreter="/usr/bin/env python3.12", compressed=True)
```

Bootstrap (`bake_main.py`):
1. Open its own archive (`sys.argv[0]`) and read `meta.json`.
2. Extract once to `$WYND_HOME/bake/<id>/`. `WYND_HOME` falls back to `~/.wynd` only when unset (§7.1). Extraction goes to a temp dir first, restores exec bits from `ZipInfo.external_attr >> 16` (needed for the `claude` binary bundled in `claude-agent-sdk`), writes a `.complete` marker, then does an atomic rename.
3. Put `site` first on `sys.path` and call `wynd.runtime.bake.main(plan_path, site, argv)` (consumed). That function sets `venvs[0].python = sys.executable` and `sys_path = [site]`, runs the process with inputs from argv/stdin, prints the outputs JSON, and returns the exit code. Every worker is the same interpreter, so there is still one worker per (single) venv.

**Why zipapp and not pex/shiv:** pydantic-core is a compiled extension, which `zipimport` cannot load. Any single-file Python app must therefore extract to disk. shiv's core is exactly this extract-once bootstrap (~60 lines), and adding it or pex as a dependency buys nothing over stdlib `zipapp` plus our bootstrap. The output is platform-specific (host platform); cross-platform bake is out of scope for v1.

Result: `BuildInfo.bake = "<build dir>/<slug>.pyz"`, and the artefact kind is `bake`.

---

## 9. `wynd-base` image family (§4, §10)

### 9.1 Template

A single template, `templates/base.Dockerfile`, with `@TOKEN@` substitution (Dockerfile `$` syntax stays untouched). Two variant blocks are selected in Python. Constants live in `base.py`:

```python
UV_VERSION = "0.10.7"
PYTHON_IMAGES = {"slim": "python:3.12-slim-trixie", "alpine": "python:3.12-alpine3.22"}
USER_SETUP = {
    "slim":   "groupadd --system --gid 10001 wynd && useradd --system --uid 10001 --gid 10001 --home-dir /home/wynd --create-home wynd",
    "alpine": "addgroup -S -g 10001 wynd && adduser -S -u 10001 -G wynd -h /home/wynd wynd",
}
```

Rendered `wynd-base:0.1.0-slim`:

```dockerfile
# syntax=docker/dockerfile:1.7
# wynd-base 0.1.0-slim. Generated by wynd-process 0.1.0 (`wynd base build 0.1.0`). Apache-2.0 contents (spec, runtime).
FROM ghcr.io/astral-sh/uv:0.10.7 AS uv
FROM python:3.12-slim-trixie
COPY --from=uv /uv /uvx /usr/local/bin/
ENV PYTHONUNBUFFERED=1 \
    UV_PYTHON_DOWNLOADS=never \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_CACHE_DIR=/var/cache/uv \
    WYND_BASE_VERSION=0.1.0 \
    WYND_BASE_VARIANT=slim \
    WYND_HOME=/var/lib/wynd \
    WYND_VENV_ROOT=/opt/wynd/venvs \
    WYND_PLAN=/opt/wynd/process/process.lock.yaml \
    WYND_ENV_MANIFEST=/opt/wynd/process/process.env.yaml \
    WYND_PORT=8080
COPY wheels/ /opt/wynd/wheels/
COPY runtime-requirements.txt /opt/wynd/runtime-requirements.txt
RUN --mount=type=cache,target=/var/cache/uv,sharing=locked \
    uv venv --python /usr/local/bin/python3.12 /opt/wynd/supervisor \
 && uv pip install --python /opt/wynd/supervisor/bin/python --no-deps --find-links /opt/wynd/wheels \
      -r /opt/wynd/runtime-requirements.txt \
 && groupadd --system --gid 10001 wynd && useradd --system --uid 10001 --gid 10001 --home-dir /home/wynd --create-home wynd \
 && mkdir -p /opt/wynd/venvs /opt/wynd/process /var/lib/wynd /work \
 && chown wynd:wynd /var/lib/wynd /work
WORKDIR /work
USER wynd
EXPOSE 8080
LABEL dev.wynd.base-version="0.1.0" dev.wynd.base-variant="slim" org.opencontainers.image.licenses="Apache-2.0"
ENTRYPOINT ["/opt/wynd/supervisor/bin/wynd-supervisor"]
CMD ["serve"]
```

The alpine variant is identical except for two lines: `FROM python:3.12-alpine3.22` and the `adduser` line. uv's distributed binary is statically linked, so it runs on musl.

What the base contains (§4):
- Python 3.12.
- `uv` (pinned).
- The vendored `wynd-spec` + `wynd-runtime` wheels at version V in `/opt/wynd/wheels`. Process venvs install from here.
- A supervisor venv containing only spec + runtime and their pins.
- The supervisor entrypoint `wynd-supervisor` (not named `wynd`).
- The worker entry `python -m wynd.runtime.worker`, available in every venv because runtime is installed in each.
- Nothing provider-specific: providers contribute fragments (§3.9); there is no agent variant.

### 9.2 Build and publish

```python
def base_image_ref(version: str, variant: str, repo: str | None = None) -> str
    # repo = repo or os.environ.get("WYND_BASE_REPO", "wynd-base") → f"{repo}:{version}-{variant}"
def render_base_dockerfile(version: str, variant: Literal["slim", "alpine"]) -> str
def build_base(version: str, variants: Sequence[str] = ("slim", "alpine"), *, log, image_builder: ImageBuilder | None = None,
               runtime: RuntimeSource | None = None, resolver: Resolver | None = None, repo: str | None = None,
               platforms: Sequence[str] = (), push: bool = False) -> list[str]            # (06) returns refs
def publish_base(version: str, variants: Sequence[str], registry: Mapping[str, str], *, log,
                 platforms: Sequence[str] = ("linux/amd64", "linux/arm64"), image_builder=None) -> list[str]   # (06)
```

`build_base`:
1. `runtime = detect_runtime_source()`. If `runtime.editable` and `runtime.version != version` → error `"source tree is wynd-runtime {runtime.version}; cannot build base {version}"`. The runtime version and the base version are the same number (§4).
2. Create a context tempdir under `.wynd/tmp/` (or the system tempdir outside a workspace):
   - `wheels/`: `uv build --wheel packages/spec packages/runtime` if editable, else empty (the index is used).
   - `runtime-requirements.txt`: `uv pip compile --universal --python-version 3.12 --find-links wheels` of `wynd-runtime==V`.
   - `Dockerfile` per variant.
3. `image_builder.build(ImageBuildRequest(tags=(base_image_ref(version, variant, repo),), platforms=platforms, push=push))` per variant. Default: native platform, `--load` into the local image store.

`publish_base` is `build_base` with `repo=f"{registry['url']}/wynd-base"`, `platforms=("linux/amd64", "linux/arm64")` and `push=True` (BuildKit multi-platform push), after `login` if `password_env` is set.

### 9.3 Local builds without a registry

`ensure_base(ref, version, variant, *, image_builder, log)`:
1. If `image_builder.exists(ref)`, return.
2. If `ref`'s repository has no registry host (the default `WYND_BASE_REPO=wynd-base`), run `build_base(version, [variant])` now. This works whenever the runtime source is available, which is the monorepo dev loop. If it is not available, fail with `"base image {ref} not found locally; run wynd base build {version} or set WYND_BASE_REPO to a registry"`.
3. If `ref` names a registry (for example `WYND_BASE_REPO=ghcr.io/peterddod/wynd-base`), do nothing: BuildKit pulls on `FROM`.

Local builds therefore resolve `FROM wynd-base:0.1.0-slim` from the local image store, which works because buildx uses the docker driver.

Dev-loop caveat: runtime source edits reach images only after `wynd base build <ver>`. Local mode, being editable, sees them immediately.

---

## 10. Env manifest and `wynd env check` (§4, §4.1, §3.8)

### 10.1 Format

Model: `wynd.spec.envmanifest`, authored here and usable in-image. `used_by` is a list of strings, as in (06).

```python
Mode = Literal["local", "image"]

class EnvVar(BaseModel):
    name: str
    description: str = ""
    secret: bool = False
    required: bool = True                 # ignored for group members (the group decides)
    default: str | None = None            # documented default (e.g. storage selectors); satisfies `required`
    group: str | None = None              # member of an alternatives group
    modes: list[Mode] = ["local", "image"]  # modes in which `required` is enforced
    used_by: list[str] = []               # "step:<id>", "tool:<step id>/<tool>", "mcp:<server>", "provider:<name>",
                                          # "edge:<process>:<from>[<j>].<field>", "storage", "runtime"

class EnvGroup(BaseModel):
    description: str = ""
    min: int = 1
    modes: list[Mode] = ["local", "image"]

class EnvManifest(BaseModel):
    schema_version: Literal[1] = 1
    process: str
    commit: str | None                    # None when assembled from a working tree
    vars: list[EnvVar]                    # sorted by name
    groups: dict[str, EnvGroup] = {}

class EnvProblem(BaseModel):
    code: Literal["missing", "group_unsatisfied"]
    name: str                             # var or group name
    message: str

def check_env(manifest: EnvManifest, environ: Mapping[str, str], *, mode: Mode) -> list[EnvProblem]:
    # var (no group), required, no default, mode ∈ var.modes, environ.get(name, "") == "" → missing
    # group g with mode ∈ g.modes: count(v in members if environ.get(v.name)) < g.min → group_unsatisfied
```

Dogfood `process.env.yaml`:

```yaml
schema_version: 1
process: process_supplier_invoice
commit: 9f1c0a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f
vars:
  - name: ANTHROPIC_API_KEY
    description: Anthropic API key accepted by the claude-code provider (alternative to CLAUDE_CODE_OAUTH_TOKEN).
    secret: true
    group: claude-code-auth
    used_by: ["provider:claude-code", "step:process_supplier_invoice#extract_invoice_fields", "step:process_supplier_invoice#fix_fields"]
  - name: CLAUDE_CODE_OAUTH_TOKEN
    description: Claude Code subscription token (create with `claude setup-token`); the dev key for containers and CI.
    secret: true
    group: claude-code-auth
    used_by: ["provider:claude-code", "step:process_supplier_invoice#extract_invoice_fields", "step:process_supplier_invoice#fix_fields"]
  - name: RECORDS_DIR
    description: Referenced by edge expression.
    used_by: ["edge:process_supplier_invoice:validate.done[0].dest"]
  - name: REVIEW_DIR
    description: Referenced by edge expression.
    used_by: ["edge:process_supplier_invoice:validate.done[0].dest"]
  - name: WYND_HOME
    description: User-level registries location (MCP servers, provider tiers).
    required: false
    default: ~/.wynd
    used_by: ["runtime"]
  - name: WYND_RUN_REGISTRY
    description: RunRegistry backend selector.
    required: false
    default: local
    used_by: ["storage"]
  # … WYND_TRACE_SINK, WYND_WORKSPACE_STORE, WYND_REGISTRY (from runtime.storage_env_vars())
groups:
  claude-code-auth:
    description: One credential for the claude-code provider. Not required locally, where the logged-in `claude` CLI subscription is used.
    min: 1
    modes: [image]
```

### 10.2 Assembly

```python
def assemble_env_manifest(ws: Workspace, pid: str, user_registry: UserRegistry | None = None, *,
                          commit: str | None = None, providers: ProviderCatalog | None = None,
                          storage_vars: Sequence[EnvVar] | None = None) -> EnvManifest     # (06)
```

Sources, merged by name:
- `used_by` is the union.
- `secret` is true if any source says so.
- `required` is true if any source says so.
- `description` is the first non-empty one, with sources ordered deterministically (steps by id, then providers, then MCP, edges, storage, runtime).
- Groups are merged by name; `min` takes the maximum and `modes` the union.

| Source | Contributes |
|---|---|
| each compiled step in the closure: `lock.env_vars` | `EnvVar(name, description, secret, used_by=["step:<id>", "tool:<id>/<tool>"…])` |
| each step's `lock.mcp[*]` | `auth_env` from the snapshot. If absent, from `user_registry.get("mcp", server)["auth_env"]`. The var is `secret`, `used_by: "mcp:<server>"`. A `url_env` in the registry entry becomes a non-secret var. |
| each provider in `MergedEnv.providers` | `provider_info(p).fragment.env` + `.env_groups`, with `used_by` including the agentic steps using it |
| `ValidationReport.env_refs` for every process in the closure | `EnvVar(name, description="Referenced by edge expression.", used_by=[edge refs])` |
| `wynd.runtime.storage.storage_env_vars()` (consumed) | storage backend selectors (§7.1), `required: false` with defaults |
| constant | `WYND_HOME` (`required: false`, default `~/.wynd`) |

Only names, descriptions and defaults are recorded, never values (§4, §3.8).

### 10.3 `wynd env check`

06 owns the command (`envcheck.py`: `.env` loading and the resolution order). This workstream supplies:
- the manifest: `assemble_env_manifest` for a working tree (no build needed, M1), or `BuildInfo.manifest` / the image label for a built image;
- `check_env`.

Mode is `local` for `wynd run --local`/`wynd test --live`, and `image` for `wynd run --image`/`serve`, releases and the in-image startup gate. The supervisor runs `check_env(read(WYND_ENV_MANIFEST), os.environ, mode="image")` at start (runtime, consumed). That makes it a Kubernetes startup probe or init container (§4.1).

---

## 11. Artefact store (§5.1, §6.4)

"Build outputs … live in an artefact store: `.wynd/build/<process>/<commit>/` locally, object storage in a cluster" (§5.1). This is a backend selected by env var:

```python
class BuildInfo(BaseModel):                         # (06 fields + extras)
    process: str; commit: str; source_sha: str; job_id: str | None
    process_hash: str
    image: str | None                               # local tag, e.g. wynd/process_supplier_invoice:9f1c0a2b3c4d
    image_id: str | None; image_digest: str | None
    pushed: list[str] = []                          # ["ghcr.io/peterddod/process_supplier_invoice:9f1c0a2b3c4d@sha256:…"]
    base: dict                                      # BaseChoice as dict
    manifest: EnvManifest
    bake: str | None = None                         # path to .pyz when target was bake
    created_at: datetime
    dir: str                                        # local path (or URI for remote stores)

class ArtefactStore(Protocol):
    def put_build(self, staged_dir: Path, info: BuildInfo) -> BuildInfo: ...   # moves/uploads; writes build.json; returns info with dir
    def get_build(self, pid: str, commit: str) -> BuildInfo | None: ...
    def list_builds(self, pid: str) -> list[BuildInfo]: ...                    # newest first
    def local_dir(self, pid: str, commit: str) -> Path: ...                    # materialise locally (download for remote stores)

class LocalArtefactStore:           # root = <state_dir>/build ; <root>/<pid>/<commit>/ ; put = rmtree old + os.rename(staged)
    def __init__(self, state_dir: Path) -> None: ...

def open_artefact_store(state_dir: Path, environ: Mapping[str, str] = os.environ) -> ArtefactStore   # (06)
    # WYND_ARTEFACT_STORE (default "local"); entry point group "wynd.artefact_stores"
```

A build of the same commit replaces the previous one. Status "built" (§11) = `get_build(pid, closure_head)` is not `None` (06 derives it).

---
## 12. Jobs: `JobRunner` interface, records, handlers, session hook (§6.6)

`wynd.process.jobs` holds the **interface and data model** only. Implementations are in the controller (06): in-process (M1), subprocess (M2), and Kubernetes (post-M5, `wynd.kube`). The controller draft also owns the job harness (`controller.jobs.harness.execute_job`) and the checkout backends. This section specifies the contract the harness must honour and the git primitives it calls (§13).

```python
JobKind  = Literal["compile", "test_live", "build", "bake", "optimise"]
JobState = Literal["queued", "running", "awaiting_input", "succeeded", "failed", "cancelled"]
TERMINAL: frozenset[JobState] = frozenset({"awaiting_input", "succeeded", "failed", "cancelled"})

class Artefact(BaseModel):
    kind: Literal["branch", "commit", "build_dir", "image", "bake", "report", "log"]
    name: str
    ref: str                                   # branch name / sha / path / image ref
    digest: str | None = None

class JobUsage(BaseModel):                     # §15 observability
    wall_s: float = 0.0
    cpu_s: float = 0.0                         # children rusage
    model: dict[str, dict[str, float]] = {}    # "<provider>/<tier>" → {input_tokens, output_tokens, cost_usd, calls}

class JobRecord(BaseModel):                    # RunRegistry kind "job", id = job id
    id: str                                    # new_job_id(): "20260922-221503-a1b2c3"
    kind: JobKind
    process: str
    ref: str                                   # as submitted ("HEAD", a branch, a sha)
    base_sha: str                              # resolved at submit; the job's checkout is exactly this commit
    target_branch: str | None                  # branch to integrate into (the branch `ref` named, or the one checked out when ref == "HEAD")
    workspace_rel: str                         # workspace root relative to the repo toplevel ("" or "examples/invoices")
    inputs: dict[str, Any]
    state: JobState
    parent_job: str | None = None              # resubmission chain (answers to awaiting_input)
    session: dict | None = None                # serialised compiler session (opaque here; compiler-owned format)
    questions: list[dict] = []                 # pending questions when awaiting_input (copied from the session for listing)
    result_branch: str | None = None
    result_sha: str | None = None
    artefacts: list[Artefact] = []
    report: dict = {}                          # kind-specific (compile report, TestReport, BuildInfo summary)
    integration: dict | None = None            # IntegrationResult once integrated (§13.8)
    error: str | None = None
    usage: JobUsage = JobUsage()
    created_at: datetime; started_at: datetime | None = None; finished_at: datetime | None = None

class JobRunner(Protocol):                     # §6.6 exactly, plus nothing
    def submit(self, kind: JobKind, ref: str, inputs: Mapping[str, Any]) -> str: ...   # → job id
    def status(self, job_id: str) -> JobRecord: ...
    def logs(self, job_id: str, offset: int = 0) -> tuple[list[str], int]: ...          # (lines, next offset)
    def artefacts(self, job_id: str) -> list[Artefact]: ...

@dataclass
class JobContext:                              # filled by the harness (06 execute_job)
    job: JobRecord
    inputs: dict[str, Any]                     # = job.inputs (06)
    session: dict | None                       # parent job's session when resuming (06), else None
    workspace_root: Path                       # the USER's workspace (hosts .wynd/venvs, .wynd/build, registries)
    checkout: Path                             # repo toplevel of the job checkout at job.base_sha
    workspace: Path                            # checkout / job.workspace_rel
    registry: RunRegistry
    log: Callable[[str], None]
    commit: Callable[..., str | None]          # commit(message, paths=None) → sha | None (nothing to commit); harness adds trailer
    save_session: Callable[[dict], None]       # checkpoint mid-job (crash safety)

class JobOutcome(BaseModel):
    state: Literal["succeeded", "failed", "awaiting_input"]
    commit: str | None = None                  # sha to publish as the result branch
    artefacts: list[Artefact] = []
    report: dict = {}
    session: dict | None = None                # required when awaiting_input
    questions: list[dict] = []
    error: str | None = None
    model_usage: dict[str, dict[str, float]] = {}

JobHandler = Callable[[JobContext], JobOutcome]

# Helpers (GIT, M1)
def new_job_id(now: datetime | None = None) -> str: ...        # "%Y%m%d-%H%M%S-" + token_hex(3); sortable
def new_job_record(kind: JobKind, ref: str, inputs: Mapping[str, Any], *, ws_root: Path,
                   parent: JobRecord | None = None) -> JobRecord: ...
    # validates inputs["process"] exists in load_workspace(ws_root); base_sha = rev_parse(ref) (parent.base_sha when
    # resuming); target_branch = current_branch() if ref == "HEAD" else (ref if it names a local branch else None)
def wait_for(runner: JobRunner, job_id: str, *, poll_s: float = 0.5, timeout_s: float | None = None) -> JobRecord: ...
def answers_inputs(parent: JobRecord, answers: Mapping[str, str]) -> dict[str, Any]: ...
    # {**parent.inputs, "resume": parent.id, "answers": {**parent.inputs.get("answers", {}), **answers}}
```

Job inputs by kind:

| kind | inputs | handler (owner) | result branch |
|---|---|---|---|
| `compile` | `{"process", "answers"?: {qid: text}, "resume"?: job id, "accept_proposals"?: bool}` | `wynd.compiler.jobs:run_compile_job` (compiler) | `wynd/compile/<process>/<job-id>` |
| `test_live` | `{"process"}` | `wynd.process.testing:run_test_live_job` | `wynd/test-live/<process>/<job-id>` |
| `build` | `{"process", "push"?: registry name, "platform"?: "linux/amd64"}` | `wynd.process.build:run_build_job` | none |
| `bake` | `{"process"}` | `wynd.process.bake:run_bake_job` | none |
| `optimise` (M5) | `{"process", "apply"?: bool, "window_days"?: int}` | `wynd.compiler.optimise:run_optimise_job` (compiler) | `wynd/optimise/<process>/<job-id>` |

**Harness contract.** 06 implements it. It is listed here because handlers depend on it.

1. `record.state = "running"`, set `started_at`, and `registry.put("job", id, record)`.
2. Create the checkout at `record.base_sha`. The local backend runs `git.add_worktree(repo, <ws_root>/.wynd/jobs/<id>/checkout, base_sha)`. The clone-based backend (kube) clones and checks out.
3. Build the `JobContext`. When `record.parent_job` is set, `session` = the parent record's `session`.
4. `outcome = handler(ctx)`. Every `ctx.log` line is appended to `.wynd/jobs/<id>/job.log` (local) with a UTC timestamp. Subprocess output from `uv`, `docker` and `pytest` is streamed through `ctx.log`.
5. If `outcome.commit` is set, publish the branch with `git.result_branch(kind, process, id)`. Locally this is `git.set_branch(repo, branch, sha)`: worktrees share the repo's refs, which is how "push back into the same repo" happens. The clone backend runs `git push origin <sha>:refs/heads/<branch>`. Set `result_branch` and `result_sha`.
6. Copy `state`, `artefacts`, `report`, `session`, `questions`, `error` and usage into the record. Set `finished_at` and save.
7. Remove the checkout on success or `awaiting_input`. **Keep it on failure** for inspection, recording its path in `report.checkout`.
8. Any exception from the handler → `state = "failed"`, `error = f"{type(e).__name__}: {e}"`, traceback in the log.

**Session serialisation hook** (§6.6, §9):
- A compile handler that needs a human returns `JobOutcome(state="awaiting_input", session=…, questions=…)`. It may commit partial progress first. The record is terminal.
- Answering means `runner.submit("compile", parent.base_sha, answers_inputs(parent, answers))`. The new record carries `parent_job = parent.id`, and its handler receives the parent's session in `ctx.session`.
- The chain of records is the conversation. The web chat is a view over it (06 `compile_view.py`).
- Resubmitting against the parent's `base_sha` keeps the session consistent with the tree it was created on. Integration later handles any movement of the target branch (§13.8).

---

## 13. Git utilities (§6.5, §6.6, §11)

`wynd.process.git` wraps the `git` CLI through `_proc.run` (no GitPython). Every function takes explicit paths; nothing depends on the process cwd.

```python
class GitError(WyndProcessError):
    args_: list[str]; returncode: int; stderr: str

def git(cwd: Path, *args: str, input: bytes | None = None, check: bool = True, env: Mapping[str, str] | None = None) -> str
def toplevel(path: Path) -> Path                                        # rev-parse --show-toplevel
def rev_parse(cwd: Path, ref: str) -> str                               # rev-parse --verify <ref>^{commit}
def current_branch(cwd: Path) -> str | None                             # symbolic-ref --short -q HEAD
def is_ancestor(cwd: Path, a: str, b: str) -> bool                      # merge-base --is-ancestor
```

### 13.1 Reference closure (§11)

```python
def reference_closure(ws: Workspace, pid: str) -> list[str]            # (06) workspace-relative POSIX, sorted, deduped
```

Closure of `P`:
- `wynd.yaml`, because it defines how `P`'s references resolve;
- `P.dir`;
- for each root-step binding, the step package dir (proto-only steps included: the dir holding `proto.yaml`);
- for each `process:` binding, the child's closure, recursively.

Local steps and protos are inside `P.dir`. Descendant paths already covered by an ancestor path in the list are dropped.

Dogfood: `["processes/process_supplier_invoice", "wynd.yaml"]`.

### 13.2 HEAD of the process

```python
def closure_head(ws_root: Path, paths: Sequence[str], ref: str = "HEAD") -> str | None
    # git -C ws_root log -1 --format=%H <ref> -- <paths…>     (None if never committed)
```

The closure is computed at `ref`, through a `CommitTree`, when `ref` is not the working tree. "HEAD of the process" (§6.5, §11) is `closure_head(ws.root, reference_closure(load_workspace(root, CommitTree(root, ref)), pid), ref)`.

### 13.3 Dirty checks

```python
def dirty_paths(ws_root: Path, paths: Sequence[str] | None = None) -> list[str]
    # git -C ws_root status --porcelain=v1 -z --untracked-files=all -- <paths or ".">
    # returns workspace-relative paths with staged, unstaged or untracked (non-ignored) changes
```

`wynd build`, `wynd bake` and `wynd compile` refuse when `dirty_paths(root, reference_closure(ws, pid))` is non-empty. The message lists the paths and says `commit or stash them; builds run from commits only`. See I-6 for why the check is restricted to the closure.

### 13.4 Worktrees

```python
def add_worktree(repo: Path, path: Path, sha: str) -> None      # git -C repo worktree add --detach <path> <sha>
def remove_worktree(repo: Path, path: Path) -> None             # git -C repo worktree remove --force <path>; worktree prune
def worktree_for_branch(repo: Path, branch: str) -> Path | None # parse `git worktree list --porcelain` for "branch refs/heads/<branch>"
```

Job checkouts live at `<ws_root>/.wynd/jobs/<job-id>/checkout` (06), inside the ignored `.wynd/`, so the user's `git status` is unaffected. A worktree is always a full repo checkout. The workspace inside it is `checkout / workspace_rel`, which matters for the dogfood at `examples/invoices/`.

### 13.5 Commits and branches

```python
def commit_paths(checkout_ws: Path, message: str, paths: Sequence[str] | None = None,
                 *, trailers: Mapping[str, str] = {}) -> str | None
    # git add -A -- <paths or "."> ; if `git diff --cached --quiet` → None
    # author = repo config user.name/email if set, else "Wynd <wynd@localhost>"; committer always "Wynd <wynd@localhost>"
    #   (passed via GIT_AUTHOR_*/GIT_COMMITTER_* env so jobs work with no git identity configured)
    # message + "\n\n" + "Wynd-Job: <id>" etc. from trailers
    # returns new HEAD sha (detached HEAD in the job checkout)
def result_branch(kind: JobKind, pid: str, job_id: str) -> str
    # {"compile": "wynd/compile", "test_live": "wynd/test-live", "optimise": "wynd/optimise"}[kind] + f"/{pid}/{job_id}"
    # (process ids contain "/" → nested ref dirs; leaves-don't-nest guarantees no ref/dir clash)
def set_branch(repo: Path, branch: str, sha: str, *, expected_old: str | None = None) -> None
    # git update-ref refs/heads/<branch> <sha> [<expected_old>]   (compare-and-swap when expected_old given)
def delete_branch(repo: Path, branch: str) -> None               # git branch -D
```

### 13.6 Intervening commits

```python
def commits_touching(ws_root: Path, base: str, tip: str, paths: Sequence[str]) -> list[tuple[str, str]]
    # git -C ws_root log --format=%H%x00%s <base>..<tip> -- <paths…>  → [(sha, subject)], newest first
```

This is per commit, not a net diff: a commit that touches the closure and a later one that reverts it both count. That is the literal reading of "every intervening commit is outside the closure".

### 13.7 Compile-job workspace changes

The compiler writes only inside the closure of the process being compiled (source packages, cassettes, confirmed examples appended to protos). `ctx.commit(message, paths=reference_closure(...))` stages only closure paths, so a compile commit can never leak outside the closure.

### 13.8 Integration (§6.5)

```python
class IntegrationResult(BaseModel):
    outcome: Literal["fast_forward", "rebased", "needs_review", "noop"]
    target_branch: str
    branch: str                                   # result branch (deleted after fast_forward/rebased; kept on needs_review)
    new_head: str | None = None
    reason: str | None = None

def integrate(ws_root: Path, *, process_id: str, base_sha: str, branch: str, target_branch: str,
              scratch_dir: Path) -> IntegrationResult
```

Algorithm. The user's checkout is never rebased or reset. It only ever sees a fast-forward, or nothing.

```text
repo = toplevel(ws_root); result = rev_parse(repo, branch); tip = rev_parse(repo, f"refs/heads/{target_branch}")
if is_ancestor(repo, result, tip): return noop                                   # already integrated
if tip == base_sha:
    new, outcome = result, "fast_forward"
else:
    closure = ∪ reference_closure(load_workspace(ws_root, CommitTree(ws_root, s)), process_id) for s in (base_sha, tip)
              (a commit where the process no longer exists contributes nothing; if it's missing at tip → needs_review
               "process <id> no longer exists at <target>")
    touching = commits_touching(ws_root, base_sha, tip, closure)
    if touching: return needs_review(reason=f"{len(touching)} commit(s) on {target_branch} since {base_sha[:12]} touch "
                                            f"{process_id}'s reference closure, e.g. {sha[:12]} {subject!r}; review {branch}")
    tmp = scratch_dir / "rebase"; add_worktree(repo, tmp, result)
    try:   git(tmp, "rebase", "--onto", tip, base_sha)                          # replays base..result onto tip (detached)
    except GitError as e: git(tmp, "rebase", "--abort", check=False); remove_worktree(repo, tmp)
                          return needs_review(reason=f"rebase conflict: {first_line(e.stderr)}")
    new = rev_parse(tmp, "HEAD"); remove_worktree(repo, tmp)
    set_branch(repo, branch, new)                                                # branch now holds the rebased commits
    outcome = "rebased"
# advance the target branch
wt = worktree_for_branch(repo, target_branch)
if wt is None: set_branch(repo, target_branch, new, expected_old=tip)           # CAS; GitError → needs_review "target moved"
else:
    try: git(wt, "merge", "--ff-only", new)                                      # updates the user's files; refuses to clobber
    except GitError as e: return needs_review(reason=f"{target_branch} is checked out at {wt} and cannot fast-forward: "
                                                     f"{first_line(e.stderr)}", branch kept)
delete_branch(repo, branch)
return IntegrationResult(outcome=outcome, target_branch=target_branch, branch=branch, new_head=new)
```

The same function integrates `test_live` and `optimise` results. The controller (06 `jobs/integrate.py`) should call this function rather than re-implementing it, and store the result in `JobRecord.integration`. On `needs_review`, the CLI prints the branch and suggests `gh pr create --head <branch>` (06).

---

## 14. Compile state (inputs to derived status, §11)

Status is derived per commit by the controller (06 `status.py`). This workstream supplies:

```python
class StepState(BaseModel):
    name: str; use: str; step_id: str | None
    proto_hash: str | None; locked_proto_hash: str | None; step_hash: str | None
    compiled: bool                                       # phase == "compiled"

class CompileState(BaseModel):
    design: bool                                         # any closure step not compiled, or any child in design
    reasons: list[str]                                   # "step extract: proto-step changed since compile", "child finance/x is in design", …
    steps: list[StepState]
    process_hash: str

def compile_state(ws: Workspace, pid: str) -> CompileState                      # (06)
```

Use `load_workspace(root, CommitTree(root, sha))` for per-commit status (§11: "a process can be released at commit X and in design at HEAD simultaneously").

Flag derivation:
- *design* = `CompileState.design`.
- *compiled* = not design **and** `tests_status(registry, ws, pid, closure_head).status == "passed"`.
- *built* = `artefacts.get_build(pid, closure_head)` is not `None`.
- *released* = the controller's `Release` records.

---
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
