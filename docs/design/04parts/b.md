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
