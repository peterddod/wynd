# Wynd — Specification

> Wynd is a framework for turning a known, step-by-step process into a tested, reproducible, cheap-to-run automation. Humans describe the process as a graph of steps with typed inputs/outputs and examples; an LLM compiles each step into deterministic code where possible and an agent where not; the result runs in a single Docker image per process.

## 1. Goals

- Let someone who knows exactly how a process should be done encode it as a graph, without needing to program.
- Compile that graph into something **testable, verifiable and reproducible**, and cheaper per run than handing the whole task to a frontier LLM.
- Use deterministic code for any step that can be a pure function of its inputs; use agents only where judgement or external information is needed.
- Make steps modular and reusable: a step is a contract, the process is the wiring.
- Keep the fast path fast: a warm process with mostly deterministic steps should complete in seconds, with step-to-step latency in milliseconds.

## 2. Non-goals (v1)

- A global, self-growing graph shared across processes / users. Later.
- Learned routing / router model. Routing in v1 is deterministic edges.
- Agentic edges (LLM verifiers on transitions). The edge type should exist in the schema but v1 ships only deterministic edges.
- WASM or micro-VM isolation. Plain Docker.
- Multi-tenant hosting. Single-user, local-first.
- Parallel steps, fan-out or join. Execution is strictly sequential: one edge per exit, one target per branch. The executor is a state machine, not a DAG scheduler.

## 3. Core concepts

### 3.1 Step

A step is the unit of work. Its public surface is deliberately tiny and there are few options — a step definition is explicit, verbose code, not configuration:

- `Input`: a pydantic model type
- `Output`: a discriminated union of pydantic models, **one per exit**, each carrying `exit: Literal["<name>"]`. Non-`done` exits declare their own (possibly empty) shape. Fields are never made optional to accommodate another exit.
- `pre()`: per-run hook before `run` (env vars, sessions, temp resources)
- `run(input: Input) -> Output`
- `post()`: per-run hook after `run`, for releasing state middleware can't see (temp files, sessions, subprocesses)
- private helpers as the derivation needs

The executor reads `Output.exit` for routing. **A step does not know where it goes next.**

Hooks are code, not configuration. They run per step run, never per worker lifetime. Module-level state in steps is forbidden; the only cross-run caching allowed is via an explicit `self.runtime.cache`.

Examples and tests are **not** part of a step. Examples live on the proto-step (the authoring artefact) and are consumed by the compiler, which generates a pytest file per compiled step. The step itself is only the implementation.

### 3.2 Step kinds (derivations of the base class)

- `DeterministicStep` — pure Python function of inputs. No LLM, no network unless declared.
- `AgenticStep` — an LLM loop, NOOA-shaped (see 3.8). Everything LLM-specific lives here and not in the base class: the class docstring is the instruction, `run` with a `...` body is completed by the loop, `@tool` methods are the model-callable capabilities, `tools`/`mcp` declare shared and external tools, structured output is enforced to `Output`, and `context` declares which trace context the step pulls (3.7). Model tier, thinking effort and validation retries are lockfile fields (6.3), not class attributes.
- `ShellStep` — runs a command in the step's environment; stdout/stderr/exit code mapped to outputs and exits.
- `ProcessStep` — wraps a whole process as a step. Its inputs/outputs are the child process's inputs/outputs; internals are invisible to the parent. The child runs in the **same container**: the builder merges the child's env fragments recursively into the parent's single image. The child's `env.base`, `provider` and `latency` are ignored — the parent's apply — and the validator warns when they differ. Child trace events carry the parent `run.id` and a step path (`parent.child`), so `wynd trace` nests them and `steps.<name>.runs` counters are scoped per process instance.

### 3.3 Process

A process is a graph of step references plus edges. It has its own `inputs`, `outputs`, `examples`, an `entry:` step, and is itself usable as a step (see `ProcessStep`). The entry step's `Input` is bound from `process.inputs` **by field name**; a name mismatch is a validator error. There is no `$entry` edge.

### 3.4 Edge

An edge connects a step exit to the next step. Edges hold all routing and control logic:

