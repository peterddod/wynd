# 02 — wynd.runtime core: steps, middleware, worker, executor, trace, storage

Status: draft for the synthesizer. Owner area: **runtime-core** (Apache-2.0, `packages/runtime`, deps = `wynd-spec` + stdlib only).
Out of scope here (other architects): the agentic loop, providers, tool library, MCP, cassettes, the supervisor and run API, the image builder, the process loader and validator, the compiler, the controller, the CLI, and the web UI. For each, this doc defines the **seam** it plugs into.

Contents
0. Summary of decisions
1. Spec clauses this area owns
2. Module and file list with owners
3. Steps (3.1, 3.2, 7)
4. The middleware chain (3.6) and `run_step`
5. The worker and its protocol
6. The venv plan and WorkerPool
7. The executor state machine
8. Trace
9. Storage (7.1)
10. Interfaces consumed (from other areas)
11. Interfaces provided (to other areas)
12. Interpretations
13. Test plan
14. Items for the synthesizer to reconcile

---

## 0. Summary of decisions

- `Step` is `Input`, `Output`, `pre`, `run`, `post` and nothing else. It also has a `runtime` attribute that middleware sets on each fresh instance. The worker creates **a fresh instance for every attempt**, so instance state never outlives a run. Kind is found with `issubclass`; there is no `kind` attribute.
- `Output` is introspected once per class by `interface_of(cls)` into a `StepInterface`: the declared exits, a discriminated `TypeAdapter`, and the input field names. Every step also has the implicit `error` exit. Its model is `ErrorOutput`, which carries `cause, message, type, traceback, inputs, partial_outputs, attempts, child`. Because `inputs` is included, `steps.X.outputs.inputs.<f>` lets an `X.error` edge re-bind X's inputs, which the compiler's det→agentic fallback needs.
- The middleware is `run_chain(cls, params, emit=, cache=)`. It runs inside the worker and in-process for tests. `run_step(step, input)` is a thin in-process wrapper around it.
- The worker is `<venv>/bin/python -m wynd.runtime.worker`. It speaks newline-delimited JSON-RPC 2.0 over *duplicated* stdin/stdout fds. fd 0 is replaced with /dev/null and fd 1 with stderr, so no `print`, C-level write or child process can corrupt the protocol. Stdout and stderr produced during a run are captured to a temp file and emitted as one `step.log` event.
- Step code is always imported as `wynd_steps.<step_package_name(key)>.<module>`. The name is unique per step package key (`<slug>_<sha256(key)[:10]>`). In local mode the worker mounts the package directory under that name. In image mode the builder installs the wheel under the same name. Two `./steps/read` packages in one venv therefore never collide.
- `RunPlan` (in `wynd.runtime.plan`) is the one contract between the producers (process loader, local venv manager, image builder) and the consumers (executor, WorkerPool, supervisor). It holds the resolved processes, step packages and venvs. Local mode and image mode differ **only in the data**: paths, and `path: null` in image mode.
- The `Executor` is a sequential state machine over the `RunPlan`, driven through a `Dispatcher`: either `WorkerPool` (subprocess workers) or `InProcessDispatcher` (the test seam). A `ProcessStep` node is executed by recursing into the child graph in the same executor. Counters are per process instance. Step paths nest as `parent.child`. Trace spans nest through `parent`.
- Trace is JSONL with a common envelope `{v, seq, ts, run_id, type, ...}`. The event types are `run.start`, `step.start`, `step.end`, `step.log`, `step.event`, `edge.taken`, `model.call`, `tool.call`, `process.error`, `worker.start` and `run.end`.
- Storage has four interfaces: `WorkspaceStore`, `TraceSink`, `RunRegistry` and `Registry`. Each backend is selected by an env var of the form `<scheme>[:<location>]`, resolved through the entry-point group `wynd.storage`. The local backends are `file` and `jsonl`. The runtime never assumes a home directory, except for the spec-mandated `WYND_HOME` fallback to `~/.wynd`.

---

## 1. Spec clauses this area owns (coverage audit)

| Clause | What runtime-core provides |
|---|---|
| 1 (fast path, ms step-to-step) | long-lived workers, pre-imported steps, JSON envelopes over pipes, O(1) routing |
| 2 (sequential; agentic edges schema-only in v1) | strictly sequential executor; `BranchSelector` seam for M5 agentic edges |
| 3.1 all bullets | `Step` class, per-run hooks, fresh instance per run, `self.runtime.cache`, module-state lint |
| 3.2 all four kinds | `DeterministicStep`, `AgenticStep` (attributes plus loop seam), `ShellStep`, `ProcessStep` (recursion, path nesting, scoped counters) |
| 3.3 | entry binding by field name; process usable as a step (`ProcessStep.for_process`) |
| 3.4 (runtime semantics of from/to/when/with/limits/kind) | routing algorithm, `max_traversals`, timeouts, per-branch retry override |
| 3.4.1 (runtime side) | scope construction: `steps.<n>.outputs/exit/runs`, `process.inputs`, `env`, `run.id`, `previous`, `edges[...]` counters, latest-completed-run |
| 3.5 all bullets | implicit error exit, the routing failures that go to the handler, default handler, `on_error`, `finally`, retry policy application (non-agentic), ProcessStep error propagation |
| 3.6 steps 1–6 | `run_chain` |
| 3.7 (runtime side) | `assemble_context` in the executor; the worker receives the assembled context |
| 3.8 (partial) | `self.runtime.http`; `@tool` methods remain plain methods (nothing in `Step` hides them) |
| 3.9 (partial) | `Usage`/`ModelInfo` on every `step.end`; provider/tier resolution call site |
| 4 Dispatch bullet | one worker per venv, JSON-RPC over stdio, local mode uses the same scheme |
| 4 Workspace per run | `WorkspaceStore`, kept on the error handler, deleted otherwise |
| 4 Warm pools (in-container half) | `WorkerPool` is reusable across runs; `Executor` is reusable across runs |
| 4 Env manifest (storage vars) | `STORAGE_ENV` list for `wynd build` |
| 4.1 storage-backend bullet | env-selected backends |
| 5 runtime row | the module list in §2 |
| 6.3 test results | `RunRegistry.put_test_result/get_test_result` keyed by commit + step hash |
| 6.6 serialisable compile sessions / jobs vs runs | `RunRecord.kind`, `RunRecord.session` |
| 7 all bullets except loop/cassette internals | Step API, `run_step`, handle, effects not enforced (see Interpretations) |
| 7.1 all | four storage interfaces, local implementations, env selection |
| 8 Executor bullet, Trace bullet | executor with a `mode` field and one trace format |
| 13 decisions touching the runtime | all respected; see Interpretations for the ones needing a reading |
| 15 no privileged code paths / usage observable | backends by env var; usage per step and per run in trace and run registry |

---

## 2. Module and file list with owners

The runtime-core area is split into four sub-owners so that four agents can build it in parallel against this doc: **RC-A** step model and middleware, **RC-B** worker and plan, **RC-C** executor and trace, **RC-D** storage and ids. Agentic modules (`agentic/`, `providers/`, `tools/`, `mcp/`, `cassette/`) and `supervisor/` belong to other architects. They are listed only where core imports them.

```
packages/runtime/
  pyproject.toml                         # shared file; see §2.1 for the fragment runtime-core needs
  LICENSE                                # Apache-2.0
  src/wynd/runtime/                      # NO src/wynd/__init__.py (PEP 420)
    __init__.py            RC-A  public re-exports (light: must not import executor/storage/worker)
    step.py                RC-A  Step, DeterministicStep, AgenticStep, ShellStep, ProcessStep, step_kind, StepDefinitionError
    interface.py           RC-A  StepInterface, interface_of, process_interface, StepDescription, describe_step, describe_process
    errors.py              RC-A  ErrorOutput, ProcessError, TracePointer, ErrorCause, ProcessErrorCause, StepFailure, InvalidProcessInputs
    usage.py               RC-A  Usage, ModelInfo
    policy.py              RC-A  ExecPolicy, CassetteConfig, default_retries, build_policy
    handle.py              RC-A  RuntimeHandle, StepTrace, StepCache
    http.py                RC-A  HttpClient, HttpResponse, HttpError (urllib)
    shell.py               RC-A  format_command, execute_shell (ShellStep default run)
    summary.py             RC-A  Summary, summarise, project
    middleware.py          RC-A  run_chain, ChainResult
    testing.py             RC-A  run_step, load_step, StepResult
    lint.py                RC-A  check_step_module, LintIssue
    ids.py                 RC-D  new_id, valid_id
    plan.py                RC-B  RunPlan, ProcessPlan, NodeRef, StepPlan, VenvSpec, step_package_name, dep_set_hash
    worker/__init__.py     RC-B  (empty)
    worker/__main__.py     RC-B  `python -m wynd.runtime.worker` → server.main()
    worker/protocol.py     RC-B  PROTOCOL_VERSION, RunStepParams, RunStepResult, StepTimings, InitStep, error codes, encode/decode
    worker/loader.py       RC-B  mount_step_package, load_step_class
    worker/server.py       RC-B  WorkerServer, main, stdio claim, per-run output capture
    worker/client.py       RC-B  WorkerClient, WorkerCrashed, StepTimeout, WorkerRpcError
    worker/pool.py         RC-B  Dispatcher (Protocol), WorkerPool, InProcessDispatcher
    executor/__init__.py   RC-C  re-exports Executor, ProcessResult, BranchSelector, BranchChoice, SelectCall
    executor/engine.py     RC-C  Executor (run, validate_inputs), the state machine
    executor/instance.py   RC-C  Instance, StepRecord, Deadline, scope construction
    executor/routing.py    RC-C  BranchSelector, BranchChoice, SelectCall, DeterministicSelector
    executor/context.py    RC-C  assemble_context
    trace.py               RC-C  event models, parse_event, TraceEmitter, TraceNode/TraceTree, build_tree
    storage/__init__.py    RC-D  Stores, stores_from_env, registry_from_env, STORAGE_ENV, StorageConfigError
    storage/base.py        RC-D  WorkspaceStore, TraceSink, RunRegistry, Registry (Protocols)
    storage/models.py      RC-D  RunRecord, RunKind, RunStatus, TestResult
    storage/local.py       RC-D  FileWorkspaceStore, JsonlTraceSink, FileRunRegistry, FileRegistry + factories
  tests/
    conftest.py                         RC-C  graph()/steps() helpers for synthetic plans (InProcessDispatcher)
    fixtures/steps/a/read/step.py        RC-B  class Read (exits: done)            ─┐ same folder name,
    fixtures/steps/b/read/step.py        RC-B  class Read (exits: done, empty)     ─┘ different packages
    fixtures/steps/b/read/helpers.py     RC-B  relative-import target
    fixtures/steps/misbehave/step.py     RC-B  prints, os.write(1,..), reads stdin, os._exit, sleeps
    test_step_interface.py               RC-A
    test_middleware.py                   RC-A
    test_shell_step.py                   RC-A
    test_summary.py                      RC-A
    test_run_step.py                     RC-A
    test_lint.py                         RC-A
    test_http.py                         RC-A  (local ThreadingHTTPServer, offline)
    test_plan.py                         RC-B
    test_worker_protocol.py              RC-B  (real subprocess, sys.executable)
    test_pool.py                         RC-B
    test_executor_routing.py             RC-C
    test_executor_errors.py              RC-C
    test_executor_nested.py              RC-C
    test_trace.py                        RC-C
    test_storage_local.py                RC-D
    test_ids.py                          RC-D
```

### 2.1 pyproject fragment runtime-core needs

```toml
[project]
name = "wynd-runtime"
requires-python = ">=3.12"
license = "Apache-2.0"
dependencies = ["wynd-spec==<same version>"]      # runtime and spec share a version (4)

[project.entry-points."wynd.storage"]
"workspace.file" = "wynd.runtime.storage.local:workspace_file"
"trace.jsonl"    = "wynd.runtime.storage.local:trace_jsonl"
"runs.file"      = "wynd.runtime.storage.local:runs_file"
"registry.file"  = "wynd.runtime.storage.local:registry_file"

[tool.hatch.build.targets.wheel]
packages = ["src/wynd"]      # namespace: wheel ships wynd/runtime/** only
```

The worker is started with `python -m wynd.runtime.worker`. It needs no console script. The supervisor's script (`wynd-supervisor`) belongs to the supervisor architect.

---

## 3. Steps (3.1, 3.2, 7)

### 3.1 Base class — `wynd/runtime/step.py`

```python
from __future__ import annotations
from abc import ABC, abstractmethod
from types import UnionType
from typing import Any, ClassVar, Literal, TYPE_CHECKING
from pydantic import BaseModel

if TYPE_CHECKING:
    from wynd.runtime.handle import RuntimeHandle
    from wynd.runtime.tools import Tool            # agentic architect
    from wynd.runtime.mcp import McpServer         # agentic architect
    from wynd.spec.process import ProcessSpec      # spec architect

StepKind = Literal["deterministic", "agentic", "shell", "process"]

class StepDefinitionError(TypeError):
    """A step class violates the Input/Output contract (raised by interface_of)."""

class Step(ABC):
    Input: ClassVar[type[BaseModel]]
    Output: ClassVar[type[BaseModel] | UnionType]
    runtime: RuntimeHandle            # set by middleware on each fresh instance, before pre()

    def pre(self) -> None:
        return None

    @abstractmethod
    def run(self, input: Any) -> Any: ...

    def post(self) -> None:
        return None

class DeterministicStep(Step):
    """Pure function of its inputs. No LLM; no network unless step.lock.yaml declares it."""

class AgenticStep(Step):
    """The class docstring is the instruction."""
    context: ClassVar[list[str]] = []
    tools: ClassVar[list[Tool]] = []
    mcp: ClassVar[list[McpServer]] = []

    def run(self, input: Any) -> Any:           # authors write `def run(self, input: Input) -> Output: ...`
        raise RuntimeError("AgenticStep.run is completed by the agentic loop; call it through run_chain/run_step")

class ShellStep(Step):
    command: ClassVar[list[str]]                                   # argv template, see 3.4
    exit_codes: ClassVar[dict[int | str, str]] = {0: "done", "*": "error"}

    def run(self, input: Any) -> Any:
        from wynd.runtime.shell import execute_shell
        return execute_shell(self, input)

class ProcessStep(Step):
    """A whole process used as a step. Created from `use: process:<id>`; never hand-written."""
    process_id: ClassVar[str]

    @classmethod
    def for_process(cls, process_id: str, spec: ProcessSpec) -> type[ProcessStep]: ...
    def run(self, input: Any) -> Any:
        raise RuntimeError("ProcessStep nodes are executed by the executor")

def step_kind(cls: type[Step]) -> StepKind:
    """issubclass-based: ProcessStep > ShellStep > AgenticStep > DeterministicStep. A bare Step subclass is 'deterministic'."""
```

Rules:

- Middleware sets `runtime` on the instance. It is not a constructor argument, so `run` signatures stay plain (7). Steps must have a **no-arg constructor**.
- `pre` and `post` run once per attempt, never per worker lifetime.
- `AgenticStep.run` is never called. Middleware always routes an `AgenticStep` through the loop seam (§4.3). Authors keep writing `def run(self, input: Input) -> Output: ...` for typing, per 3.2.
- `ProcessStep.for_process` builds a subclass with a dynamically built `Input` (from `spec.inputs`) and `Output` (union of exit models from `spec.outputs`), using the spec type builder (§10 C3). The executor uses it through `process_interface` only.

### 3.2 Exits and `Output` introspection — `wynd/runtime/interface.py`

