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