- **from**: which exit of the source step this edge handles. One edge per exit.
- **to**: a list of branches, evaluated top to bottom. Each branch names a `step` and may carry a `when:` condition (an expression over the step's outputs and anything else in scope) plus its own `with:` and `limits:`. Semantics:
  - a single branch with no `when:` is a plain transition to the next step (shorthand: `to: save`)
  - several branches form an if / elif chain; the first branch whose `when:` is true is taken
  - the first branch with no `when:` is the else; any branches after it are ignored (the validator warns)
  - if every branch has a `when:` and none is true, the run routes to the process error handler (3.5)
- **with**: mapping onto the target's inputs, declared on the branch. With the `to: <step>` shorthand, `with:` and `limits:` sit on the edge itself. Values are expressions, so value-level conditionals are inline too.
- **limits**: `max_traversals`, `timeout`, retry policy — declared on the branch. `max_traversals` is a mandatory safety guard that the validator fills in automatically (default 10) on **every branch that lies on a cycle** (not just the one that "closes" it, which is DFS-order-dependent); authors never have to write it, and may raise or lower it. Exceeding it routes to the process error handler (3.5). Intended loop exits are expressed with counters in `when:` and an else branch.
- **kind**: `deterministic` (v1) or `agentic` (schema only in v1)

Edges are compiled like steps: the compiler emits code for the condition and transform.

### 3.4.1 Expression language

A small, safe, side-effect-free language used everywhere a value or condition appears (`when:`, `with:`, `limits:`). Deliberately tiny:

- **References**: `steps.<name>.outputs.<field>`, `steps.<name>.exit`, `process.inputs.<field>`, `env.<NAME>`, `run.id`. Inside a loop, `steps.<name>` refers to that step's **latest completed run** on the current path.
- **Reserved**: `previous` — the immediately preceding step in this run (`previous.outputs`, `previous.summary`).
- **Counters** (reset per process instance): `steps.<name>.runs` — times that step has executed so far; `edges["validate.done"][0].taken` — times a specific branch (by index in the `to:` list) has been taken; a branch may also carry `name:` and be referenced as `edges["validate.done"].retry.taken` (bracket syntax because the exit name contains a dot; index because two branches may target the same step). These are how loops get an intended exit: `when: fixable and steps.fix.runs < 3`, with the next branch as the escape route.
- **Literals**: strings, numbers, booleans, null, lists, objects.
- **Operators**: `== != < <= > >= and or not in + - * / %`.
- **Inline conditionals**: `if <cond> then <a> else <b>`, chainable with `elif`. This replaces the Actions `cond && a || b` idiom, which misbehaves when `a` is falsy.
- **Functions**: a fixed builtin set (`len`, `lower`, `upper`, `contains`, `startswith`, `join`, `split`, `default(x, y)`, `coalesce`, `now()`), no user-defined functions.

Examples:

```yaml
with:
  dest: if steps.classify.outputs.kind == "invoice" then env.INVOICE_DIR
        elif steps.classify.outputs.kind == "receipt" then env.RECEIPT_DIR
        else env.MISC_DIR
  priority: if steps.extract.outputs.total > 10000 then "high" else "normal"
  label: default(steps.extract.outputs.label, "unlabelled")
```

Implemented as its own module in `spec` with a Lark grammar (no hand-rolled parser) and a tree-walking evaluator, with no `eval`. Validate-time checking is staged: in M1 it verifies **reference validity** only (does `steps.x.outputs.y` exist, is `to` a real step); full **type checking** against step schemas lands in M3 alongside schema inference.

### 3.5 Built-in error handling

Errors are handled by the runtime, not by the process author, unless they choose to. The defaults:

- Every step implicitly has an `error` exit. A raised exception, a failed output validation after retries, a tool failure, or a hook failure all resolve to it. Authors never have to declare or wire it.
- A `to:` list whose branches all have `when:` and none match, an `error` exit with no edge, a traversal limit exceeded, or a timeout all route to the **process error handler**.
- The default process error handler terminates with `$exit.error`, attaches a structured `ProcessError` (step, cause, inputs, partial outputs, trace pointer) to the process output, and leaves the workspace intact for inspection.
- Retries are a per-step policy set by the compiler in the lockfile (defaults: deterministic 0, agentic 2 on validation failure, tools 1 on transient network errors **only if the tool declares `idempotent: true`**, otherwise 0), overridable per branch via `limits:`.
- **An agentic retry on validation failure continues the conversation**: the validation error is appended as a message and structured output is re-requested. The loop is never restarted once any tool has been called; full restarts happen only for transport failures before the first tool call. A transport failure **after** a tool call resolves the step to its `error` exit. Side effects therefore fire at most once regardless of tool idempotency.
- A process may optionally declare its own handler, which is just a step: `on_error: notify_and_park`. It receives the `ProcessError` as input; its exits map to `$exit.<name>`. An error inside the handler terminates with `$exit.error` — no recursion. Cleanup steps declared as `finally: [close_session]` run regardless of outcome.
- A `ProcessStep` propagates the child's `error` exit to the parent as its own `error` exit, so nested processes compose without extra wiring.

### 3.6 Runtime middleware (under the hood)

Every `run` is wrapped in a fixed chain the step never sees:

1. **Receive bound inputs** — the executor has already evaluated the branch's `with:` when resolving the edge; middleware validates the result against `Input`.
2. **Build context** — for agentic steps only, assemble the trace context the step declared it needs.
3. **Call** `pre` → `run` → `post`, with `os.environ` and cwd snapshotted before `pre` and restored after `post`. `post` is only for state middleware can't see.
4. **Parse & validate outputs** — structured output enforced against `outputs` schema; retry per policy on validation failure.
5. **Summarise** — write a structured summary `{step, exit, key_outputs, note}` into the trace. This is **derived deterministically, never a model call**: `key_outputs` is a projection of the output model, `note` is the agentic loop's final free text or empty. Deterministic steps therefore still pay nothing.
6. **Emit exit** — hand the exit to the executor, which resolves the edge.

### 3.7 Trace context

Context is **pull, not push**. `AgenticStep` (not the base class) declares what it needs, as a class attribute on the compiled step:

```python
context = ["process.goal", "previous.summary", "steps.classify.outputs"]  # or ["full_trace"]
```

The runtime assembles exactly that. Deterministic and shell steps have no context and pay nothing. Summaries are structured (`{step, exit, key_outputs, note}`), not free prose, so downstream steps can read fields rather than trust paraphrase. Raw outputs remain addressable in the workspace so a later step can fetch source rather than summary.

### 3.8 Tools and MCP

Agentic steps get capabilities three ways, all landing in one flat tool list the model sees:

1. **`@tool` methods on the step.** The decorator marks a method as model-callable; its signature and docstring are the tool schema. Undecorated methods are private helpers the model cannot see. Explicit over implicit: the model never gets "every public method".
2. **Shared tool library** in `runtime.tools`: plain decorated functions (`http_get`, `web_search`, `workspace_read`, `workspace_write`, `shell`, `now`). A step opts in with `tools = [web_search, http_get]`. The compiler reaches for these first and writes bespoke `@tool` methods only for step-specific logic.
3. **MCP servers**, declared on the step:

```python
class TriageStep(AgenticStep):
    """Triage a support message into a typed ticket."""
    class Input(BaseModel):  message: str
    class Done(BaseModel):   exit: Literal["done"] = "done"; ticket: Ticket
    class Spam(BaseModel):   exit: Literal["spam"] = "spam"
    Output = Done | Spam

    tools = [web_search]
    mcp = [McpServer("github", allow=["list_issues", "get_issue"])]

    @tool
    def lookup_customer(self, email: str) -> Customer:
        """Fetch the customer record for an email address."""
        return self.runtime.http.get(...)

    def run(self, input: Input) -> Output: ...   # agentic: completed by the loop
```

Rules:

- Every tool (method, library or MCP) declares **effects** (`network`, `filesystem`, `shell`), whether it is **idempotent** (default false; only idempotent tools are ever retried), and any **env vars** it needs, so the build knows what a step requires and the trace records what it used.
- MCP tool lists are **discovered at compile time and snapshotted** into the lockfile (names, schemas). The runtime verifies the live server still matches and fails loudly if not. `allow` is required; exposing a server's entire tool surface to a cheap model is not permitted by default.
- Secrets and auth are always **references resolved at run time** from the process env (`env("GITHUB_TOKEN")`); nothing lands in an image or lockfile.
- **MCP servers are configured once, globally.** `wynd mcp add github --url ... --auth-env GITHUB_TOKEN` (or the equivalent OAuth flow in the web UI) writes to a user-level registry; any step in any process then references the server by name. A server's auth is set up once and reused across all workflows.
- A `@tool` method is just a method, so deterministic steps and other processes can call the same code directly without going through a model.
- `runtime`'s own loop is constrained to calling declared tools; it never executes model-written code. An `AgentProvider` harness (3.9) may, but only inside the process container, with declared tools exposed to it over in-process MCP.
- Not in scope: exposing steps or processes *as* MCP servers.

### 3.9 Providers

`AgenticStep` never talks to a model directly. It talks to a **provider** resolved from the lockfile (`provider: anthropic`, `model: cheap`), with a process-level default. Two kinds:

**ModelProvider** — single turn; `runtime` runs the loop.

```python
class ModelProvider(Protocol):
    name: str
    def generate(self, req: GenerateRequest) -> GenerateResponse: ...
    def tiers(self) -> dict[str, str]: ...        # tier name -> concrete model id

# GenerateRequest:  messages, tools (schemas), output_schema, thinking, model_id
# GenerateResponse: text | tool_calls | structured_output, usage, raw
```

`runtime` owns the loop: send request, execute tool calls (`@tool` methods, `runtime.tools`, MCP), append results, repeat until structured output validates or the retry policy is exhausted.

**AgentProvider** — whole loop; an external harness runs it.

```python
class AgentProvider(Protocol):
    name: str
    def run(self, req: AgentRequest) -> AgentResponse: ...

# AgentRequest:  instruction, context, input, output_schema,
#                tools (exposed as an in-process MCP server), mcp servers, workspace path, model_id
# AgentResponse: structured_output, usage, transcript
```

`runtime` skips its loop and validates the returned output against `Output`. Retries on validation failure still apply.

Rules:

- The structured-output mechanism (native schema, constrained decoding, parse-and-retry) is the provider's concern; `runtime` only validates.
- Tier names (`cheap`, `standard`, `strong`) are abstract in lockfiles; each provider maps them to model ids in the user-level registry (`wynd provider add|list|remove`, alongside `wynd mcp`). Tier-to-model mapping never lives in a lockfile. **Tiers must mean comparable strength across providers**: swapping the process-default provider must not silently change quality.
- `provider` is an optional per-step lockfile field with a process-level default. In v1 `wynd compile` only ever sets the process default; the per-step override exists in the schema and validator but is hand-edited into the lockfile only.
- **Providers contribute env fragments** exactly like steps, merged by the same builder. `claude-code` declares what it needs (Node, the harness) in its fragment. There is no separate agent base-image variant.
- Cassette recording and replay wrap the provider boundary (`generate` or `run`), so `wynd test` works identically for any provider.
- Providers register via Python entry points (`wynd.providers`); `runtime` ships `anthropic` (ModelProvider) and `claude-code` (AgentProvider). Others are separate packages.
- Every response must report `usage` (input/output tokens, cost if known, **per-call latency**); the trace records it per step regardless of provider, so `latency: fast` warnings can later be data-driven and resource accounting (15) is a report over the trace.
- AgentProviders are for steps where the harness is the point (coding, multi-file investigation). Their per-call startup cost is recorded in the trace, and the validator warns if one is used in a process declared `latency: fast`.

## 4. Environments

- **The process is the image boundary. The compiler emits exactly one Dockerfile per process.** No per-step containers, no mixed fleets. The default base is `debian-slim-python`. A process may opt into `alpine-python`; if any step's fragment cannot be satisfied on musl, the builder falls back to `debian-slim` for the whole process and records why in `process.lock.yaml`. One Debian slim beats three Alpines and a Debian slim.
- Each step (and each provider) defines an **environment fragment**: Python dependencies, optional `system:` packages, an optional minimum requirement (`requires: glibc`). Fragments are declared; hooks are code on the step (3.1). Steps never name a base image. The base image is chosen at **process level** (`env.base` in the process YAML), because the process is the image boundary.
- At build time, the process builder merges step fragments into that one image with **one virtualenv per distinct dependency set** (built with `uv` at image build time). **The builder owns venv assignment**; steps and hooks cannot select a venv. `runtime` (and `spec`) are installed into **every venv** at the version pinned to the base image — not via system-site-packages, so each venv stays hermetic.
- **Dispatch**: the supervisor starts **one long-lived worker per venv** (a `venv_n/bin/python` process running a stdin/stdout JSON-RPC loop with all step modules pre-imported). A step run is a message to its venv's worker, so switching venvs between steps costs a local IPC round trip, not an interpreter start. Inputs and outputs travel as small JSON envelopes; anything large goes via the workspace. **Local mode uses the same scheme**, with `uv` venvs under `.wynd/`; the only difference between `local` and `image` is where the venvs live.
- **Versioned base images.** All process images derive from a `wynd-base` family built and published by this project: `wynd-base:<ver>-slim` (default) and `wynd-base:<ver>-alpine` (opt-in, for when every dependency is known to have musl wheels). Each contains Python 3.12+, `uv`, the vendored `runtime` package at the matching version, and the worker entrypoint. A process image is always `FROM wynd-base:<ver>-<variant>`, with the version pinned in `process.lock.yaml`. **`runtime` version and base version are the same number.**
- Process builds add only what declared fragments (step or provider) require on top of the base: venvs, step wheels, `system:` packages. Nothing undeclared is ever installed at build time.
- **Env manifest.** The compiler records each step's required env vars in `step.lock.yaml`; `wynd build` assembles `process.env.yaml` from those plus provider and storage vars: every env var the process needs to run (MCP server URLs and auth, API tokens, secrets, config, `WYND_HOME`, and the storage backend selectors from 7.1), with a description and which steps/tools use each. Nothing is baked in; the manifest is the contract for running the image anywhere. `wynd env check` verifies the current environment satisfies it before a run.
- **Workspace per run**, keyed by `run.id` and cleaned up on completion, locally and in image mode — except when the run ends in the process error handler, which leaves it for inspection (3.5). Steps pass file paths or small JSON envelopes; nothing large goes through the orchestrator. Per-run workspaces are also what prevents state leaking between runs in a warm container.
- **Warm pools**: for a served process, keep a container alive and dispatch runs into it. First run may be slow; second run should not touch Docker.
- **Secrets**: env var references (3.8) sourced from a `.env` file locally or Kubernetes Secrets in a cluster. `wynd env check` is the gate. Nothing is ever baked into an image.

### 4.1 Deployment (Kubernetes readiness)

Already implied by the decisions above; stated explicitly so nothing drifts away from it:

- A built process image runs unchanged as a long-lived pod with `wynd serve` as the entrypoint. One image per process = one Kubernetes Deployment per process.
- The supervisor's run API is a **contract**, not an internal detail: submit run, stream trace events, fetch outputs, health/readiness. Its schema is specified in M2.
- The env manifest maps directly to Secrets/ConfigMaps; `wynd env check` runs as a startup probe or init container.
- Storage backends (7.1) are how a cluster plugs in its own database and object store.
- Out of scope: a Kubernetes-level scheduler or operator, CRDs, and `ProcessStep` spawning child pods. Same-container nesting stands. (The controller's own trigger scheduler is in scope for M4.)

## 5. Monorepo layout

```
repo/
  packages/
    spec/       # pydantic models for steps, processes, edges, examples, env fragments. No Docker, no LLM.
    runtime/    # Step base class, derivations, middleware chain, providers, tool library, cassette record/replay, storage interfaces, executor, supervisor, worker entrypoint. Everything that runs inside an image; vendored into wynd-base.
    process/    # YAML loader (via spec), graph validation, JobRunner interface, image builder. Never inside an image.
    compiler/   # LLM turns proto-steps + process into concrete steps; runs example tests; clarification loop.
    controller/ # API surface, JobRunner implementations, trigger scheduler, process/release registries. A library.
    cli/        # the `wynd` commands (10). Imports the controller and runs it in-process.
    web/        # Graph editor + compile chat. Pure view over the controller API served by `wynd serve-api`. LAST.
  examples/     # a sample workspace (see 5.1) used as fixtures and for dogfooding
  docs/
```

Dependency direction: `spec ← runtime ← process ← compiler ← controller ← {cli, web}`. `spec` is the shared leaf; `runtime` is the vendored package. The controller is a **library**: the CLI imports it and runs it in-process, and `wynd serve-api` runs the same code as a service for the web UI. Neither client imports Docker or the executor directly. This is a deliberate move from "local-first tool" to **small control plane**: the same controller runs on a laptop in-process and in a cluster as a service.

### 5.1 Workspace repo layout (the user's repo)

```
workspace/
  wynd.yaml                 # workspace marker and config (committed)
  processes/              # default process root
    <name>/
      process.yaml
      proto/              # proto-step YAML
      steps/              # compiled source packages, committed
  shared/
    steps/                # a step root; nothing special about the name
  .wynd/                    # ignored: venvs, build artefacts, registries, job state
```

- One repo per workspace, not per process.
- **Build outputs are never in the tracked tree.** They live in an artefact store: `.wynd/build/<process>/<commit>/` locally, object storage in a cluster. A `processes/<name>/build/` directory, if wanted for discoverability, is an ignored mirror, not the source of truth.
- Folder name equals process id, enforced by the validator.

**Configurable roots.** `wynd.yaml` declares process roots and named step roots; both may be flat or hierarchical:

```yaml
process_roots:
  - processes
step_roots:
  shared: shared/steps
  finance: teams/finance/steps
  vendor: vendor/steps
```

- Discovery is by marker, not depth: a process is any directory under a process root containing `process.yaml`; a step package is any directory under a step root containing `pyproject.toml` + `step.lock.yaml` (or a proto-step YAML before compilation).
- Identity is the relative path: `processes/finance/invoices/` has id `finance/invoices`; `teams/finance/steps/extract/invoice/` is `finance:extract/invoice`. Display names come from YAML, ids from paths; registries and job records key on ids.
- Leaves don't nest: a `process.yaml` below another `process.yaml`, or a step package inside another, is a validator error. Layout only; it does not restrict what a process may reference.
- Processes live only under process roots; steps only under step roots.
- Roots are committed workspace config, so every job sees the same map. The root schema has a `url:` field reserved for git- or registry-backed roots later; v1 resolves local paths only.

**`use:` forms.** Exactly three; anything else, including relative traversal, is a validator error:

- `./steps/<name>` — process-local step
- `<alias>:<path>` — step from a configured step root (`shared:extract`, `finance:extract/invoice`)
- `process:<id>` — another process as a `ProcessStep` (`process:finance/invoices`), resolved through process roots; child inputs/outputs are the contract, env fragments merge into the parent image, error exit propagates, all per 3.2

Relative traversal (`../`) across roots or processes is not permitted.

A parent's compiled hash includes the hashes of every child process's compiled steps, so editing a child returns the parent to design phase; same commit, same repo, so no version pinning is needed. Reference cycles are a load-time validator error.

Tooling: Python 3.12+, `uv` workspace, `pydantic` v2, `typer` for CLI, `pytest`. Keep third-party deps in `runtime` minimal since it is vendored into every image.

## 6. Spec schemas (YAML)

### 6.1 Proto-step (what a user writes)

```yaml
kind: proto_step
name: extract_invoice_fields
instruction: |
  Given the text of a supplier invoice, extract the invoice number, total, currency and due date.
inputs:
  invoice_text: string
outputs:                          # nested by exit; a flat mapping here means a done-only step
  done:
    invoice_number: string
    total: number
    currency: string
    due_date: date
  not_an_invoice: {}
exits: [done, not_an_invoice]     # one Output model per exit, discriminated on `exit`
# shell steps only: map exit codes to named exits
# exit_codes: { 0: done, 1: not_found, "*": error }
examples:
  - inputs: { invoice_text: "..." }
    outputs: { invoice_number: "INV-1042", total: 1200.50, currency: GBP, due_date: 2026-10-01 }
    exit: done
  - inputs: { invoice_text: "Dear customer, your order has shipped" }
    exit: not_an_invoice
env:
  deps: []          # steps declare minimum requirements only; base image is chosen per process
  # requires: glibc   # optional; forces the process onto debian-slim
```

That is the whole proto-step. No hints or tuning knobs in v1; the compiler decides kind and model tier and records its choice in the lockfile, where a user can override it after the fact.

Schemas may be written by the user or **inferred from examples** by the compiler and shown back in plain language. Examples exist to tell the compiler what the step must do and to become its tests; they never ship inside the step.

### 6.2 Process

```yaml
kind: process
name: process_supplier_invoice
goal: Turn a supplier invoice PDF into a validated record in the records store.   # optional; used for compiler context and search display
latency: fast                # optional; validator warns on slow constructs (e.g. AgentProvider steps)
provider: anthropic          # process-level default; steps may override in their lockfile
env:
  base: debian-slim-python   # process-level; alpine-python is the only other option (opt-in)
entry: read                  # entry step; its Input is bound from process.inputs by field name
inputs:
  pdf_path: path             # read.Input must declare pdf_path
outputs:                     # nested by exit, as for proto-steps
  done:
    record: object
  not_an_invoice: {}
  needs_review: {}
examples:
  - inputs: { pdf_path: examples/inv1.pdf }
    outputs: { record: { ... } }
    exit: done
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
  # Branches evaluated top to bottom; first true `when` wins, first bare step is the else.
  - from: validate.done
    to:
      - step: save
        when: steps.validate.outputs.valid
        with:
          record: steps.validate.outputs.record
          dest: if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR
                else env.RECORDS_DIR
      - step: fix
        when: steps.validate.outputs.fixable and steps.fix.runs < 3
        with:
          fields: steps.validate.outputs.fields    # the fields just validated: extract's or fix's
          errors: steps.validate.outputs.errors
      - step: escalate      # else: not fixable, or already fixed 3 times
        with: { fields: steps.validate.outputs.fields, errors: steps.validate.outputs.errors }
  - from: fix.done
    to: validate
    with: { fields: steps.fix.outputs }
  - from: save.done
    to: $exit.done
    with: { record: steps.save.outputs.record }
  - from: escalate.done
    to: $exit.needs_review
```

`$exit.<name>` terminates the process with that exit, which must be declared under `outputs`. Process outputs are nested by exit, like step outputs, and are bound by the terminating edge's `with`. With the `to: <step>` shorthand, `with:` sits on the edge itself. Routing is decided by the `to:` list: one edge per step exit, and the branches under `to:` form an if / elif / else chain evaluated top to bottom, each carrying its own `with:` and `limits:`. `to: save` is shorthand for a single unconditioned branch. Step exits (`Output.exit`) remain for outcomes that are categorically different; ordinary decisions are made from output fields via `when:`. A fully conditioned `to:` with no matching branch is an error (3.5).

### 6.3 Compiled step (what the compiler emits)

A compiled step is a **source package** committed at `steps/<name>/`: `pyproject.toml`, the step module (subclassing the appropriate derivation), generated `test_<step>.py` (one test per example, plus compiler-proposed and user-confirmed edge cases), recorded cassettes, and `step.lock.yaml` recording kind chosen, provider + tier + thinking effort (if agentic), declared effects, tools and MCP tool snapshots, env vars required, dependency lockfile, and proto-step hash. Test results are **not** in the lockfile (they would dirty the tree on every `wynd test`); they are recorded in the `RunRegistry` keyed by commit hash + step hash. This is the **review surface**: it is committed, diffed and hand-edited like any other code.

The **wheel** is a build output (6.4), containing the step module, its pinned dependency set and metadata (proto-step hash, kind, tool snapshot). Model tier is **not** in the wheel — it lives only in `step.lock.yaml` — so wheels stay reusable across processes at different tiers.

### 6.4 Built process (the build output)

All build outputs go under the git-ignored `.wynd/build/<process>/<commit>/` (object storage in a cluster); nothing `wynd build` writes touches the tracked tree, so a build never dirties it. Releases are images, not committed files.

- `Dockerfile` — one, for the whole process
- `process.env.yaml` — the env manifest (4)
- `process.lock.yaml` — resolved step lockfiles, venv plan, base image chosen and why, `wynd-base` version, **git commit hash** of the compile tree
- `dist/` — one wheel per step

### 6.5 Three phases: Design → Compile → Build

Deploy is what you do with a build, not a phase the tool owns.

- **Design**: proto-steps + process YAML. Gate: `wynd validate`. No LLM.
- **Compile**: `wynd compile` submits a compile job against the process's HEAD. **Its output is always a commit** on a branch `wynd/compile/<process>/<job-id>`, pushed by the job — never a working-tree write. When the job finishes, the CLI or web integrates it: fast-forward if nothing moved; **rebase automatically if every intervening commit is outside the process's reference closure** (the common case, since design auto-commits from other processes land continuously); otherwise leave the branch for PR review. Gate: `wynd test`. Hand edits are expected; the compiler treats committed source as the baseline and only re-touches a step when its proto-step hash changes. `wynd test --live` is likewise a job whose output (re-recorded cassettes) is a commit on a branch.
- **Build**: `wynd build` produces wheels, image, env manifest and lockfile under `.wynd/build/<process>/<commit>/`, reproducibly from a compile commit without the compiler. It refuses to run on a dirty git tree and records the commit hash in `process.lock.yaml`. "HEAD of the process" throughout means the last commit touching the process's reference closure (11), not just its directory. **Tests must pass on the commit being built**: `wynd build` first runs `wynd test` in replay mode, or accepts a passing result already recorded in the `RunRegistry` against the same commit hash. Gate: `wynd env check` before run.
- **Cassettes** are committed with the source package but can grow; the repo tracks `cassettes/` with git-lfs, and the compile job warns when a single step's cassettes exceed a configurable size (default 5 MB).

### 6.6 Jobs versus runs

Compile, live test and build are **jobs**; runs are **requests** to a serving container. Jobs are batch, cold and privileged (BuildKit, registry push, model API keys, git checkout); runs are latency-sensitive and go to a warm container over the run API (4.1). Runs are never modelled as jobs.

- `JobRunner` **interface** lives in `process`: `submit(kind, ref, inputs) -> job_id`, `status(job_id)`, `logs(job_id)`, `artefacts(job_id)`. **Implementations** live in `controller`: in-process (M1), subprocess (M2), Kubernetes Job (separate package, post-M5). The interface exists from M1 so the CLI never grows a direct code path to Docker.
- **Jobs operate on a git checkout at a ref, never on a working tree.** A job can only ever build a commit. Locally, with no remote, the in-process and subprocess runners use a `git worktree` of the workspace repo under `.wynd/jobs/<job-id>/` and push their result branch back into the same repo.
- Build jobs use BuildKit or kaniko, never a Docker socket, and push to an image registry held in the user registry (`wynd registry add|list|remove`).
- CLI and web both submit jobs and poll; `wynd build` is `submit(build, commit)` plus a wait.
- **The compiler session is serialisable.** A compile job that hits a clarification question exits with status `awaiting_input`; session state lives in the job record; answering resubmits the job with the answer appended. The web chat is a view over that record.

## 7. Runtime package

```python
class Step(ABC):
    Input: type[BaseModel]
    Output: type[BaseModel] | UnionType   # one model per exit, each with `exit: Literal["<name>"]`

    def pre(self) -> None: ...
    def run(self, input: Input) -> Output: ...
    def post(self) -> None: ...
```

That is the entire base class. A plain (non-union) model is permitted as shorthand when a step has only the `done` exit; it must still carry `exit: Literal["done"] = "done"`. Workspace path, logger and trace access are available via a runtime-injected `self.runtime` handle rather than extra parameters, so `run` signatures stay plain.

```python
class AgenticStep(Step):
    # the class docstring is the instruction
    context: list[str] = []
    tools: list[Tool] = []
    mcp: list[McpServer] = []
    # model tier, thinking effort and retries come from step.lock.yaml, not the class

    def run(self, input: Input) -> Output:
        # build prompt from docstring + assembled context + input
        # call model with structured output = Output schema
        # validate; retry per policy; return
```

- Prompting, structured output, context assembly, retries and tool handling are `AgenticStep` concerns only. `DeterministicStep` and `ShellStep` have none of this.
- `run` must be side-effect free outside the workspace unless its `step.lock.yaml` declares `effects: [network, filesystem, ...]`.
- Tests are ordinary pytest files generated by the compiler; `runtime` provides a `run_step(step, input)` helper the generated tests use so they exercise the full middleware chain, not just `run`.
- **Agentic steps are tested by replay.** Every model call (request + response) is recorded into the trace as a cassette. Generated tests for agentic steps run in replay mode by default, so `wynd test` and the compiler's generate-test-revise loop are fast, deterministic and free. `wynd test --live` re-records against the real model.
- **Cassettes cannot go stale silently.** Each entry is keyed on a hash of the full request (model, system prompt, messages, tool schemas) with volatile fields normalised out (timestamps, `now()`, `run.id`). A cache miss in replay mode fails the test with an explicit "no recording for this request — re-record with `wynd test --live`" error rather than passing on an old response. The compile job always records live while generating or revising a step and includes fresh cassettes in its output commit. Process-level cassettes are more brittle than step-level ones because upstream summaries are part of the request; expect to re-record them more often.

### 7.1 Storage interfaces

Four things would otherwise live implicitly on local disk. From M1 they sit behind interfaces in `runtime`, with local implementations only; backend selection is by env var and appears in the env manifest. No code may assume a home directory.

- `WorkspaceStore` — per-run workspace. Default: local filesystem. Alternatives via env (e.g. object storage). Cassettes are **recorded** into the run workspace and **promoted** into `steps/<name>/cassettes/` by `wynd compile` and `wynd test --live`; replay reads from the source package only.
- `TraceSink` — trace events. Default: JSONL on disk. Alternatives via env (e.g. a database).
- `RunRegistry` — one registry for runs and jobs, distinguished by a `kind` field: ids, status, outputs/artefacts pointer, test results keyed by commit + step hash, serialised compile sessions.
- `Registry` — the user-level MCP and provider registries. Location is `WYND_HOME`, falling back to `~/.wynd` only when unset. In a cluster: a ConfigMap or database, selected by env.

## 8. Process package

- **Loader**: parse YAML via `spec`, resolve `use:` references, validate the graph (all declared exits routed or explicitly ignored — the implicit `error` exit is exempt; no unreachable steps; expressions reference real steps/fields; every branch on a cycle has a `max_traversals`, filled in by default if absent; **reference checks are exit-aware** — since output fields are per exit, `steps.x.outputs.f` in an edge is valid only if every path reaching that edge had `x` take an exit that declares `f`; the `entry` step's `Input` field names match `process.inputs`; `ProcessStep` children whose `env.base`/`provider`/`latency` differ from the parent produce a warning).
- **Executor** (lives in `runtime`, since image mode needs it in-container; `process` only invokes it): one executor, two execution modes, one trace format:
  - `local` — steps run from source, dispatched to venv workers backed by `uv` venvs under `.wynd/`. Used by development, `wynd test`, and the compiler's generate-test-revise loop.
  - `image` — steps run as installed wheels inside the process image, dispatched to venv workers.
  Same executor, same worker scheme, same edge evaluation, limits, retries and trace recording; the trace carries a `mode` field so local and image runs are directly comparable.
- **Trace**: every run writes a JSONL trace (step, inputs, outputs, exit, summary, timings, model/cost if agentic). This is the eval and debugging substrate; log everything from day one.
- **Builder**: merge step env fragments → generate Dockerfile `FROM wynd-base:<ver>-<variant>` → install `system:` packages → create one venv per dependency set and install step wheels into them → entrypoint is the supervisor (already in the base). Also `bake` to a single executable where the process is pure-Python (`pex`/`shiv`).
- **Supervisor** (in `runtime`, inside the image): serves the run API (4.1) — submit run, stream trace events, fetch outputs, health/readiness — dispatches step runs to the long-lived venv workers per the build plan, and uses the per-run workspace for handoff.

## 9. Compiler package

Input: a git ref of the workspace plus a process id. Output: a commit on `wynd/compile/<process>/<job-id>` containing the source packages per step (6.3), plus a compile report attached to the job record. The compiler never writes to a working tree and never produces wheels or images; that is `wynd build` (6.5).

**Per-step compile decision** (default rule; the only override in v1 is editing the lockfile after compilation):

1. If examples are consistent with a pure function of inputs → generate `DeterministicStep`, run examples as tests.
2. If any example requires judgement, world knowledge or external data → generate `AgenticStep` at the default tier (`cheap`, low thinking effort).
3. If deterministic code passes most but not all examples → emit **two steps**, a `DeterministicStep` and an `AgenticStep`, plus an edge from the deterministic step's `error` exit to the agentic one. No hybrid step kind; the base-class taxonomy stays as is. Report this clearly.
4. If the instruction is clearly a command → `ShellStep`.

**Tests from examples**: the compiler turns each proto-step example into a pytest case before writing the step, so it is writing code against a failing test suite it can run. Process-level examples become integration tests over the whole graph.

**Loop**: generate tests → generate step → run tests → on failure, revise up to N times → if still failing, raise a clarification question.

**Example proposal**: before finalising a step, the compiler proposes edge-case examples ("what should happen if `due_date` is missing?") and asks the user to confirm or correct. Confirmed examples are appended to the proto-step. This is the primary defence against overfitting to the user's few examples.

**Schema inference**: if a proto-step has examples but no schemas, infer them and present in plain language; the user edits examples, not schemas.

**Design as a conversation, not a one-shot call.** The compiler exposes a session object: `state`, `pending_questions`, `answer(question_id, text)`, `next()`. The CLI drives it non-interactively (fail on unanswered questions); the web chat is a view over the same session.

**Tool selection**: the compiler prefers `runtime.tools` builtins, then `@tool` methods it writes, then MCP servers from the user's registry, choosing the narrowest `allow` list that covers the step's examples. Every env var a chosen tool needs is recorded in the step's `step.lock.yaml`; `wynd build` assembles the manifest from there.

**Model tiering** (`cheap` / `standard` / `strong`) and provider are per step and recorded in the lockfile, with the process-level provider as default. Every trace records cost and attempts so a later `optimise` command can promote/demote tiers per step from real data. v1 just records; it does not auto-tune.

## 10. CLI

Commands take process **ids** (relative path under a process root), not file paths.

```
wynd init                        # scaffold a workspace (wynd.yaml, roots, .wynd/ ignore)
wynd new <id>                    # scaffold a process under a process root
wynd validate <id>               # graph + schema checks, no LLM
wynd compile <id>                # submits a compile job; output is a branch wynd/compile/<id>/<job>
wynd test <id> [--live]          # replay by default; --live is a job whose output is a branch
wynd run <id> --local | --image  # local: source via .wynd/ venv workers; image: built artefacts
wynd build <id>                  # submits a build job against HEAD of the process; refuses on a dirty tree
wynd env check <id>              # verify required env vars are set
wynd serve <image>               # warm container, run API endpoint
wynd serve-api                   # run the controller as a service (for the web UI)
wynd trace <run_id>              # pretty-print a trace
wynd mcp | provider | registry add|list|remove   # user-level registries
wynd base build|publish <ver>    # build the wynd-base family
```

## 11. Web package (last)

A pure view over the controller API. No Docker or executor imports.

- **Process selector.** The UI has a currently open process. "Open process" is a pointer sent with each chat message, not a property of the chat.
- **Chats are decoupled from processes.** A chat can list and read *any* process (design YAML, step source, traces, status) via tools. Write actions — edit design, start compile, start build — apply only to the open process, and the chat shows an "acting on: <process>" chip.
- **Graph editor** over the process YAML (nodes = step refs, edges with branch/transform editors, examples editor with plain-language schema view). The editor is a design-phase tool; code review of compiled steps happens in git.
- **No save button; design auto-commits.** Autosave to the working tree continuously; commit on a meaningful boundary (end of a chat turn that edited something, or blur after a manual edit). Never one commit per keystroke; auto-commits never span two processes. The design phase is therefore always at a clean tree, so compile and build can always run.
- **Compile** button submits a compile job and opens a chat bound to its serialised session: compile decisions per step and why, clarification questions, edge-case example proposals. The UI fast-forwards or auto-rebases per 6.5; only genuine conflicts within the process's closure become a PR.
- **Build** button is a separate action that submits a build job against the process's HEAD.
- **Status is derived per commit, never stored.** A process can be released at commit X and in design at HEAD simultaneously; show both. Per process, HEAD means the last commit touching **its reference closure**: its own directory plus every step-root path and child process it references, transitively. The same closure defines "this commit" for `wynd build`'s tests-passed check. Derived flags: *design* (proto-steps at HEAD with no matching compiled source), *compiled* (source matches proto hashes and tests pass on HEAD), *built* (a build artefact exists for HEAD in the registry), *released* (a `Release` record points at a built image).
- **Search** covers all processes together: name, goal, step instructions, plus the four derived flags as filters.
- **Release.** A `Release` record = built image ref + trigger (schedule, webhook, manual) + env binding. Named `Release` to avoid colliding with a Kubernetes Deployment. Only built processes can be released. Triggers live in the controller, which calls the run API; processes stay ignorant of how they are invoked.
- **Run panel**: enter inputs, watch the trace stream, inspect each step's inputs/outputs/summary.

## 12. Build order / milestones

1. **M1 — hand-written end to end.** `spec`, `runtime` (including the storage interfaces with local implementations), `process` loader + local executor using the venv-worker scheme + trace, and a minimal `controller` (API surface, in-process `JobRunner`, local registry) imported by the CLI. One real process in the sample workspace with hand-written steps runs locally. `wynd validate/test/run` work.
2. **M2 — image builder and controller.** `wynd-base` family (`wynd base build`), step wheels, env fragments, venv merging, Dockerfile generation, supervisor with venv workers and the run API schema, `wynd build/serve`, `wynd run --image`. `controller` gains the subprocess `JobRunner`, image registry entries in the user registry, and the serialisable compile session. The M1 process runs inside its image in seconds and its trace matches the local one.
3. **M3 — compiler.** Compile decision, generate-test-revise loop, example proposal, schema inference, session API. `wynd compile` produces the M1 process from proto-steps alone.
4. **M4 — web.** Process selector, decoupled chats with the "acting on" chip, graph editor, auto-commit, build chat over job sessions, derived status and search, `Release` records and triggers in the controller, run panel.
5. **M5 — optimise.** Per-step tier promotion/demotion from trace data; agentic edges.
6. **Post-M5, separate package:** Kubernetes `JobRunner` and trigger backends.

**Before M1: pick the dogfood process.** It determines what `runtime.tools` must contain first and which env fragments the builder has to handle. Target: a real, boring internal data process Peter already does by hand.

## 13. Decisions already made (don't relitigate)

- Steps declare exits (as an `Output.exit` field), not destinations. Routing lives on edges.
- Path decisions are the `to:` list: branches with `when:` conditions evaluated top to bottom, first bare step is the else, no match with no else is an error. `with:` and `limits:` live on the branch. Inline `if/then/else` remains for values. No `eval`.
- Error handling is built in: implicit `error` exits, a default process error handler, retry policy in the lockfile, optional `on_error` and `finally` steps. Authors wire error paths only when they want custom behaviour.
- The step base class is `Input`, `Output`, `pre`, `run`, `post` and nothing else. Prompting, structured output and context are `AgenticStep` concerns.
- Examples belong to proto-steps and become compiler-generated tests. Steps never carry examples or test logic.
- Few options. Step definitions are explicit code; knobs live in the lockfile, not the step.
- Process = image boundary. Steps contribute env fragments, not images.
- Python base class for steps; venv-per-dependency-set inside one image; one long-lived worker per venv, dispatch over local IPC. Builder owns venv assignment.
- `debian-slim-python` is the default base; Alpine is opt-in.
- Agentic step tests replay recorded model calls by default; `--live` re-records.
- Sequential execution only in v1; no fan-out or join.
- Steps are never executed as standalone executables inside a process; per-step spawn contradicts the worker-per-venv latency decision. Executables exist only as `ShellStep` commands and `wynd bake` whole-process outputs.
- Compiled steps are source packages in git; wheels are build outputs. A process image installs wheels into venvs. Wheels are the reuse unit across processes and carry no model tier.
- Three phases: Design → Compile → Build. `wynd build` refuses a dirty tree and records the commit hash.
- Hooks are code; env fragments are declared. Hooks are per-run; module-level state in steps is forbidden.
- `Output` is a discriminated union, one model per exit. Reference checks are exit-aware.
- Processes declare `entry:`; the entry step's `Input` binds from `process.inputs` by name.
- Executor, supervisor and worker live in `runtime`; the in-image set is exactly `spec + runtime`.
- Process HEAD and status are computed over the reference closure, not the directory alone.
- Summaries are derived deterministically; never a model call.
- Agentic retries continue the conversation; no loop restart after a tool call.
- Workspace is per run.
- Storage (workspace, traces, run registry, user registries) is behind interfaces from M1.
- Local mode uses the venv-worker scheme; one executor, not two.
- Providers contribute env fragments; no agent base-image variant.
- Compile, live test and build are jobs; runs are requests to a serving container.
- Jobs run against a git checkout at a ref, never a working tree.
- `runtime` is the vendored package; `spec` is the shared leaf. Dependency direction: `spec ← runtime ← process ← compiler ← controller ← {cli, web}`.
- The controller is a library run in-process by the CLI and as a service by `wynd serve-api`; `web` and `cli` are both its clients.
- Compile and live-test jobs emit commits on branches, never working-tree writes.
- Process status is derived per commit, never stored.
- Only built processes can be released; triggers live in the controller. The Wynd record is a `Release`, distinct from a Kubernetes Deployment.
- Design auto-commits; there is no save.
- One repo per workspace; build outputs never in the tracked tree.
- Roots are configured in `wynd.yaml`; discovery is by marker; identity is relative path; `use:` has exactly four forms.
- Everything is open source (Apache 2.0 for `spec`/`runtime`, AGPL 3.0 for the rest). No environment-specific code paths; differences are backends behind interfaces.
- All process images derive from a versioned `wynd-base`; `runtime` and base share a version.
- Two provider interfaces, not one: ModelProvider (single turn, `runtime` loops) and AgentProvider (harness loops).
- Provider is a per-step lockfile field with a process-level default.
- Tier-to-model mapping lives in the user registry, never in the lockfile.
- No WASM.
- Context is pull-based and structured.
- Deterministic first: anything that can be a function is a function.
- Traces are recorded from day one and are the basis for all later optimisation.
- `AgenticStep` is NOOA-shaped: docstring is the instruction, `run` with `...` is completed by the loop, `@tool` methods are the only model-callable methods. Pattern adopted in-house; the `nooa` package is not a dependency.
- Tools and MCP consumption are in v1. Exposing processes as MCP servers is not.
- One Dockerfile per process, on the single most capable base image needed. Never a mix of images.
- Every secret is an env var reference; the env manifest is the runtime contract. MCP servers are registered once per user and reused across processes.

## 14. Open questions

- Auto-rebase policy for compile branches (6.5): "rebase when intervening commits are outside the closure" is the starting rule; revisit if PRs still dominate.
- Applying `max_traversals` to every branch on a cycle is conservative; may over-warn on large graphs.

## 15. Licensing and deployability

Everything in this repo is open source, and the repo is set up so the whole system can be deployed by anyone from source alone.

- **Licences.** `spec` and `runtime` are Apache 2.0: they are the only packages inside users' images (`spec + runtime` is exactly the in-image set) and must carry no friction. `process`, `compiler`, `controller`, `cli` and `web` are AGPL 3.0. No source-available or BSL-style licences anywhere.
- **No privileged code paths.** Any behaviour that differs between environments (laptop, self-hosted cluster, anything else) must be expressed as a backend selected by env var (7.1) or a `JobRunner`/trigger implementation, never as a conditional branch. If Claude Code finds itself adding an `if <environment>:` check, stop and add an interface instead.
- **Cluster deployment is first-class.** A cluster install (controller service + Kubernetes runner + user-supplied storage and secrets) is documented and tested to the same standard as the laptop install. The Kubernetes `JobRunner` and trigger backends ship in this repo (post-M5).
- **Usage is fully observable.** Model usage (tokens per provider and tier, cost where known), compute time for jobs and serving containers, and storage are recorded in the trace and job registry, so any operator can account for resource consumption from the data the system already collects.
- **Telemetry** is opt-in and off by default.

## 16. Prior art and positioning

The core thesis — compile once with a strong model, execute repeatedly with deterministic code and cheap models — is shared with several 2026 papers and should be cited as prior art in the README, not presented as novel.

**Same thesis (papers, not products):**

- *Compiled AI: Deterministic Code Generation for LLM-Based Workflow Automation* (arXiv 2604.05150, April 2026; code at github.com/XY-Corp/CompiledAI). Confines LLM invocation to a one-time compilation step from a YAML specification, with runtime LLM calls only as bounded tool calls for narrow subtasks. Benchmark suite; no examples-as-tests, no exits/edges model, no build pipeline.
- *PlanCompiler* (arXiv 2604.13092) and *LLM-as-Code: Agentic Programming for Agent Harness* (arXiv 2606.15874, KDD 2026 AgenticSE workshop). Same argument from the harness side: an LLM in charge of control flow produces control-flow hallucination and unreliable completion; the fix is code-driven workflows with agents as functions over a graph.
- *llm-compiler* (Go, github.com/LiboWorks/llm-compiler). Compiles LLM workflows into explicit, inspectable, versionable execution graphs. No LLM-authored steps.
- *NOOA* (NVIDIA Labs, July 2026). The inside of one agentic step; Wynd adopts its pattern (3.8).

**Adjacent products (the graph without the compiler):**

- LangGraph, n8n, Gumloop, Lindy, Relevance: step graphs with LLM nodes; an LLM node stays an LLM node forever, nothing is compiled to code from examples.
- Temporal, Prefect, Inngest: durable execution and retries; no authoring or compilation story.

**What Wynd combines that none of the above do:**

1. Examples on the proto-step become the step's tests; the compiler writes against a failing suite (6.1, 9).
2. The per-step compile decision — deterministic, agentic, or two-step fallback — is a first-class, reviewable output (9).
3. Git-native compilation: source packages, cassette replay, dirty-tree refusal, commit-pinned builds. Generated code is reviewed and owned, not ephemeral (6.3–6.6).
4. One image per process with venv workers and a run API, so the compiled process is a deployable unit (4, 4.1).
5. Human-authored and machine-compiled steps in the same graph, editable by non-coders (11).

The differentiation is engineering, not thesis. Design and review effort should go to 1–5, and any feature that doesn't strengthen one of them should be questioned.

## 17. Working style

Plan first. Write a plan for each milestone, get it approved, then execute. Prefer small, well-tested increments over large speculative scaffolding. Keep `runtime` dependency-light. Every package ships with tests; the sample workspace is the integration test.