```python
from dataclasses import dataclass
from pydantic import BaseModel, TypeAdapter

@dataclass(frozen=True)
class StepInterface:
    kind: StepKind
    input_model: type[BaseModel]
    exits: dict[str, type[BaseModel]]       # declared exits, declaration order; never contains "error"
    output_adapter: TypeAdapter[Any]        # Annotated[Union[*exits], Field(discriminator="exit")] (or the single model)

    def exit_model(self, name: str) -> type[BaseModel]: ...   # "error" -> ErrorOutput; KeyError otherwise
    def input_fields(self) -> list[str]: ...                  # list(input_model.model_fields)
    def output_json_schema(self) -> dict[str, Any]: ...        # output_adapter.json_schema(); for providers
    def validate_output(self, value: BaseModel | Mapping[str, Any]) -> BaseModel: ...
        # data = value.model_dump(mode="json") if BaseModel else dict(value); return output_adapter.validate_python(data)
        # raises pydantic.ValidationError; raises TypeError for any other type

def interface_of(cls: type[Step]) -> StepInterface: ...              # functools.cache per class
def process_interface(process_id: str, spec: ProcessSpec) -> StepInterface: ...   # cached per (id, id(spec))

class StepDescription(BaseModel):
    key: str
    cls: str                               # "wynd_steps.read_pdf_3f9a1c2b7d.step:ReadPdf" or "process:<id>"
    kind: StepKind
    doc: str | None
    input_schema: dict[str, Any]
    input_fields: list[str]
    required_inputs: list[str]
    exits: dict[str, dict[str, Any]]       # exit -> JSON schema of its model, INCLUDING "error"
    context: list[str] = []                # AgenticStep.context, else []
    agentic: dict[str, Any] | None = None  # from wynd.runtime.agentic.describe_agentic(cls) (agentic architect)
    shell: dict[str, Any] | None = None    # {"command": [...], "exit_codes": {"0": "done", "*": "error"}}

def describe_step(cls: type[Step], key: str) -> StepDescription: ...
def describe_process(process_id: str, spec: ProcessSpec) -> StepDescription: ...
```

`interface_of` algorithm:

1. `Input` must be a `BaseModel` subclass; otherwise raise `StepDefinitionError`.
2. `out = cls.Output`. If `get_origin(out) is Annotated`, unwrap it to `get_args(out)[0]`.
3. If `out` is a `BaseModel` subclass, it uses the **plain-model shorthand**. It must have an `exit` field annotated `Literal["done"]`, so `members = [out]`. Any other literal raises: "plain Output model must be the done exit; use a union for other exits".
4. Otherwise `get_origin(out)` must be `types.UnionType` or `typing.Union`. Then `members = get_args(out)`, and each member must be a `BaseModel` subclass.
5. For each member: `ann = m.model_fields["exit"].annotation`. It must satisfy `get_origin(ann) is Literal` with exactly one `str` arg, which is the exit name. The name must match `^[a-z_][a-z0-9_]*$`, must not be `"error"` (reserved; the implicit exit is never declared), and must be unique.
6. Build `output_adapter = TypeAdapter(Annotated[Union[tuple(members)], Field(discriminator="exit")])`. With a single member, use `TypeAdapter(members[0])`.

`process_interface` builds exit models with `wynd.spec` (§10 C3) for **every** declared process exit. A single exit of any name is allowed here, because the plain-model rule applies to hand-written steps only.

### 3.3 The implicit `error` exit and `ProcessError` — `wynd/runtime/errors.py`

```python
ErrorCause = Literal[
    "input_validation",   # bound inputs failed Input validation (no retry)
    "exception",          # run() raised
    "hook",               # pre() or post() raised
    "output_validation",  # outputs invalid after retries
    "tool",               # a tool call failed (agentic loop)
    "transport",          # provider transport failure after a tool call, or restarts exhausted
    "shell_exit",         # ShellStep exit code mapped to "error"
    "child_process",      # ProcessStep child ended in $exit.error
    "worker_crash",       # worker process died, or JSON-RPC failure
    "import",             # step class failed to import or violates the interface
    "config",             # missing registry entry (provider/MCP) at dispatch time
]

class ErrorOutput(BaseModel):
    exit: Literal["error"] = "error"
    cause: ErrorCause
    message: str
    type: str | None = None                   # qualified exception class, e.g. "ValueError"
    traceback: str | None = None
    inputs: dict[str, Any] = {}               # bound inputs as received (JSON), even when invalid
    partial_outputs: dict[str, Any] | None = None   # best-effort JSON of what run/loop produced
    attempts: int = 1
    child: ProcessError | None = None         # set when cause == "child_process"

ProcessErrorCause = Literal[
    "step_error",              # a step took `error` and there is no `<step>.error` edge
    "unrouted_exit",           # a non-error exit has no edge (validator normally prevents)
    "no_branch_matched",       # every branch had `when:` and none was true
    "max_traversals",          # branch taken count reached limits.max_traversals
    "timeout",                 # limits.timeout on the branch into the step (or an enclosing ProcessStep)
    "expression_error",        # when:/with: evaluation raised
    "invalid_process_outputs", # $exit.<name> binding failed the process outputs model
    "unsupported_edge",        # edge.kind has no BranchSelector registered
    "internal",                # executor/storage bug; run still ends cleanly
]

class TracePointer(BaseModel):
    run_id: str
    uri: str                                  # TraceSink.uri(run_id)
    seq: int                                  # seq of the process.error event

class ProcessError(BaseModel):
    process: str                              # process id of the instance that failed
    step: str | None                          # full step path ("fix", "sub.read"); None for process-level
    cause: ProcessErrorCause
    message: str
    edge: str | None = None                   # "validate.done" or "validate.done[1]"
    inputs: dict[str, Any] = {}               # the failing step's bound inputs
    partial_outputs: dict[str, Any] | None = None
    step_error: ErrorOutput | None = None     # when cause == "step_error"
    handler_error: ErrorOutput | None = None  # set if a custom on_error handler itself failed
    trace: TracePointer | None = None
    workspace: str | None = None              # filled at run end when the workspace is kept

ErrorOutput.model_rebuild()

class StepFailure(Exception):
    """Raised by the agentic loop / shell helper to resolve a step to its error exit with a specific cause."""
    def __init__(self, cause: ErrorCause, message: str, *, partial_outputs: dict[str, Any] | None = None,
                 usage: "Usage | None" = None, attempts: int | None = None) -> None: ...

class InvalidProcessInputs(ValueError):
    """Top-level process inputs failed validation. Raised before a run is created (boundary check)."""
    def __init__(self, errors: list[dict[str, Any]]) -> None: ...
```

Example: an `error` exit output. Its `steps.parse.outputs` is exactly this without `exit`.

```json
{"exit":"error","cause":"exception","message":"ValueError: no total found","type":"ValueError",
 "traceback":"Traceback (most recent call last):\n ...","inputs":{"invoice_text":"INV-1042 ..."},
 "partial_outputs":null,"attempts":1,"child":null}
```

Here is the compiler's det→agentic fallback. It works because `inputs` travels with the error:

```yaml
- from: extract_det.error
  to: extract_llm
  with: { invoice_text: steps.extract_det.outputs.inputs.invoice_text }
```

### 3.4 `ShellStep` — `wynd/runtime/shell.py`

```python
def format_command(template: list[str], inputs: dict[str, Any]) -> list[str]: ...
def execute_shell(step: ShellStep, input: BaseModel) -> BaseModel | dict[str, Any]: ...
```

- **Command.** `command` is an argv **list**; no shell is involved. Each element is formatted with a `string.Formatter` subclass over `inputs = input.model_dump(mode="json")`, so `"{pdf_path}"` works. There is one extra conversion, `!q`, which applies `shlex.quote(str(value))`. It is for templates that invoke a shell explicitly: `["sh","-c","pdftotext {pdf_path!q} - | head -50"]`.
- **Environment.** The child gets `os.environ` as it is after `pre()`, plus `WYND_INPUT=<inputs JSON>`. Its stdin is `DEVNULL` and its cwd is the run workspace (middleware already did the chdir). It is run with `subprocess.run(argv, capture_output=True, text=True, stdin=DEVNULL, env=..., check=False)`. Timeouts are executor-level (§7.8).
- **Exit mapping.** `name = exit_codes.get(returncode, exit_codes.get(str(returncode), exit_codes.get("*", "error")))`. Keys may be ints, digit strings or `"*"`.
- If `name == "error"`: raise `StepFailure("shell_exit", f"exit code {rc}: {stderr[-2000:]}", partial_outputs={"exit_code": rc, "stdout": stdout[-2000:], "stderr": stderr[-2000:]})`.
- Otherwise `model = interface.exit_model(name)` and `fields = set(model.model_fields) - {"exit"}`. Set `data = {k: v for k, v in {"stdout": stdout, "stderr": stderr, "exit_code": rc}.items() if k in fields}`. If `fields - {"stdout","stderr","exit_code"}` is non-empty, stdout must be a JSON object and `data.update(json.loads(stdout))`; a JSON error means output validation fails. The step returns `{"exit": name, **data}` and middleware validates it.
- Authors may override `run` to post-process (it is just code). Proto-step `exit_codes: {0: done, 1: not_found, "*": error}` maps 1:1 onto the class attribute.

### 3.5 `ProcessStep` semantics (executed by the executor, §7.7)

- The child process runs in the **same executor, same container, same run id**. Its step paths are `<node path>.<child step>` (for example `sub.read`, then `sub.inner.read`). Child events carry `parent = <span of the ProcessStep node's step.start>`.
- Each child invocation is a **new process instance**, with fresh `steps.<n>.runs`, `edges[...]` counters and `previous`.
- Child inputs are validated against the child's inputs model. A failure is the node's `error` exit with cause `input_validation`.
- When the child ends in `$exit.error`, the node's exit is `error` with `ErrorOutput(cause="child_process", message=child.message, inputs=bound, partial_outputs=child.partial_outputs, child=<child ProcessError>)`. Any other child exit `X` becomes the node's exit `X`, with outputs equal to the child's outputs.
- The child's `env.base`, `provider` and `latency` are ignored. Provider resolution in the child uses the **root** process's default (§7.9).

### 3.6 The `self.runtime` handle — `wynd/runtime/handle.py`

```python
class StepCache:
    """Explicit cross-run cache: one instance per step key per worker process lifetime (lost on worker restart)."""
    def get(self, key: str, default: Any = None) -> Any: ...
    def set(self, key: str, value: Any) -> None: ...
    def get_or_set(self, key: str, factory: Callable[[], T]) -> T: ...
    def delete(self, key: str) -> None: ...
    def clear(self) -> None: ...

class StepTrace:
    def event(self, name: str, **data: Any) -> None: ...          # emits {"type":"step.event","name":name,"data":data}
    def emit(self, type: str, **fields: Any) -> None: ...         # low-level; reserved for runtime internals
                                                                  # (agentic loop: "model.call", "tool.call")

@dataclass
class RuntimeHandle:
    run_id: str
    step_path: str
    step_run: int
    workspace: Path                       # the run workspace (also the cwd during pre/run/post)
    logger: logging.Logger                # "wynd.step.<step_path>"; records -> step.log events (live)
    trace: StepTrace
    cache: StepCache
    http: HttpClient
    env: Mapping[str, str]                # os.environ (live; restored by middleware after post)
```

- `logger`: at startup the worker installs one handler on logger `wynd.step` (level DEBUG, `propagate=False`). It forwards each record as a `step.log` notification (`{"level","message","stream":"logger"}`) for the run in progress. In-process (`run_step`), records go into `StepResult.events`.
- `http` (`wynd/runtime/http.py`, stdlib `urllib.request`):

```python
@dataclass(frozen=True)
class HttpResponse:
    url: str; status: int; headers: dict[str, str]; body: bytes
    def text(self) -> str: ...
    def json(self) -> Any: ...
    def raise_for_status(self) -> "HttpResponse": ...     # raises HttpError on status >= 400

class HttpError(Exception):
    response: HttpResponse | None

class HttpClient:
    def __init__(self, *, timeout: float = 30.0, user_agent: str = f"wynd-runtime/{__version__}") -> None: ...
    def request(self, method: str, url: str, *, params: Mapping[str, str] | None = None,
                headers: Mapping[str, str] | None = None, json: Any = None, data: bytes | None = None,
                timeout: float | None = None) -> HttpResponse: ...   # non-2xx returned, not raised; network errors raise HttpError(response=None)
    def get(self, url: str, **kw: Any) -> HttpResponse: ...
    def post(self, url: str, **kw: Any) -> HttpResponse: ...
```

### 3.7 "No module-level state": how it is enforced

The rule is enforced **by construction** plus **one static check**. There is no runtime watching.

1. By construction: there is a fresh instance per attempt; `os.environ` and the cwd are restored after every run; and the only persistent store offered is `self.runtime.cache`.
2. Static lint, `wynd/runtime/lint.py`. The process validator calls it (M1) on every non-test `.py` file of each step package, and so does the compiler (M3):

```python
@dataclass(frozen=True)
class LintIssue:
    path: str; line: int; col: int; code: str; severity: Literal["error", "warning"]; message: str

def check_step_module(source: str, path: str) -> list[LintIssue]: ...
```

Rules (AST only, no imports):
- `W001` (**error**): a `global` statement anywhere, or `nonlocal` at module level.
- `W002` (**error**): a module-level `Assign`/`AnnAssign`/`AugAssign` whose value contains a `List`, `Dict`, `Set`, `ListComp`, `DictComp`, `SetComp` or `GeneratorExp`. The one exception is target `__all__`.
- `W003` (**warning**): a module-level `Assign` whose value is a `Call`. The allowlist is `re.compile`, `frozenset`, `tuple`, `TypeVar`, `NewType` and `logging.getLogger`.
- The following are allowed at module level: imports, `def`/`class`, the docstring, `if TYPE_CHECKING:` blocks, and assignments of constants, names, attributes, subscripts (`Literal[...]`, `Annotated[...]`) and `|` unions of names. Class bodies are **not** checked, because `tools = [...]`, `context = [...]` and `mcp = [...]` are declarations.

---

## 4. The middleware chain (3.6) and `run_step`

### 4.1 Wire models used by the chain — `wynd/runtime/policy.py`, `wynd/runtime/usage.py`

```python
# usage.py  (runtime-core owns; the agentic architect's GenerateResponse/AgentResponse reuse it)
class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    cost_usd: float | None = None          # None = unknown
    latency_ms: float = 0.0                # sum of per-call latencies
    calls: int = 0
    def __add__(self, other: "Usage") -> "Usage": ...   # cost: None if both None, else sum of known

class ModelInfo(BaseModel):
    provider: str; model_id: str; tier: str | None = None; thinking: str | None = None

# policy.py
class ExecPolicy(BaseModel):
    kind: StepKind
    retries: RetryPolicy                   # wynd.spec.lock.RetryPolicy (see §10 C4)
    provider: str | None = None            # agentic only: resolved provider name
    model_id: str | None = None            # agentic only: resolved via Registry tier map
    tier: str | None = None
    thinking: str | None = None
    effects: list[str] = []
    mcp: list[dict[str, Any]] = []         # lock MCP snapshot entries, each + {"server": <Registry "mcp" entry>}

class CassetteConfig(BaseModel):
    mode: Literal["live", "record", "replay"] = "live"
    dir: str | None = None                 # source package cassettes dir (replay reads here)
    record_dir: str | None = None          # <workspace>/.wynd/cassettes/<step_package_name>/ (record writes here)

def default_retries(kind: StepKind) -> RetryPolicy: ...
    # deterministic/shell/process: validation=0, tool=0, exception=0 ; agentic: validation=2, tool=1, exception=0
def build_policy(kind: StepKind, lock: StepLock | None, *, default_provider: str, registry: Registry,
                 retry_override: RetryPolicy | None = None) -> ExecPolicy: ...
    # retries = lock.retries or default_retries(kind); if retry_override: retries = retries.model_copy(
    #     update=retry_override.model_dump(exclude_unset=True))
    # agentic: provider = lock.provider or default_provider; tier = lock.tier or "cheap";
    #          thinking = lock.thinking or "low"; model_id = resolve_model(provider, tier, registry)  (§10 C7)
    #          mcp = [{**snap, "server": registry.get("mcp", snap["name"])} for snap in lock.mcp]
    # A missing provider/MCP registry entry raises StepFailure("config", ...), which the executor
    # converts into the step's error exit without dispatching.
```

### 4.2 `run_chain` — `wynd/runtime/middleware.py`

```python
@dataclass
class ChainResult:
    exit: str
    output: BaseModel                     # validated exit model instance, or ErrorOutput
    outputs: dict[str, Any]               # output.model_dump(mode="json") without "exit"
    summary: Summary
    attempts: int
    timings: StepTimings                  # worker-side timings (protocol.py)
    usage: Usage | None
    model: ModelInfo | None
    def to_result(self) -> RunStepResult: ...

def run_chain(cls: type[Step], params: RunStepParams, *,
              emit: Callable[[dict[str, Any]], None], cache: StepCache) -> ChainResult: ...
```

Exact algorithm (these are the numbered 3.6 stages):

```
started_at = utcnow(); t0 = perf_counter()
try: iface = interface_of(cls)
except StepDefinitionError as e: return error("import", e, attempts=0)

handle = RuntimeHandle(run_id, step_path, step_run, workspace=Path(params.workspace), logger, trace, cache, http, os.environ)

# 1. Receive bound inputs (executor evaluated with:) → validate
try: inp = iface.input_model.model_validate(params.inputs)
except ValidationError as e: return error("input_validation", e, attempts=0)

kind = step_kind(cls); r = params.policy.retries
exc_left = r.exception if kind in ("deterministic", "shell") else 0
val_left = r.validation if kind != "agentic" else 0     # agentic validation retries live in the loop
attempts = 0
while True:
    attempts += 1
    step = cls(); step.runtime = handle                   # fresh instance per attempt
    env_before, cwd_before = dict(os.environ), os.getcwd()   # 3. snapshot before pre
    os.chdir(params.workspace)
    raw = None; agent = None; failure = None               # failure: (cause, exc)
    try:
        try: step.pre()
        except (Exception, SystemExit) as e: failure = ("hook", e)
        else:
            try:
                if kind == "agentic":
                    # 2. Build context: the executor assembled it (params.context); handed to the loop
                    agent = complete(step, AgentCall(step_path, inp, params.context, iface,
                                                     params.policy, params.cassette, handle))
                    raw = agent.output
                else:
                    raw = step.run(inp)
            except StepFailure as e: failure = (e.cause, e)
            except (Exception, SystemExit) as e: failure = ("exception", e)
            finally:
                try: step.post()                            # post runs iff pre returned
                except (Exception, SystemExit) as e: failure = failure or ("hook", e)
    finally:
        restore(env_before, cwd_before)                     # 3. restore after post
    if failure:
        if exc_left > 0 and failure[0] in ("exception", "hook", "shell_exit"):
            exc_left -= 1; continue
        return error(failure[0], failure[1], attempts, partial=best_effort_json(raw) or getattr(failure[1], "partial_outputs", None),
                     usage=getattr(failure[1], "usage", None))
    # 4. Parse & validate outputs
    try: out = iface.validate_output(raw)
    except (ValidationError, TypeError) as e:
        if val_left > 0: val_left -= 1; continue
        return error("output_validation", e, attempts, partial=best_effort_json(raw))
    break
outputs = out.model_dump(mode="json"); exit = outputs.pop("exit")
# 5. Summarise (deterministic)
summary = summarise(params.step_path, exit, outputs, note=agent.note if agent else "")
# 6. Emit exit: returned to the caller (worker server → JSON-RPC response → executor)
return ChainResult(exit, out, outputs, summary, attempts, timings, usage=agent.usage if agent else None,
                   model=agent.model if agent else None)
```

- `restore(env_before, cwd_before)`: `os.chdir(cwd_before)`. Delete keys that were added, then re-set every key whose value changed. This only touches keys that differ, so there is no `clear()`.
- `error(cause, exc, attempts, partial, usage)` builds `ErrorOutput(cause, message=f"{type(exc).__name__}: {exc}", type=qualname, traceback="".join(traceback.format_exception(exc)), inputs=params.inputs, partial_outputs=partial, attempts=attempts)` and summarises it.
- `best_effort_json(raw)`: `model_dump(mode="json")` for a `BaseModel`, `pydantic_core.to_jsonable_python` for a mapping, otherwise `{"repr": repr(raw)[:2000]}`. `None` stays `None`.
- `StepTimings` holds the worker view: `started_at`, `ended_at`, `worker_ms`, and `pre_ms`/`run_ms`/`post_ms` of the last attempt.

### 4.3 The agentic seam (the loop is another architect's)

Middleware lazily imports `from wynd.runtime.agentic.loop import complete` inside the agentic branch. Deterministic steps therefore never import agentic code.

```python
# provided by runtime-core in wynd/runtime/middleware.py (types) — consumed by the loop
@dataclass(frozen=True)
class AgentCall:
    step_path: str
    input: BaseModel                     # validated Input instance
    context: dict[str, Any] | None       # assembled by the executor (3.7); keys are the declared strings
    interface: StepInterface             # exits, output_adapter, output_json_schema()
    policy: ExecPolicy                   # provider, model_id, tier, thinking, retries (validation, tool), mcp
    cassette: CassetteConfig
    runtime: RuntimeHandle               # workspace, logger, cache, http, env; runtime.trace.emit("model.call"/"tool.call", ...)

@dataclass(frozen=True)
class AgentResult:
    output: BaseModel                    # a validated declared-exit model instance
    note: str                            # final free text ("" if none) → Summary.note
    usage: Usage
    attempts: int                        # structured-output attempts (1 + validation retries used)
    model: ModelInfo

# implemented by the agentic architect in wynd/runtime/agentic/loop.py
def complete(step: AgenticStep, call: AgentCall) -> AgentResult: ...
    # raises StepFailure(cause in {"output_validation","tool","transport","config"}, ..., usage=..., attempts=...)
```

The loop owns everything 3.5 says about agentic retries: continuing the conversation after validation, the ban on restarting after a tool call, and idempotent-only tool retries. Middleware never retries an agentic step. It re-validates `AgentResult.output` once through `validate_output`, which is cheap and guards the boundary.

### 4.4 Summary — `wynd/runtime/summary.py`

```python
class Summary(BaseModel):
    step: str                 # step path
    exit: str
    key_outputs: dict[str, Any]
    note: str = ""

MAX_CHARS = 200
def project(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float)): return value
    if isinstance(value, str): return value if len(value) <= MAX_CHARS else value[:MAX_CHARS - 3] + "..."
    s = json.dumps(value, separators=(",", ":"), ensure_ascii=False, default=str)
    if len(s) <= MAX_CHARS: return value
    if isinstance(value, list): return f"<list: {len(value)} items>"
    if isinstance(value, dict): return f"<object: {len(value)} keys>"
    return f"<{type(value).__name__}>"

def summarise(step: str, exit: str, outputs: dict[str, Any], note: str = "") -> Summary:
    keys = [k for k in outputs if not (exit == "error" and k in ("traceback", "inputs", "partial_outputs", "child"))]
    return Summary(step=step, exit=exit, key_outputs={k: project(outputs[k]) for k in keys}, note=note)
```

The summary is a pure function of the JSON-mode outputs and never calls a model. Field order follows model declaration order.

### 4.5 `run_step` and `load_step` — `wynd/runtime/testing.py`

These are for compiler-generated tests. They run **in-process** in the pytest interpreter, which is the step's venv under `wynd test`.

```python
@dataclass
class StepResult:
    exit: str
    output: BaseModel                    # exit model instance, or ErrorOutput
    outputs: dict[str, Any]
    summary: Summary
    attempts: int
    usage: Usage | None
    model: ModelInfo | None
    events: list[dict[str, Any]]         # notifications emitted during the run (step.log, model.call, tool.call, ...)
    workspace: Path

def run_step(step: type[Step], input: BaseModel | Mapping[str, Any], *,
             mode: Literal["live", "record", "replay"] | None = None,   # default: env WYND_CASSETTE_MODE or "replay"
             cassettes: str | Path | None = None,     # default: <package dir>/cassettes
             lock: StepLock | Mapping[str, Any] | None = None,   # default: <package dir>/step.lock.yaml if present
             context: Mapping[str, Any] | None = None,
             workspace: str | Path | None = None,     # default: tempfile.mkdtemp(prefix="wynd-step-") (not removed)
             retry: RetryPolicy | None = None) -> StepResult: ...

def load_step(step_dir: str | Path, cls: str | None = None) -> type[Step]: ...
    # reads entrypoint from <step_dir>/step.lock.yaml via wynd.spec (C4) unless cls ("module:Class") given;
    # mounts the directory with worker.loader.mount_step_package(key=str(resolved step_dir), path=step_dir)
```

- `<package dir>` is `Path(inspect.getfile(step)).parent`.
- `run_step` builds `RunStepParams`: `run_id="test"`, `step_path=step.__name__`, `step_run=1`. The policy comes from `build_policy(kind, lock, default_provider=DEFAULT_PROVIDER, registry=registry_from_env(), retry_override=retry)`. It then calls `run_chain(step, params, emit=events.append, cache=StepCache())` and exercises the full chain.
- Replay: `CassetteConfig(mode, dir=cassettes, record_dir=<workspace>/.wynd/cassettes/<pkg>)`. The cassette layer (agentic architect) raises its explicit "no recording" error on a miss. That error surfaces as `StepFailure("transport", ...)`, and the test sees `exit == "error"`. The generated test asserts on the exit, so it fails loudly.
- A generated test looks like this:

```python
from pathlib import Path
from wynd.runtime.testing import load_step, run_step
ExtractInvoiceFields = load_step(Path(__file__).parent)

def test_example_1():
    r = run_step(ExtractInvoiceFields, {"invoice_text": "..."})
    assert r.exit == "done"
    assert r.outputs == {"invoice_number": "INV-1042", "total": 1200.5, "currency": "GBP", "due_date": "2026-10-01"}
```

(`wynd test` must run pytest with `--import-mode=importlib`, so that two `test_read.py` files in one session do not clash. That is the test-runner owner's concern; see §14.)

---

## 5. The worker and its protocol

### 5.1 Process and stdio — `wynd/runtime/worker/server.py`

It is launched as `<venv python> -m wynd.runtime.worker`, with no arguments.

```python
def main() -> None:
    signal.signal(signal.SIGINT, signal.SIG_IGN)           # the parent owns shutdown (EOF / "shutdown")
    proto_in, proto_out = claim_stdio()
    server = WorkerServer(proto_out)
    for line in proto_in:                                  # one JSON object per line
        reply = server.handle_line(line)
        if reply is not None: server.write(reply)
        if server.stopping: break
    sys.exit(0)                                            # EOF => exit 0

def claim_stdio() -> tuple[TextIO, TextIO]:
    in_fd, out_fd = os.dup(0), os.dup(1)                   # PEP 446: dups are non-inheritable
    devnull = os.open(os.devnull, os.O_RDONLY); os.dup2(devnull, 0); os.close(devnull)
    os.dup2(2, 1)                                          # fd 1 -> stderr: print()/C writes/children can't touch protocol
    sys.stdout.reconfigure(line_buffering=True)
    return (os.fdopen(in_fd, "r", encoding="utf-8", newline="\n"),
            os.fdopen(out_fd, "w", encoding="utf-8", newline="\n"))
```

- **Per-run output capture.** Before `run_chain`, flush `sys.stdout` and `sys.stderr`, save `dup(1)` and `dup(2)`, truncate a worker-lifetime `tempfile.TemporaryFile()`, and `dup2` it over fds 1 and 2. After the chain, flush, restore the fds, read up to the last 64 KiB, and if the text is non-empty emit one notification: `{"type":"step.log","level":"INFO","stream":"output","message":<text>}`. Worker stderr outside runs (for example crash tracebacks) is inherited by the parent, so it reaches the terminal or container logs.
- **Writes.** `WorkerServer.write(obj)` holds a `threading.Lock`, calls `json.dumps(obj, separators=(",",":"), ensure_ascii=False, default=str)`, appends `"\n"` and flushes. `json.dumps` never emits a raw newline inside a string, so line framing is safe.
- **State.** `steps: dict[str, type[Step] | Exception]` holds the loaded class or the import error. `caches: dict[str, StepCache]` holds one cache per step key. `current: RunStepParams | None` is used by the log handler.
- The server handles one request at a time on the main thread.

### 5.2 Unique module naming — `wynd/runtime/plan.py`, `wynd/runtime/worker/loader.py`

```python
def step_package_name(key: str) -> str:
    """'processes/process_supplier_invoice/steps/read_pdf' -> 'read_pdf_3f9a1c2b7d'."""
    last = key.rstrip("/").rsplit("/", 1)[-1].rsplit(":", 1)[-1]
    slug = re.sub(r"\W", "_", last.lower()) or "step"
    if slug[0].isdigit(): slug = "s_" + slug
    return f"{slug}_{hashlib.sha256(key.encode()).hexdigest()[:10]}"

# loader.py
STEPS_NS = "wynd_steps"

def mount_step_package(key: str, path: str | Path) -> str:
    """Register directory `path` as package wynd_steps.<step_package_name(key)>; return its dotted name. Idempotent."""
    # 1. ensure 'wynd_steps' in sys.modules: try importlib.import_module (installed wheels make it a PEP 420 namespace);
    #    on ModuleNotFoundError create ModuleSpec("wynd_steps", None, is_package=True) with empty search path.
    # 2. name = f"wynd_steps.{step_package_name(key)}"; if name in sys.modules: return name
    # 3. init = path/"__init__.py": spec = spec_from_file_location(name, init, submodule_search_locations=[str(path)])
    #    if init exists, else ModuleSpec(name, None, is_package=True) with submodule_search_locations=[str(path)]
    # 4. module = module_from_spec(spec); sys.modules[name] = module; exec_module if loader; return name

def load_step_class(key: str, entrypoint: str, path: str | None) -> type[Step]:
    """entrypoint = '<module>:<Class>' relative to the step package ('step:ReadPdf')."""
    pkg = mount_step_package(key, path) if path is not None else f"{STEPS_NS}.{step_package_name(key)}"
    mod_name, _, cls_name = entrypoint.partition(":")
    cls = getattr(importlib.import_module(f"{pkg}.{mod_name}"), cls_name)
    if not (isinstance(cls, type) and issubclass(cls, Step)): raise StepDefinitionError(...)
    interface_of(cls)                                       # validate at import time
    return cls
```

**Step package contract** (shared with the compiler, the loader and the builder; see §14):
- The step directory *is* the Python package. It holds `step.py` (the entrypoint module) and optional helper modules. Intra-package imports are **relative only** (`from .helpers import parse`), never by folder name.
- In local mode the worker mounts the directory by path under `wynd_steps.<step_package_name(key)>`.
- In image mode, the **builder must install the wheel so that its modules land at `site-packages/wynd_steps/<step_package_name(key)>/`**. `wynd_steps` is a namespace package with no `__init__.py`. The suggested way to do this is for the builder to stage `wynd_steps/<pkg>/*.py` (excluding `test_*.py` and `cassettes/`) and build the wheel from the staging directory.
- The import path is identical in both modes, so tracebacks and cassette keys that include module names match.

### 5.3 Messages — `wynd/runtime/worker/protocol.py`

The transport is JSON-RPC 2.0, one message per line. Requests go client → worker. Notifications go worker → client, and are sent only while a request is in flight.

```python
PROTOCOL_VERSION = 1

class InitStep(BaseModel):
    key: str
    entrypoint: str                 # "step:ReadPdf"
    path: str | None                # local: absolute package dir; image: None

class RunStepParams(BaseModel):
    run_id: str
    step_path: str                  # "validate", "sub.read"
    step_run: int                   # 1-based run count of this node in this process instance
    key: str
    inputs: dict[str, Any]
    context: dict[str, Any] | None = None
    workspace: str                  # absolute local path
    policy: ExecPolicy
    cassette: CassetteConfig = CassetteConfig()

class StepTimings(BaseModel):
    started_at: datetime; ended_at: datetime
    worker_ms: float
    pre_ms: float | None = None; run_ms: float | None = None; post_ms: float | None = None

class RunStepResult(BaseModel):
    exit: str
    outputs: dict[str, Any]         # without "exit"
    summary: Summary
    attempts: int
    timings: StepTimings
    usage: Usage | None = None
    model: ModelInfo | None = None

# JSON-RPC error codes
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = -32700, -32600, -32601, -32602, -32603
NOT_INITIALISED, UNKNOWN_STEP, PROTOCOL_MISMATCH = 1001, 1002, 1003
```

**`init`** comes first. The worker imports every step. An import failure is reported per step and is **not** fatal to the worker.

```json
{"jsonrpc":"2.0","id":1,"method":"init","params":{"protocol":1,"runtime_version":"0.1.0","steps":[
  {"key":"processes/process_supplier_invoice/steps/read_pdf","entrypoint":"step:ReadPdf",
   "path":"/Users/peter/.../examples/invoices/processes/process_supplier_invoice/steps/read_pdf"},
  {"key":"processes/child/steps/read","entrypoint":"step:Read","path":"/Users/peter/.../processes/child/steps/read"}]}}
{"jsonrpc":"2.0","id":1,"result":{"protocol":1,"runtime_version":"0.1.0","python":"3.12.7","pid":48213,
  "steps":{"processes/process_supplier_invoice/steps/read_pdf":{"ok":true},
           "processes/child/steps/read":{"ok":false,"error":{"type":"ModuleNotFoundError","message":"No module named 'pypdf'","traceback":"..."}}}}}
```

A differing `protocol` gets error `1003`. A differing `runtime_version` is only reported, and the client emits a warning log.

**`describe`** takes `keys` (or `null` for all loaded steps):

```json
{"jsonrpc":"2.0","id":2,"method":"describe","params":{"keys":null}}
{"jsonrpc":"2.0","id":2,"result":{"steps":{"processes/process_supplier_invoice/steps/read_pdf":{
  "key":"processes/process_supplier_invoice/steps/read_pdf","cls":"wynd_steps.read_pdf_3f9a1c2b7d.step:ReadPdf",
  "kind":"deterministic","doc":"Extract text from a PDF.","input_schema":{"type":"object","properties":{"pdf_path":{"type":"string"}},"required":["pdf_path"]},
  "input_fields":["pdf_path"],"required_inputs":["pdf_path"],
  "exits":{"done":{"...":"json schema"},"not_a_pdf":{"...":"..."},"error":{"...":"ErrorOutput schema"}},
  "context":[],"agentic":null,"shell":null}}}}
```

A step whose import failed is described as `{"error": {...}}` instead.

**`run_step`**:

```json
{"jsonrpc":"2.0","id":7,"method":"run_step","params":{
  "run_id":"run_20260922T215001123_a1b2c3","step_path":"extract","step_run":1,
  "key":"processes/process_supplier_invoice/steps/extract_invoice_fields",
  "inputs":{"invoice_text":"INVOICE INV-1042 ..."},
  "context":{"process.goal":"Turn a supplier invoice PDF into a validated record in the records store.",
             "previous.summary":{"step":"read","exit":"done","key_outputs":{"text":"INVOICE INV-1042 ...","pages":1},"note":""}},
  "workspace":"/Users/peter/.../examples/invoices/.wynd/workspaces/run_20260922T215001123_a1b2c3",
  "policy":{"kind":"agentic","retries":{"validation":2,"tool":1,"exception":0},"provider":"claude-code",
            "model_id":"haiku","tier":"cheap","thinking":"low","effects":[],"mcp":[]},
  "cassette":{"mode":"live","dir":".../steps/extract_invoice_fields/cassettes",
              "record_dir":".../workspaces/run_.../.wynd/cassettes/extract_invoice_fields_9c0e4b1a22"}}}
{"jsonrpc":"2.0","method":"event","params":{"type":"step.log","level":"INFO","stream":"logger","message":"calling provider"}}
{"jsonrpc":"2.0","method":"event","params":{"type":"model.call","provider":"claude-code","model_id":"haiku","...":"agentic architect's fields"}}
{"jsonrpc":"2.0","id":7,"result":{"exit":"done",
  "outputs":{"invoice_number":"INV-1042","total":1200.5,"currency":"GBP","due_date":"2026-10-01"},
  "summary":{"step":"extract","exit":"done","key_outputs":{"invoice_number":"INV-1042","total":1200.5,"currency":"GBP","due_date":"2026-10-01"},"note":""},
  "attempts":1,
  "timings":{"started_at":"2026-09-22T21:50:01.201Z","ended_at":"2026-09-22T21:50:03.114Z","worker_ms":1913.2,"pre_ms":0.01,"run_ms":1912.8,"post_ms":0.01},
  "usage":{"input_tokens":812,"output_tokens":64,"cache_read_tokens":0,"cache_write_tokens":0,"cost_usd":0.0011,"latency_ms":1890.0,"calls":1},
  "model":{"provider":"claude-code","model_id":"haiku","tier":"cheap","thinking":"low"}}}
```

A step error exit is a **successful** response with `exit: "error"` and `outputs` = `ErrorOutput` without `exit`. JSON-RPC errors are only for protocol faults: an unknown key (`1002`), a call before init (`1001`), bad params (`-32602`), or a runtime-internal bug (`-32603`, with the traceback in `data`).

**Notifications** have the form `{"jsonrpc":"2.0","method":"event","params":{"type": <event type>, ...type fields}}`. The worker never sets `seq`, `ts`, `run_id`, `step`, `span` or `parent`. The executor stamps those (§8.3).

**`ping`** returns `{"pid": 48213}`. **`shutdown`** returns `{}`, after which the worker exits 0. EOF on the protocol stdin also means exit 0.

### 5.4 Client — `wynd/runtime/worker/client.py`

```python
class WorkerCrashed(RuntimeError):   returncode: int | None
class StepTimeout(TimeoutError):     pass
class WorkerRpcError(RuntimeError):  code: int; data: Any

class WorkerClient:
    def __init__(self, venv: VenvSpec, steps: list[InitStep], *, env: Mapping[str, str], cwd: str | None = None) -> None: ...
    def start(self) -> dict[str, Any]: ...          # Popen + init; returns init result; raises WorkerCrashed
    def call(self, method: str, params: dict[str, Any], *, on_event: Callable[[dict[str, Any]], None],
             timeout: float | None) -> dict[str, Any]: ...
    def kill(self) -> None: ...                      # terminate, wait 2s, kill
    def close(self) -> None: ...                     # "shutdown", wait 2s, else kill
    @property
    def alive(self) -> bool: ...
```

- The worker is spawned with `subprocess.Popen([venv.python, "-m", "wynd.runtime.worker"], stdin=PIPE, stdout=PIPE, stderr=None, env={**env, "PYTHONUNBUFFERED": "1"}, cwd=cwd, bufsize=0)`.
- A daemon reader thread reads stdout lines into a `queue.Queue`. At EOF it pushes the sentinel `None`.
- `call` writes the request, then loops on `queue.get(timeout=remaining)`:
  - A notification goes to `on_event(params)`.
  - The response with the matching id is returned as `result`, or raises `WorkerRpcError`.
  - The `None` sentinel raises `WorkerCrashed(proc.poll())`.
  - `queue.Empty` raises `StepTimeout`.
- Ids are a per-client counter.

### 5.5 Crash handling and restart

- A crash **during `run_step`** (EOF) raises `WorkerCrashed`. The pool drops the client. The executor turns the step into its `error` exit with cause `worker_crash`, and the message includes the return code and a note that stderr has the traceback. The next dispatch to that venv spawns a fresh worker and re-inits it.
- A crash **during init** raises `WorkerCrashed` from `start()`, and the step becomes `error` with cause `worker_crash`. There is no retry loop and no backoff: every later dispatch tries a fresh spawn once, so a fixed environment recovers without a restart.
- On a **timeout**, the pool calls `kill()` and drops the client, and the executor routes to the process error handler (3.5). Killing is the only safe way to stop a Python thread. Cache contents are lost, which is allowed because the cache is an optimisation.

---

## 6. The venv plan and WorkerPool

### 6.1 The plan data model — `wynd/runtime/plan.py`

It is shared by local mode (built in memory by the process loader and local venv manager) and image mode (the builder writes it as JSON into the image, and the supervisor loads it).

```python
class VenvSpec(BaseModel):
    id: str                       # "ds_<dep_hash>"
    dep_hash: str                 # dep_set_hash(requirements)
    requirements: list[str]       # normalised pinned requirement lines (what the venv was built from)
    path: str                     # venv root: local <workspace>/.wynd/venvs/<id>; image: chosen by builder
    python: str                   # absolute interpreter path (<path>/bin/python); tests may use sys.executable
    steps: list[str]              # step keys assigned to this venv (builder owns assignment)

class StepPlan(BaseModel):
    key: str                      # canonical step id; recommended: workspace-relative posix dir of the package
    kind: Literal["deterministic", "agentic", "shell"]
    entrypoint: str               # "<module>:<Class>" relative to the package
    path: str | None              # local: absolute package dir; image: None (installed under wynd_steps.<pkg>)
    venv: str                     # VenvSpec.id
    cassettes: str | None = None  # local: <path>/cassettes; image: None
    lock: StepLock                # wynd.spec.lock.StepLock (C4)

class NodeRef(BaseModel):
    kind: Literal["step", "process"]
    key: str | None = None        # kind == "step": StepPlan.key
    process: str | None = None    # kind == "process": child process id (ProcessPlan.id)

class ProcessPlan(BaseModel):
    id: str                       # process id (relative path under a process root)
    spec: ProcessSpec             # wynd.spec, NORMALISED: shorthand to: expanded to one branch carrying with/limits,
                                  # max_traversals filled on every cycle branch, `finally` default []
    nodes: dict[str, NodeRef]     # every name in spec.steps

class RunPlan(BaseModel):
    format: Literal[1] = 1
    mode: Literal["local", "image"]
    root: str                     # top-level process id
    commit: str | None = None
    runtime_version: str
    processes: dict[str, ProcessPlan]   # the whole reference closure, keyed by id
    steps: dict[str, StepPlan]
    venvs: dict[str, VenvSpec]

def dep_set_hash(requirements: Iterable[str]) -> str:
    """sha256 over sorted unique stripped non-empty non-comment lines joined by '\n' + '\n'; first 16 hex chars."""
```

A local-mode example (the dogfood; only one of each shown):

```json
{"format":1,"mode":"local","root":"process_supplier_invoice","commit":"764ca6e","runtime_version":"0.1.0",
 "processes":{"process_supplier_invoice":{"id":"process_supplier_invoice",
   "spec":{"kind":"process","name":"process_supplier_invoice","entry":"read","provider":"claude-code","...":"normalised ProcessSpec"},
   "nodes":{"read":{"kind":"step","key":"processes/process_supplier_invoice/steps/read_pdf"},
            "extract":{"kind":"step","key":"processes/process_supplier_invoice/steps/extract_invoice_fields"}}}},
 "steps":{"processes/process_supplier_invoice/steps/read_pdf":{
   "key":"processes/process_supplier_invoice/steps/read_pdf","kind":"deterministic","entrypoint":"step:ReadPdf",
   "path":"/Users/peter/.../examples/invoices/processes/process_supplier_invoice/steps/read_pdf",
   "venv":"ds_4b1e0c9a7f3d2e10","cassettes":".../read_pdf/cassettes","lock":{"kind":"deterministic","entrypoint":"step:ReadPdf","...":"StepLock"}}},
 "venvs":{"ds_4b1e0c9a7f3d2e10":{"id":"ds_4b1e0c9a7f3d2e10","dep_hash":"4b1e0c9a7f3d2e10","requirements":["pypdf==5.1.0"],
   "path":"/Users/peter/.../examples/invoices/.wynd/venvs/ds_4b1e0c9a7f3d2e10",
   "python":"/Users/peter/.../examples/invoices/.wynd/venvs/ds_4b1e0c9a7f3d2e10/bin/python",
   "steps":["processes/process_supplier_invoice/steps/read_pdf"]}}}
```

The image-mode plan is the same document with `mode:"image"`, `path:null`, `cassettes:null` and image venv paths. `process.lock.yaml` embeds `venvs` as `[VenvSpec.model_dump()]` (builder).

### 6.2 Dispatcher and WorkerPool — `wynd/runtime/worker/pool.py`

```python
class Dispatcher(Protocol):
    def start(self) -> None: ...                                   # idempotent warm-up
    def describe(self, key: str) -> StepDescription: ...           # raises KeyError / WorkerCrashed
    def dispatch(self, key: str, params: RunStepParams, *, on_event: Callable[[dict[str, Any]], None],
                 timeout: float | None) -> RunStepResult: ...     # raises StepTimeout, WorkerCrashed, WorkerRpcError
    def close(self) -> None: ...

class WorkerPool:                       # implements Dispatcher
    def __init__(self, plan: RunPlan, *, env: Mapping[str, str] | None = None, cwd: str | None = None) -> None: ...
    def start(self, venvs: Iterable[str] | None = None) -> None: ...
    def describe(self, key: str) -> StepDescription: ...
    def dispatch(self, key: str, params: RunStepParams, *, on_event, timeout) -> RunStepResult: ...
    def close(self) -> None: ...
    def __enter__(self) -> "WorkerPool": ...
    def __exit__(self, *exc: object) -> None: ...

class InProcessDispatcher:              # implements Dispatcher — the unit-test seam
    def __init__(self, classes: Mapping[str, type[Step]]) -> None: ...   # key -> class, no files, no subprocess
    # describe = describe_step(cls, key); dispatch = run_chain(cls, params, emit=on_event, cache=self._caches[key]).to_result()
    # timeout is ignored (documented); timeouts are tested with a stub Dispatcher or the real pool
```

`WorkerPool` behaviour:

- There is at most **one worker per venv**, guarded by `threading.Lock` per venv. A worker runs one step at a time, because env and cwd are process-global. Concurrent runs (supervisor) queue at step granularity.
- `start()` spawns every requested venv's worker **concurrently**: all `Popen` calls first, then all init replies are collected. It is idempotent. The supervisor calls it at boot; `Executor.run` calls it at the start of every run, which is a no-op when warm.
- `dispatch` takes the venv lock, `_ensure(venv, on_event)`, then `client.call("run_step", params.model_dump(mode="json"), on_event, timeout)`, and returns `RunStepResult.model_validate(result)`.
  - `_ensure` spawns and inits the worker if it is absent or dead. When it spawns during a dispatch it emits `{"type":"worker.start","venv":id,"pid":..,"startup_ms":..}` through `on_event`.
  - On `StepTimeout`: `kill()`, drop the client, re-raise. On `WorkerCrashed`: drop the client, re-raise.
- `describe(key)`: on the first call for a venv it sends `describe` with `keys: null` and caches every description. Later calls are served from the cache.
- Worker env is `env` (default `dict(os.environ)` at pool creation). The caller (CLI/controller) has already merged `.env` into it, so workers inherit secrets such as `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY`.
- A pool is bound to one `RunPlan`. A caller whose plan or sources change makes a new pool; the pool does not watch files.

**Test seam.** A `RunPlan` whose `VenvSpec.python == sys.executable` makes the pool use the current interpreter as the "venv". This is real subprocess workers with no uv, as used in `test_worker_protocol.py`. `InProcessDispatcher` exercises executor logic without subprocesses.

---

## 7. The executor state machine

### 7.1 API — `wynd/runtime/executor/engine.py`

```python
class ProcessResult(BaseModel):
    run_id: str
    process: str
    exit: str
    outputs: dict[str, Any]
    error: ProcessError | None             # set whenever a process error handler ran (default or custom)
    status: Literal["succeeded", "failed"] # failed iff exit == "error"
    trace: str                             # TraceSink.uri(run_id)
    workspace: str | None                  # URI if kept
    duration_ms: float
    usage: Usage                           # total over all leaf step runs (incl. nested)
    finally_errors: list[ErrorOutput] = []

class Executor:
    def __init__(self, plan: RunPlan, dispatcher: Dispatcher, stores: Stores, *,
                 env: Mapping[str, str] | None = None,                     # for env.<NAME>; default dict(os.environ)
                 selectors: Mapping[str, BranchSelector] | None = None) -> None: ...
                 # default {"deterministic": DeterministicSelector()}; M5 adds "agentic"
    def validate_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]: ...   # raises InvalidProcessInputs
    def run(self, inputs: Mapping[str, Any], *, run_id: str | None = None,
            cassette_mode: Literal["live", "record", "replay"] = "live",
            on_event: Callable[[TraceEvent], None] | None = None) -> ProcessResult: ...
```

An `Executor` is reusable and thread-safe across concurrent `run()` calls, because all per-run state lives in local objects. At construction it parses every `when:`/`with:` expression in every process once with `wynd.spec` and caches the ASTs by source string. A syntax error raises immediately.

### 7.2 Per-run and per-instance state — `wynd/runtime/executor/instance.py`

```python
@dataclass
class StepRecord:
    name: str; path: str; run: int; exit: str
    inputs: dict[str, Any]; outputs: dict[str, Any]; summary: Summary

@dataclass(frozen=True)
class Deadline:
    at: float            # time.monotonic() deadline
    owner: object        # token identifying the frame that set it

@dataclass
class RunCtx:
    run_id: str; workspace: Path; emitter: TraceEmitter; env: Mapping[str, str]
    cassette_mode: str; default_provider: str; usage: Usage

@dataclass
class Instance:                          # one per process instance (root, and each ProcessStep invocation)
    plan: ProcessPlan
    prefix: str                          # "" for root, "sub." for a child of node "sub"
    parent_span: int | None
    inputs: dict[str, Any]               # validated JSON-mode process inputs
    deadline: Deadline | None
    runs: dict[str, int]                 # steps.<n>.runs (0 for never-run)
    latest: dict[str, StepRecord]        # latest completed run per node name
    taken: dict[tuple[str, int], int]    # (edge from-key, branch index) -> times taken
    history: list[StepRecord]
    previous: StepRecord | None
    usage: Usage                         # this instance incl. its children

    def scope(self, ctx: RunCtx) -> dict[str, Any]: ...
```

`scope()` builds a plain nested mapping. The expression evaluator resolves `a.b` on a Mapping as `a["b"]` and `a[k]` as `a[k]` (§10 C2).

```python
{
  "steps": {name: {"runs": runs[name],
                   "exit": latest[name].exit if name in latest else None,
                   "outputs": latest[name].outputs if name in latest else None}
            for name in plan.spec.steps},
  "process": {"inputs": inputs, "name": spec.name, "goal": spec.goal},
  "env": ctx.env,
  "run": {"id": ctx.run_id},
  "previous": None if previous is None else
              {"step": previous.name, "exit": previous.exit, "outputs": previous.outputs,
               "summary": previous.summary.model_dump()},
  "edges": {edge.from_: {**{i: {"taken": taken[(edge.from_, i)]} for i in range(len(edge.to))},
                         **{b.name: {"taken": taken[(edge.from_, i)]} for i, b in enumerate(edge.to) if b.name}}
            for edge in spec.edges},
}
```

All values are JSON-mode: dates are ISO strings and numbers are numbers.

### 7.3 Entry, `with:` and handler/finally binding

- **Entry.** `bound = {f: inputs[f] for f in input_fields(entry) if f in inputs}`. `input_fields` comes from `dispatcher.describe(key).input_fields`, or from `process_interface(...)` for a process node. A name mismatch is a validator error, so at runtime a missing name simply fails Input validation, giving an `error` exit with cause `input_validation`.
- **Branch `with:`.** `bound = {k: to_jsonable_python(evaluate(ast(v), scope)) for k, v in branch.with_.items()}`. Every expression of one edge resolution, the `when:` conditions and the `with:` values alike, sees the **same scope snapshot**, taken before the chosen branch's counter is incremented.
- **Handler.** `bound = filter_by_fields(handler, perr.model_dump(mode="json"))`.
- **Finally.** `bound = filter_by_fields(step, {**instance.inputs, "run_id": run_id, "exit": outcome.exit})`.

### 7.4 Main loop (exact)

```python
def _run_process(self, pp, inputs, ctx, prefix, parent_span, deadline) -> Outcome:
    inst = Instance(pp, prefix, parent_span, inputs, deadline, ...)
    # outcome = self._walk(inst, ctx), wrapped in the try/finally of §7.6 that runs `finally` steps
    # (also while an outer DeadlineExceeded unwinds), then returns outcome

def _walk(self, inst, ctx) -> Outcome:
    spec = inst.plan.spec
    name, via, via_ref = spec.entry, None, None
    bound = self._bind_by_name(inst, name, inst.inputs)
    while True:
        try:
            rec = self._run_node(inst, ctx, name, bound, via, via_ref, role="node")
        except LocalTimeout as t:
            return self._fail(inst, ctx, "timeout", step=name, inputs=bound, message=str(t), edge=via_ref)
        edge = inst.plan_edges.get(f"{name}.{rec.exit}")           # from-key -> Edge (built once per ProcessPlan)
        if edge is None:
            cause = "step_error" if rec.exit == "error" else "unrouted_exit"
            return self._fail(inst, ctx, cause, rec=rec)
        scope = inst.scope(ctx)
        selector = self.selectors.get(edge.kind)
        if selector is None:
            return self._fail(inst, ctx, "unsupported_edge", rec=rec, edge=edge.from_)
        try:
            choice = selector.select(edge, scope, SelectCall(...))
        except ExpressionError as e:
            return self._fail(inst, ctx, "expression_error", rec=rec, edge=edge.from_, message=str(e))
        if choice.index is None:
            return self._fail(inst, ctx, "no_branch_matched", rec=rec, edge=edge.from_)
        i, br = choice.index, edge.to[choice.index]
        ref = f"{edge.from_}[{i}]"
        limit = br.limits.max_traversals if br.limits else None
        if limit is not None and inst.taken[(edge.from_, i)] >= limit:
            return self._fail(inst, ctx, "max_traversals", rec=rec, edge=ref)
        try:
            bound = {k: to_jsonable_python(evaluate(self._ast(v), scope)) for k, v in br.with_.items()}
        except ExpressionError as e:
            return self._fail(inst, ctx, "expression_error", rec=rec, edge=ref, message=str(e))
        inst.taken[(edge.from_, i)] += 1
        ctx.emitter.emit("edge.taken", process=inst.plan.id, parent=inst.parent_span, **{"from": edge.from_},
                         branch=i, name=br.name, to=br.step, kind=edge.kind, **{"with": bound},
                         taken=inst.taken[(edge.from_, i)], usage=choice.usage, note=choice.note)
        if br.step.startswith("$exit."):
            exit_name = br.step.removeprefix("$exit.")
            try:
                outputs = self._process_output(inst.plan, exit_name, bound)   # validate against outputs[exit] model
            except ValidationError as e:
                return self._fail(inst, ctx, "invalid_process_outputs", rec=rec, edge=ref, message=str(e))
            return Outcome(exit=exit_name, outputs=outputs, error=None, handled=False)
        name, via, via_ref = br.step, br, ref
```

`DeterministicSelector.select` (`executor/routing.py`) scans the branches top to bottom. A branch with `when is None` is chosen (it is the else, and later branches are ignored). A branch whose `bool(evaluate(when, scope))` is true is chosen. If nothing is chosen the result is `BranchChoice(None)`.

### 7.5 Running a node

```python
def _run_node(self, inst, ctx, name, bound, via, via_ref, role, *, no_deadline=False) -> StepRecord:
    node = inst.plan.nodes[name]; path = inst.prefix + name
    n = inst.runs[name] = inst.runs.get(name, 0) + 1            # runs counts started runs (== completed, sequential)
    deadline = None if no_deadline else combine(inst.deadline, via.limits.timeout if via and via.limits else None)
    span = ctx.emitter.emit("step.start", step=path, name=name, process=inst.plan.id, parent=inst.parent_span,
                            key=..., kind=..., run=n, role=role, via=via_ref, inputs=bound, venv=...).seq
    if node.kind == "process":
        res = self._run_child(inst, ctx, node, path, span, bound, deadline)
    else:
        sp = self.plan.steps[node.key]
        try:
            policy = build_policy(sp.kind, sp.lock, default_provider=ctx.default_provider, registry=self.stores.registry,
                                  retry_override=via.limits.retry if via and via.limits else None)
        except StepFailure as e:                                   # cause "config"
            res = error_result(path, "config", e, bound)
        else:
            desc = self.dispatcher.describe(sp.key)
            params = RunStepParams(run_id=ctx.run_id, step_path=path, step_run=n, key=sp.key, inputs=bound,
                                   context=assemble_context(desc.context, inst, ctx) if desc.context else None,
                                   workspace=str(ctx.workspace), policy=policy,
                                   cassette=CassetteConfig(mode=ctx.cassette_mode, dir=sp.cassettes,
                                       record_dir=str(ctx.workspace / ".wynd/cassettes" / step_package_name(sp.key))))
            remaining = deadline.remaining() if deadline else None
            try:
                if remaining is not None and remaining <= 0: raise StepTimeout()
                res = self.dispatcher.dispatch(sp.key, params, timeout=remaining,
                        on_event=lambda ev: ctx.emitter.emit(ev.pop("type"), step=path, span=span, parent=inst.parent_span, **ev))
            except StepTimeout:
                ctx.emitter.emit("step.end", step=path, span=span, parent=inst.parent_span, run=n, exit=None, timed_out=True, ...)
                raise self._timeout_for(deadline, inst)            # LocalTimeout if the branch's own timeout bound,
                                                                   # else DeadlineExceeded(deadline.owner)
            except (WorkerCrashed, WorkerRpcError) as e:
                res = error_result(path, "worker_crash", e, bound)
    rec = StepRecord(name, path, n, res.exit, bound, res.outputs, res.summary)
    inst.latest[name] = rec; inst.previous = rec; inst.history.append(rec)
    if res.usage: inst.usage += res.usage; ctx.usage += res.usage  (leaf steps only; child usage already counted)
    write_json(ctx.workspace / ".wynd/steps" / path / str(n) / "inputs.json", bound)     # 3.7 raw outputs addressable
    write_json(ctx.workspace / ".wynd/steps" / path / str(n) / "outputs.json", {"exit": res.exit, **res.outputs})
    ctx.emitter.emit("step.end", step=path, span=span, parent=inst.parent_span, run=n, exit=res.exit,
                     outputs=res.outputs, summary=res.summary, attempts=res.attempts,
                     timings={**res.timings.model_dump(mode="json"), "duration_ms": ...}, usage=res.usage, model=res.model)
    return rec
```

`error_result(path, cause, exc, bound)` builds a `RunStepResult` with `exit="error"` and outputs `ErrorOutput(cause, message, inputs=bound)` without `exit`, then summarises it.

### 7.6 The error handler, `on_error` and `finally`

```python
def _fail(self, inst, ctx, cause, *, rec=None, step=None, inputs=None, edge=None, message=None) -> Outcome:
    name = rec.name if rec else step
    step_err = ErrorOutput(exit="error", **rec.outputs) if rec and rec.exit == "error" else None
    perr = ProcessError(process=inst.plan.id, step=(inst.prefix + name) if name else None,
                        cause=cause, message=message or default_message(cause, rec), edge=edge,
                        inputs=rec.inputs if rec else (inputs or {}),
                        partial_outputs=step_err.partial_outputs if step_err else (rec.outputs if rec else None),
                        step_error=step_err if cause == "step_error" else None)
    ev = ctx.emitter.emit("process.error", process=inst.plan.id, parent=inst.parent_span, error=perr,
                          handler=inst.plan.spec.on_error or "default")
    perr.trace = TracePointer(run_id=ctx.run_id, uri=self.stores.traces.uri(ctx.run_id), seq=ev.seq)
    handler = inst.plan.spec.on_error
    if handler is None:                                            # default handler
        return Outcome(exit="error", outputs={}, error=perr, handled=True)
    bound = self._bind_by_name(inst, handler, perr.model_dump(mode="json"))
    rec = self._run_node(inst, ctx, handler, bound, via=None, via_ref="on_error", role="on_error")
    if rec.exit == "error":                                        # error inside handler: no recursion
        perr.handler_error = ErrorOutput(exit="error", **rec.outputs)
        return Outcome(exit="error", outputs={}, error=perr, handled=True)
    try:
        outputs = self._process_output(inst.plan, rec.exit, rec.outputs)   # handler exit X -> $exit.X
    except (ValidationError, KeyError) as e:
        perr.handler_error = ErrorOutput(cause="output_validation", message=f"handler exit {rec.exit!r}: {e}", inputs=bound)
        return Outcome(exit="error", outputs={}, error=perr, handled=True)
    return Outcome(exit=rec.exit, outputs=outputs, error=perr, handled=True)
```

`finally` runs in `_run_process` once `_walk` has returned or raised:

```python
outcome = None
try:
    outcome = self._walk(inst, ctx)
    return outcome
finally:
    exit_for_finally = outcome.exit if outcome else "error"
    for name in inst.plan.spec.finally_:
        bound = self._bind_by_name(inst, name, {**inst.inputs, "run_id": ctx.run_id, "exit": exit_for_finally})
        rec = self._run_node(inst, ctx, name, bound, via=None, via_ref="finally", role="finally", no_deadline=True)
        if rec.exit == "error" and outcome is not None:
            outcome.finally_errors.append(ErrorOutput(exit="error", **rec.outputs))
```

A timeout inside the handler cannot happen, because the handler has no branch limits. An enclosing `DeadlineExceeded` still propagates through the handler.

### 7.7 ProcessStep (recursion)

```python
def _run_child(self, inst, ctx, node, path, span, bound, deadline) -> RunStepResult:
    child = self.plan.processes[node.process]
    iface = process_interface(child.id, child.spec)
    try:
        child_inputs = iface.input_model.model_validate(bound).model_dump(mode="json")
    except ValidationError as e:
        return error_result(path, "input_validation", e, bound)
    token = object()
    child_deadline = Deadline(deadline.at, token) if deadline else None
    t0 = perf_counter()
    try:
        out = self._run_process(child, child_inputs, ctx, prefix=path + ".", parent_span=span, deadline=child_deadline)
    except DeadlineExceeded as d:
        if d.owner is token: raise LocalTimeout(f"{path} exceeded its timeout")   # parent frame routes to ITS handler
        raise
    if out.exit == "error":
        err = ErrorOutput(cause="child_process", message=out.error.message, inputs=bound,
                          partial_outputs=out.error.partial_outputs, child=out.error)
        outputs = err.model_dump(mode="json"); outputs.pop("exit")
        return RunStepResult(exit="error", outputs=outputs, summary=summarise(path, "error", outputs), attempts=1,
                             timings=..., usage=out.usage, model=None)
    return RunStepResult(exit=out.exit, outputs=out.outputs, summary=summarise(path, out.exit, out.outputs),
                         attempts=1, timings=..., usage=out.usage, model=None)
```

- **Deadline ownership.** `combine(outer, timeout)` returns whichever deadline is earlier. When the branch's own timeout is the binding one, it gets a fresh owner token local to this frame. `_timeout_for` then raises `LocalTimeout` when the expired deadline belongs to the current frame (the branch's own timeout, or the token of this instance's ProcessStep node, caught in `_run_child`). Otherwise it raises `DeadlineExceeded(owner)` so the error unwinds to the frame that owns it.
- A child that ends in `$exit.error` does **not** make the top-level run keep its workspace unless the top-level run also ends in its own handler (§12, I-8).

### 7.8 Timeouts and retries (summary)

- `limits.timeout` (seconds) on the branch **into** a step bounds that step's dispatch: pre, run, post and all retries. For a ProcessStep node it bounds the whole child run. When it expires, the pool kills the worker and the executor routes to the process error handler with cause `timeout` (3.5). This is not the step's `error` exit. A `step.end` with `exit:null, timed_out:true` is still written.
- `limits.retry` on the branch into a step overrides the lockfile's retries field by field (`build_policy(retry_override=...)`). Entry, handler and finally steps use the lockfile's retries, or `default_retries(kind)` when the lockfile has none.
- `limits.max_traversals` is enforced as `taken >= limit` before the branch is taken (§7.4). The validator fills it on every cycle branch; `None` means unlimited.

### 7.9 Provider default and context

- `ctx.default_provider = root.spec.provider or DEFAULT_PROVIDER`. `DEFAULT_PROVIDER` is `"claude-code"` and comes from `wynd.runtime.providers` (C7). Child processes inherit the root's value (3.2).
- `assemble_context(decl: list[str], inst: Instance, ctx: RunCtx) -> dict[str, Any]` lives in `executor/context.py`. It returns a dict whose keys are the declared strings:
  - `"full_trace"` gives `{"process": {"name", "goal", "inputs"}, "steps": [{"step": path, "run", "exit", "inputs", "outputs", "summary"} for rec in inst.history]}`.
  - Any other entry, such as `"process.goal"`, `"previous.summary"` or `"steps.classify.outputs"`, is evaluated as a wynd expression against `inst.scope(ctx)`. The scope includes `process.goal` and `process.name`.
  - An `ExpressionError` gives `None`, with a `step.log` warning. A declared context that is not yet available is not fatal.

### 7.10 Run lifecycle (`Executor.run`)

```
run_id = run_id or new_id("run"); assert valid_id(run_id)
inputs = self.validate_inputs(inputs)                 # InvalidProcessInputs raised BEFORE anything is created
ws = stores.workspaces.open(run_id)
emitter = TraceEmitter(run_id, stores.traces, on_event)
stores.runs.create(RunRecord(id=run_id, kind="run", process=plan.root, status="running", inputs=inputs,
                             mode=plan.mode, ref=plan.commit, trace=stores.traces.uri(run_id), started_at=now))
self.dispatcher.start()
emitter.emit("run.start", process=plan.root, mode=plan.mode, inputs=inputs, commit=plan.commit,
             runtime_version=__version__, workspace=str(ws), cassette=cassette_mode)
try:
    out = self._run_process(root, inputs, ctx, prefix="", parent_span=None, deadline=None)
except Exception as e:                                 # executor/storage bug: end the run cleanly
    out = Outcome("error", {}, ProcessError(process=plan.root, step=None, cause="internal", message=repr(e)), handled=True)
keep = out.handled                                     # the top-level run ended in its error handler
ws_uri = stores.workspaces.close(run_id, keep=keep)
if out.error and keep: out.error.workspace = ws_uri
status = "failed" if out.exit == "error" else "succeeded"
emitter.emit("run.end", exit=out.exit, outputs=out.outputs, error=out.error, status=status,
             duration_ms=..., workspace_kept=keep, workspace=ws_uri, usage=ctx.usage, finally_errors=out.finally_errors)
stores.traces.close(run_id)
stores.runs.update(run_id, {"status": status, "exit": out.exit, "outputs": out.outputs, "error": ..., "workspace": ws_uri,
                            "ended_at": now, "duration_ms": ..., "usage": ctx.usage})
return ProcessResult(...)
```

### 7.11 The agentic-edge seam (M5) — `executor/routing.py`

```python
@dataclass(frozen=True)
class SelectCall:
    run_id: str
    process: str
    step_path: str                        # the source step's path
    emit: Callable[..., TraceEvent]       # emitter.emit bound to the run (for model.call events)
    registry: Registry
    dispatcher: Dispatcher                # an agentic selector may run its verifier in a worker venv
    default_provider: str

@dataclass(frozen=True)
class BranchChoice:
    index: int | None                     # None -> no_branch_matched
    usage: Usage | None = None
    note: str = ""

class BranchSelector(Protocol):
    def select(self, edge: Edge, scope: Mapping[str, Any], call: SelectCall) -> BranchChoice: ...

class DeterministicSelector:              # registered under "deterministic"
    def __init__(self, ast: Callable[[str], Any]) -> None: ...
    def select(self, edge: Edge, scope: Mapping[str, Any], call: SelectCall) -> BranchChoice: ...
```

In v1 an edge with `kind: agentic` hits `unsupported_edge`; the validator also rejects it. M5 registers `selectors={"agentic": AgenticSelector(...)}` (its module and design belong to the M5 architect). Its `usage` is recorded on `edge.taken` and added to the run's usage.

---

## 8. Trace

### 8.1 The envelope (every line)

```python
class EventBase(BaseModel):
    model_config = ConfigDict(extra="allow", populate_by_name=True)
    v: Literal[1] = 1
    seq: int                   # per run, starts at 1, strictly increasing, assigned by the TraceEmitter
    ts: datetime               # UTC, serialised "2026-09-22T21:50:01.123456Z"
    run_id: str
    type: str
```

The events are one pydantic model per type, combined in a discriminated union `TraceEvent` on `type`. `parse_event(d: dict) -> TraceEvent` falls back to `EventBase` for unknown types (forward compatibility). Serialisation is `model_dump_json(by_alias=True)`, one event per line.

### 8.2 Event types

| type | extra fields |
|---|---|
| `run.start` | `process` (id), `mode` ("local"\|"image"), `inputs`, `commit`, `runtime_version`, `workspace`, `cassette` (mode) |
| `step.start` | `step` (path), `name` (node), `process` (instance process id), `span` (= own seq), `parent` (span\|null), `key` (step key or `process:<id>`), `kind`, `run` (n), `role` ("node"\|"on_error"\|"finally"), `via` ("validate.done[1]"\|"on_error"\|"finally"\|null), `inputs`, `venv` (id\|null) |
| `step.end` | `step`, `span`, `parent`, `run`, `exit` (str\|null), `timed_out` (bool), `outputs`, `summary` ({step,exit,key_outputs,note}), `attempts`, `timings` ({started_at, ended_at, duration_ms, worker_ms, pre_ms, run_ms, post_ms}), `usage` (Usage\|null), `model` (ModelInfo\|null) |
| `step.log` | `step`, `span`, `parent`, `level`, `stream` ("logger"\|"output"), `message` |
| `step.event` | `step`, `span`, `parent`, `name`, `data` (from `self.runtime.trace.event`) |
| `edge.taken` | `process`, `parent`, `from`, `branch` (index), `name` (branch name\|null), `to` (step name or `$exit.<x>`), `kind`, `with` (evaluated bindings), `taken` (count after this take), `usage` (null for deterministic), `note` |
| `model.call` | `step`, `span`, `parent` + fields owned by the agentic architect (provider, model_id, tier, attempt, request_hash, request, response, usage, latency_ms, cassette: live\|record\|replay) |
| `tool.call` | `step`, `span`, `parent` + fields owned by the agentic architect (tool, source: method\|library\|mcp, args, result, ok, error, effects, idempotent, attempt, latency_ms) |
| `process.error` | `process`, `parent`, `error` (ProcessError), `handler` ("default" or step name) |
| `worker.start` | `step`, `span`, `parent`, `venv`, `pid`, `startup_ms` |
| `run.end` | `exit`, `outputs`, `error` (ProcessError\|null), `status`, `duration_ms`, `workspace_kept`, `workspace` (URI\|null), `usage` (totals), `finally_errors` |

Example, the dogfood with a nested child (abridged, one event per line):

```json
{"v":1,"seq":1,"ts":"2026-09-22T21:50:01.100000Z","run_id":"run_20260922T215001100_a1b2c3","type":"run.start","process":"process_supplier_invoice","mode":"local","inputs":{"pdf_path":"examples/inv1.pdf"},"commit":"764ca6e","runtime_version":"0.1.0","workspace":"/…/.wynd/workspaces/run_20260922T215001100_a1b2c3","cassette":"live"}
{"v":1,"seq":2,"ts":"…","run_id":"run_…","type":"step.start","step":"read","name":"read","process":"process_supplier_invoice","span":2,"parent":null,"key":"processes/process_supplier_invoice/steps/read_pdf","kind":"deterministic","run":1,"role":"node","via":null,"inputs":{"pdf_path":"examples/inv1.pdf"},"venv":"ds_4b1e0c9a7f3d2e10"}
{"v":1,"seq":3,"ts":"…","run_id":"run_…","type":"step.end","step":"read","span":2,"parent":null,"run":1,"exit":"done","timed_out":false,"outputs":{"text":"INVOICE INV-1042 …","pages":1},"summary":{"step":"read","exit":"done","key_outputs":{"text":"INVOICE INV-1042 …","pages":1},"note":""},"attempts":1,"timings":{"started_at":"…","ended_at":"…","duration_ms":14.2,"worker_ms":12.9,"pre_ms":0.0,"run_ms":12.7,"post_ms":0.0},"usage":null,"model":null}
{"v":1,"seq":4,"ts":"…","run_id":"run_…","type":"edge.taken","process":"process_supplier_invoice","parent":null,"from":"read.done","branch":0,"name":null,"to":"extract","kind":"deterministic","with":{"invoice_text":"INVOICE INV-1042 …"},"taken":1,"usage":null,"note":""}
{"v":1,"seq":9,"ts":"…","run_id":"run_…","type":"edge.taken","process":"process_supplier_invoice","parent":null,"from":"validate.done","branch":1,"name":"retry","to":"fix","kind":"deterministic","with":{"fields":{"…":"…"},"errors":["total is negative"]},"taken":1,"usage":null,"note":""}
{"v":1,"seq":20,"ts":"…","run_id":"run_…","type":"step.start","step":"archive","name":"archive","process":"process_supplier_invoice","span":20,"parent":null,"key":"process:archive_record","kind":"process","run":1,"role":"node","via":"save.done[0]","inputs":{"record":{"…":"…"}},"venv":null}
{"v":1,"seq":21,"ts":"…","run_id":"run_…","type":"step.start","step":"archive.write","name":"write","process":"archive_record","span":21,"parent":20,"key":"processes/archive_record/steps/write","kind":"deterministic","run":1,"role":"node","via":null,"inputs":{"record":{"…":"…"}},"venv":"ds_…"}
{"v":1,"seq":30,"ts":"…","run_id":"run_…","type":"run.end","exit":"done","outputs":{"record":{"…":"…"}},"error":null,"status":"succeeded","duration_ms":1834.0,"workspace_kept":false,"workspace":null,"usage":{"input_tokens":812,"output_tokens":64,"cache_read_tokens":0,"cache_write_tokens":0,"cost_usd":0.0011,"latency_ms":1890.0,"calls":1},"finally_errors":[]}
```

### 8.3 Emitter — `wynd/runtime/trace.py`

```python
class TraceEmitter:
    def __init__(self, run_id: str, sink: TraceSink, on_event: Callable[[TraceEvent], None] | None = None) -> None: ...
    def emit(self, type: str, **fields: Any) -> TraceEvent: ...
        # under a lock: seq += 1; ev = parse_event({"v":1,"seq":seq,"ts":utcnow(),"run_id":run_id,"type":type,**fields})
        # sink.write(ev); on_event(ev) if set (exceptions from on_event are logged, never propagated); return ev
```

For `step.start` the emitter sets `span = seq` itself. Worker notifications are stamped by the executor's `on_event` lambda in §7.5 with `step`, `span` and `parent`, and the emitter adds `seq`, `ts` and `run_id`.

### 8.4 Nesting for `wynd trace`

```python
@dataclass
class TraceNode:
    start: StepStart
    end: StepEnd | None
    events: list[TraceEvent]                          # step.log / step.event / model.call / tool.call / worker.start
    children: list["TraceNode | TraceEvent"]          # nested step nodes + edge.taken + process.error, in seq order

@dataclass
class TraceTree:
    start: RunStart | None; end: RunEnd | None
    children: list[TraceNode | TraceEvent]

def read_trace(sink: TraceSink, run_id: str) -> list[TraceEvent]: ...
def build_tree(events: Iterable[TraceEvent]) -> TraceTree: ...
```

`build_tree` sorts events by `seq` and handles each as follows:
- `step.start` creates a node, attached to `spans[parent].children`, or to `tree.children` when `parent` is null.
- `step.end` sets `spans[span].end`.
- Events carrying a `span` are appended to `spans[span].events`.
- `edge.taken` and `process.error` are attached like `step.start`, under `parent`.

The CLI owns the rendering. A suggested format: one line per node, `step  exit  duration  [model tier tokens $cost]`, with `→ from[branch] to` lines between steps and children indented.

---

## 9. Storage (7.1)

### 9.1 Interfaces — `wynd/runtime/storage/base.py`

```python
class WorkspaceStore(Protocol):
    def open(self, run_id: str) -> Path: ...                   # create; return a LOCAL dir steps can use
    def close(self, run_id: str, *, keep: bool) -> str | None: ...   # keep: persist and return URI; else delete, None
    def locate(self, run_id: str) -> str | None: ...           # URI of a kept workspace

class TraceSink(Protocol):
    def write(self, event: TraceEvent) -> None: ...            # append (events carry run_id)
    def close(self, run_id: str) -> None: ...                  # flush/release per-run resources
    def read(self, run_id: str) -> list[TraceEvent]: ...       # seq order; [] if unknown
    def uri(self, run_id: str) -> str: ...

class RunRegistry(Protocol):
    def create(self, record: RunRecord) -> None: ...           # raises FileExistsError-equivalent on duplicate id
    def update(self, id: str, patch: Mapping[str, Any]) -> RunRecord: ...   # atomic read-modify-write; sets updated_at
    def get(self, id: str) -> RunRecord | None: ...
    def list(self, *, kind: RunKind | None = None, process: str | None = None,
             status: RunStatus | None = None, limit: int = 100) -> list[RunRecord]: ...   # newest first (created_at)
    def put_test_result(self, result: TestResult) -> None: ...
    def get_test_result(self, commit: str, step_hash: str) -> TestResult | None: ...

class Registry(Protocol):                                     # user-level: mcp / providers / registries
    def list(self, section: str) -> dict[str, dict[str, Any]]: ...
    def get(self, section: str, name: str) -> dict[str, Any] | None: ...
    def put(self, section: str, name: str, entry: Mapping[str, Any]) -> None: ...
    def remove(self, section: str, name: str) -> bool: ...
    def location(self) -> str: ...

REGISTRY_SECTIONS = ("mcp", "providers", "registries")       # entry schemas owned by agentic/builder/controller architects
```

### 9.2 Models — `wynd/runtime/storage/models.py`

```python
RunKind = Literal["run", "compile", "test", "build"]          # "test" = a `wynd test --live` job
RunStatus = Literal["queued", "running", "awaiting_input", "succeeded", "failed", "cancelled"]

class RunRecord(BaseModel):
    id: str
    kind: RunKind
    process: str | None = None
    status: RunStatus
    created_at: datetime                     # set by create() if absent
    updated_at: datetime
    started_at: datetime | None = None
    ended_at: datetime | None = None
    ref: str | None = None                   # commit / git ref the run or job operated on
    mode: Literal["local", "image"] | None = None   # runs only
    inputs: dict[str, Any] = {}
    exit: str | None = None                  # runs
    outputs: dict[str, Any] | None = None    # runs: process outputs (small)
    error: dict[str, Any] | None = None      # ProcessError (runs) or job error
    artefacts: str | None = None             # pointer URI (build dir, result branch, …)
    trace: str | None = None                 # trace URI
    workspace: str | None = None             # kept workspace URI
    duration_ms: float | None = None
    usage: dict[str, Any] | None = None      # Usage totals (runs) / compute accounting (jobs)
    session: dict[str, Any] | None = None    # serialised compile session (opaque; compiler owns the schema)
    meta: dict[str, Any] = {}                # kind-specific extras (e.g. {"branch": "...", "logs": "file:///..."})

class TestResult(BaseModel):
    commit: str
    step_hash: str                           # compiled-step hash (or process compiled hash for process-level tests)
    subject: str                             # step key or "process:<id>"
    passed: bool
    counts: dict[str, int]                   # {"passed": n, "failed": n, "skipped": n}
    ran_at: datetime
    job_id: str | None = None
    report: str | None = None                # pointer to junit/log output
```

Example RunRecord:

```json
{"id":"run_20260922T215001100_a1b2c3","kind":"run","process":"process_supplier_invoice","status":"succeeded",
 "created_at":"2026-09-22T21:50:01.100Z","updated_at":"2026-09-22T21:50:02.934Z","started_at":"2026-09-22T21:50:01.100Z",
 "ended_at":"2026-09-22T21:50:02.934Z","ref":"764ca6e","mode":"local","inputs":{"pdf_path":"examples/inv1.pdf"},
 "exit":"done","outputs":{"record":{"invoice_number":"INV-1042"}},"error":null,"artefacts":null,
 "trace":"file:///…/.wynd/traces/run_20260922T215001100_a1b2c3.jsonl","workspace":null,"duration_ms":1834.0,
 "usage":{"input_tokens":812,"output_tokens":64,"cost_usd":0.0011,"latency_ms":1890.0,"calls":1},"session":null,"meta":{}}
```

### 9.3 Local implementations and on-disk layout — `wynd/runtime/storage/local.py`

```
$WYND_DATA_DIR/                       local runs: <workspace>/.wynd (passed by the CLI); image: set by the builder (e.g. /var/lib/wynd)
  workspaces/<run_id>/                FileWorkspaceStore — the run workspace (cwd of every step run)
    .wynd/steps/<step_path>/<n>/inputs.json    written by the executor (3.7: raw outputs addressable)
    .wynd/steps/<step_path>/<n>/outputs.json   {"exit": ..., ...outputs}
    .wynd/cassettes/<step_package_name>/…      cassette recorder (agentic architect)
  traces/<run_id>.jsonl               JsonlTraceSink
  registry/records/<id>.json          FileRunRegistry (one JSON file per run/job)
  registry/tests/<commit>/<step_hash>.json
  registry/.lock                      fcntl.flock for writes
  (venvs/, build/, jobs/ are owned by other areas and never touched here)

$WYND_HOME/  (default ~/.wynd, only when WYND_HOME is unset)
  mcp.json  providers.json  registries.json        FileRegistry
```

- `FileWorkspaceStore(root)`: `open` runs `mkdir(parents=True, exist_ok=False)`. `close(keep=True)` returns `root/run_id` as a `file://` URI. `close(keep=False)` calls `shutil.rmtree(ignore_errors=True)` and returns `None`.
- `JsonlTraceSink(root)`: keeps one append-mode file handle per run id in a lock-guarded dict. `write` appends the line and calls `flush()` with no fsync. `close` closes the handle. `read` parses lines, skips a truncated last line, and sorts by `seq`. `uri` is `file://<root>/<run_id>.jsonl`.
- `FileRunRegistry(root)`: every write goes to a temp file in the same directory, then `os.replace`. `create` and `update` hold `flock(registry/.lock)`. `list` scans `records/*.json`, filters, sorts by `created_at` descending and slices to `limit`.
- `FileRegistry(home)`: each section file is `{"version": 1, "entries": {"<name>": {...}}}` and is written atomically under `flock(<home>/.lock)`. A missing file means an empty section. Names must match `^[A-Za-z0-9][A-Za-z0-9_.-]*$` and sections must match `^[a-z_]+$`. Because the file layout is a plain directory of JSON files, a Kubernetes ConfigMap mounted at `WYND_HOME` works unchanged (read-only).

Example `$WYND_HOME/providers.json` (the entry schema belongs to the providers architect):

```json
{"version":1,"entries":{"claude-code":{"kind":"agent","tiers":{"cheap":"haiku","standard":"sonnet","strong":"opus"}}}}
```

### 9.4 Backend selection by env var — `wynd/runtime/storage/__init__.py`

```python
@dataclass(frozen=True)
class Stores:
    workspaces: WorkspaceStore
    traces: TraceSink
    runs: RunRegistry
    registry: Registry

class StorageConfigError(RuntimeError): ...

def stores_from_env(env: Mapping[str, str] | None = None, *, data_dir: str | Path | None = None) -> Stores: ...
def registry_from_env(env: Mapping[str, str] | None = None) -> Registry: ...

STORAGE_ENV: list[dict[str, str]]    # for wynd build's process.env.yaml (name, description, default, required="false")
```

| Env var | Default | Meaning |
|---|---|---|
| `WYND_DATA_DIR` | caller's `data_dir`, else error | root for local data backends (workspaces/, traces/, registry/) |
| `WYND_WORKSPACE_STORE` | `file` | workspace backend, `<scheme>[:<location>]` |
| `WYND_TRACE_SINK` | `jsonl` | trace backend |
| `WYND_RUN_REGISTRY` | `file` | run/job registry backend |
| `WYND_REGISTRY` | `file` | user registry backend |
| `WYND_HOME` | `~/.wynd` (only when unset) | location of the `file` user registry |
| `WYND_CASSETTE_MODE` | `replay` | **test-only**: default mode for `run_step`. Set by `wynd test --live`. Not part of the image manifest |

Resolution algorithm, for each interface `i` in `workspace`, `trace`, `runs` and `registry`:

```
spec = env.get(VAR[i]) or DEFAULT_SCHEME[i]
scheme, _, location = spec.partition(":")
ep = importlib.metadata.entry_points(group="wynd.storage", name=f"{i}.{scheme}")  -> none: StorageConfigError(f"unknown {VAR[i]} scheme {scheme!r}")
root = default_root(i)  # lazily: workspace→data/"workspaces", trace→data/"traces", runs→data/"registry", registry→WYND_HOME or Path.home()/".wynd"
                        # data = env WYND_DATA_DIR or data_dir; neither → StorageConfigError("WYND_DATA_DIR is not set")
backend = ep.load()(location=location or None, root=root, env=env)
```

- Local factories: `workspace_file`, `trace_jsonl`, `runs_file` and `registry_file`. Each has the signature `(location: str | None, root: Path | None, env: Mapping[str, str]) -> <backend>`. A `location` overrides `root`.
- Another package adds a backend (object storage, a database, a ConfigMap) by registering an entry point in `wynd.storage`. The runtime never changes and never gains an `if environment:` branch.
- `data_dir` is resolved only when a `file`/`jsonl` backend has no explicit location.

### 9.5 IDs — `wynd/runtime/ids.py`

```python
def new_id(prefix: str) -> str:   # f"{prefix}_{utc:%Y%m%dT%H%M%S}{ms:03d}_{secrets.token_hex(3)}"  e.g. run_20260922T215001100_a1b2c3
def valid_id(value: str) -> bool: # ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$  (filesystem- and URL-safe; externally supplied ids allowed)
```

The IDs sort lexicographically in time order. The controller uses `new_id("job")` for jobs.

---

## 10. Interfaces consumed (from other areas)

| # | From | Name | What runtime-core relies on |
|---|---|---|---|
| C1 | spec | `wynd.spec.process.ProcessSpec`, `Edge`, `Branch`, `Limits` | Fields: `name, goal, latency, provider, env, entry, inputs, outputs (nested by exit), steps: dict[str, StepRef{use}], edges: list[Edge], on_error: str \| None, finally_: list[str]` (alias `finally`). `Edge{from_ (alias from): "step.exit", to: list[Branch], kind: "deterministic"\|"agentic"}`. `Branch{step: str (node name or "$exit.<x>"), when: str \| None, with_ (alias with): dict[str, str], limits: Limits \| None, name: str \| None}`. `Limits{max_traversals: int \| None, timeout: float \| None (seconds), retry: RetryPolicy \| None}`. The loader supplies a **normalised** form: the shorthand is expanded, and YAML scalar `with`/`when` values become expression source strings. JSON round trip uses `by_alias=True`. |
| C2 | spec | `wynd.spec.expr.parse(src: str) -> Expr`, `evaluate(expr: Expr, scope: Mapping[str, Any]) -> Any`, `ExpressionError` | Attribute access on a Mapping is a key lookup, and subscript works on a Mapping or list (int keys allowed: `edges["v.done"][0]`). JSON-mode values. Evaluation errors raise `ExpressionError`. Missing-reference semantics inside `default()`/`coalesce()` are spec's call. |
| C3 | spec | `wynd.spec.types.fields_model(name: str, fields: Mapping[str, TypeExpr]) -> type[BaseModel]` and `exit_model(exit: str, fields) -> type[BaseModel]` (adds `exit: Literal[exit] = exit`) | Builds the process inputs/outputs models for `validate_inputs`, `$exit` binding, `ProcessStep.for_process` and `process_interface`. |
| C4 | spec | `wynd.spec.lock.StepLock`, `RetryPolicy`, `load_step_lock(path) -> StepLock` | `StepLock.kind`, **`entrypoint: "<module>:<Class>"`** (required), `provider \| None`, `tier \| None`, `thinking \| None`, `retries: RetryPolicy \| None`, `effects: list[str]`, `mcp: list[{name, allow, tools}]` snapshot. `RetryPolicy{validation: int, tool: int, exception: int}`, each with a default, and `model_fields_set` preserved for override merging. |
| C5 | process | loader, validator, local venv manager | Produces a `RunPlan(mode="local")` (§6.1) with max_traversals filled. Creates venvs at `.wynd/venvs/<id>` with `wynd-spec` + `wynd-runtime` (same version, editable in this monorepo) + requirements. Calls `check_step_module`, and uses `describe` (via `WorkerPool.describe`) for exit-aware checks. Checks `on_error`/`finally` names exist in `steps:` (and exempts them from "unreachable"), that `$exit.error` is not a target, and that handler exits are declared process outputs. |
| C6 | agentic | `wynd.runtime.agentic.loop.complete(step, call: AgentCall) -> AgentResult`, `wynd.runtime.agentic.describe_agentic(cls) -> dict` | See §4.3. It raises `StepFailure` with cause `output_validation`/`tool`/`transport`/`config`, and emits `model.call`/`tool.call` via `call.runtime.trace.emit`. |
| C7 | providers | `wynd.runtime.providers.DEFAULT_PROVIDER = "claude-code"`, `resolve_model(provider: str, tier: str, registry: Registry) -> str` | Maps a tier to a model id using the `providers` registry section plus built-in defaults (claude-code: cheap→haiku, standard→sonnet, strong→opus). Raises `StepFailure("config", ...)` for an unknown provider. |
| C8 | agentic | `Tool`, `McpServer`, `tool` decorator (`wynd.runtime.tools`, `wynd.runtime.mcp`) | Used only as type annotations on `AgenticStep` and as re-exports from `wynd.runtime`. |
| C9 | cassettes | semantics of `CassetteConfig.mode/dir/record_dir`, the `model.call` payload | Pass-through. |
| C10 | builder | installs step wheels under `wynd_steps/<step_package_name(key)>/`, writes the image `RunPlan` JSON, sets `WYND_DATA_DIR` in the image | See §5.2 and §6.1. |
| C11 | supervisor | constructs `WorkerPool(plan).start()` at boot and `Executor(plan, pool, stores_from_env())` | Calls `run(..., on_event=<SSE fan-out>)` per request in a thread, and maps `InvalidProcessInputs` to 422. |

## 11. Interfaces provided (to other areas)

| To | Provided |
|---|---|
| everyone writing steps | `wynd.runtime`: `Step, DeterministicStep, AgenticStep, ShellStep, ProcessStep, ErrorOutput, ProcessError, RuntimeHandle` (plus re-exports of `tool, McpServer, env` from the agentic area) |
| process (loader/validator/venvs) | `RunPlan, ProcessPlan, NodeRef, StepPlan, VenvSpec, step_package_name, dep_set_hash`; `WorkerPool.describe → StepDescription`; `describe_process`; `ErrorOutput` field set (for exit-aware `X.error` reference checks: `cause, message, type, traceback, inputs, partial_outputs, attempts, child`); `check_step_module`; `Executor` (process only *invokes* it, 8) |
| builder | `step_package_name` (wheel layout), `VenvSpec` (plan.json / process.lock.yaml), `STORAGE_ENV` (manifest), worker command `python -m wynd.runtime.worker` |
| supervisor / run API | `Executor`, `ProcessResult`, `InvalidProcessInputs`, `WorkerPool`, `RunPlan`, `TraceEvent` models and `parse_event`, `stores_from_env`, `new_id` |
| agentic / providers / cassettes | `AgenticStep` attributes, `AgentCall`, `AgentResult`, `StepFailure`, `StepInterface` (`output_json_schema`, `validate_output`), `ExecPolicy`, `CassetteConfig`, `Usage`, `ModelInfo`, `RuntimeHandle.trace.emit`, `HttpClient`, `Registry`, `registry_from_env` |
| compiler | `run_step`, `load_step`, `StepResult`, `check_step_module`, `RunRegistry` (`session`, `put_test_result`), `Executor` + `WorkerPool` for process-level tests |
| controller | `RunRegistry`, `RunRecord`, `TestResult`, `Registry`, `stores_from_env`, `new_id`, `TraceSink.read`, `build_tree` |
| cli | `build_tree`, `read_trace` for `wynd trace`; `STORAGE_ENV` for `wynd env check` |
| web | trace event JSON (§8.2) and `RunRecord` JSON (§9.2), for TypeScript types |
| M5 | `BranchSelector`, `BranchChoice`, `SelectCall`, `Executor(selectors=...)`; `step.end` carries `model.tier`, `usage.cost_usd`, `attempts` for tier promotion/demotion |

---

## 12. Interpretations (where the spec needed a reading)

- **I-1** `on_error` and `finally` entries name nodes declared in `steps:` (each has a `use:`). They may be ProcessSteps.
- **I-2** The `on_error` handler's Input is bound **by field name** from the `ProcessError` JSON. A handler may declare `class Input(ProcessError)` or any subset. Handler exit `X` terminates the process with `$exit.X`, and its outputs are the handler's output fields validated against `outputs.X`.
- **I-3** A `finally` step's Input is bound by field name from `process.inputs ∪ {run_id, exit}`. Its exit is not routed. Its errors are recorded (`ProcessResult.finally_errors`, trace) but **do not change** the process exit. It runs with no deadline, even while an outer timeout unwinds.
- **I-4** A timeout routes to the **process error handler** (3.5 lists timeout there), not to the step's `error` exit. It is enforced by killing the worker.
- **I-5** A worker crash, or a JSON-RPC fault, is a step `error` exit with cause `worker_crash`, like a raised exception.
- **I-6** `steps.<n>.runs` increments when the step starts. Since execution is sequential this equals completed runs whenever an expression can see it. Handler and finally runs count too. All counters, `previous` and `latest` are per process instance. `previous` is `null` at an instance's entry step.
- **I-7** `max_traversals: N` allows the branch to be taken N times. The (N+1)-th attempt routes to the handler with cause `max_traversals`. All expressions of one edge resolution see one scope snapshot, taken before that branch's counter increments.
- **I-8** "The run ends in the process error handler" means the **top-level** instance entered its handler, default or custom. Only then is the workspace kept. A child's handler returning a normal exit, or propagating `error` to a parent that routes it, does not keep the workspace.
- **I-9** `ProcessResult.error` is set whenever the top-level handler ran, even if a custom handler mapped the run to a non-error exit. The status is `failed` iff the final exit is `error`.
- **I-10** Top-level process inputs are validated at the boundary (`InvalidProcessInputs`), before any run, trace or registry record exists. Child inputs (from `with:`) fail as the ProcessStep node's `error` exit (`input_validation`).
- **I-11** Entry binding passes **only** the process input fields that the entry Input declares. This is safe for `extra="forbid"` models.
- **I-12** Step `outputs` in the trace, in the scope and on the wire never include the `exit` key. `steps.X.exit` is separate. Values are JSON-mode, so dates are ISO strings and compare correctly as strings.
- **I-13** The retry policy has three counters: `validation` (agentic: continue the conversation, done in the loop; other kinds: re-run pre/run/post), `tool` (idempotent tools only, in the loop) and `exception` (deterministic/shell only: re-run after an exception, hook failure or `shell_exit`). The defaults are the spec's: deterministic 0/0/0 and agentic 2/1/0. `limits.retry` overrides field by field.
- **I-14** `AgenticStep.run` is never called. The loop always completes it.
- **I-15** "Edges are compiled like steps" is read as: expressions are parsed once when the plan loads and evaluated by the spec's tree-walking evaluator. There is no code generation and no `eval`.
- **I-16** Declared `effects` and "no network unless declared" are **not enforced at runtime** in v1 (plain Docker, no sandbox). They are recorded in the lock and trace for the build and for review.
- **I-17** `full_trace` context is the current instance's history: `{process:{name,goal,inputs}, steps:[{step,run,exit,inputs,outputs,summary}]}`. Other context entries are wynd expressions over the scope, which additionally contains `process.goal` and `process.name`. An entry that is not yet available evaluates to `null`.
- **I-18** An unrouted **non-error** exit at runtime (the validator's "explicitly ignored" case) routes to the handler with cause `unrouted_exit`.
- **I-19** An expression evaluation error in `when:` or `with:` routes to the handler with cause `expression_error`. Truthiness of `when:` is Python `bool()`.
- **I-20** A worker serves one step run at a time. Concurrent runs in one container queue per venv, at step granularity.
- **I-21** `self.runtime.cache` is scoped per step package key within one worker process, and lives until that worker exits or restarts.
- **I-22** The provider default chain is: step lock `provider` → the root process's `provider` → `"claude-code"`. A child process's `provider` is ignored. The tier defaults to `cheap` and thinking to `low`.
- **I-23** `ShellStep` gets its inputs through argv templating (`{field}`, `{field!q}`) and `WYND_INPUT` (JSON). Its stdin is /dev/null. Stdout, stderr and the exit code fill the matching output fields, and other fields are parsed from stdout JSON.
- **I-24** A step path joins node names with `.`. The validator must forbid `.` in node names (the spec's names are identifiers).

---

## 13. Test plan (all offline, deterministic; no `live`/`docker` markers needed in core)

**test_step_interface.py (RC-A)**
- `X | Y` unions, `typing.Union` and `Annotated[..., Field(discriminator="exit")]` are all accepted.
- The plain `done` shorthand is accepted.
- Rejected: a plain model with a non-done exit, a missing `exit` field, a non-Literal exit, a multi-value Literal, duplicate exits, a declared `error` exit, and a non-BaseModel `Input`.
- `exit_model("error") is ErrorOutput`.
- `describe_step` JSON includes `error` in `exits`.
- `ProcessStep.for_process` / `process_interface` on a spec with 3 exits.

**test_middleware.py (RC-A)** uses `run_chain` directly with `emit=list.append`.
- Input validation failure gives `error`/`input_validation` with `attempts=0`, and raw inputs are preserved.
- A `run` exception gives `error`/`exception` with type, traceback and inputs.
- A `pre` failure gives `hook`, and `post` is not called. A `post` failure after a successful run gives `hook` with `partial_outputs` equal to the run output. `post` still runs after a `run` exception.
- `os.environ` keys added, changed or removed in `pre`/`run` are restored. A `chdir` inside `run` is restored. The cwd during `run` equals the workspace.
- Returning the wrong type or an invalid dict gives `output_validation`. `exception: 2` retries give 3 attempts and a fresh instance each time (identity check). `validation: 1` re-runs once.
- The agentic path uses a monkeypatched `wynd.runtime.agentic.loop.complete`. The test asserts the `AgentCall` fields (context, policy, cassette, interface) and that `note`, `usage` and `model` land in the result. A `StepFailure("tool")` becomes `error`/`tool` with usage preserved.
- `self.runtime` fields are populated. The cache persists across two calls sharing a `StepCache` and not across different ones. `logger.info` produces a `step.log` event.

**test_shell_step.py (RC-A)**
- Exit-code mapping: 0 → done, 1 → not_found, `"*"` → error gives `shell_exit` with a stderr tail.
- The `stdout`/`exit_code` fields are filled, and stdout JSON is merged into the extra fields.
- The `!q` quoting is exercised with an input containing spaces, `;` and quotes.
- `WYND_INPUT` is visible to the child. Stdin is empty.

**test_summary.py (RC-A)**: projection rules (≤200 chars kept, long strings truncated, big list/object markers), declaration order preserved, error summaries omit the traceback/inputs/partial/child fields.

**test_run_step.py (RC-A)**
- A deterministic step returns a `StepResult` with the typed `output`.
- `load_step` on a fixture directory resolves the entrypoint from `step.lock.yaml`.
- An agentic step in replay mode with a fake cassette-backed loop (monkeypatched `complete`) runs. `WYND_CASSETTE_MODE` changes the default mode.

**test_lint.py (RC-A)**: each rule, both positive and negative cases, class-body lists not flagged, `__all__` allowed.

**test_http.py (RC-A)**: against a local `ThreadingHTTPServer`: GET with params, POST json, a 404 returned rather than raised, `raise_for_status`, a timeout raising `HttpError`.

**test_plan.py (RC-B)**: `step_package_name` is stable and unique for `a/read` vs `b/read` and handles a leading digit. `dep_set_hash` is order- and duplicate-insensitive and ignores comments. `RunPlan` round-trips through JSON, including `ProcessSpec` aliases.

**test_worker_protocol.py (RC-B)** runs real `sys.executable -m wynd.runtime.worker` subprocesses against the fixtures.
- `init` with `a/read` and `b/read` in one worker: both import, and `describe` returns different classes and exits. `b/read` uses a relative import of `helpers`.
- A `run_step` round trip.
- A step that calls `print()`, `os.write(1, b"junk\n")`, `subprocess.run(["echo","x"])` and `sys.stdin.read()` does not break the protocol. Its output arrives as one `step.log` (stream=output) notification, and stdin reads return "".
- `logger.warning` gives a live `step.log` notification before the response.
- A step with a missing dependency: init reports `ok:false`, and `run_step` returns `error`/`import`.
- An unknown key gives JSON-RPC error `1002`. A call before init gives `1001`. A protocol mismatch gives `1003`.
- `shutdown` exits 0. EOF exits 0. SIGINT is ignored.

**test_pool.py (RC-B)**
- `os._exit(3)` in a step raises `WorkerCrashed`, and the next dispatch transparently starts a new worker (the pid changes).
- A step that sleeps 5s with `timeout=0.5` raises `StepTimeout`, the worker is killed and restarted on the next dispatch.
- `start()` over two venvs, both `sys.executable` with different step sets, starts both concurrently.
- The per-venv lock serialises two threads.
- `worker.start` is emitted on a lazy spawn.
- The `InProcessDispatcher` contract matches the `WorkerPool` contract on the same fixture (same `RunStepResult` minus timings).

**test_executor_routing.py (RC-C)** uses `InProcessDispatcher` and synthetic classes defined in the test. `conftest.graph(yaml_str, classes) -> (RunPlan, dispatcher, stores(tmp_path))`.
1. Entry binding by name, with extra process inputs dropped for an `extra="forbid"` Input.
2. Shorthand edge with `with:`.
3. if/elif/else order, the first bare branch as the else, and later branches ignored.
4. A fully conditioned `to:` with no match gives `$exit.error` with cause `no_branch_matched` and `edge="v.done"`.
5. The dogfood-shaped validate↔fix loop exits through the else after `steps.fix.runs < 3`. `edges["validate.done"][1].taken` and the named `.retry.taken` agree.
6. Latest-completed-run: validate's second run sees fix's outputs, and `steps.validate.outputs` after the loop is the last run's.
7. `previous.outputs` and `previous.summary`.
8. `max_traversals: 2` exceeded gives cause `max_traversals`, with `taken==2` recorded.
9. `limits.retry` override: a stub dispatcher captures `params.policy.retries`.
10. `env.X`, `run.id` and inline `if/then/else` inside `with:`.
11. `$exit.done` binds outputs via `with:`, and a missing field gives `invalid_process_outputs`.
12. An expression error gives `expression_error`.
13. An agentic edge kind with no selector gives `unsupported_edge`. A custom selector is called, and its usage is recorded on `edge.taken`.
14. The context for a step whose description declares `["process.goal","previous.summary","steps.a.outputs","full_trace"]` arrives in `params.context`.

**test_executor_errors.py (RC-C)**
- `X.error` with an edge re-binds `steps.X.outputs.inputs.text` into a fallback step, and the run succeeds.
- `X.error` with no edge goes to the default handler: `exit=="error"`, `ProcessError.step=="X"`, cause `step_error`, `step_error.cause=="exception"`, a trace pointer to a `process.error` event, the workspace kept (`locate` returns a URI) and status `failed`.
- A custom `on_error` receives the ProcessError fields. Its exit `parked` gives `$exit.parked` with `ProcessResult.error` set. A handler that raises gives `$exit.error` with `handler_error`, and the handler runs only once (no recursion).
- `finally` steps run on success and on error, in order, with `exit` bound. A failing finally step leaves the exit unchanged and is listed in `finally_errors`.
- A timeout: a stub dispatcher raises `StepTimeout` for the branch with `limits.timeout`, giving cause `timeout` and a `step.end` with `timed_out`.
- `WorkerCrashed` from a stub dispatcher gives an `error` exit with cause `worker_crash`, and that exit is routable.
- `InvalidProcessInputs` is raised with no trace file and no registry record.
- On success the workspace is deleted, and the trace and registry record have status `succeeded`.
- `internal`: a sink that raises on one `step.end` still produces a `ProcessResult` with cause `internal`.

**test_executor_nested.py (RC-C)**
- A parent with a `use: process:child` node gives step paths `sub.read`, child `step.start.parent == span(sub)` and a shared `run_id`.
- A child invoked twice (in a loop) sees `steps.read.runs == 1` each time.
- A child `$exit.error` gives the parent node `error`/`child_process` with `child` populated. When the parent routes `sub.error` to a fallback, the run succeeds and the workspace is deleted.
- Child `$exit.partial` gives the parent exit `partial` with the outputs passed through.
- A child's `provider` is ignored: the root default reaches the child's agentic `params.policy.provider`.
- A parent timeout on the ProcessStep branch, with the child's dispatch stubbed to time out, gives the **parent** handler cause `timeout` at step `sub`, and the child's finally steps still run.
- Usage totals are summed across nested steps with no double counting.

**test_trace.py (RC-C)**
- A golden JSONL for a 3-step run, with `ts`, timings and ids normalised: `seq` is 1..n contiguous, the event order is exact and `span == seq` of step.start.
- `parse_event` falls back for unknown types.
- `build_tree` nests the child under the ProcessStep node with edges interleaved.
- An `on_event` callback exception does not break the run.

**test_storage_local.py (RC-D)**
- Workspace open/close keep/delete, and a duplicate open raises.
- The JSONL sink writes and reads, skips a truncated last line, and its URI is a `file://` path.
- RunRegistry create, update (merge plus `updated_at`), get, list filtered by kind/process/status newest-first with limit, test results round-trip, and concurrent `update`s from two threads lose no field.
- Registry put, get, list and remove, atomic files, an invalid name rejected and a missing file giving an empty section.
- `stores_from_env`: defaults, `jsonl:/tmp/x` location override, an unknown scheme error, no `WYND_DATA_DIR` and no `data_dir` raising `StorageConfigError`, the `WYND_HOME` fallback to `Path.home()/".wynd"` (monkeypatched HOME), and a backend supplied by a test entry point (monkeypatched `entry_points`).

**test_ids.py (RC-D)**: format, sortability and `valid_id`.

The integration coverage (dogfood process through `WorkerPool` with real uv venvs) belongs to the process and examples owners. Core tests only need `sys.executable`.

---

## 14. Items for the synthesizer to reconcile

1. **Step package layout** (compiler, process, builder). The step directory is the import package; relative imports only; `step.lock.yaml.entrypoint = "step:<Class>"`; wheels install to `wynd_steps/<step_package_name(key)>/`. If another architect proposes a `src/` layout or a named import package, the worker needs only `(key, entrypoint, path)`. Keep `wynd_steps.<pkg>` as the loaded name either way.
2. **Step key.** It is the canonical step id. I recommend the workspace-relative posix directory of the package, so that it is unique and trivially derived. The spec's display ids (`finance:extract/invoice`) can live alongside it.
3. **`RetryPolicy` shape** (spec lock + `limits.retry`): `{validation, tool, exception}` with kind-dependent defaults via `default_retries(kind)`. The spec architect may choose other names; the executor only needs `model_copy(update=exclude_unset)`.
4. **`Limits.timeout` units.** Seconds as float, after parsing. If spec accepts `"30s"` in YAML it must normalise.
5. **Expression scope conventions** (C2): attribute on a Mapping becomes a key, int subscripts work on mappings, values are JSON-mode, and missing-reference behaviour in `default()`/`coalesce()` needs a decision.
6. **`Usage`/`ModelInfo`** are owned here (`wynd.runtime.usage`). Provider `GenerateResponse`/`AgentResponse` should reuse `Usage`, so the per-call `latency_ms` is summed into step usage.
7. **Cassette key vs tier mapping.** If cassette keys include the concrete `model_id`, a user who remaps `cheap` breaks replay. The cassette architect should decide whether to key on `(provider, tier)` or on `model_id`. `ExecPolicy` carries both.
8. **Validator obligations** that follow from the executor's semantics: forbid `.` in node names, forbid `$exit.error` as a target, require handler exits ⊆ process outputs, require `finally` Input fields ⊆ `process.inputs ∪ {run_id, exit}`, and exempt `on_error`/`finally` nodes from the unreachable check.
9. **`wynd test`** must run pytest with `--import-mode=importlib` inside each step's venv, and set `WYND_CASSETTE_MODE` for `--live`. Local venvs need `pytest` installed.
10. **The image `RunPlan` location and `WYND_DATA_DIR` default** are chosen by the builder and supervisor, for example `/opt/wynd/plan.json` and `/var/lib/wynd`.
11. **The controller's long-lived local-mode runs** (`wynd serve-api`): make a new `WorkerPool` when the plan or step sources change (include a source hash in `StepPlan` if needed). The pool does not watch files.
12. **The claude-agent-sdk subprocess** inherits the worker env (the dev key `CLAUDE_CODE_OAUTH_TOKEN`) and uses its own pipes, so the fd redirection in §5.1 does not affect it. The lead has confirmed nested `claude` invocation works.
