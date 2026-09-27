# Wynd — Implementation Plan (build contract)

Source of truth for behaviour: `docs/SPEC.md` ("SPEC §x"). This plan is the source of truth for **how it is built**:
module layout, ownership, every cross-package contract, and the order of work. Where the eight area drafts disagreed,
this plan decides; the decision is stated once, here, and every other section refers to it.

- **Authority order:** SPEC.md (behaviour) → this plan (contracts, names, formats, ownership) → the area drafts
  (non-normative detail). The drafts live in the repo at **`docs/design/`** (`$DRAFTS` below: `01-spec.md`,
  `02-runtime-core.md`, `03-runtime-llm.md`, `04-process-build-jobs.md`, `05-compiler.md`, `06-controller-cli.md`,
  `07-web.md`, `08-m5-kube-examples-docs.md`, plus the partial files under `parts/`, `04parts/`, `06-parts/` they cite);
  the verified spikes are at `docs/design/spikes/` (`expr/`, `mcp_client_stdlib.py`, `fake_mcp_http.py`,
  `ns/pyproject.toml`). They were copied out of the session scratchpad when this plan was written and are committed
  with the W0 checkpoint (§0 rule 9), before wave 1; no unit reads the scratchpad. When this plan says
  "adopt `$DRAFTS/0N §x`", that section is normative **except** for any name, field, format or signature this plan
  defines differently. Anything a draft says that this plan does not adopt is not part of the build.
- **Conventions:** "unit" = a work unit in §14 (e.g. `RT-EXEC`). Code blocks are contracts: names, fields, defaults
  and signatures are exact. `…` inside a signature means "as the text says", never "anything".
- **Code style (user rules):** minimal, explicit, simple direct functions; `match`/early returns; no speculative
  abstractions; validate at system boundaries only; no `if <environment>:` branches — environment differences are
  backends selected by env var or entry point.

Contents: §0 ground rules · §1 verified facts · §2 layout · §3 cross-cutting contracts (3.1 identifiers, 3.2
diagnostics, 3.3 documents, 3.4 types/interfaces, 3.5 expressions/scope/routing, 3.6 step package + lock, 3.7 edges
lock, 3.8 records, 3.9 retries/error exits, 3.10 env, 3.11 run plan + process.lock, 3.12 worker protocol, 3.13 trace,
3.14 storage/registries, 3.15 providers, 3.16 cassettes, 3.17 run API, 3.18 jobs/integration, 3.19 closure/HEAD/hashing,
3.20 tests/test runner, 3.21 controller HTTP API, 3.22 CLI, 3.23 env vars, 3.24 on-disk state) · §4 spec · §5 runtime ·
§6 process · §7 compiler · §8 controller · §9 cli · §10 web · §11 kube · §12 sample workspace · §13 docs/infra/CI ·
§14 work breakdown · §15 interpretations · §16 spec coverage · §17 risks · Review log.

---

## 0. Ground rules for every work unit

1. **Ownership is by file.** Each file is owned by exactly one unit per wave (§14). A unit edits only its own files.
   If it needs a change in someone else's file, it asks the lead; it never edits it. Later-wave units may take over
   files explicitly listed as "transferred" in §14.
2. **Wave 0 owns all shared files and they are frozen afterwards:** root `pyproject.toml`, every package
   `pyproject.toml`, **every `__init__.py` listed as W0 in §4–§11** (the re-exporting package inits and the empty
   subpackage inits enumerated in §14 W0), `conftest.py` (root), `.gitignore`, `.gitattributes`, `LICENSE*`,
   `uv.lock`. A definition-bearing subpackage `__init__.py` (runtime `providers`, `storage`, `cassettes`, `tools`,
   `mcp`, `agentic`, `executor`, `supervisor`; process `validation`; controller `api`) belongs to the unit that owns
   its subpackage. Every Python module named in this plan exists after wave 0 as a **stub** (docstring, the public
   names listed in this plan, bodies `raise NotImplementedError("PLAN §x")`) so imports work from day one. **Pure-data
   and contract types are not stubs:** W0 writes their complete definitions, copied mechanically from this plan's code
   blocks (list in §14 W0); implementing units add behaviour and validators but never add, remove or rename fields.
   A unit replaces its stubs with implementations; it never renames or removes a public name.
3. **Same-wave dependencies** are avoided by sub-waves (§14: `2a → 2b`, `3a → 3b → 3c`, …). Where two units of one
   sub-wave still meet, the consumer codes against this plan's contract and the W0 data types and tests with a double;
   real code is used for every earlier (sub-)wave. The milestone integration unit wires and fixes.
4. **Tests:** pytest everywhere; default run fully offline and deterministic. `@pytest.mark.live` (skipped unless
   `WYND_LIVE=1`), `@pytest.mark.docker` (unless `WYND_DOCKER=1`), `@pytest.mark.kube` (unless `WYND_KUBE=1`).
   Tests never write outside `tmp_path` (except integration units operating on `examples/invoices`); the root
   conftest's autouse isolation fixture (§13) enforces the environment side of this. A unit's Accept lists its own
   test **files** explicitly; `-k` filters are not used (they match other units' tests).
5. **Git:** work units never `git commit`, `git checkout` or change branches in the shared tree. Integration units
   and the lead commit. Tests that need git use temporary repos under `tmp_path`.
6. **Python is always run through uv** (`uv run …`, uv-managed CPython 3.12; system python3 is 3.9). **Only W0 and
   the `*-INT` units run `uv sync`, `uv lock`, `uv add` or `uv pip install` against the root environment**; every
   other unit uses `uv run` on the environment as W0 left it. Never run `uv` with a step package directory as cwd
   (its `pyproject.toml` would be taken as a separate project).
7. **No new dependencies** beyond §1.3. The only addition this plan makes (claude-agent-sdk as a real dependency of
   `wynd-compiler`) is justified in §17.
8. **Every owned file stays importable after every edit.** Write whole-file replacements rather than partial edits
   that leave a module half-written, check with `uv run python -c "import <module>"`, and import lazily (inside the
   function) across unit boundaries where a cycle is possible (as middleware does with `agentic.loop`). Package
   `__init__` re-exports (W0) use a module-level `__getattr__` that imports the leaf on first access, so a broken leaf
   only breaks its own importers.
9. **Checkpoints:** the lead commits a checkpoint on `feat/wynd-v1` at the end of every (sub-)wave, after that
   wave's Accept commands pass (W0's checkpoint includes `docs/PLAN.md` and `docs/design/`).
10. **Shared test helpers** live in `packages/<pkg>/tests/support/<unit>_*.py` (lowercase unit prefix, e.g.
    `support/proc_git_harness.py`); `support/` directories have **no** `__init__.py` (one namespace package across
    packages, importable via the root pytest `pythonpath`, §13). Package `tests/conftest.py` files have one owner each
    (named in §4–§9); nobody else edits them.
11. **Web:** only WEB-SCAFFOLD runs `npm ci`/`npm install`; other web units never install, build or run whole-project
    checks (§10).

---

## 1. Verified facts (from the drafts' spikes and this machine)

### 1.1 Toolchain on this machine
uv 0.10.7, uv-managed CPython 3.12.12, git-lfs 3.8.0, Docker Desktop 29 with buildx v0.32 (default builder
`desktop-linux`, docker driver, BuildKit v0.27, containerd image store), node v26.9.0 + npm, `claude` 2.1.280,
claude-agent-sdk 0.2.157 on PyPI (bundled CLI 2.1.277), pypdf 6.19.0, kubectl client 1.34 with no cluster.

### 1.2 Namespace packaging (spike `docs/design/spikes/ns/pyproject.toml`, verified)
- Root `pyproject.toml` is a virtual workspace (`[tool.uv] package = false`); `members = ["packages/*"]` **requires**
  `exclude = ["packages/web"]` (uv errors on a member glob without `pyproject.toml`).
- The root `[project] dependencies` lists **all seven members** (`wynd-spec`, `wynd-runtime`, `wynd-process`,
  `wynd-compiler`, `wynd-controller`, `wynd-cli`, `wynd-kube`), each `{ workspace = true }` in `[tool.uv.sources]`,
  as the spike does. Verified: without this, a plain `uv sync` (which is exact) uninstalls members that
  `uv sync --all-packages` installed, for every agent sharing `.venv`.
- Each package: hatchling, src layout, `[tool.hatch.build.targets.wheel] packages = ["src/wynd"]` (**required**:
  auto-detection looks for `src/wynd_spec`). **There is no `src/wynd/__init__.py` in any package**; a stray one
  shadows every other member. `wynd.__path__` becomes a `_NamespacePath` across members.
- `uv sync` installs members editable; source edits are live without re-sync; built wheels contain only
  `wynd/<pkg>/…` plus `dist-info/licenses/LICENSE` and `License-Expression`; an exact pin
  `wynd-spec==0.1.0` between members resolves.
- pytest needs `--import-mode=importlib` (test basenames collide across packages). Importlib mode does not put
  test dirs on `sys.path` (verified: `from support.x import y` fails), hence the root `pythonpath` setting (§13).
- `uv run --isolated --package wynd-spec --with pytest pytest packages/spec` proves the dependency direction.

### 1.3 Approved dependencies (exhaustive)
| Dist | Runtime deps |
|---|---|
| wynd-spec | `pydantic>=2.7`, `pyyaml>=6`, `lark>=1.2` |
| wynd-runtime | `wynd-spec==0.1.0` (stdlib otherwise; `claude-agent-sdk` imported lazily, declared only in the claude-code provider fragment) |
| wynd-process | `wynd-spec`, `wynd-runtime`, `pyyaml>=6` |
| wynd-compiler | `wynd-spec`, `wynd-runtime`, `wynd-process`, `pyyaml>=6`, `claude-agent-sdk>=0.2.157,<0.3` (§17 R1) |
| wynd-controller | `wynd-spec`, `wynd-runtime`, `wynd-process`, `wynd-compiler`, `fastapi>=0.115`, `uvicorn>=0.30`, `httpx>=0.27`, `pyyaml>=6` |
| wynd-cli | `wynd-controller`, `typer>=0.12`, `pyyaml>=6` |
| wynd-kube | `wynd-controller`, `httpx>=0.27`, `pyyaml>=6`, `typer>=0.12` |
| root dev group | `pytest>=8`, `pypdf>=6,<7` (warms the uv cache for offline step venvs + sample tool tests), `claude-agent-sdk>=0.2.157,<0.3` (offline provider tests build real SDK message types) |
| web | runtime: `react ^19`, `react-dom ^19`, `@xyflow/react ^12`; dev: `vite ^8`, `@vitejs/plugin-react ^6`, `typescript`, `vitest`, `jsdom`, `@testing-library/react`, `@testing-library/dom`, `@types/react`, `@types/react-dom` (versions per `$DRAFTS/07 §3.2`) |
| sample steps | `pypdf>=6,<7` (read_pdf step only) |

### 1.4 claude-agent-sdk (0.2.157, live with `haiku`; `$DRAFTS/03 §1`)
- Structured output: `ClaudeAgentOptions(output_format={"type":"json_schema","schema": S})`; result in
  `ResultMessage.structured_output` (dict). **A top-level `anyOf`/`oneOf` is rejected** (API 400); wrapping the union
  under `{"type":"object","properties":{"output": <union>},"required":["output"],"additionalProperties":false}` works,
  including `$defs`/`$ref`, `format: date`, nested models. Both providers use this envelope (§3.15).
- In-process tools: `create_sdk_mcp_server("wynd", tools=[sdk_tool])` with `@tool(name, desc, <json-schema dict>)`
  and an async handler; the model sees `mcp__wynd__<name>`.
- Isolation that works: `tools=[]` (no built-ins; init tool list is only `StructuredOutput` + our MCP tools),
  `allowed_tools=[…]`, `permission_mode="dontAsk"`, `strict_mcp_config=True`, `setting_sources=[]`, `cwd=<workspace>`,
  `effort="low"|"medium"|"high"`, `max_turns`.
- Usage: `ResultMessage.usage` (per call), `model_usage` (per model: `inputTokens`, `outputTokens`,
  `cacheReadInputTokens`, `cacheCreationInputTokens`, `costUSD`, `costBasis`), `total_cost_usd`, `duration_ms`,
  `session_id`. **On resume, `model_usage`/`total_cost_usd` are cumulative** → per-call = delta. Startup ≈ 0.6 s,
  small haiku structured call ≈ 3–5 s wall.
- Validation-retry continuation works statelessly: capture the transcript through a duck-typed `session_store`
  (`append`/`load`), delete the local session (`delete_session(sid, directory=cwd)` + rmdir of the empty project dir
  under `~/.claude/projects/<project_key_for_directory(cwd)>`), then `resume=<sid>` with a store preloaded with the
  entries; same session id, no tool re-call, nothing left in `~/.claude`.
- **Do not set `CLAUDE_CONFIG_DIR` per run**: it breaks local subscription auth (macOS keychain).
- Errors: missing CLI → `CLINotFoundError`; bad token → `ResultError(api_error_status=401)`; logged out →
  `result="Not logged in · Please run /login"`; 400 → `ResultError("API Error: 400 …")`; others `ProcessError`,
  `CLIConnectionError`, `CLIJSONDecodeError`.
- Wheels: manylinux x86_64/aarch64 (bundled glibc-linked `claude` binary, no Node, no libstdc++) and macOS;
  **no musllinux wheel** → the provider fragment declares `requires: glibc`.
- Auth needs no code: the CLI's own precedence `ANTHROPIC_API_KEY` > `CLAUDE_CODE_OAUTH_TOKEN` > keychain/`~/.claude`
  login. Nested invocation from inside a Claude Code session works (the SDK strips `CLAUDECODE`).
- Lead-verified CLI equivalent: `claude -p "…" --output-format json --model haiku --json-schema '<schema>' --tools ""`.

Working snippet (the shape `ClaudeCodeProvider` uses; `$DRAFTS/03 §7` is adopted verbatim for the provider body):
```python
opts = ClaudeAgentOptions(
    model=req.model_id, system_prompt=system, tools=[],
    mcp_servers={"wynd": create_sdk_mcp_server("wynd", tools=bridged_tools)} if bridged_tools else {},
    allowed_tools=[f"mcp__wynd__{h.name}" for h in req.tools], strict_mcp_config=True, setting_sources=[],
    permission_mode="dontAsk", output_format={"type": "json_schema", "schema": wrap_output_schema(schema)},
    cwd=str(req.workspace), max_turns=req.max_turns, effort=EFFORT[req.thinking],
    resume=prev_session_id, session_store=store,
    env={"CLAUDE_AGENT_SDK_CLIENT_APP": f"wynd/{__version__}", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"})
async for m in query(prompt=user_or_continuation_text, options=opts): ...
```

### 1.5 Other verified facts
- Lark LALR(1) grammar in `$DRAFTS/01 §7.1` has no conflicts (Lark 1.3.1); 126 spike cases pass
  (`docs/design/spikes/expr/`). Keywords are usable as member names after `.` (contextual lexer).
- PyYAML rejects `{x: date?}` / `{x: list[string]}` in flow style (quote them); YAML 1.2 booleans loader verified.
- A stdlib-only PDF writer round-trips exactly through `pypdf.PdfReader(strict=True)` text extraction, blank lines
  excepted (`$DRAFTS/08 §6.9`, adopted verbatim).
- The stdlib MCP client (~130 lines) interoperates with the official `mcp` 2.x server over streamable HTTP (JSON and
  SSE bodies) and stdio, protocol `2025-11-25` (`docs/design/spikes/mcp_client_stdlib.py`).
- `docker buildx build` with the docker driver resolves `FROM wynd-base:<ver>-<variant>` from the **local** image
  store with no registry.

---

## 2. Repository layout (final state)

```
wynd/                                   (git repo; branch feat/wynd-v1)
  pyproject.toml  uv.lock  .python-version  conftest.py  .gitignore  .gitattributes
  LICENSE  LICENSES/{Apache-2.0.txt,AGPL-3.0-or-later.txt}  README.md
  docs/  SPEC.md  PLAN.md  architecture.md  run-api.md  providers.md  testing.md
         agentic-edges.md  optimise.md  cluster-install.md  design/ (drafts + spikes, non-normative)
  packages/
    spec/        wynd-spec        Apache-2.0          src/wynd/spec/...        tests/
    runtime/     wynd-runtime     Apache-2.0          src/wynd/runtime/...     tests/
    process/     wynd-process     AGPL-3.0-or-later   src/wynd/process/...     tests/
    compiler/    wynd-compiler    AGPL-3.0-or-later   src/wynd/compiler/...    tests/
    controller/  wynd-controller  AGPL-3.0-or-later   src/wynd/controller/...  tests/
    cli/         wynd-cli         AGPL-3.0-or-later   src/wynd/cli/...         tests/
    kube/        wynd-kube        AGPL-3.0-or-later   src/wynd/kube/...        tests/
    web/         (npm, not a uv member) AGPL-3.0-or-later  src/...
  examples/invoices/                    sample workspace (AGPL-3.0-or-later), SPEC §5.1 layout
  deploy/  wynd.Dockerfile  wynd.Dockerfile.dockerignore  ci/kind-with-registry.sh
  tests/   test_repo_hygiene.py  test_skeleton.py
  .github/workflows/ci.yml
```

Dependency direction (enforced by `uv run --isolated --package …` checks and AST import tests):
`spec ← runtime ← process ← compiler ← controller ← {cli, kube}`; `web` talks HTTP only. The in-image set is exactly
`spec + runtime`.

---

## 3. Cross-cutting contracts (each defined exactly once)

### 3.1 Identifiers
| Thing | Format | Defined in |
|---|---|---|
| Process id | path relative to its process root; segments `SEG = [A-Za-z0-9_][A-Za-z0-9_-]*` joined by `/`. The last segment must not be one of `RESERVED_PROCESS_SEGMENTS = {design, compile, build, builds, interface, status, validate, test, test-live, bake, optimise, history, env, files}` (HTTP routing, §3.21). | `wynd.spec.workspace` |
| Process `name:` | equals the last segment of its id (validator E113) | |
| Step key (node name in `steps:`) | `^[a-z_][a-z0-9_]*$` | `wynd.spec.base.StepKey` |
| Step id | process-local: `<process-id>#<name>`; step-root: `<alias>:<path>` | `wynd.spec.workspace` |
| Step module name | `step_module_name(step_id) = f"{slug}_{sha256(step_id.encode()).hexdigest()[:10]}"`; `slug` = last name segment (after the last `#`, `:` or `/`), lowercased, `re.sub(r"\W", "_", …)`, `"s_"` prefix if it starts with a digit, `"step"` if empty. Import path of the step package is always `wynd_steps.<step_module_name>`. | `wynd.spec.workspace` |
| Step wheel dist | `wynd-step-` + step_module_name with `_`→`-` | `wynd.process.build` |
| Slug | `slug(text) -> str`: lowercase; `/` → `-`; every char outside `[a-z0-9_.-]` → `-`; runs of `-` collapsed; leading/trailing `-._` stripped; empty → `"x"`. `slug("process_supplier_invoice") == "process_supplier_invoice"`, `slug("finance/invoices") == "finance-invoices"`. Used for the image tag `wynd/<slug(pid)>:<C[:12]>`, container names `wynd-<slug(pid)>-<commit[:7]>` and the step pyproject name `<slug(pid or alias)>-<name>` — nowhere else. | `wynd.spec.workspace.slug` |
| Step path (trace) | node names joined by `.` (`validate`, `sub.read`) | runtime |
| Branch key | `f"{edge_from}[{name if name else index}]"` e.g. `validate.done[save]`, `validate.done[1]`. **Every branch reference string** — `step.start.via`, `ProcessError.edge` when branch-specific, `EnvVar.used_by`/`env_refs` edge refs, `edges.lock.yaml` keys, `RunPlan.edge_venvs`, `edge.check.branch_key`, diagnostics such as `I201` — is produced by `branch_key(edge_from, index, name)`; no other format exists. (The expression counter syntax `edges["validate.done"][1]` is a language form, not a reference string.) | `wynd.spec.lockfiles.branch_key` |
| Run/job/other ids | `new_id(prefix) = f"{prefix}_{utc:%Y%m%dT%H%M%S}{ms:03d}_{secrets.token_hex(3)}"`, prefixes `run job rel chat fire turn it`. `valid_id`: `^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$`. Ids sort by time. | `wynd.runtime.ids` |
| Result branches | `result_branch(kind, pid, job_id) = f"wynd/{ {'compile': 'compile', 'test_live': 'test-live', 'optimise': 'optimise'}[kind] }/{pid}/{job_id}"`; `ValueError` for `build`/`bake` (they never produce commits). Used by the harness, the kube runner and integration. | `wynd.process.git.result_branch` |

### 3.2 Diagnostics (one type for spec, process, controller, cli, web)
```python
# wynd/spec/errors.py
Severity = Literal["error", "warning", "info"]
Loc = tuple[str | int, ...]

@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str                      # stable; tests assert codes, not prose
    message: str
    file: str | None = None        # workspace-relative
    line: int | None = None        # 1-based
    column: int | None = None      # 1-based
    loc: Loc = ()                  # document path, e.g. ("edges", 3, "to", 0, "with", "dest")
    process: str | None = None     # process id the diagnostic belongs to
    span: tuple[int, int] | None = None   # 0-based [start, end) character offsets inside an expression
    snippet: str | None = None
    def format(self) -> str        # "{file}:{line}:{column}: {severity}[{code}] {format_loc(loc)}: {message}"
    def to_json(self) -> dict      # all fields; loc as list; span as list or null

class SpecError(ValueError):       # raised by loaders; str() = "\n".join(d.format())
    diagnostics: list[Diagnostic]
def format_loc(loc: Loc) -> str    # ("edges",3,"to",0,"with","dest") -> "edges[3].to[0].with.dest"
```
**Code namespaces** (disjoint; a `CODES` table in each owning module; a test asserts every emitted code is listed):
- **spec** (`wynd.spec`): `E-YAML`, `E-YAML-DUP`, `E-YAML-ROOT`, `E-SCHEMA`, `E-KIND`, `E-TYPE`, `E-OUTPUTS`,
  `W-OUTPUTS-AMBIGUOUS`, `E-USE`, `E-TO`, `E-ROOTS`, `E-EXPR-SYNTAX`, `E-EXPR-FUNC`, `E-EXPR-ARITY`, `E-REF-NAME`,
  `E-REF-SHAPE`, `E-REF-STEP`, `E-REF-FIELD`, `E-REF-UNRUN`, `E-REF-EXIT`, `E-REF-INPUT`, `E-REF-EDGE`, `E-CONTEXT`,
  `E-EXAMPLE`, `W-PROTO-NO-EXAMPLES`, `W-PROTO-EXIT-UNTESTED`, `E-EXIT-CODES`, `E-ENTRY`, `E-STEP-UNKNOWN`,
  `E-EDGE-DUP`, `E-EXIT-TARGET`, `E-IGNORE-FORM`, `E-BRANCH-NAME`, `W-BRANCH-UNREACHABLE`, `W-LIMITS-EXIT`,
  `E-HANDLER-ROLE`, `E-LOCK`, `E-ENV-MISSING`, `E-ENV-ONE-OF`, `W-ENV-ONE-OF`, `E-ENV-CONFLICT`, `E-TYPE-OP`,
  `W-TYPE-NULL`, `E-TYPE-ASSIGN`, `W-TYPE-ASSIGN`.
- **process** loader/validator (numeric, adopted from `$DRAFTS/04 §4.12`, message templates verbatim): `E100–E116`,
  `E119–E125`, `E127`, `E128` (reserved final id segment), `W128`, `W129`, `I130`, `E204`, `E208`, `E209`, `E210`,
  `E211`, `E212`, `E215`, `E219`, `E223` (new, not in 04: `on_error step '{step}' requires input(s) {missing} that
  ProcessError does not provide`), `I201`, `W202`, `W203–W207`. (Dropped from 04 because spec owns them: E117/E118 →
  `E-YAML*`/`E-SCHEMA`; E202/E217 → `E-SCHEMA` (edge `from` format, `max_traversals` positive int);
  E201/E203/E205/E206/E207/E213/E214/E218/E220–E222/W201 → spec document checks; E216/E301–E307 →
  `E-EXPR-*`/`E-REF-*`; E401–E406/W209 → `E-TYPE-*`/`W-TYPE-*`; E126 → not needed, §3.6.) **Template override:**
  `W128` = `step '{id}': pyproject dependency '{name}' is missing from step.lock.yaml fragment.deps` (04's says
  `env.deps`).
- **process M5 agentic edges** (`$DRAFTS/08 §4.3`, verbatim): `E-CHECK-NOT-AGENTIC`, `E-AGENTIC-NO-CHECK`,
  `E-CHECK-EMPTY`, `E-CHECK-CONTEXT`, `W-AGENTIC-NO-ELSE`, `W-AGENTIC-EDGE-FAST`, `W-EDGE-LOCK-MISSING`,
  `W-EDGE-LOCK-STALE`, `W-EDGE-LOCK-ORPHAN`, `W-EDGE-PROVIDER-UNKNOWN`.
- **process M5 latency** (`$DRAFTS/08 §3.5`): `W-LATENCY-CALL`, `W-LATENCY-RUN`, `W-LATENCY-DISPATCH`.
- **runtime step lint** (`wynd.runtime.lint`, reported by the validator per step module file): `L001`–`L005`, all
  errors (§5.1 lint row). An `L` error fails `wynd validate` like any other error.
- Controller converts a `Diagnostic` to the web `Issue` DTO (§3.21) 1:1 (`loc`→list, `span`→list|null).

### 3.3 Authored document formats

All authored documents are loaded with `wynd.spec.yamlio` (YAML 1.2 core booleans: `yes/no/on/off` stay strings;
duplicate keys are `E-YAML-DUP`; line/column marks for every node). Models are `SpecModel`
(`extra="forbid"`, `populate_by_name=True`). YAML keys that are Python keywords use aliases (`from`, `with`, `finally`).
**Every YAML string under `when:`, `with:`, `limits:`, `finally[].with` is an expression** (literal text needs inner
quotes: `label: '"high"'`); non-string scalars are literals; mappings/lists recurse.

**`wynd.yaml`** (`WorkspaceConfig`):
```yaml
process_roots: [processes]            # default [processes]; relative POSIX, no '..', not under .wynd
step_roots: {shared: shared/steps, vendor: {path: vendor/steps}}   # alias ^[a-z][a-z0-9_-]*$, != "process"; url: reserved (E-ROOTS)
cassette_warn_mb: 5                   # compile job warns above this per step (SPEC §6.5)
```
Roots are pairwise disjoint (no root equals or contains another). Layout constants (spec `workspace.py`):
`WORKSPACE_FILE="wynd.yaml"`, `PROCESS_FILE="process.yaml"`, `PROTO_DIR="proto"`, `STEPS_DIR="steps"`,
`STEP_PROTO_FILE="proto.yaml"` (proto inside a step-root package dir), `STEP_LOCK_FILE="step.lock.yaml"`,
`EDGES_LOCK_FILE="edges.lock.yaml"`, `PROCESS_LOCK_FILE="process.lock.yaml"`, `ENV_MANIFEST_FILE="process.env.yaml"`,
`STATE_DIR=".wynd"`, `CASSETTES_DIR="cassettes"`. Process-local protos: `processes/<id>/proto/<name>.yaml`; their
packages `processes/<id>/steps/<name>/`.

**`use:` forms** — exactly three (SPEC §5.1; §13's "four" is a typo, §15 item 1):
`./steps/<name>` · `<alias>:<path>` · `process:<id>`. Anything else (incl. `../`) is `E-USE` with the three-forms
message (+ one hint per `$DRAFTS/04 §3.5` table). `parse_use(text) -> UseRef(form: "local"|"root"|"process",
target, alias)`.

**Proto-step** (`ProtoStep`, SPEC §6.1): `kind: proto_step`, `name`, `instruction` (non-empty), `inputs`
(field→type), `outputs`, `exits`, `exit_codes` (shell only; keys `0..255` or `"*"`, digit strings coerced), `examples`,
`env: EnvFragment`. No other keys. **`outputs`/`exits` normalisation** adopts `$DRAFTS/01 §5.3` table verbatim
(nested by exit only if `exits:` is declared, or there is a `done` key and every value is a mapping; else flat =
done-only; `error` can never be declared). After normalisation the model holds `exits: list[str]` and
`outputs: dict[exit, dict[field, TypeNode]]` with one entry per exit. `to_authoring()` writes flat outputs and no
`exits` for done-only docs.

**Example** (shared by protos and processes):
```python
class Example(SpecModel):
    inputs: dict[str, Any] = {}
    outputs: dict[str, Any] = {}      # fields of `exit`'s model, without the exit key; subset allowed (§3.20)
    exit: str = "done"                # a declared exit, or "error" (tests that the step fails loudly)
    description: str | None = None    # set by the compiler to the confirmed edge-case question
    env: dict[str, str] = {}          # process examples only: env for that run (values may use {tmp})
```
Example value conventions (implemented by the test runner and the compiler's test generator): a relative value of a
`path`-typed field resolves against the **process directory** (step-root protos: the package dir); the literal
prefix `{tmp}` in any string is replaced by a fresh per-example temporary directory.

**Process** (`ProcessDoc`, SPEC §6.2):
```python
class ProcessEnv(SpecModel):
    base: Literal["debian-slim-python", "alpine-python"] = "debian-slim-python"
    vars: dict[EnvName, str] = {}                 # env vars used by expressions: name -> description (manifest)
class RetryOverride(SpecModel): run: int|None = None; validation: int|None = None; tool: int|None = None   # each >= 0
class Limits(SpecModel):
    max_traversals: int | str | None = None       # int >= 1 or expression; filled 10 on cycle branches (§6 validator)
    timeout: float | str | None = None            # seconds (> 0) for the target step's run, or expression
    retries: RetryOverride | None = None          # field-wise override of the target's lockfile RetryPolicy
class Branch(SpecModel):
    step: str                                     # step key | "$exit.<exit>" | "$ignore"
    when: str | bool | None = None                # expression (never prose)
    with_: dict[FieldName, WithValue] = Field(default_factory=dict, alias="with")
    limits: Limits | None = None
    name: BranchName | None = None                # ^[A-Za-z_][A-Za-z0-9_]*$, unique within the edge
    check: str | None = None                      # M5 agentic edges only: natural-language condition
    context: list[str] | None = None              # M5 agentic edges only: pull context for the check
class Edge(SpecModel):
    from_: str = Field(alias="from")              # "<step>.<exit>" (^[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*$)
    to: list[Branch]                              # normalised: shorthand `to: x` (+ edge-level with/limits/check/context) -> one branch
    kind: Literal["deterministic", "agentic"] = "deterministic"
class FinallyStep(SpecModel):
    step: StepKey
    with_: dict[FieldName, WithValue] = Field(default_factory=dict, alias="with")   # "name" string items accepted
class ProcessDoc(SpecModel):
    kind: Literal["process"]; name: Name; goal: str | None = None
    latency: Literal["fast", "normal"] | None = None
    provider: str | None = None                   # process default; None -> DEFAULT_PROVIDER ("claude-code")
    env: ProcessEnv = ProcessEnv(); entry: StepKey
    inputs: dict[FieldName, TypeSpec] = {}; outputs: ...; exits: ...     # normalised as for protos
    examples: list[Example] = []
    steps: dict[StepKey, StepRef]                 # StepRef(use: str) validated with parse_use; non-empty
    edges: list[Edge] = []
    on_error: StepKey | None = None
    finally_: list[FinallyStep] = Field(default_factory=list, alias="finally")
    def interface(self) -> Interface               # = interface_from_fields(self.inputs, self.outputs); the ONLY
                                                   # process-interface builder (runtime process_interface wraps it)
def is_else(b: Branch) -> bool: return b.when is None and b.check is None
```
- `to:` normalisation (`E-TO`): string → one branch carrying edge-level `with`/`limits`/`check`/`context`; list items
  that are strings become `{step: s}`; edge-level `with`/`limits` alongside a list `to:` is an error.
- **`$ignore`** is the explicit-ignore target (SPEC §8 "routed or explicitly ignored"): allowed only as the single
  unconditioned branch of an edge (`E-IGNORE-FORM`); at run time taking it routes to the process error handler with
  cause `ignored_exit`.
- `$exit.error` is **not** a routable target (`E-EXIT-TARGET`): the `error` process exit is produced only by the
  error handler.
- There is **no `ui` field**: the web editor keeps node positions in the browser (§15 item 27).
- Branch evaluation order and else rule: first branch where `is_else(b)` is the else; branches after it are ignored
  (`W-BRANCH-UNREACHABLE`) and dropped from the normalised definition.

### 3.4 Type language and interface JSON Schema
Adopt `$DRAFTS/01 §5.1–§5.2` verbatim: scalars `string number integer boolean date datetime path object`,
`list[T]`, one-item YAML sequence `[T]` (list of records), nested mapping = closed object, `T?` optional (on any type string; nested
mappings and the `[T]` form cannot carry `?` — use `object?`/`list[...]?`). Mapping to JSON Schema: `date` →
`{"type":"string","format":"date"}`, `datetime` → `format: date-time`, **`path` → `{"type":"string","format":"path"}`**
(this is what pydantic emits for `Path`; the web reads `format === "path"`), `object` → open object, mapping → closed
object with `required`, `T?` → `anyOf[S, null]` and not required. `build_models(name, inputs, outputs) -> ModelSet`
builds pydantic models (lax mode; extra="forbid").

```python
class Interface(SpecModel):                   # wynd.spec.interface — the step/process contract snapshot
    input: dict[str, Any]                     # JSON Schema of Input
    outputs: dict[ExitName, dict[str, Any]]   # exit -> JSON Schema of that exit's model, incl. "exit" const
    @property
    def exits(self) -> list[str]
    def with_error(self) -> dict[str, dict]   # outputs + {"error": STEP_ERROR_SCHEMA}
def split_output(output: Any) -> dict[str, type[BaseModel]]      # the ONE implementation of the Output rules below
def output_adapter(output: Any) -> TypeAdapter                   # discriminated on "exit"
def interface_from_models(input_model, output) -> Interface      # model_json_schema(mode="validation")
def interface_from_fields(inputs, outputs) -> Interface
def interfaces_equivalent(a: Interface, b: Interface) -> list[str]   # [] when equal modulo titles/defaults/additionalProperties
```
**Output rules** (combined from `$DRAFTS/01 §6.10` and `$DRAFTS/02 §3.2`; `TypeError` naming the class on
violation): `Output` is a pydantic model or a `|`/`Union` of models; every model has an `exit` field annotated
`Literal["<name>"]` with a single value **and** default equal to that value; `<name>` matches `^[a-z_][a-z0-9_]*$`;
`error` is reserved (never declared); names are unique across the union; a plain (non-union) model is allowed only
for `done`. The runtime's `interface_of` calls `split_output`/`output_adapter` — there is no second implementation.

### 3.5 Expression language, scope and routing semantics
Grammar, precedence, AST, value domain, operators, builtins, syntax-error messages and positions: **adopt
`$DRAFTS/01 §7.1–§7.8` verbatim** (Lark LALR, tree-walking evaluator, no `eval`; `and`/`or` return booleans;
`not in` supported; unary minus; member/index access on `null` or a missing key yields `null`; ordering/arithmetic on
`null` raises `EvalError`). Static checks: `$DRAFTS/01 §7.9` (`references`, `check_expression`, `StepView`,
`TypeEnv`, `check_references`) with **guarded** references = inside `default()`'s first argument or any `coalesce()`
argument (a field that exists on no exit is still an error). Volatile-value tracking from 01 §7.11 is **dropped**
(cassettes normalise by regex, §3.16).

**Scope (the single evaluation context; spec-owned, runtime maintains it):**
```python
# wynd/spec/expr/scope.py
@dataclass
class StepState:
    runs: int = 0                          # completed runs in this process instance (any exit)
    exit: str | None = None                # latest completed run's exit; None if never ran
    outputs: dict[str, Any] | None = None  # latest run's fields, JSON-mode, WITHOUT "exit"; None if never ran
    summary: dict[str, Any] | None = None  # latest Summary as JSON
@dataclass
class EdgeState:
    taken: list[int]                       # per branch index
    names: dict[str, int]                  # branch name -> index
@dataclass
class Scope:
    steps: dict[str, StepState]            # every step key of this process instance
    edges: dict[str, EdgeState]            # every edge key ("validate.done") of this instance
    process_inputs: dict[str, Any]
    env: Mapping[str, str]
    run_id: str
    previous: str | None = None            # key of the most recently completed step
    clock: Callable[[], datetime] = utc_now
    def complete(self, step: str, exit: str, outputs: Mapping[str, Any], summary: Mapping[str, Any] | None) -> None
    def take(self, edge: str, branch: int) -> int
def new_scope(doc: ProcessDoc, *, inputs, env, run_id, clock=utc_now) -> Scope
```
References: `steps.<k>.{outputs[.f…],exit,runs}`, `edges["<k>"][i].taken`, `edges["<k>"].<name>.taken`,
`process.inputs.<f>`, `env.<NAME>`/`env["NAME"]` (null if unset), `run.id`, `previous.outputs[.f]`,
`previous.summary[.k]`. `evaluate(expr, scope)`, `evaluate_condition(when, scope)` (None → True, bool as-is, str →
truthy), `evaluate_value(value, scope)` (recurses; dates → ISO), `evaluate_with(with_, scope)`,
`evaluate_limit(value, scope)`.

**Routing semantics (runtime executor; one algorithm, SPEC §3.4/§3.5):** after step `s` completes with exit `e`:
1. `scope.complete(s, e, outputs, summary)`; `edge = edges[f"{s}.{e}"]`. No edge → error handler with cause
   `step_error` (e == "error") or `unrouted_exit`.
2. For `i, b` in branches (normalised, after-else dropped), all evaluated against the **same scope snapshot**:
   - `when` false → next branch (no model call).
   - `limits.max_traversals = N` and `scope.edges[key].taken[i] >= N` → error handler, cause `max_traversals`.
   - evaluate `with` (expression errors → cause `expression_error`).
   - `b.check` (M5) → `EdgeChecker.check(...)`; negative verdict → next branch; checker failure → cause `edge_check`.
   - take: `scope.take(key, i)`; emit `edge.taken`; target `$exit.<x>` → validate `with` against the process output
     model of `x` (failure → cause `invalid_process_outputs`) and end the instance; `$ignore` → cause `ignored_exit`;
     else run the target step with the bound inputs.
3. No branch taken → cause `no_branch_matched`.
- `max_traversals: N` allows N takes; the (N+1)-th attempt fails. Counters and `previous` are per process instance
  (each `ProcessStep` invocation gets a fresh scope).
- **Entry binding:** `{f: inputs[f] for f in entry Input fields if f in inputs}` (validated at the boundary).
- **`on_error` handler:** Input bound by field name from the `ProcessError` JSON; handler exit `x` terminates the
  process with `$exit.x` (outputs validated against process `outputs[x]`); an error inside the handler terminates with
  `$exit.error` and `ProcessError.handler_error` set — no recursion.
- **`finally` steps:** run after the instance ends (success or error, even while an outer timeout unwinds, with no
  deadline), in order; inputs from `with:` (evaluated against the final scope) or else bound by field name from
  `{**process.inputs, "run_id": run_id, "exit": <final exit>}`; their exits are not routed; their errors are recorded in
  `ProcessResult.finally_errors` and never change the process exit.
- **Timeouts:** `limits.timeout` on the branch into a step bounds that step's dispatch (all attempts; for a
  ProcessStep node, the whole child). Expiry kills the worker and routes to the **process error handler** with cause
  `timeout` (not the step's `error` exit). Deadline ownership per `$DRAFTS/02 §7.7` (verbatim).
- **Workspace retention:** kept iff the **top-level** instance ended in its error handler (default or custom);
  otherwise deleted.

### 3.6 Compiled step package and `step.lock.yaml`

**Layout** (identical for hand-written M1 steps and compiler output; SPEC §6.3):
```
<step dir>/                        processes/<pid>/steps/<name>/  or  <step root>/<path>/
  pyproject.toml                   marker + declared dependency ranges (mirror of lock.fragment.deps)
  step.lock.yaml                   the review surface (below)
  <name>.py                        the step module: exactly one Step subclass (the entrypoint)
  [helpers.py ...]                 optional sibling modules, imported RELATIVELY only (from .helpers import x)
  test_<name>.py                   generated/hand-written tests (§3.20)
  cassettes/                       agentic steps: replay recordings (git-lfs)
  proto.yaml                       step-root steps only (process-local protos live in <process>/proto/<name>.yaml)
```
- The step directory **is** the Python package. It is always imported as `wynd_steps.<step_module_name(step_id)>`:
  local mode mounts the directory under that name (`wynd.runtime.worker.loader.mount_step_package`, algorithm
  `$DRAFTS/02 §5.2`); image mode installs a wheel whose files land at `site-packages/wynd_steps/<module name>/`
  (`wynd_steps` is a PEP 420 namespace; the builder stages the wheel, §6). So two `read` packages never collide and
  no module-uniqueness rule is needed.
- `entrypoint` is `"<module>:<Class>"` **relative to the package** (e.g. `read_pdf:ReadPdf`).
- Step `pyproject.toml` template (the builder never builds from it; it is a marker and declares ranges):
```toml
[project]
name = "<slug(pid or alias)>-<name>"        # e.g. process_supplier_invoice-read_pdf (slug, §3.1)
version = "0.1.0"
requires-python = ">=3.12"
dependencies = ["pypdf>=6,<7"]               # third-party only; wynd-spec/runtime are installed into every venv
[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"
```
  Validator `W128` if its dependency names differ from `lock.fragment.deps` names (PEP 503-normalised).

**`step.lock.yaml`** (`wynd.spec.lockfiles.StepLock`, `wynd: 1`):
```python
StepKind = Literal["deterministic", "agentic", "shell"]      # ProcessStep is never a package
Tier = Literal["cheap", "standard", "strong"]; Thinking = Literal["none", "low", "medium", "high"]
Effect = Literal["network", "filesystem", "shell"]
class RetryPolicy(SpecModel):  run: int = 0; validation: int = 0; tool: int = 0         # each >= 0 (§3.9)
DEFAULT_RETRIES = {"deterministic": RetryPolicy(), "shell": RetryPolicy(),
                   "agentic": RetryPolicy(run=2, validation=2, tool=1)}
def effective_retries(policy: RetryPolicy, override: RetryOverride | None) -> RetryPolicy   # field-wise
class ToolSnapshot(SpecModel):
    name: str; source: Literal["method", "library"]; effects: list[Effect] = []; idempotent: bool = False; env: list[EnvName] = []
    allow: list[str] = []                       # the `shell` builtin only: permitted executables (argv[0]), §5.1 builtins
class McpToolSnapshot(SpecModel):
    name: str; description: str = ""; input_schema: dict[str, Any]; output_schema: dict | None = None; idempotent: bool = False
class McpSnapshot(SpecModel):
    server: str; allow: list[str] (min 1); tools: list[McpToolSnapshot]   # exactly the allowed tools, sorted by name
    hash: HashStr                                                        # hash_obj([t.model_dump(mode="json") for t in tools])
class ShellLock(SpecModel): exit_codes: dict[int | Literal["*"], str] = {0: "done", "*": "error"}
class StepLock(SpecModel):
    wynd: Literal[1] = 1
    name: Name                                  # == package dir name
    kind: StepKind
    entrypoint: str                             # ^[A-Za-z_][\w.]*:[A-Za-z_]\w*$ relative to the package
    proto_hash: HashStr | None = None           # None for proto-less hand-written steps
    provider: str | None = None                 # agentic only; None = process default (hand-edited override, SPEC §3.9)
    tier: Tier | None = None                    # agentic only; filled "cheap" when absent
    thinking: Thinking | None = None            # agentic only; filled "low" when absent
    builtin_tools: list[str] = []               # agentic only: AgentProvider harness built-ins; ⊆ HARNESS_BUILTINS; default none
    max_turns: int | None = None                # agentic only; None -> 25
    retries: RetryPolicy | None = None          # None -> DEFAULT_RETRIES[kind]
    effects: list[Effect] = []
    tools: list[ToolSnapshot] = []              # agentic only
    mcp: list[McpSnapshot] = []                 # agentic only
    context: list[str] = []                     # agentic only; snapshot of the class attribute
    shell: ShellLock | None = None              # shell only
    fragment: EnvFragment = EnvFragment()       # declared deps (minimums), system, requires, env vars
    locked_deps: list[str] = []                 # pinned closure "name==version", sorted, excluding wynd-spec/runtime
    interface: Interface | None = None          # snapshot of Input / per-exit Output (W129 when absent)
    compiled: dict[str, Any] | None = None      # compiler provenance (opaque to everyone but the compiler)
```
`E-LOCK` post-validation rules: adopt `$DRAFTS/01 §6.5` verbatim (agentic-only fields on non-agentic steps; `shell`
only on shell; `effects ⊇` tool effects `∪ {network}` if `mcp`; snapshot tools == allow; `shell.exit_codes` values ⊆
interface exits ∪ {error}; every `context` entry parses; every tool `env` name ∈ `fragment.vars`) plus two rules of
this plan: `builtin_tools ⊆ HARNESS_BUILTINS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch")` — `Bash`,
`NotebookEdit` and any other harness tool that executes model-written code are rejected with `builtin tool '{t}' is
not allowed: harnesses run on the host in local mode and may execute model-written code only inside the process
container (SPEC §3.8)`; `allow` is non-empty iff the snapshot is the `shell` library tool.
Tier→model mapping never appears in any lockfile. Test results never appear in any lockfile.
**Snapshot verification:** the lock's `interface`, `context`, `kind` and (shell) `shell.exit_codes` are snapshots of
the class. The worker receives them in `init` (`InitStep`, §3.12) and verifies at import:
`interface_hash(interface_from_models(cls.Input, cls.Output)) == InitStep.interface_hash` (when set),
`list(getattr(cls, "context", [])) == InitStep.context`, `step_kind(cls) == InitStep.kind`, and for shell steps
`{str(k): v for k, v in cls.exit_codes.items()} == InitStep.exit_codes`; a mismatch makes that step's `init` entry
`{"ok": false, "error": {"type": "SnapshotDrift", "message": "interface snapshot drift for step <step id>: run wynd
validate --sync-interfaces", …}}` and every run of it resolves with cause `import`.

### 3.7 `edges.lock.yaml` (M5; committed at `processes/<id>/edges.lock.yaml`)
```python
class EdgeLockEntry(SpecModel):
    check_hash: HashStr                 # check_hash(check, context)
    provider: str | None = None         # None -> RunPlan.provider (the ROOT process's effective default; a child's
                                        # own `provider:` is ignored, SPEC §3.2)
    tier: Tier = "cheap"; thinking: Thinking = "low"
    retries: int = 2                    # validation retries (continue the conversation)
    timeout_s: int = 120                # bounds the whole check incl. retries
class EdgesLock(SpecModel):
    wynd: Literal[1] = 1
    edges: dict[str, EdgeLockEntry] = {}          # key = branch_key (§3.1)
def branch_key(edge_from: str, index: int, name: str | None) -> str
def check_hash(check: str, context: list[str] | None) -> str
    # hash_obj({"check": " ".join(check.split()), "context": context or ["previous.outputs"]})
def load_edges_lock(path: Path) -> EdgesLock      # missing file -> EdgesLock()
```
Written only by the compile job (`sync_edge_lock`, §6); hand-editable; a missing/stale entry makes the process
*design* (status) and uses defaults at run time.

### 3.8 Runtime records (spec `records.py`; embedded in outputs, traces, errors)
```python
class Summary(SpecModel):            # SPEC §3.6 step 5, derived deterministically (never a model call)
    step: str; exit: str; key_outputs: dict[str, Any] = {}; note: str = ""
StepErrorCause = Literal["input_validation", "exception", "hook", "output_validation", "tool", "transport",
                         "model", "config", "cassette_miss", "shell_exit", "child_process", "worker_crash", "import"]
class StepError(SpecModel):          # payload of EVERY step's implicit `error` exit
    exit: Literal["error"] = "error"
    cause: StepErrorCause
    message: str
    type: str | None = None          # exception class name
    traceback: str | None = None
    inputs: dict[str, Any] = {}      # the bound inputs as received (lets `X.error` edges re-bind X's inputs)
    partial_outputs: dict[str, Any] | None = None
    attempts: int = 1
    child: "ProcessError | None" = None   # cause == "child_process"
ProcessErrorCause = Literal["step_error", "unrouted_exit", "ignored_exit", "no_branch_matched", "max_traversals",
                            "timeout", "expression_error", "invalid_process_outputs", "edge_check", "internal"]
class TracePointer(SpecModel): run_id: str; uri: str; seq: int      # seq of the process.error event
class ProcessError(SpecModel):       # SPEC §3.5: step, cause, inputs, partial outputs, trace pointer
    run_id: str
    process: str                     # process id of the failing instance
    step: str | None                 # full step path; None if not step-specific
    cause: ProcessErrorCause
    message: str
    edge: str | None = None          # edge key "validate.done", or branch_key(...) when branch-specific (§3.1)
    inputs: dict[str, Any] = {}      # the failing step's bound inputs (process inputs when step is None)
    partial_outputs: dict[str, Any] | None = None
    step_error: StepError | None = None       # cause == "step_error"
    handler_error: StepError | None = None    # a custom on_error handler itself failed
    detail: dict[str, Any] = {}               # e.g. edge_check {"branch_key", "error_cause"}
    trace: TracePointer | None = None
    workspace: str | None = None              # URI when the workspace was kept
STEP_ERROR_SCHEMA: dict = StepError.model_json_schema()
```
Process output for `$exit.error` is `{"error": <ProcessError JSON>}` (SPEC §3.5 "attaches … to the process output");
a `ProcessStep` whose child ends in `$exit.error` takes its own `error` exit with
`StepError(cause="child_process", child=<child ProcessError>)`. In traces, scopes and the wire, step `outputs` never
include the `exit` key.

### 3.9 Retry policy and error-exit decision table (one table, SPEC §3.5)
`RetryPolicy.run` — deterministic/shell: re-run `pre/run/post` on a fresh instance after `exception`/`hook`/`shell_exit`;
agentic: full loop restarts on a **transport** failure **before any tool call** (backoff 1 s, 4 s).
`validation` — agentic only: append the validation error and re-request structured output **in the same
conversation**. `tool` — agentic only: per tool call, transient network errors, **idempotent tools only**.

| Situation | Result |
|---|---|
| bound inputs fail `Input` validation | `error` exit, cause `input_validation`, attempts 0, no retry |
| `pre`/`post` raise | cause `hook` (post runs iff pre returned) |
| `run` raises (non-agentic) | retry per `run`; then cause `exception` |
| non-agentic output fails validation | cause `output_validation` (no retry: deterministic code is deterministic) |
| agentic output invalid / missing / truncated, `failures ≤ validation` | continue the conversation with the error |
| agentic validation retries exhausted | cause `output_validation`, attempts = 1 + retries |
| agentic transport error, no tool called yet | full restart, at most `run` times; then cause `transport` |
| agentic transport error after any tool call | cause `transport` (never restart) |
| auth failure / CLI missing / unknown provider / missing tier / missing MCP registry entry / MCP snapshot mismatch / missing tool env / `self.runtime.http` used without `network` in the step's effects | cause `config` |
| refusal, invalid request (400), max turns exceeded | cause `model` |
| a tool raises (after its retries) | cause `tool` (claude-code aborts the harness first) |
| tool argument validation fails / `ToolInputError` (incl. a `shell` argv[0] outside its allowlist) / MCP `isError` | error result returned to the model; loop continues |
| replay cassette miss — of a model call, an agent run **or a recorded tool call** | cause `cassette_miss`, always: a `CassetteMissError` raised while replaying a tool call is never turned into cause `tool` or into an error result for the model; it aborts the loop/harness and resolves the step. `run_step` re-raises `CassetteMissError` so pytest shows the spec text; `run_process_examples` fails the example (§3.20) |
| shell exit code maps to `error` | cause `shell_exit` |
| worker process dies / JSON-RPC fault | cause `worker_crash` |
| step class fails to import / violates the interface / snapshot drift | cause `import` |
| `limits.timeout` expires | **process error handler**, cause `timeout` |
Branch `limits.retries` overrides the target's policy field by field, for runs entered through that branch only.

### 3.10 Env fragments and the env manifest
```python
# wynd/spec/fragments.py
class EnvVar(SpecModel):
    name: EnvName; description: str = ""; secret: bool = False; required: bool = True
    default: str | None = None                 # documented default; satisfies `required`
    one_of: str | None = None                  # alternatives group name (see EnvGroup)
    used_by: list[str] = []                    # "step:<id>" | "tool:<step id>/<tool>" | "mcp:<server>" | "provider:<name>"
                                               # | "edge:<pid>:<branch_key>.<field>" | "storage" | "runtime"
Mode = Literal["local", "image"]
class EnvGroup(SpecModel): description: str = ""; modes: list[Mode] = ["local", "image"]   # modes where the group is required
class EnvFragment(SpecModel):
    deps: list[str] = []                       # PEP 508 requirement strings (minimums)
    system: list[str] = []                     # OS package names, used verbatim by apt-get / apk
    requires: Literal["glibc"] | None = None
    vars: list[EnvVar] = []
    groups: dict[str, EnvGroup] = {}
def merge_env_vars(vars) -> list[EnvVar]       # $DRAFTS/01 §6.2 merge rules (E-ENV-CONFLICT on differing defaults)
def merge_fragments(frags) -> EnvFragment      # deps order-preserving unique; system sorted; requires glibc if any; groups merged (modes union)
# wynd/spec/env_manifest.py
class EnvManifest(SpecModel):
    wynd: Literal[1] = 1; process: ProcessId; commit: str | None = None
    vars: list[EnvVar]                          # sorted by name
    groups: dict[str, EnvGroup] = {}
@dataclass(frozen=True)
class EnvCheck: ok: bool; diagnostics: list[Diagnostic]
def check_env(manifest: EnvManifest, environ: Mapping[str, str], *, mode: Mode) -> EnvCheck
```
`check_env`: a var is *set* when present and non-empty. Non-group var, required, no default, unset → `E-ENV-MISSING`.
A group is satisfied when any member is set; unsatisfied → `E-ENV-ONE-OF` if `mode in group.modes`, else
`W-ENV-ONE-OF`. Messages name the variable and `used_by`, never a value. It is pure and runs in-image
(`wynd-supervisor env-check`, `mode="image"`) and locally (`wynd env check`, `mode="local"`).
**claude-code auth:** `CLAUDE_CODE_OAUTH_TOKEN` and `ANTHROPIC_API_KEY`, both `secret, required: false,
one_of: claude-code-auth`; group `claude-code-auth: {modes: [image]}` — optional locally (logged-in CLI), required in
images/CI. This is data, not an environment branch.
**User-registry data in images** (SPEC §4 "MCP server URLs and auth … `WYND_HOME`", §3.9 tiers): an image never
reads a user's `$WYND_HOME` files. Instead the base image sets `WYND_REGISTRY=env`, selecting the read-only
`registry.env` backend (§3.14), which reads `WYND_REGISTRY_JSON` = compact JSON `{"mcp": {name: McpServerEntry},
"providers": {name: ProviderEntry}}`. `wynd.process.envmanifest.registry_snapshot(lp, registry) -> dict` returns
exactly the entries the closure uses (every MCP server named by a closure step's `mcp`; the `providers` entry of
every provider used by an agentic step or agentic branch, when the registry has one); the manifest then contains:
- `WYND_REGISTRY_JSON` — present **iff** the snapshot is non-empty; non-secret, `required: false`,
  `one_of: user-registry` with group `user-registry: {modes: [image]}` (required in images; locally the file registry
  is used, so it is only a `W-ENV-ONE-OF` note); `used_by ["mcp:<server>"…, "provider:<name>"…]`; description listing
  each server with its URL (or command) and each tier override, e.g. `User-registry snapshot. MCP: github (http
  https://api.githubcopilot.com/mcp/). Tiers: claude-code cheap=sonnet.` The controller supplies the value whenever it
  runs or serves an image (`EnvService.resolve_image`, §8.1); in a cluster it is a ConfigMap key. So an image of a
  process that uses MCP or remapped tiers fails `env-check` at startup instead of failing mid-run, and image tiers
  always equal the user's local tiers.
- `WYND_MCP_<NAME>_URL` for every http MCP server used (`NAME = name.upper().replace("-", "_")`), non-secret,
  `required: false`, `default` = the registry URL, `used_by ["mcp:<server>"]`. When set, `build_policy` uses it
  instead of the entry's `url` (so a cluster can point at an in-cluster address).
The MCP servers' `auth_env` vars (secrets) are listed as usual.

### 3.11 Run plan and `process.lock.yaml`
One run-plan format consumed by the executor, worker pool and supervisor, produced by the process package for local
mode (in memory) and image mode (inside `process.lock.yaml`). Local and image differ **only in data**.
```python
# wynd/spec/plan.py
class PlanVenv(SpecModel):
    id: str                                  # directory name under venv_root
    python: str | None = None                # explicit interpreter (tests, bake); None -> f"{venv_root}/{id}/bin/python"
    steps: list[str]                         # step ids served; their entrypoints are pre-imported at worker start
class PlanStep(SpecModel):
    id: str                                  # step id (§3.1)
    kind: StepKind
    entrypoint: str                          # "<module>:<Class>" relative to the package
    venv: str                                # PlanVenv.id (the builder owns assignment)
    package_dir: str | None                  # local: ABSOLUTE package dir (mounted by the worker; step-level cassettes);
                                             # image: None (installed as wynd_steps.<module name>)
    lock: StepLock
class PlanNode(SpecModel):
    step: str | None = None                  # step id, or
    process: str | None = None               # child process id (ProcessStep)
class PlanProcess(SpecModel):
    id: str
    dir: str | None                          # local: absolute process dir; image: None
    definition: ProcessDoc                   # NORMALISED: to-lists, max_traversals filled on cycle branches, after-else dropped
    nodes: dict[str, PlanNode]               # every key of definition.steps
    edges_lock: EdgesLock = EdgesLock()      # M5
class RunPlan(SpecModel):
    wynd: Literal[1] = 1
    mode: Literal["local", "image"]
    root: str                                # root process id
    commit: str | None = None                # closure HEAD when known
    provider: str                            # effective default provider of the ROOT (applies to children, SPEC §3.2)
    venv_root: str                           # local: <ws>/.wynd/venvs ; image: /opt/wynd/venvs
    venvs: list[PlanVenv]
    steps: dict[str, PlanStep]
    processes: dict[str, PlanProcess]        # root + every descendant in the reference closure
    edge_venvs: dict[str, str] = {}          # M5: "<pid>:<branch_key>" -> venv id (rule §6.4)
```
```python
# wynd/spec/lockfiles.py (process.lock.yaml; written by the build job, read by the supervisor)
class BaseChoice(SpecModel):
    requested: Literal["debian-slim-python", "alpine-python"]; variant: Literal["slim", "alpine"]
    version: str                             # == wynd-runtime version
    image: str                               # e.g. "wynd-base:0.1.0-slim"
    reason: str | None = None                # why alpine was not used (None when requested == used)
class FragmentRecord(SpecModel): source: str; deps: list[str]; system: list[str]; requires: str | None   # "step:<id>" | "provider:<name>"
class LockedWheel(SpecModel): dir: str; hash: HashStr; wheel: str          # workspace-relative dir, step_hash, dist/<file>
class LockedVenv(SpecModel): id: str; inputs: list[str]; requirements: list[str]   # resolved pins (incl. wynd-spec/runtime)
class ProcessLock(SpecModel):
    wynd: Literal[1] = 1
    process: ProcessId
    commit: str                              # closure HEAD = "git commit hash of the compile tree"
    source_sha: str                          # commit checked out by the job (same closure content)
    process_hash: HashStr
    runtime_version: str
    platform: str                            # e.g. linux/arm64
    base: BaseChoice
    system_packages: list[str] = []
    fragments: list[FragmentRecord] = []
    wheels: dict[str, LockedWheel] = {}      # step id -> wheel
    venvs: list[LockedVenv] = []
    plan: RunPlan                            # mode "image"
```
No timestamps: the same commit yields a byte-identical `process.lock.yaml`.

### 3.12 Venv worker JSON-RPC protocol (runtime; local and image identical)
Adopt `$DRAFTS/02 §5.1–§5.5` verbatim (stdio claim: dup fds, fd 1 → stderr, per-run output capture emitted as one
`step.log`; one request at a time; crash/timeout handling) with these exact messages:
- Spawn: `[<venv python>, "-m", "wynd.runtime.worker"]`, env = caller env + `PYTHONUNBUFFERED=1`, no arguments.
- Newline-delimited JSON-RPC 2.0. Requests client→worker; notifications worker→client only while a request is in
  flight: `{"jsonrpc":"2.0","method":"event","params":{"type": <trace type>, ...}}` (the executor stamps
  `seq/ts/run_id/step/span/parent`).
- `init` params `{"protocol": 1, "runtime_version": "0.1.0", "steps": [InitStep]}` (`InitStep` below; the executor
  and pool fill the snapshot fields from `PlanStep.lock`) → result `{"protocol", "runtime_version", "python", "pid",
  "steps": {id: {"ok": true} | {"ok": false, "error": {"type","message","traceback"}}}}`. Import failure or snapshot
  drift (§3.6) of one step is not fatal to the worker.
- `describe` params `{"ids": [..] | null}` → `{"steps": {id: StepDescription | {"error": …}}}`;
  `StepDescription = {id, cls, kind, doc, input_schema, input_fields, required_inputs, exits: {exit: schema incl. "error"},
  context, agentic: describe_agentic(cls) | null, shell: {"exit_codes": …} | null}`.
- `run_step` params `RunStepParams` → result `RunStepResult` (below). A step `error` exit is a **successful** response.
- `edge.check` (M5) params `{"call": EdgeCheckCall, "cassette": CassetteConfig, "workspace": str}` → `EdgeCheckResult`
  or JSON-RPC error `-32001` with `data = {"cause": EdgeCheckCause, "message"}` (types in §5.4); worker `model.call`
  notifications during the call are stamped by the executor like step events.
- `ping` → `{"pid"}`; `shutdown` → `{}` then exit 0; EOF → exit 0; SIGINT ignored.
- Errors: `-32700/-32600/-32601/-32602/-32603` standard; `1001` not initialised; `1002` unknown step; `1003` protocol mismatch.
```python
# wynd/runtime/policy.py (ExecPolicy, CassetteConfig)  +  wynd/runtime/worker/protocol.py (the rest)
PROTOCOL_VERSION = 1
class CassetteConfig(BaseModel):
    mode: Literal["live", "record", "replay"] = "live"; dir: str | None = None; record_dir: str | None = None
    literals: dict[str, str] = {}           # extra Normaliser literals {absolute path: placeholder}, §3.16
class ExecPolicy(BaseModel):
    kind: StepKind; retries: RetryPolicy
    provider: str | None = None; model_id: str | None = None; tier: str | None = None; thinking: str | None = None
    builtin_tools: list[str] = []; max_turns: int = 25; effects: list[str] = []
    mcp: list[dict[str, Any]] = []          # McpSnapshot JSON + {"entry": McpServerEntry JSON from the user registry}
class InitStep(BaseModel):
    id: str; entrypoint: str; package_dir: str | None
    kind: StepKind                          # = lock.kind
    interface_hash: str | None = None       # = interface_hash(lock.interface) when lock.interface is set
    context: list[str] = []                 # = lock.context
    exit_codes: dict[str, str] | None = None   # shell only: lock.shell.exit_codes with str keys
class RunStepParams(BaseModel):
    run_id: str; step_path: str; step_run: int; step_id: str
    inputs: dict[str, Any]; context: dict[str, Any] | None = None
    workspace: str; policy: ExecPolicy; cassette: CassetteConfig = CassetteConfig()
class StepTimings(BaseModel):
    started_at: datetime; ended_at: datetime; worker_ms: float
    pre_ms: float | None = None; run_ms: float | None = None; post_ms: float | None = None
class RunStepResult(BaseModel):
    exit: str; outputs: dict[str, Any]; summary: Summary; attempts: int
    validation_failures: int = 0; timings: StepTimings
    usage: Usage | None = None; model: ModelInfo | None = None; replayed: bool = False
```

### 3.13 Trace events (JSONL; one line per event; runtime `trace.py`)
Envelope on every line: `{"v": 1, "seq": int (per run, from 1, contiguous), "ts": "…Z", "run_id": str, "type": str}`.
Event types and fields (exact; web, controller, optimise and `wynd trace` read these names):

| type | fields |
|---|---|
| `run.start` | `process, mode ("local"\|"image"), inputs, commit, runtime_version, workspace, cassette ("live"\|"record"\|"replay"), metadata` |
| `step.start` | `step (path), name (node), process, span (= own seq), parent (span\|null), id (step id or "process:<pid>"), kind (TraceStepKind: "deterministic"\|"agentic"\|"shell"\|"process" — "process" for ProcessStep nodes), run (n), role ("node"\|"on_error"\|"finally"), via (branch_key, e.g. "validate.done[1]" / "validate.done[fix]" \| "on_error" \| "finally" \| null), inputs, venv (null for "process")` |
| `step.end` | `step, span, parent, run, kind (TraceStepKind), exit (str\|null), timed_out, outputs (for exit "error": the StepError JSON without `exit`, so `outputs.cause` is readable), summary, attempts, validation_failures, timings {started_at, ended_at, duration_ms, worker_ms, pre_ms, run_ms, post_ms}, usage (Usage\|null), model ({provider, model_id, tier, thinking}\|null), replayed (bool)` |
| `step.log` | `step, span, parent, level, stream ("logger"\|"output"), message` |
| `step.event` | `step, span, parent, name, data` |
| `edge.taken` | `process, parent, from, branch, name, to, kind, with, taken, verdicts ([{branch, take, reason}], agentic only)` |
| `edge.check` (M5) | `process, parent, edge, branch, branch_key, target, take (bool\|null), reason, attempts, validation_failures, error_cause, provider, tier, model_id, usage, duration_ms, replayed` |
| `model.call` | `step (path, or "edge:<branch_key>"), span, parent, provider, kind ("model"\|"agent"), tier, thinking, model_id, n, attempt, restart, reason, outcome ("tool_calls"\|"valid"\|"invalid"\|"no_output"\|"truncated"\|"refusal"\|"error"), errors, request_hash, request, messages_new, response, usage, startup_ms, cost_basis, cassette ("live"\|"record"\|"replay")` — payload per `$DRAFTS/03 §6.8` |
| `tool.call` | `step, span, parent, tool, source ("builtin"\|"method"\|"mcp:<server>"\|"harness"), effects, idempotent, args, result, ok, is_error, error, tries, latency_ms, replayed` (args/result truncated to 4 kB) |
| `process.error` | `process, parent, error (ProcessError), handler ("default"\|<step key>)` |
| `worker.start` | `step, span, parent, venv, pid, startup_ms` |
| `run.end` | `exit, outputs, error (ProcessError\|null), status ("succeeded"\|"failed"), duration_ms, workspace_kept, workspace, usage (totals), finally_errors` |

`Usage = {input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, cost_usd (null = unknown), latency_ms
(sum of per-call latencies), calls}`. Unknown types parse to the envelope (forward compatible). Nesting for display:
`build_tree(events) -> TraceTree` (`$DRAFTS/02 §8.4`). Every consumer uses these runtime names (the web's
`run_started`-style names from `$DRAFTS/07` are replaced). `TraceStepKind = Literal["deterministic", "agentic",
"shell", "process"]` is defined in `wynd.spec.lockfiles` next to `StepKind` (optimise, `render_tree`, the web reducer
and `StepInfo.kind` use it). Events carry exactly these fields: no producer adds `mode`/`path` to every event
(`mode` is on `run.start` only; the step path is `step`).

### 3.14 Storage interfaces, records and user registries (SPEC §7.1; runtime `storage/`)
```python
class WorkspaceStore(Protocol):
    def open(self, run_id: str) -> Path                       # create; a LOCAL dir usable as cwd
    def close(self, run_id: str, *, keep: bool) -> str | None # keep: return URI; else delete, None
    def locate(self, run_id: str) -> str | None
class TraceSink(Protocol):
    def write(self, event: dict) -> None                      # JSON-mode event dicts (independent of trace models)
    def close(self, run_id: str) -> None
    def read(self, run_id: str) -> list[dict]                 # seq order; [] if unknown; skips a truncated last line
    def uri(self, run_id: str) -> str
class RunRegistry(Protocol):                                  # one registry for runs AND jobs (records carry "kind")
    def create(self, record: Mapping[str, Any]) -> None       # needs id, kind ("run"|"job"), status; sets created_at/updated_at; duplicate id -> FileExistsError
    def update(self, id: str, patch: Mapping[str, Any]) -> dict   # atomic shallow merge; sets updated_at
    def get(self, id: str) -> dict | None
    def list(self, *, kind: str | None = None, process: str | None = None, status: str | None = None, limit: int = 100) -> list[dict]  # newest first
    def put_test_result(self, result: TestResult) -> None
    def get_test_result(self, commit: str, key: str) -> TestResult | None
class Registry(Protocol):                                     # user-level (WYND_HOME)
    def list(self, section: str) -> dict[str, dict]; def get(self, section: str, name: str) -> dict | None
    def put(self, section: str, name: str, entry: Mapping) -> None; def remove(self, section: str, name: str) -> bool
    def put_secret(self, name: str, value: str) -> None       # local: $WYND_HOME/secrets.env, mode 0600
    def secrets(self) -> dict[str, str]
    def location(self) -> str
REGISTRY_SECTIONS = ("mcp", "providers", "registries")
@dataclass(frozen=True)
class Stores: workspaces: WorkspaceStore; traces: TraceSink; runs: RunRegistry; registry: Registry
def stores_from_env(env: Mapping[str, str] | None = None, *, data_dir: str | Path | None = None) -> Stores
def registry_from_env(env: Mapping[str, str] | None = None) -> Registry
STORAGE_ENV: list[EnvVar]   # the selector vars below, required=False with defaults, used_by ["storage"]
```
Selection (`$DRAFTS/02 §9.4` algorithm, entry-point group `wynd.storage`, names `workspace.file`, `trace.jsonl`,
`runs.file`, `registry.file`, `registry.env`; factory `(location, root, env)`): `WYND_DATA_DIR` (root for
`workspaces/`, `traces/`, `registry/`; caller's `data_dir` else `StorageConfigError`), `WYND_WORKSPACE_STORE=file`,
`WYND_TRACE_SINK=jsonl`, `WYND_RUN_REGISTRY=file`, `WYND_REGISTRY=file` (`env` in images, set by the base image),
`WYND_HOME` (default `~/.wynd` only when unset). Local backends are multi-process safe on a shared filesystem: one
JSON file per record, temp file + `os.replace`, `fcntl.flock` on a lock file, no index file. **`registry.env`**
(`EnvRegistry`): read-only view of `WYND_REGISTRY_JSON` (§3.10; unset or empty → no entries); `list/get` read it,
`put/remove/put_secret` raise `StorageConfigError("registry is read-only (WYND_REGISTRY=env)")`, `secrets()` → `{}`,
`location()` → `"env:WYND_REGISTRY_JSON"`.
```python
# wynd/runtime/storage/models.py
class RunRecord(BaseModel):
    id: str; kind: Literal["run"] = "run"; process: str
    status: Literal["queued", "running", "succeeded", "failed"]      # failed iff exit == "error" (or failed to start)
    created_at: datetime; updated_at: datetime; started_at: datetime | None = None; finished_at: datetime | None = None
    ref: str | None = None; mode: Literal["local", "image"] | None = None
    inputs: dict[str, Any] = {}; exit: str | None = None; outputs: dict[str, Any] | None = None
    error: dict[str, Any] | None = None; trace: str | None = None; workspace: str | None = None
    duration_ms: float | None = None; usage: Usage | None = None          # §3.13 Usage totals
    trace_bytes: int | None = None; workspace_bytes: int | None = None     # storage accounting (SPEC §15), set at run end
    meta: dict[str, Any] = {}              # = dict(metadata) exactly (below)
class TestResult(BaseModel):
    commit: str                            # closure HEAD
    key: str                               # "step:<step_id>:<step_hash>" | "process:<pid>:<process_hash>"
    passed: bool; counts: dict[str, int]   # passed/failed/error/skipped
    ran_at: datetime; job_id: str | None = None; report: dict[str, Any] | None = None
```
**Run metadata:** `RunRecord.meta = dict(metadata)` where `metadata` is `Executor.run(metadata=…)` /
`RunRequest.metadata`; reserved keys, always sent by the controller: `trigger ∈ {"api", "manual", "schedule",
"webhook"}`, `release_id: str | None`, `target: {"kind": "local"|"image"|"release", "commit"?: str}`; other keys pass
through. (`$DRAFTS/03 §13`'s example `{"trigger": "cron", "release": …}` is corrected to `{"trigger": "schedule",
"release_id": …}`.) `workspace_bytes` is the size of the run workspace at close, measured just before it is kept or
deleted (so a deleted workspace still records its measured size; it is not 0), `trace_bytes` the size of the trace file.
`JobRecord` (kind `"job"`) is defined in `wynd.process.jobs` (§3.18) and stored through the same `RunRegistry`.
On-disk: `$WYND_DATA_DIR/registry/records/<id>.json`, `…/registry/tests/<commit>/<sha256(key)[:32]>.json`,
`$WYND_DATA_DIR/traces/<run_id>.jsonl`, `$WYND_DATA_DIR/workspaces/<run_id>/`, `$WYND_HOME/{mcp,providers,registries}.json`
(`{"version": 1, "entries": {name: {...}}}`), `$WYND_HOME/secrets.env`.

**Registry entry formats (defined once):**
```python
class ProviderEntry(BaseModel):            # section "providers" (runtime providers/__init__.py)
    model_config = ConfigDict(extra="forbid")
    tiers: dict[Literal["cheap", "standard", "strong"], str] = {}      # overlays the provider's default_tiers
class McpServerEntry(BaseModel):           # section "mcp" (runtime mcp/entry.py)
    model_config = ConfigDict(extra="forbid")
    name: str                              # ^[a-z0-9][a-z0-9_-]*$
    transport: Literal["http", "stdio"]
    url: str | None = None                 # http
    headers: dict[str, str] = {}           # values may contain ${env:NAME}; never literal secrets
    command: list[str] | None = None       # stdio argv
    env: dict[str, str] = {}               # stdio child env overlay; ${env:NAME} allowed
    auth_env: list[str] = []               # every env var referenced (manifest)
    timeout_s: float = 30.0
    description: str = ""
    oauth: dict[str, Any] | None = None    # {client_id, token_endpoint, expires_at, refresh_env} (controller OAuth, M4)
class ImageRegistryEntry(BaseModel):       # section "registries" (process artefacts.py; the builder, base.py and the
                                           # controller's registries.py all import it from wynd.process.artefacts)
    name: str; url: str                    # e.g. "localhost:5001/wynd" or "ghcr.io/peterddod"
    username_env: str | None = None; password_env: str | None = None
    insecure: bool = False; default: bool = False
```
`wynd mcp add github --url U --auth-env GITHUB_TOKEN` writes `{transport: http, url: U, headers: {"Authorization":
"Bearer ${env:GITHUB_TOKEN}"}, auth_env: [GITHUB_TOKEN]}`; `--header K=${env:X}` overrides; `--command "…"` → stdio
(shlex-split).

### 3.15 Providers (runtime `providers/`; SPEC §3.9)
Adopt `$DRAFTS/03 §5` verbatim for types and registration, with `AgentRequest` extended by `cancel`:
```python
@dataclass(frozen=True) class ToolSchema: name: str; description: str; input_schema: dict
@dataclass(frozen=True) class ToolCall: id: str; name: str; arguments: dict
@dataclass(frozen=True) class ToolResult: text: str; is_error: bool = False
@dataclass(frozen=True) class ToolHandle: name: str; description: str; input_schema: dict; invoke: Callable[[dict], ToolResult]
    local: bool = False     # True iff the tool has no `network` effect and is not MCP (re-executed on AgentProvider replay, §3.16)
@dataclass(frozen=True) class McpServerRef: name: str; allow: tuple[str, ...]
@dataclass(frozen=True) class GenerateRequest: model_id: str; thinking: Thinking; system: str; messages: list[Message]; tools: list[ToolSchema]; output_schema: dict | None
@dataclass(frozen=True) class GenerateResponse: message: Message; text: str; tool_calls: list[ToolCall]; structured_output: Any | None
    stop: Literal["end", "tool_calls", "max_tokens", "refusal"]; usage: Usage; model_id: str; cost_basis: Literal["api", "list"] | None = None; raw: dict | None = None
@dataclass(frozen=True) class Continuation: session: dict; message: str
@dataclass(frozen=True) class AgentRequest:
    model_id: str; thinking: Thinking; instruction: str; context: dict; input: dict; output_schema: dict | None
    tools: list[ToolHandle]; mcp_servers: list[McpServerRef]; workspace: Path; max_turns: int = 25
    builtin_tools: list[str] = field(default_factory=list)   # never None; [] = no harness built-ins (StepLock/ExecPolicy default)
    continuation: Continuation | None = None
    prompt: str | None = None                 # raw mode: instruction = system prompt verbatim, prompt = user message
    on_event: Callable[[dict], None] | None = None   # {"type":"text"|"tool_use"|"tool_result", ...}; not part of cassette keys
    cancel: threading.Event | None = None     # checked between SDK messages; set -> ProviderError(kind="transport", retryable=False) "cancelled"
@dataclass(frozen=True) class AgentResponse: structured_output: Any | None; usage: Usage; model_id: str; transcript: list[dict]
    session: dict; note: str = ""; tool_calls: int = 0; startup_ms: float | None = None; cost_basis: Literal["api", "list"] | None = None
class ModelProvider(Protocol): name: str; def generate(self, req: GenerateRequest) -> GenerateResponse; def tiers(self) -> dict[str, str]
class AgentProvider(Protocol): name: str; def run(self, req: AgentRequest) -> AgentResponse; def tiers(self) -> dict[str, str]
class ProviderError(Exception): kind: Literal["transport","auth","unavailable","invalid_request","refusal","max_turns"]; retryable: bool; tool_called: bool; status: int | None
def check_provider(name: str) -> tuple[bool, str]   # readiness without a model call: claude-code = SDK importable and
                                                     # bundled CLI found; anthropic = ANTHROPIC_API_KEY set; fake = True
```
All of the above live in `wynd/runtime/providers/types.py` (`check_provider` in `providers/__init__.py`), RT-PROVIDERS;
`ProviderError` is defined **only** there — `wynd.runtime.agentic.errors` imports and re-exports it.
- Registration: entry-point group `wynd.providers`: `claude-code`, `anthropic`, `fake`. Provider classes carry
  class attributes `name, kind ("model"|"agent"), default_tiers, env_fragment: EnvFragment` readable without importing
  any SDK. `provider_info(name) -> ProviderInfo(name, kind, default_tiers, env_fragment)` (KeyError if unknown),
  `available_providers()`, `provider_tiers(name, registry) = {**default_tiers, **ProviderEntry(registry["providers"][name]).tiers}`,
  `resolve_model(provider, tier, registry) -> str` (raises `StepFailure("config", …)` with the exact messages of
  `$DRAFTS/03 §5.2`), `load_provider(name, *, registry=None)`, `register_for_tests(name, cls) -> undo`.
  `DEFAULT_PROVIDER = "claude-code"` is defined in `wynd.spec.base` and re-exported by `wynd.runtime.providers`.
- Default tiers (never in lockfiles): `claude-code` cheap→`haiku`, standard→`sonnet`, strong→`opus`;
  `anthropic` cheap→`claude-haiku-4-5`, standard→`claude-sonnet-5`, strong→`claude-opus-5`; `fake` all→`fake`.
  **Tier parity rule** (SPEC §3.9 "comparable strength"): for each tier, the model Claude Code actually runs for the
  claude-code alias must be the same family and version as `AnthropicProvider.default_tiers[tier]`. An RT-PROVIDERS
  `@live` test (`test_tier_parity_live`) runs one tiny call per tier through claude-code and compares the model id
  in `ResultMessage.model_usage` (date suffix stripped) with the anthropic default; when it fails, the anthropic
  defaults are updated in the same change. Documented in `docs/providers.md`.
- Fragments: `claude-code`: `deps ["claude-agent-sdk>=0.2.157,<0.3"]`, `requires: glibc`, vars + group per §3.10;
  `anthropic`: no deps, `ANTHROPIC_API_KEY` (secret, required), `ANTHROPIC_BASE_URL` (optional); `fake`:
  `WYND_FAKE_PROVIDER_SCRIPT` (optional; a value whose first non-space character is `{` or `[` is the inline JSON
  script, otherwise a file path — so it can be passed to a container with `-e` and no mount).
- Effective provider of an agentic step: `lock.provider or RunPlan.provider` (= `<ROOT process>.provider or
  DEFAULT_PROVIDER`); of an agentic branch: `EdgeLockEntry.provider or RunPlan.provider` — never a child's own
  `provider:`. Tier `lock.tier or "cheap"`; thinking `lock.thinking or "low"`; `model_id = resolve_model(...)`
  executor-side (`build_policy`, `EdgeCheckCall`), with `resolve_model` falling back to the provider class's
  `default_tiers` when the registry has no entry (so it needs no registry and no credentials).
- Output schema envelope (§1.4): providers receive the **unwrapped** Output JSON schema and wrap it with
  `wrap_output_schema` (`$DRAFTS/03 §6.4` verbatim: `oneOf`→`anyOf`, `$defs` hoisted, every object closed with all
  properties required, unsupported keywords dropped); results are unwrapped with `unwrap_output`. This is the **only**
  strictness transform: every caller (steps, edge checks, the compiler, the chat) passes the plain schema.
- `claude-code` provider body: `$DRAFTS/03 §7` verbatim (bridge tools over the in-process `wynd` MCP server, stateless
  continuation, usage deltas, `_classify`, session cleanup); plus `req.cancel` checked between messages.
- `anthropic` provider: `$DRAFTS/03 §8` verbatim (stdlib urllib via runtime `HttpClient`; thinking/effort table;
  strict structured output with prompt fallback; pricing table; no internal retries).
- `fake` provider: `$DRAFTS/03 §14.5` script format (`WYND_FAKE_PROVIDER_SCRIPT`), AgentProvider, zero usage;
  test-only, shipped in the runtime wheel deliberately (§15 item 63).

### 3.16 Cassettes (runtime `cassettes/`; SPEC §7)
Modes (`CassetteConfig.mode`): `live` (real calls, nothing written; `model.call` still traced), `record` (real calls,
one JSON file per call written to `record_dir`), `replay` (read only from `dir`; never builds the real provider; a
miss raises `CassetteMissError`).
- **Key** = `sha256(Normaliser.apply(canonical_json(canonical_request).decode()))`; canonical requests per
  `$DRAFTS/03 §11.2` (generate / agent / agent_continue chained on the previous key / tool). The canonical request
  includes provider, tier, thinking **and the resolved `model_id`** (`resolve_model(provider, tier, registry)`,
  which falls back to the provider class's `default_tiers`, so replay still needs no registry and no credentials). A
  tier remap is a request change and misses with the standard `NO_RECORDING` error (SPEC §7: a changed request never
  replays an old response). **Normaliser** (`Normaliser.for_run(run_id, workspace, extra: Mapping[str, str] = {})`)
  replaces, longest literal first: the run workspace path and its realpath with `<workspace>`, each `extra` literal
  (and its realpath) with its placeholder, the run id with `<run.id>`; then `DATETIME_RE` (full ISO-8601 datetimes
  with `T`, seconds and zone) with `<datetime>`. `extra` = `CassetteConfig.literals`; the test runner fills it with
  `{<example tmp dir>: "<tmp>", <abs process dir>: "<process>", <abs workspace root>: "<ws>"}` (plus realpaths), so
  `{tmp}` values, env values under `{tmp}` and checkout-vs-workspace paths never change a key; `run_step` reads it from
  `WYND_CASSETTE_LITERALS` (JSON object) and always adds its own `workspace`. It is threaded through
  `Executor.run(cassette_literals=…)`, `run_local(…, cassette_literals=…)` and `run_process_examples`.
  `canonical_json` is `wynd.spec.hashing.canonical_json`.
- **Files:** `<dir>/<key[:32]>.json`, entry format `$DRAFTS/03 §11.3` (`wynd_cassette: 1`, `kind`, `key`, `package`,
  `step`, `provider`, `tier`, `thinking`, `model_id`, `recorded_at`, normalised `request`, `response` without
  `raw`/`session`).
- **Tools:** in the runtime-owned loop, tool calls of tools declaring `network` and all MCP tools are recorded and
  replayed with the model calls; `filesystem`/`shell`/pure (`ToolHandle.local`) tools always execute. An
  AgentProvider is replayed as a whole `run()`, and then — so side effects match the runtime-owned loop — the
  replay wrapper re-executes, in recorded order, every `tool_use` entry of the cassette's `response.transcript` whose
  handle is `local` (via its `ToolHandle.invoke`; results discarded, the recording stands). A miss on any recorded
  call (model, agent run or tool) is `CassetteMissError` and resolves the step with cause `cassette_miss` (§3.9).
- **Miss text (exact):** `no recording for this request — re-record with \`wynd test --live\`` followed by
  `\n  key: <key>\n  cassettes: <dir>` and `\n  request: <dump path>`; the normalised request is dumped to
  `<workspace>/.wynd/cassettes/misses/<key[:32]>.request.json`.
- **git-lfs pointer** files in `dir` raise `CassetteError("cassette <path> is a git-lfs pointer, not a recording:
  install git-lfs (https://git-lfs.com) and run 'git lfs pull'")`.
- **Directories:** step-level `<step dir>/cassettes/`; process-level `<process dir>/cassettes/<step_module_name>/`
  for steps and `<process dir>/cassettes/edges/` for M5 edge checks. Recording writes to a caller-supplied staging
  directory (`record_dir`); **promotion** replaces the destination directory's `*.json` with the staged files, only
  after the tests passed (`promote(staging: Path, dest: Path) -> list[Path]`). Replay reads only committed source.
- `.gitattributes`: `**/cassettes/** filter=lfs diff=lfs merge=lfs -text`.

### 3.17 Run API (in-image supervisor; SPEC §4.1 contract; runtime `supervisor/`)
Adopt `$DRAFTS/03 §13` verbatim: routes, schemas (`RunRequest{inputs, run_id?, files: {name: {content_base64}},
metadata}`, `RunCreated`, `Run`, `RunSummary`, `ProcessInfo`, `ErrorBody`), status codes, SSE framing, concurrency
(`WYND_MAX_CONCURRENT_RUNS=4`, `WYND_MAX_QUEUED_RUNS=64`, 429), retention, bearer token (`WYND_RUN_API_TOKEN`), drain on
SIGTERM, idempotent `run_id`, `{"$file": name}` input substitution. Summary:

| Method, path | Success |
|---|---|
| `GET /healthz` | 200 `{"status":"ok"}` |
| `GET /readyz` | 200 `{"status":"ready"\|"degraded","workers":[{"venv","pid","alive"}]}`; 503 `starting`/`draining` |
| `GET /v1/info` | `ProcessInfo{api_version:"1", process, name, goal, commit, runtime_version, inputs_schema, outputs_schema, steps, limits}` |
| `POST /v1/runs` | 202 `RunCreated{run_id, status, links}` (200 on idempotent repeat); 409/413/422/429/503 |
| `GET /v1/runs?status=&limit=` | `{"runs":[RunSummary]}` |
| `GET /v1/runs/{id}?wait=<s≤60>` | `Run` (long-poll) |
| `GET /v1/runs/{id}/outputs` | `{"exit","outputs","error"}`; 409 `not_finished` |
| `GET /v1/runs/{id}/events?after=<n>` | SSE: `event: status` / `event: trace` (data = trace event §3.13) / final `event: end` |

Console script `wynd-supervisor` (never named `wynd`): `serve [--plan PATH] [--host] [--port]` (image ENTRYPOINT,
default CMD), `env-check [--manifest PATH]` (exit 0/1; missing vars on stderr), `schema` (prints the run API JSON
Schema; a test pins `supervisor/run_api.schema.json`), `check-plan <process.lock.yaml>` (spawns every venv worker,
inits all steps, exits non-zero on any import failure; used as the Dockerfile smoke test). Env: `WYND_PLAN`
(default `/opt/wynd/process/process.lock.yaml`; the supervisor reads `ProcessLock.plan`), `WYND_ENV_MANIFEST`
(`/opt/wynd/process/process.env.yaml`), `WYND_HOST=0.0.0.0`, `WYND_PORT=8080`, plus the §3.14 storage vars with
`WYND_DATA_DIR=/var/lib/wynd` set by the base image. `serve` runs `check_env(mode="image")` at start and refuses to
become ready if it fails. Client: `wynd.runtime.supervisor.client.RunApiClient(base_url, *, token=None, timeout_s=30)`
(stdlib; `health, ready, wait_ready, info, submit(inputs, run_id=None, files=None, metadata=None), get, outputs,
events(after=None), run(...)`) is the only run-API client (the controller and triggers use it; no httpx client is
written). `RunRequest.metadata` follows §3.14 "Run metadata". **`SUPERVISOR_ENV: list[EnvVar]`** (in
`wynd.runtime.supervisor.schema`; W0 writes it complete): `WYND_RUN_API_TOKEN` (secret), `WYND_HOST`, `WYND_PORT`,
`WYND_MAX_CONCURRENT_RUNS`, `WYND_MAX_QUEUED_RUNS`, `WYND_RUN_RETENTION`, `WYND_DRAIN_TIMEOUT_S`, `WYND_MAX_BODY_MB`,
each `required: false` with its documented default and `used_by: ["runtime"]`; it is part of every env manifest.

### 3.18 Jobs (SPEC §6.6; types in `wynd.process.jobs`, runners/harness in `wynd.controller.jobs`)
```python
JobKind = Literal["compile", "test_live", "build", "bake", "optimise"]
JobStatus = Literal["queued", "running", "awaiting_input", "succeeded", "failed", "cancelled"]
TERMINAL: frozenset[str] = frozenset({"succeeded", "failed", "cancelled"})   # awaiting_input = stable wait state
class JobRecord(BaseModel):                  # stored in RunRegistry with kind "job"
    id: str                                  # new_id("job")
    kind: Literal["job"] = "job"
    job_kind: JobKind
    process: str
    ref: str                                 # full sha the checkout is made at (updated on requeue)
    base_commit: str                         # sha at first submit (compile squashes onto it)
    target_branch: str | None = None         # branch to integrate into (commit-producing kinds)
    workspace_rel: str = ""                  # workspace path inside the repo, e.g. "examples/invoices"
    inputs: dict[str, Any]
    status: JobStatus
    runner: str; handler: str                # "module:function", fixed at submit
    attempt: int = 1
    created_at: datetime; updated_at: datetime
    started_at: datetime | None = None; finished_at: datetime | None = None
    duration_ms: int | None = None; cpu_ms: int | None = None; pid: int | None = None; host: str | None = None
    result_branch: str | None = None; result_commit: str | None = None
    artefacts: dict[str, Any] = {}           # kind-specific (build: commit, image, image_digest, build_dir, pushed, tests,
                                             # sizes {build_dir_bytes, image_bytes, wheels_bytes}; bake: path, size_bytes;
                                             # phased build: prepared (Prepared JSON) between the two phases)
    report: dict[str, Any] | None = None     # compile report / TestReport / build summary / optimise result
    session: dict[str, Any] | None = None    # compile SessionData JSON (compiler-owned format)
    questions: list[dict] = []               # pending questions (copied from the session for listing)
    usage: JobUsage = JobUsage()             # model usage only; wall/cpu time live in duration_ms/cpu_ms
    error: dict[str, Any] | None = None      # {"message", "detail"}
    integration: dict[str, Any] | None = None   # IntegrationResult JSON once integrated
    chat_id: str | None = None
@dataclass
class JobContext:                            # built by the harness; everything a handler may use
    job: JobRecord
    inputs: dict[str, Any]                   # = job.inputs
    session: dict | None                     # = job.session from the previous attempt (resume)
    worktree: Path                           # git toplevel of the job checkout (detached at job.ref)
    workspace: Path                          # worktree / job.workspace_rel
    workspace_root: Path                     # the USER's workspace (shared .wynd/venvs, .wynd/build)
    state_dir: Path                          # workspace_root / ".wynd"
    scratch: Path                            # state_dir / "jobs" / <id> / "scratch" (outside the checkout)
    runs: RunRegistry
    registry: Registry                       # user registry
    log: Callable[[str], None]               # appended to .wynd/jobs/<id>/job.log with "HH:MM:SS " prefix
    commit: Callable[[str, Sequence[str] | None], str | None]
        # commit(message, paths=None): stage paths (None = reference closure of job.process at the checkout, incl.
        # deletions), commit on the detached HEAD with identity "Wynd <wynd@localhost>" unless the repo configures one,
        # append trailer "Wynd-Job: <id>"; returns the new sha, or None if nothing staged
    save_session: Callable[[dict], None]     # checkpoint the session into the record (crash safety, live web view)
class JobOutcome(BaseModel):
    status: Literal["succeeded", "failed", "awaiting_input"]
    commit: str | None = None                # the harness publishes it as the job's result branch
    artefacts: dict[str, Any] = {}
    report: dict[str, Any] | None = None
    session: dict[str, Any] | None = None    # required when awaiting_input
    questions: list[dict] = []
    error: str | None = None
    usage: JobUsage = JobUsage()
class JobUsage(Usage):                       # Usage = wynd.runtime.usage.Usage (§3.13): totals over the job
    by: dict[str, Usage] = {}                # "<provider>/<tier>" -> Usage
JobHandler = Callable[[JobContext], JobOutcome]
class JobRunner(Protocol):                   # SPEC §6.6's four operations + cancel + requeue
    name: str
    def submit(self, kind: JobKind, ref: str, inputs: Mapping[str, Any]) -> str
    def status(self, job_id: str) -> JobRecord
    def logs(self, job_id: str, offset: int = 0) -> tuple[str, int, bool]      # (text, next byte offset, done)
    def artefacts(self, job_id: str) -> dict[str, Any]
    def cancel(self, job_id: str) -> None
    def requeue(self, job_id: str, ref: str, inputs: Mapping[str, Any]) -> None  # answering = resubmit the SAME job
```
- `submit` requires `inputs["process"]`; commit-producing kinds (`compile`, `test_live`, `optimise`) also
  `inputs["target_branch"]`. `JobService.submit` is the **only** place that resolves the current branch (raising
  `DetachedHead`); `new_job_record` takes `target_branch` from `inputs["target_branch"]` (required for those kinds,
  `ValueError` otherwise, `None` for build/bake) and never reads a checkout's branch. Handler table (controller
  `jobs/handlers.py`): `compile → wynd.compiler.jobs:run_compile_job`, `test_live →
  wynd.process.testing:run_test_live_job`, `build → wynd.process.build.job:run_build_job`, `bake →
  wynd.process.bake:run_bake_job`, `optimise → wynd.controller.optimise:run_optimise_job`; phased execution (kube build
  Jobs): `PHASE_HANDLERS = {"build": {"prepare": "wynd.process.build.job:prepare_build_job", "finalize":
  "wynd.process.build.job:finalize_build_job"}}`.
- **Harness contract** (`wynd.controller.jobs.harness.execute_job(job_id, *, phase: Literal["all", "prepare",
  "finalize"] = "all", checkout: CheckoutBackend, stores, registry, handlers=None)`; `$DRAFTS/06 §6.4` adopted with these
  rules): load record; `status=running`; checkout via `CheckoutBackend.prepare` (local: `git worktree add --detach
  .wynd/jobs/<id>/checkout-<attempt> <ref>`; kube: clone); build `JobContext`; run the handler (`phase="all"`: the
  handler table; `"prepare"`: `PHASE_HANDLERS[kind]["prepare"]`, whose `Prepared` result is stored as
  `artefacts["prepared"]` while the record **stays `running`**; `"finalize"`: `PHASE_HANDLERS[kind]["finalize"]`, which
  reads `artefacts["prepared"]` and returns the `JobOutcome`; both phases use `$WYND_BUILD_CONTEXT_DIR` as the shared
  build-context dir); if `outcome.commit`: publish `result_branch(job_kind, process, id)` at that sha (local:
  `update-ref`, worktrees share refs; clone: `git push -f origin <sha>:refs/heads/<branch>`), set
  `result_branch/result_commit`; fold status/artefacts/report/session/questions/error/usage into the record; remove the
  checkout on success/awaiting_input, **keep it on failure**; handler exception → `failed` with traceback in the log.
- **Answering:** `JobService.answer(id, answers)` calls `wynd.controller.compile_view.apply_answers(job, answers) ->
  (session JSON, ready)` (CTL-M3; a W0 stub raising `NotImplementedError` until then; CTL-JOBS tests fake it), stores
  the returned session and questions; when `ready` → `runner.requeue(id, ref=job.result_commit or job.ref,
  inputs={**inputs, "answers": {**old, **new}})` (same job id, `attempt+1`, same branch name). Partial answers just
  update the record. `JobService` never imports the compiler.
- **Integration** (one algorithm, `wynd.process.git.integrate`; the controller only wraps it with the git lock,
  optional remote fetch, test-result re-keying and record update):
```python
class IntegrationResult(BaseModel):
    mode: Literal["fast_forward", "rebased", "pr_branch", "noop"]
    branch: str; target: str; head: str | None = None
    skipped_commits: int = 0; conflicts: list[str] = []; reason: str | None = None; at: datetime
    pr_url: str | None = None                # always None in v1 (no PR is opened; `reason` carries the gh hint)
def integrate(ws_root: Path, *, process_id: str, base_sha: str, branch: str, target_branch: str,
              scratch_dir: Path) -> IntegrationResult
```
  Algorithm = `$DRAFTS/04 §13.8` verbatim with: closure = union of `reference_closure` at base, target tip and branch
  tip; the rebase rule is **per intervening commit** (`git log base..tip -- <closure>` empty ⇒ rebase in a scratch
  worktree; any hit ⇒ `pr_branch`); the user's checkout is only ever fast-forwarded (`merge --ff-only` in whichever
  worktree has the target checked out, else `update-ref` CAS); the result branch is deleted after `fast_forward`/
  `rebased`, kept on `pr_branch`. Integration applies to succeeded `compile`, `test_live`, `optimise` jobs; idempotent.
  After `rebased`, the controller copies test results recorded for the old tip to the new commit (same closure
  content ⇒ same keys).

### 3.19 Reference closure, process HEAD and hashing
- **Reference closure** `reference_closure(ws, pid) -> list[str]`: `wynd.yaml` + the process dir + each root-step
  package dir it references + each child process's closure (transitively); workspace-relative, sorted, ancestors-only.
- **Process HEAD** `closure_head(ws_root, paths, ref="HEAD") = git log -1 --format=%H <ref> -- <paths>` (computed with
  the closure at that ref via `CommitTree`). "This commit" for build's tests-passed check and every status/test key
  is the closure HEAD.
- **Dirty check** `dirty_paths(ws_root, paths: Sequence[str] | None)`: `git status --porcelain=v1 -z
  --untracked-files=all -- <pathspecs>` where `paths=None` means the **whole workspace directory** (pathspec `.` from
  `ws_root`; untracked-but-not-ignored files count; ignored files and `.wynd/` never do). `wynd build`, `bake`,
  `compile`, `test --live` and `optimise --apply` refuse when `dirty_paths(ws_root, None)` is non-empty (SPEC §6.5,
  §10, §13: "refuses a dirty tree"; when the workspace is the repo root — the SPEC case — that is the whole repo).
  The closure-scoped form `dirty_paths(ws_root, reference_closure(...))` is used for one thing only: deciding whether
  `wynd test` results may be recorded (§3.20). The web commits the open process before any job, so it stays clean.
- **Trees:** the process loader reads through `Tree` = `WorkingTree(ws_root)` (`git ls-files -co --exclude-standard`,
  `.wynd/` always excluded) or `CommitTree(ws_root, sha)` (`git ls-tree -r`, `cat-file`). Blob ids: `CommitTree` from
  `ls-tree`; `WorkingTree` via one batched `git hash-object --stdin-paths` (**applies clean filters**, so git-lfs
  cassettes hash to their pointer blob exactly as in a commit). Workspaces must be inside a git repository (`E101`) and
  may be a subdirectory of it (`examples/invoices`); all git calls run with `-C <ws_root>` and workspace-relative
  pathspecs.
- **Hashes** (`"sha256:" + hex`): spec owns `canonical_json(obj) -> bytes` (sorted keys, `(",", ":")`,
  `ensure_ascii=False`, `allow_nan=False`, `jsonable` conversions per `$DRAFTS/01 §8`), `hash_bytes`, `hash_obj`,
  `proto_hash(proto)` (normalised model, `$DRAFTS/01 §8`: formatting/comments/key order/flat-vs-nested do not change
  it), `interface_hash(iface)` (normalised schemas), `normalize_requirement(req)` and `dependency_set_hash(reqs,
  python=None)`. Process owns Tree-based `tree_hash(tree, dir)` (`sha256` over sorted `"<rel>\0<blob id>\n"`),
  `step_hash(tree, pkg) = tree_hash(pkg.dir)`, `process_hash(tree, lp) = hash_obj({"dir": tree_hash(process dir),
  "steps": {name: {"id", "hash"} for root-step bindings}, "children": {cid: process_hash(child)}})` — a parent's hash
  includes every child's (SPEC §5.1).

### 3.20 Tests, test results and the test runner (SPEC §6.3, §6.5, §7, §9)
- **Step tests** are ordinary pytest files in the package (`test_<name>.py`), one test per example
  (`test_example_<n>`, n = 1-based example position) plus confirmed edge cases; they run in the step's local venv under
  `wynd test`. Format (`$DRAFTS/05 §9.3.1` generation rules adopted):
```python
from pathlib import Path
from wynd.runtime.testing import expect, load_step, run_step, sub_tmp
ExtractInvoiceFields = load_step(Path(__file__).parent)
BASE = Path(__file__).resolve().parents[2]            # process dir (step-root steps: parents[0])
def test_example_1(tmp_path):
    result = run_step(ExtractInvoiceFields, {"invoice_text": "…"}, workspace=tmp_path)
    expect(result, exit="done", outputs={"invoice_number": "INV-1042", "total": 1200.5, "currency": "GBP", "due_date": "2026-10-01"})
```
- `wynd.runtime.testing` (runtime): `load_step(step_dir, cls=None) -> type[Step]`, `run_step(step, input, *, mode=None
  (env WYND_CASSETTE_MODE or "replay"), cassettes=None (<pkg>/cassettes), lock=None (<pkg>/step.lock.yaml), context=None,
  workspace=None, retry=None) -> StepResult`, `expect(result, *, exit, outputs=None, present=())`,
  `match_outputs(expected, actual, *, present=()) -> list[Mismatch]`, `sub_tmp(value, tmp)`. In `record` mode
  `run_step` writes to `$WYND_CASSETTE_RECORD_DIR` if set, else `<pkg>/cassettes`. The default provider in `run_step`
  is `$WYND_DEFAULT_PROVIDER` or `DEFAULT_PROVIDER` (the test runner sets it from the root `process.yaml`).
  `$WYND_CASSETTE_LITERALS` (JSON object) feeds `CassetteConfig.literals` (§3.16). If `WYND_EVENTS_FILE` is set, every
  emitted event is appended to it as a JSON line. A `cassette_miss` error re-raises `CassetteMissError`. **Comparison semantics** (`match_outputs`, used by step tests AND process examples):
  `$DRAFTS/05 §9.3.2` verbatim — exit exact; outputs subset match; nested mappings recursive subset; lists same
  length element-wise; bool exact; numbers `math.isclose(rel_tol=1e-9, abs_tol=1e-9)`; strings exact (dates as ISO);
  `present` fields must exist and be non-empty; `None` expected ⇒ actual `None` or absent. `expect` raises
  `AssertionError` whose first line is `WYND-EXPECT <json>` (`expected_exit, actual_exit, mismatches, error`).
- **Process examples are integration tests run directly from `process.yaml`** by the test runner (no generated
  process test file): relative `path` inputs resolve against the process dir, `{tmp}` substitution, example `env:`
  merged over the run env, a fixed `run_id = f"run-example-{n}"`, the local executor (venv workers), cassettes at
  `<process dir>/cassettes/<step_module_name>/`, `cassette_literals` per §3.16 (`<tmp>`, `<process>`, `<ws>`), pass iff
  `exit` matches and `match_outputs(example.outputs, result.outputs)` is empty — **except** that a case is `error`
  with the exact `NO_RECORDING` text (key and request dump from the event) whenever any `step.end` of that example's
  events has `exit == "error"` and `outputs.cause == "cassette_miss"`, or any `edge.check` has `error_cause ==
  "cassette_miss"`, whatever the final exit (so a miss behind an `X.error` edge, an `on_error` handler or an
  `exit: error` example can never pass). Every event is also appended to `$WYND_EVENTS_FILE` when that is set in
  `env`. The examples of **every process in the reference closure** run (root first, then children in closure order),
  one suite per process (`subject = "process:<id>"`), because children run inside the parent's image.
- **Test runner** (`wynd.process.testing`, one owner):
```python
class TestCase(BaseModel): name: str; outcome: Literal["passed","failed","error","skipped"]; message: str | None = None; duration_ms: int
class SuiteResult(BaseModel): subject: str; hash: str; passed: bool; counts: dict[str, int]; cases: list[TestCase]; problem: str | None = None
class TestReport(BaseModel): process: str; commit: str | None; process_hash: str; mode: Literal["replay","live"]
    passed: bool; suites: list[SuiteResult]; started_at: datetime; finished_at: datetime; recorded: bool = False
def run_tests(ws: Workspace, pid: str, *, mode: Literal["replay", "live"], commit: str | None, runs: RunRegistry | None,
              venv_root: Path, scratch: Path, env: Mapping[str, str] | None = None, log=None) -> TestReport
def run_step_suite(pkg_dir: Path, *, python: Path, mode: Literal["replay","record"], env: Mapping[str, str],
                   junit: Path, basetemp: Path, record_dir: Path | None = None, timeout_s: int = 1800) -> SuiteResult
def run_process_examples(ws, pid, *, mode: Literal["replay","record"], env, scratch: Path, venv_root: Path,
                         record_root: Path | None = None, log=None) -> SuiteResult     # one process's examples
def tests_status(runs: RunRegistry, ws: Workspace, pid: str, commit: str) -> dict   # {"status": "passed"|"failed"|"missing", "steps": {...}}
def run_test_live_job(ctx: JobContext) -> JobOutcome
```
  `run_tests` algorithm = `$DRAFTS/04 §7` (plan, per-package interface-drift check via `python -m
  wynd.runtime.describe`, pytest per compiled package in its venv, exit code 5 = "no tests collected" = failure) +
  process examples of every closure process. **Step suites are hermetic:** `run_step_suite` writes
  `<scratch>/<suite>/pytest.ini` containing only `[pytest]\naddopts = -p no:cacheprovider --import-mode=importlib` and
  runs `python -m pytest -q -c <that ini> --rootdir <pkg> --confcutdir <pkg> --basetemp <scratch>/… --junitxml
  <scratch>/… <pkg>` with `PYTHONDONTWRITEBYTECODE=1`, `WYND_CASSETTE_LITERALS` (`<process>`, `<ws>`), so the wynd
  repo's own root config/conftest never applies (the dogfood behaves exactly like a user workspace). Live mode: record
  into staging dirs under `scratch`, promote into the tree only for suites that passed, then re-run those suites in
  replay (cassette check). Results recorded only when `commit` and `runs` are given and the **closure** is clean:
  `step:<id>:<step_hash>` per step suite, `process:<cid>:<process_hash(child)>` per **child** process suite, and
  `process:<pid>:<process_hash>` for the whole report (passed iff every suite passed, the root's examples included).
- `run_test_live_job`: `run_tests(mode="live")` in the job checkout; commit changed cassettes of the suites that passed
  (`ctx.commit("wynd test --live: re-record cassettes for <pid>")`); then `run_tests(mode="replay", commit=<closure head
  of the new commit>, runs=ctx.runs)` so the recorded results match the commit that integration fast-forwards to;
  status `succeeded` iff every live suite passed, else `failed` (the branch is still published for inspection, never
  integrated).

### 3.21 Controller HTTP API (`wynd serve-api`, the web contract)
Adopt `$DRAFTS/07 §12` (routes 1–39, DTOs §12.2, save semantics §12.3, chat SSE §12.4, run SSE §12.5, error envelope
§12.0) **plus** the `(+)` routes of `$DRAFTS/06 §8.3`, with these amendments (they override the drafts):
1. `Issue.severity` is `"error" | "warning" | "info"`; `Issue = Diagnostic.to_json()` subset `{severity, code,
   message, file, loc, span}`.
2. Path-typed schema fields carry `format: "path"` (no `x-wynd-type`).
3. `JobKind` = `"compile" | "test_live" | "build" | "bake" | "optimise"` (DTO `Job.kind` = `JobRecord.job_kind`; `Job.process_id` = `JobRecord.process`; `Job.branch`/`result_commit` = `result_branch`/`result_commit`); `Integration.mode` =
   `"fast_forward" | "rebased" | "pr_branch" | "noop"`.
4. `TraceEvent` in the web is exactly §3.13 (types `run.start`, `step.start`, `step.end`, `edge.taken`, `edge.check`,
   `model.call`, `tool.call`, `step.log`, `step.event`, `process.error`, `worker.start`, `run.end`); step path is the
   `step` field. The controller passes §3.13 events through **unchanged** (`$DRAFTS/06 §5.10`'s addition of
   `mode`/`path` to every event is not adopted); the TS type is `TraceEvent = {v: number, seq: number, ts: string,
   run_id: string, type: string, [k: string]: Json}` with per-type narrowing helpers. Run SSE: `id: <seq>`,
   `event: trace`, data = event; then `event: end`.
5. `POST /api/releases/{id}/trigger` body `{"inputs"?: object, "source"?: "manual"|"schedule"}` (kube CronJobs send
   `{"source": "schedule"}`); webhooks at `POST /hooks/releases/{id}`.
6. `ProcessError` DTO = §3.8 JSON.
7. Uploads: `POST /api/uploads` (raw body, `X-Wynd-Filename`) stores `.wynd/uploads/<sha256>/<name>` and returns
   `{"path"}`; local runs read the path. **One rule for image and release targets** (`runs/image.py`, CTL-M2): every
   top-level `path`-typed input (per `ProcessService.interface(pid)`) whose value names an existing host file —
   uploads, workspace files, anything — is read and sent as run-API `files[<sha256(content)[:8]>-<basename>]` (basename id-sanitised: characters outside `[A-Za-z0-9_.-]` become `_`, cut to 128 chars, since the supervisor accepts only id-valid names), and the
   input is replaced by `{"$file": <that name>}`; values that do not name an existing file pass through unchanged. No
   upload mounts (the only container-bound data are env vars, §3.10).
8. MCP registry + OAuth routes (M4): `GET/POST /api/registries/mcp`, `DELETE /api/registries/mcp/{name}`,
   `POST /api/registries/mcp/{name}/oauth/start` → `{"authorize_url"}`, `GET /api/oauth/callback?code&state`;
   providers `GET/POST /api/providers` (body `{name, tiers?}` — no `env` field; secrets are set in `.env` or via
   OAuth), `DELETE /api/providers/{name}`; image registries `GET/POST /api/registries/images`,
   `DELETE /api/registries/images/{name}`.
9. `CompileSession` DTO = the compiler `SessionData` projection (`$DRAFTS/05 §6.5` shape; question kinds
   `example_proposal`/`clarification`; `AnswerRequest` → answer text mapping `confirm→"accept"`, `reject→"reject"`,
   `correct+example→json.dumps(example)`, `text→text`).
10. Usage: the web `Usage` DTO = §3.13 `Usage` (`input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
    cost_usd, latency_ms, calls`); `Run.usage: Usage | null`; `Job.usage: JobUsage` (§3.18).
11. `Run.status` = `RunRecord.status` (`queued|running|succeeded|failed`; `finished`/`failed_to_start` are dropped —
    a run that failed to start is `failed` with `error.cause = "internal"`); `Run.finished_at` = `RunRecord.finished_at`.
12. `Integration` DTO = `IntegrationResult` JSON (incl. `reason` and `pr_url`, always null in v1);
    `BuildResult.build_dir` (not `artefact_dir`) = `JobRecord.artefacts.build_dir`; `ProcessStatus.tests ∈
    {"passed","failed","unknown"}` with `tests_status(...)["status"] == "missing"` mapped to `"unknown"`;
    `Meta.latency` = `LATENCIES` (`["fast","normal"]`); `Issue.file: string | null`.
13. Name collisions are avoided in the controller: the web DTOs are `ValidationReportDTO{ok, issues}` (built from
    `wynd.process.ValidationReport`: `ok`, `issues = [Issue(d) for d in diagnostics]`), `EnvCheckDTO{ok, missing,
    unbound, issues}` (built from spec `EnvCheck`: `missing` = names of `E-ENV-MISSING`/`E-ENV-ONE-OF` findings,
    `unbound` = names of `W-ENV-ONE-OF` findings, `issues` = all diagnostics) and `ProviderInfoDTO{name, kind, tiers,
    ready, message}` (built from `provider_info` + `provider_tiers` + `check_provider`). The controller maps
    `wynd.process.errors.ValidationFailed` to its own `wynd.controller.errors.ValidationFailed` (code, HTTP 422, exit 1).
14. Process file reads: `GET /api/processes/{process_id:path}/files?path=<workspace-relative path>` (query parameter,
    so neither ids nor file paths are split on a `files` segment).
15. Placement: M1/M2 DTOs and controller models live in `wynd/controller/models.py` (CTL-CORE); the M3/M4 web DTOs
    (`$DRAFTS/07 §12.2` + these amendments) in `wynd/controller/api/models_web.py` (written complete by W0, owned by
    CTL-API afterwards). `$DRAFTS/07 §12.2` as amended here is authoritative: whichever side (controller model or
    `src/api/types.ts`/fixtures) deviates fixes its own file. `api/app.py` includes `wynd.controller.optimise.router`
    (an empty `APIRouter()` until M5) **before** the catch-all process routes.
Server defaults: `127.0.0.1:8780`; SSE headers `Content-Type: text/event-stream`, `Cache-Control: no-cache`,
`X-Accel-Buffering: no`, first line `retry: 2000`, `: ping` every 15 s. Optional bearer auth `WYND_API_TOKEN`
(streams may pass `?access_token=`). CORS for `http://localhost:5173`/`http://127.0.0.1:5173`. Suffixed
`{process_id:path}` routes are registered before `GET /api/processes/{process_id:path}`.

### 3.22 CLI surface and exit codes (`wynd`, SPEC §10)
Commands (ids, never paths): `init [PATH]`, `new <id>`, `validate [<id>] [--sync-interfaces] [--json]`,
`status [<id>]`, `search <q…> [--flag …]`, `compile <id> [--answers FILE] [--answer QID=TEXT]… [--accept-proposals]
[--resume JOB] [--max-revisions N] [--no-wait] [--no-integrate]`, `test <id> [--live]`, `run <id> [--local|--image]
[--commit SHA] [--input K=V]… [--inputs JSON] [--inputs-file F] [--full]`, `trace <run_id> [--full] [--follow]`,
`build <id> [--registry NAME] [--push/--no-push] [--platform P]`, `bake <id> [--output PATH]`,
`env check <id> [--env-file F]…`, `serve <image> [--port] [--env-file]… [-d] [--stop]`, `serve-api [--host] [--port
8780] [--web-dist] [--no-scheduler] [--token]`, `mcp|provider|registry add|list|remove`, `base build|publish <ver>
[--variant slim|alpine|all] [--registry NAME]`, `jobs list|show|logs|answer|cancel|integrate`, `release
create|list|show|run|enable|disable|delete|fires`, `optimise <id> [--apply] [--unit …] [--max-runs] [--min-runs]`.
Every data command accepts `--json` (exactly one JSON document on stdout). `path`-typed `--input` values are made
absolute against the cwd. Exit codes: `0` ok (incl. a run ending in a declared non-error exit), `1` operation failed
(validation errors, failing tests, failed job, run ended `$exit.error`, `wynd env check` reporting missing vars),
`2` usage/`Invalid`, `3` precondition/lookup/conflict (not a workspace, unknown id, dirty workspace, detached HEAD, not
built, a run refused by the env gate (`EnvMissing`, HTTP 412), Docker/provider unavailable, version mismatch), `4`
compile stopped at `awaiting_input`, `5` job succeeded but left
for review (`pr_branch`), `130` interrupted. The CLI imports only `wynd.controller`, `typer`, `yaml` (AST test).

### 3.23 Environment variables (all of them)
| Var | Default | Read by |
|---|---|---|
| `WYND_HOME` | `~/.wynd` (only when unset) | runtime user Registry |
| `WYND_DATA_DIR` | caller-supplied (`<ws>/.wynd` locally, `/var/lib/wynd` in images) | runtime storage |
| `WYND_WORKSPACE_STORE` / `WYND_TRACE_SINK` / `WYND_RUN_REGISTRY` / `WYND_REGISTRY` | `file` / `jsonl` / `file` / `file` (`env` in images) | runtime storage |
| `WYND_REGISTRY_JSON` | – | runtime `registry.env` backend (§3.10; supplied by the controller for image/release runs and serving) |
| `WYND_MCP_<NAME>_URL` (`NAME = name.upper().replace("-", "_")`) | registry URL | `build_policy` (overrides an http MCP entry's `url`) |
| `WYND_MCP_<NAME>_TOKEN` (same `NAME` rule) | – | MCP entries created by OAuth reference it (`${env:…}`); stored with `Registry.put_secret` |
| `WYND_CASSETTE_MODE`, `WYND_CASSETTE_RECORD_DIR`, `WYND_CASSETTE_LITERALS`, `WYND_DEFAULT_PROVIDER`, `WYND_EVENTS_FILE` | `replay`, –, –, –, – | `run_step` (tests only; not in manifests); `WYND_EVENTS_FILE` also `run_process_examples` |
| `WYND_FAKE_PROVIDER_SCRIPT` | – | fake provider (path, or inline JSON when it starts with `{`/`[`) |
| `WYND_INPUT` | – | set by `run_shell` for the ShellStep child (the bound inputs as JSON) |
| `WYND_BASE_VERSION`, `WYND_BASE_VARIANT` | set by the base image | informational (`/v1/info`, labels) |
| `WYND_PLAN`, `WYND_ENV_MANIFEST`, `WYND_HOST`, `WYND_PORT`, `WYND_MAX_CONCURRENT_RUNS`, `WYND_MAX_QUEUED_RUNS`, `WYND_RUN_RETENTION`, `WYND_RUN_API_TOKEN`, `WYND_DRAIN_TIMEOUT_S`, `WYND_MAX_BODY_MB` | see §3.17 (`SUPERVISOR_ENV`) | supervisor. The controller reads `WYND_RUN_API_TOKEN` from its own resolved env (`EnvService.resolve`), passes it to `RunApiClient(token=…)` and into the container env (`wynd serve`, `ServingBackend.ensure`) |
| `CLAUDE_AGENT_SDK_CLIENT_APP` (`wynd/<version>`), `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC` (`1`) | set per call | claude-code provider → the Claude Code CLI (§1.4) |
| `WYND_WORKSPACE` | cwd search for `wynd.yaml` | controller/cli |
| `WYND_RUNTIME_SOURCE` | detected (editable monorepo or installed version) | process venvs/build |
| `WYND_UV` | `uv` on PATH | process |
| `WYND_IMAGE_BUILDER` (`buildx`), `WYND_ARTEFACT_STORE` (`local`), `WYND_BASE_REPO` (`wynd-base`) | as shown | process build |
| `WYND_JOB_RUNNER` (`subprocess`; tests use `inprocess`), `WYND_SERVING_BACKEND` (`docker`), `WYND_TRIGGER_BACKEND` (`scheduler`), `WYND_GIT_REMOTE` (unset) | as shown | controller |
| `WYND_API_TOKEN`, `WYND_CORS_ORIGINS`, `WYND_WEB_DIST` | – | controller API |
| `WYND_COMPILER_PROVIDER` (`claude-code`) | – | compiler |
| `WYND_CHAT_PROVIDER` (`claude-code`), `WYND_CHAT_TIER` (`standard`) | – | controller chat |
| `WYND_BUILD_CONTEXT_DIR` | – | build prepare/finalize (kube) |
| `WYND_KUBE_*` | `$DRAFTS/08 §5.2` | kube |
| `WYND_LIVE`, `WYND_DOCKER`, `WYND_KUBE` | – | test gates |
| `CLAUDE_CODE_OAUTH_TOKEN`, `ANTHROPIC_API_KEY`, `ANTHROPIC_BASE_URL`, `BRAVE_API_KEY` | – | providers / web_search tool |

### 3.24 On-disk state (`<workspace>/.wynd/`, git-ignored)
```
.wynd/
  workspaces/<run_id>/            runtime WorkspaceStore (cwd of every step run)
    .wynd/steps/<step path>/<n>/{inputs,outputs}.json   raw outputs addressable (SPEC §3.7)
  traces/<run_id>.jsonl           runtime TraceSink
  registry/{records,tests}/…      runtime RunRegistry (runs + jobs + test results)
  venvs/<id>/                     process local venvs (marker wynd-venv.json written last)
  build/<pid>/<commit>/           process ArtefactStore: Dockerfile, process.lock.yaml, process.env.yaml, dist/, venvs/*/requirements.txt, build.json
  tmp/                            process build staging
  jobs/<job id>/{job.log,scratch/,checkout-<attempt>/}   controller job harness
  integrate/<job id>/             controller scratch rebase worktree
  locks/{git.lock,scheduler.lock} controller
  serve/<container>.json          controller warm/release containers (incl. started_at/stopped_at uptime, SPEC §15)
  uploads/<sha256>/<name>         controller uploads
  chats/<chat id>/                controller chat turn cwd (empty)
  controller/{releases,fires,chats}/<id>.json   controller DocStore
```

---

## 4. Package `wynd.spec` (`packages/spec`, wynd-spec, Apache-2.0) — the shared leaf

Owns every document format, the type language, the expression language, runtime records and canonical hashing. No
I/O beyond reading a file/directory it is given; never network, Docker, git or LLM. Dependencies: pydantic, pyyaml,
lark only. Detailed behaviour: adopt `$DRAFTS/01` §4–§8 (loader algorithm, diagnostics wording, type language,
normalisation table, expression grammar/evaluator/analysis, hashing) **with the changes listed in §3 of this plan**
(Diagnostic fields/severity, `$ignore`, `check`/`context`, no `ui`, `Example.env`, `EnvFragment.groups`,
`StepLock.builtin_tools/max_turns/compiled`, optional `interface`, `RetryPolicy` defaults, `StepError`/`ProcessError`
shapes, `RunPlan`/`ProcessLock` shapes, no volatile tracking, `step_module_name`, reserved process segments,
`path` format).

### 4.1 Modules
| Path (`src/wynd/spec/`) | Responsibility | Unit |
|---|---|---|
| `__init__.py` | curated re-exports (§4.2) + `__version__` | W0 |
| `base.py` | `SpecModel`; identifier aliases (`Name`, `StepKey`, `ExitName`, `FieldName`, `EnvName`, `Alias`, `ProcessId`, `BranchName`, `HashStr`); constants `DEFAULT_PROVIDER="claude-code"`, `DEFAULT_TIER="cheap"`, `DEFAULT_THINKING="low"`, `DEFAULT_MAX_TRAVERSALS=10`, `TIERS`, `RESERVED_EXIT="error"`, `EXIT_TARGET_PREFIX="$exit."`, `IGNORE_TARGET="$ignore"`, `EDGE_KINDS=("deterministic","agentic")`, `PROTO_TYPES`, `BASES`, `LATENCIES=("fast","normal")`, `LIMIT_FIELDS=("max_traversals","timeout","retries")` | SPEC-CORE |
| `errors.py` | `Diagnostic`, `SpecError`, `format_loc` (§3.2) | SPEC-CORE |
| `yamlio.py` | `WyndLoader` (YAML 1.2 bools, dup keys), `Mark`, `SourceMap`, `parse_yaml`, `read_yaml`, `parse_model`, `load_model`, `dump_yaml` (`$DRAFTS/01 §4.3`); `yaml_to_json(text, file) -> tuple[dict | None, Diagnostic | None]` (JSON-safe: dates → ISO strings, int keys kept as ints; used by the controller for web design docs) | SPEC-CORE |
| `typelang.py` | `TypeNode` (`TScalar`, `TList`, `TObject`), `parse_type`, `render_type`, `TypeSpec`, `type_schema`, `fields_schema`, `ModelSet`, `build_models`, `normalise_outputs`, `describe_type`, `describe_fields`, `infer_fields` | SPEC-CORE |
| `schemas.py` | `normalize_schema`, `schema_at`, `is_nullable`, `strip_null`, `ANY`, `MISSING` | SPEC-CORE |
| `interface.py` | `Interface`, `split_output`, `output_adapter`, `interface_from_models`, `interface_from_fields`, `interfaces_equivalent` | SPEC-CORE |
| `workspace.py` | `WorkspaceConfig`, `StepRoot`, `load_workspace_config`, layout constants, `UseRef`, `parse_use`, `find_workspace_root(start)` (upward search only; `wynd.process.workspace.find_workspace_root` wraps it and adds `WYND_WORKSPACE`), `RESERVED_PROCESS_SEGMENTS`, `local_step_id`, `root_step_id`, `step_module_name`, `slug` (§3.1) | SPEC-CORE |
| `fragments.py` | `EnvVar`, `EnvGroup`, `Mode`, `EnvFragment`, `merge_env_vars`, `merge_fragments` | SPEC-CORE |
| `proto_step.py` | `Example`, `ProtoStep`, `load_proto_step`, `check_proto_step` | SPEC-CORE |
| `process_doc.py` | `ProcessEnv`, `StepRef`, `RetryOverride`, `Limits`, `Branch`, `Edge`, `FinallyStep`, `ProcessDoc`, `is_else`, `ExprSite`, `expression_sites`, `site_position`, `load_process`, `check_process_doc` | SPEC-CORE |
| `lockfiles.py` | `StepKind`, `TraceStepKind`, `HARNESS_BUILTINS`, `Tier`, `Thinking`, `Effect`, `RetryPolicy`, `DEFAULT_RETRIES`, `effective_retries`, `ToolSnapshot`, `McpToolSnapshot`, `McpSnapshot`, `ShellLock`, `StepLock`, `EdgeLockEntry`, `EdgesLock`, `branch_key`, `check_hash`, `BaseChoice`, `FragmentRecord`, `LockedWheel`, `LockedVenv`, `ProcessLock`, `load_step_lock`, `load_edges_lock`, `load_process_lock`, `dump_lock(model) -> str` | SPEC-CORE |
| `plan.py` | `PlanVenv`, `PlanStep`, `PlanNode`, `PlanProcess`, `RunPlan` | SPEC-CORE |
| `env_manifest.py` | `EnvManifest`, `EnvCheck`, `check_env`, `load_env_manifest` | SPEC-CORE |
| `records.py` | `Summary`, `StepErrorCause`, `StepError`, `ProcessErrorCause`, `TracePointer`, `ProcessError`, `STEP_ERROR_SCHEMA` | SPEC-CORE |
| `context.py` | `ContextRef(kind, step, path)`, `parse_context_entry` — accepted: `full_trace`, `process.goal`, `process.inputs`, `previous.summary`, `previous.outputs`, `steps.<k>.outputs[.<f>…]`, `steps.<k>.summary` (`E-CONTEXT` otherwise) | SPEC-CORE |
| `hashing.py` | `jsonable`, `canonical_json`, `hash_bytes`, `hash_obj`, `proto_hash`, `interface_hash`, `normalize_requirement`, `dependency_set_hash` | SPEC-CORE |
| `expr/__init__.py` | re-exports | W0 |
| `expr/grammar.py`, `expr/nodes.py`, `expr/errors.py` (`ExprError`, `ExprSyntaxError`, `EvalError`), `expr/scope.py` (§3.5), `expr/evaluator.py` (`parse` lru-cached, `evaluate`, `evaluate_condition`, `evaluate_value`, `evaluate_with`, `evaluate_limit`, `truthy`, `strict_eq`, `BUILTINS`), `expr/analysis.py` (`Ref`, `references`, `value_references`, `env_names`, `check_expression`, `StepView`, `TypeEnv` (01 §7.9 fields + `env_declared: frozenset[str] = frozenset()` from `process.env.vars`), `check_references`) | §3.5 grammar, evaluator, scope, analysis | SPEC-EXPR |
| `expr/infer.py` | `infer_type(expr, env) -> (schema, diagnostics)`, `check_assignable(src, dst) -> (ok\|warning\|error, message)` (`$DRAFTS/01 §7.10`; `env.X` infers `string` when `X ∈ env.env_declared` — the manifest makes it required and `wynd env check` gates runs — else `anyOf[string, null]`) | SPEC-TYPES (M3) |

Notes: `check_process_doc` covers the document-only rules (`E-ENTRY`, `E-STEP-UNKNOWN`, `E-EDGE-DUP`,
`E-EXIT-TARGET`, `E-IGNORE-FORM`, `E-BRANCH-NAME`, `W-BRANCH-UNREACHABLE`, `W-LIMITS-EXIT`, `E-HANDLER-ROLE` (an
`on_error`/`finally` step must not be `entry`, a branch target, or the `from` of an edge), per-site
`check_expression`, `E-EXAMPLE`, `W-OUTPUTS-AMBIGUOUS`); graph and cross-document rules belong to `wynd.process`.
Errors in `check_references` diagnostics carry `span` (character offsets from Lark positions).
`StepError`/`ProcessError` use `model_rebuild()` for the mutual reference.

### 4.2 Public API (`wynd/spec/__init__.py`, written by W0)
Every name in the §4.1 table except the `expr` names; `wynd.spec.expr` re-exports the expr names.

### 4.3 Tests (`packages/spec/tests/`, offline)
Adopt `$DRAFTS/01 §12` test plan verbatim (expression success/error/syntax tables, reference checks, type language,
loader fixtures with `.expected.txt`, hashing, env manifest, records, performance smoke, thread-safety) plus:
`test_leaf.py` (AST: no `wynd.*` imports other than `wynd.spec`; non-stdlib imports ⊆ {pydantic, pydantic_core,
yaml, lark, typing_extensions}); `$ignore`/`check`/`context`/`is_else` cases; `E-HANDLER-ROLE`; `Example.env`;
`StepLock` agentic-only field rules incl. `builtin_tools`; `RunPlan`/`ProcessLock` JSON round-trip;
`step_module_name` stability/uniqueness (`p#read` vs `q#read`, leading digit); `slug` table; `builtin_tools` outside `HARNESS_BUILTINS` (`Bash`, `NotebookEdit`) → `E-LOCK`; `ToolSnapshot.allow` rule; `ProcessDoc.interface()`; `yaml_to_json` round-trip of the
dogfood files (dates → strings, `exit_codes` int keys); `check_env` modes; `branch_key`/`check_hash`.
`test_examples_workspace.py` (loads every YAML in `examples/invoices` with the public loaders) is added by M1-INT. The 01 §12.8 cases for `step_hash`/`process_hash` (incl. LFS pointers) move to `packages/process/tests/test_hashing.py` (PROC-WS); `normalise_volatile`/`Scope.volatile` tests are dropped (§3.5).

---

## 5. Package `wynd.runtime` (`packages/runtime`, wynd-runtime, Apache-2.0) — everything inside an image

Dependencies: `wynd-spec==0.1.0` + stdlib. `claude-agent-sdk` is imported lazily inside the claude-code provider only.
Console script `wynd-supervisor = wynd.runtime.supervisor.main:main`. Entry points: `wynd.providers`
(`claude-code = wynd.runtime.providers.claude_code:ClaudeCodeProvider`, `anthropic = …anthropic:AnthropicProvider`,
`fake = …fake:FakeProvider`), `wynd.storage` (`workspace.file = wynd.runtime.storage.local:workspace_file`,
`trace.jsonl = …:trace_jsonl`, `runs.file = …:runs_file`, `registry.file = …:registry_file`, `registry.env =
…:registry_env`).

### 5.1 Modules
| Path (`src/wynd/runtime/`) | Responsibility | Unit |
|---|---|---|
| `__init__.py` | re-exports `Step, DeterministicStep, AgenticStep, ShellStep, ProcessStep, ShellResult, tool, env, McpServer, StepError, ProcessError, Summary, RuntimeHandle, __version__` lazily (module `__getattr__`, §0 rule 8); must not import executor/storage/worker/supervisor | W0 |
| `step.py` | `Step`, `DeterministicStep`, `AgenticStep`, `ShellStep`, `ProcessStep`, `step_kind` | RT-STEP |
| `interface.py` | `StepInterface`, `interface_of` (cached; calls `wynd.spec.interface.split_output`/`output_adapter` — the §3.4 Output rules have one implementation), `process_interface` (wraps `ProcessDoc.interface()`), `StepDescription`, `describe_step`, `describe_process` | RT-STEP |
| `errors.py` | `StepFailure(cause, message, *, partial_outputs=None, usage=None, attempts=None)`, `StepDefinitionError`, `InvalidProcessInputs` | RT-STEP |
| `usage.py` | `Usage` (§3.13; `__add__`: cost None if both None else sum of known), `ModelInfo(provider, model_id, tier, thinking)` | RT-STEP |
| `policy.py` | `ExecPolicy`, `CassetteConfig` (§3.12 shapes), `build_policy(kind, lock, *, default_provider, registry, environ, retry_override=None) -> ExecPolicy` (raises `StepFailure("config")` for unknown provider/tier/MCP entry; an http MCP entry's `url` is replaced by `environ["WYND_MCP_<NAME>_URL"]` when set, §3.10) | RT-STEP |
| `handle.py` | `RuntimeHandle`, `StepTrace`, `StepCache` (below); `RuntimeHandle.http` raises `StepFailure("config", "step does not declare effects: [network]")` unless `"network" in policy.effects` | RT-STEP |
| `http.py` | `HttpClient`, `HttpResponse`, `HttpError` (urllib; `$DRAFTS/02 §3.6`) | RT-STEP |
| `shell.py` | `ShellResult`, `run_shell` (below) | RT-STEP |
| `summary.py` | `summarise(step, exit, outputs, note="") -> Summary`, `project` (`$DRAFTS/02 §4.4`; 200-char cap, container markers, error summaries omit traceback/inputs/partial/child) | RT-STEP |
| `middleware.py` | `run_chain(cls, params, *, emit, cache) -> ChainResult`; `AgentCall`, `AgentResult` (`$DRAFTS/02 §4.2–§4.3` verbatim, using §3.9 retry semantics: `run` for non-agentic only; agentic validation/transport retries live in the loop) | RT-STEP |
| `testing.py` | `load_step`, `run_step`, `StepResult`, `expect`, `match_outputs`, `Mismatch`, `sub_tmp` (§3.20) | RT-STEP |
| `lint.py` | `check_step_module(source, path) -> list[LintIssue]` (AST only, `$DRAFTS/02 §3.7` with these codes, **all errors**: `L001` `global` anywhere / `nonlocal` at module level; `L002` module-level mutable value — a list/dict/set literal, comprehension or generator **not** wrapped in an allowlisted immutable constructor (`frozenset(...)`, `tuple(...)`), target `__all__` excepted, so `CURRENCIES = frozenset({...})` is fine; `L003` module-level call outside the immutable allowlist `re.compile, frozenset, tuple, TypeVar, NewType, logging.getLogger, date, datetime, timedelta, Decimal, Path` (so `SESSION = requests.Session()`, `SEEN = dict()`, `CACHE = collections.OrderedDict()` fail); `L004` `functools.lru_cache`/`functools.cache` (any import alias) used as a decorator or called anywhere in the module; `L005` a class-body assignment of a mutable value (list/dict/set literal, comprehension, or a call outside the L003 allowlist) on any class, except the declared step attributes `tools`, `context`, `mcp`, `exit_codes` and the bodies of pydantic model classes (bases include `BaseModel`/`RootModel` or a model class of the same module)) | RT-STEP |
| `describe.py` | `python -m wynd.runtime.describe <package_dir> <entrypoint>` → prints `PackageDescription` JSON: `{kind: StepKind, interface: Interface, context: list[str], tools: list[ToolSnapshot], mcp: list[{server, allow}], exit_codes: dict[str, str] \| null, doc: str}` (mounts the package like the worker; used by `--sync-interfaces`, drift checks and the compiler; distinct from the worker's per-step `StepDescription`) | RT-STEP |
| `ids.py` | `new_id`, `valid_id` (§3.1) | RT-STORAGE |
| `storage/__init__.py`, `storage/base.py`, `storage/models.py`, `storage/local.py` | §3.14 (`FileWorkspaceStore`, `JsonlTraceSink`, `FileRunRegistry`, `FileRegistry`, `EnvRegistry`, factories, `stores_from_env`, `registry_from_env`, `STORAGE_ENV`, `StorageConfigError`) | RT-STORAGE |
| `worker/__main__.py`, `worker/protocol.py`, `worker/loader.py` (`mount_step_package`, `load_step_class`), `worker/server.py` (`WorkerServer`, `main`, stdio claim, per-run capture, `init` snapshot verification (§3.6), method dispatch incl. `edge.check` → `wynd.runtime.edges.handle_edge_check`); W0 writes an empty `worker/__init__.py`, `worker/client.py` (`WorkerClient`, `WorkerCrashed`, `StepTimeout`, `WorkerRpcError`), `worker/pool.py` (`Dispatcher` protocol, `WorkerPool`, `InProcessDispatcher`) | §3.12; `$DRAFTS/02 §5–§6.2` | RT-WORKER |
| `trace.py` | event models (§3.13), `parse_event`, `TraceEmitter`, `TraceNode`, `TraceTree`, `read_trace`, `build_tree` | RT-EXEC |
| `executor/__init__.py`, `executor/engine.py` (`Executor`, `ProcessResult`), `executor/instance.py` (`Instance`, `StepRecord`, `Deadline`, `RunCtx`), `executor/context.py` (`assemble_context`), `executor/edges.py` (the agentic-edge seam, complete in M1: `EdgeVerdict`, `EdgeCheckCall`, `EdgeCheckResult`, `EdgeCheckCause`, `EdgeCheckError`, `EdgeChecker` protocol, `make_edge_check_call`, §5.4) | §5.4 | RT-EXEC |
| `providers/__init__.py`, `providers/types.py`, `providers/claude_code.py`, `providers/anthropic.py`, `providers/fake.py`, `providers/scripted.py` (`ScriptedModelProvider`, `ScriptedAgentProvider` test fakes — written complete by W0, maintained by RT-PROVIDERS); `check_provider`; `ProviderError` (defined only here) | §3.15 | RT-PROVIDERS |
| `agentic/__init__.py`, `agentic/loop.py` (`complete`, `complete_structured`, `StructuredCall`, `ModelLoop`, `AgentLoop`), `agentic/checks.py` (`check_agentic_class`, `is_ellipsis_body`, `describe_agentic`), `agentic/prompt.py` (`SYSTEM_PREAMBLE`, `render_prompt`, `RETRY_*`, `format_validation_errors`), `agentic/schema.py` (`wrap_output_schema`, `unwrap_output`, `strict_compatible`), `agentic/errors.py` (`ProviderError` re-exported from `providers.types`, `ToolFailure`, `ToolInputError`, `OutputValidationFailed`, `AgentLoopError`, `McpSnapshotMismatch`, `McpConfigError`, `MissingEnvVar`) | `$DRAFTS/03 §6` verbatim (cause mapping per §3.9) | RT-LOOP |
| `tools/__init__.py`, `tools/decorator.py` (`tool`, `ToolDecl`, `ToolSpec`, `tool_spec`, `step_tools`, `env`), `tools/toolset.py` (`ToolSet`, `is_transient`, `current_runtime`, `handles_for(fns) -> list[ToolHandle]` for non-step callers such as the chat), `tools/builtins.py` (`http_get`, `web_search` (Brave, `BRAVE_API_KEY`), `workspace_read`, `workspace_write`, `shell`, `now`, `BUILTINS`; `shell` is usable only as `shell.allow("pdftotext", …)`, which returns a `ToolDecl` whose snapshot carries `allow`; a call whose `argv[0]` (basename) is not in `allow` is a `ToolInputError`; `allow` refuses interpreters and shells (`python*`, `sh`, `bash`, `zsh`, `dash`, `fish`, `node`, `deno`, `perl`, `ruby`, `php`, `env`, `xargs`) at class definition; a bare `shell` in `tools` is a definition error), `mcp/__init__.py`, `mcp/client.py`, `mcp/snapshot.py` (`list_tools`, `snapshot(server, tools) -> McpSnapshot`, `schema_sha256`, `verify`), `mcp/entry.py` (`McpServerEntry`, `resolve_env_refs`, `open_client`), `mcp/fake_server.py` | `$DRAFTS/03 §9–§10` verbatim; snapshot shape = spec `McpSnapshot` | RT-TOOLS |
| `cassettes/__init__.py` (`CassetteSession`, `CassetteMissError`, `CassetteError`, `NO_RECORDING`, `promote`), `cassettes/key.py` (`Normaliser` incl. `for_run(run_id, workspace, extra)`, `DATETIME_RE`, `request_key`), `cassettes/store.py`, `cassettes/wrap.py` (`CassetteModelProvider`, `CassetteAgentProvider` incl. local-tool re-execution on replay, tool replay hooks) | §3.16; `$DRAFTS/03 §11` | RT-CASSETTE |
| `supervisor/__init__.py`, `__main__.py`, `main.py`, `schema.py` (run-API models + `SUPERVISOR_ENV`, the latter written complete by W0), `runs.py` (`RunManager`), `http.py` (`Handler`, `ROUTES: list[tuple[str, str]]` of `(method, path)`), `files.py`, `client.py` (`RunApiClient`, `RunApiError`), `run_api.schema.json` | §3.17 | RT-SUPERVISOR (M2) |
| `bake.py` | `main(plan_path, site, argv) -> int` (single-venv run of a baked process, `$DRAFTS/04 §8.9`) | PROC-BAKE (M2) |
| `edges.py` | M5 agentic edges (§5.6) | EDGE-RT (M5) |

### 5.2 Step API (what step authors and the compiler write against)
```python
class Step(ABC):
    Input: ClassVar[type[BaseModel]]
    Output: ClassVar[type[BaseModel] | UnionType]   # one model per exit, each `exit: Literal["<name>"] = "<name>"`;
                                                    # a plain model is allowed only for the "done" exit
    runtime: RuntimeHandle                          # set by middleware on each fresh instance before pre()
    def pre(self) -> None: ...
    @abstractmethod
    def run(self, input: Any) -> Any: ...
    def post(self) -> None: ...
class DeterministicStep(Step): ...
class AgenticStep(Step):                            # the class docstring is the instruction
    context: ClassVar[list[str]] = []
    tools: ClassVar[list[Callable]] = []            # functions decorated with @tool (e.g. wynd.runtime.tools.web_search)
    mcp: ClassVar[list[McpServer]] = []
    def run(self, input): ...                       # authors write `def run(self, input: Input) -> Output: ...`
    # __init_subclass__ runs check_agentic_class (docstring present, run body is `...`, tools decorated, unique tool
    # names, valid context entries, @tool methods not named run/pre/post)
class ShellStep(Step):
    exit_codes: ClassVar[dict[int | str, str]] = {0: "done", "*": "error"}
    def command(self, input: Any) -> list[str]: raise NotImplementedError   # argv; never a shell string
    def outputs(self, exit: str, result: ShellResult) -> BaseModel: ...
        # default: the exit's model filled from any of its fields named stdout/stderr/returncode; else NotImplementedError
    def run(self, input): return run_shell(self, input)
        # subprocess.run(argv, cwd=workspace, capture_output=True, text=True, stdin=DEVNULL,
        #   env=os.environ + {"WYND_INPUT": json, "PATH": f"{Path(sys.executable).parent}{os.pathsep}{PATH}"})
        # (the worker is the venv's python, not an activated venv: prepending its bin dir puts console scripts of the
        #  step's own dependencies on PATH, i.e. "the step's environment", SPEC §3.2)
        # exit name = exit_codes.get(rc, exit_codes.get(str(rc), exit_codes.get("*", "error")))
        # "error" -> StepFailure("shell_exit", f"exit code {rc}: {stderr[-2000:]}", partial_outputs={"returncode", "stdout", "stderr"} tails)
@dataclass(frozen=True)
class ShellResult: returncode: int; stdout: str; stderr: str
class ProcessStep(Step): process_id: ClassVar[str]        # built from `use: process:<id>`; executed by the executor
@dataclass
class RuntimeHandle:
    run_id: str; step_path: str; step_run: int
    workspace: Path                                 # per-run directory; also the cwd during pre/run/post
    logger: logging.Logger                          # "wynd.step.<path>" -> step.log events
    trace: StepTrace                                # .event(name, **data) -> step.event; .emit(type, **fields) internal
    cache: StepCache                                # .get/.set/.get_or_set/.delete/.clear — the ONLY cross-run state
    http: HttpClient                                # StepFailure("config") unless "network" in the step's effects
    def env(self, name: str, default: str | None = None) -> str   # missing & no default -> MissingEnvVar -> StepFailure("config")
    def log(self, message: str, **fields) -> None
def tool(fn=None, /, *, name=None, effects=(), idempotent=False, env=()) -> Callable   # marks only; stays callable
class McpServer: name: str; allow: tuple[str, ...]  # McpServer("github", allow=["get_issue"]); allow required & non-empty
```
Middleware: fresh instance per attempt; `os.environ` and cwd snapshotted before `pre` and restored after `post`
(only differing keys touched); cwd = workspace during the run. Module-level and class-level state and cross-run
caching outside `self.runtime.cache` are prevented by construction plus the static lint (`L001`–`L005`, all errors):
the validator reports it (an `L` error fails `wynd validate`) and the compiler's `astcheck` rejects any module with an
`L` finding.

### 5.3 Worker pool
`WorkerPool(plan: RunPlan, *, env=None, cwd=None)`: at most one worker per `PlanVenv`, guarded by a per-venv lock;
`init` sends one `InitStep` per served step, built from `plan.steps[id]` (`kind`, `interface_hash`, `context`,
`exit_codes` from `PlanStep.lock`);
`start(venvs=None)` spawns all requested venvs concurrently (Popen all, then collect inits; idempotent); `dispatch(step_id,
params, *, on_event, timeout) -> RunStepResult` (lazy respawn emits `worker.start`; on `StepTimeout` kill + drop + re-raise;
on `WorkerCrashed` drop + re-raise); `describe(step_id) -> StepDescription` (first call per venv sends `describe` for all
and caches); `status() -> list[{"venv","pid","alive"}]` (for `/readyz`); `call(venv_id, method, params, *, on_event,
timeout)` (generic, used for `edge.check`); `close()`. Worker env = `env` (default `os.environ` at creation; callers
merge `.env`/secrets first). `InProcessDispatcher(classes: Mapping[step_id, type[Step]])` runs `run_chain` in-process
(unit-test seam; ignores timeouts). Test seam: a `PlanVenv.python = sys.executable` runs real subprocess workers
without uv.

### 5.4 Executor (one executor, local and image; SPEC §8)
```python
class ProcessResult(BaseModel):
    run_id: str; process: str; exit: str; outputs: dict[str, Any]
    error: ProcessError | None                     # set whenever the top-level error handler ran
    status: Literal["succeeded", "failed"]         # failed iff exit == "error"
    trace: str; workspace: str | None; duration_ms: float; usage: Usage; finally_errors: list[StepError] = []
class Executor:
    def __init__(self, plan: RunPlan, dispatcher: Dispatcher, stores: Stores, *,
                 env: Mapping[str, str] | None = None, edge_checker: EdgeChecker | None = None) -> None
    def validate_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]          # raises InvalidProcessInputs (boundary)
    def run(self, inputs: Mapping[str, Any], *, run_id: str | None = None,
            cassette_mode: Literal["live", "record", "replay"] = "live",
            cassette_root: Path | None = None,          # replay/record source: <cassette_root>/<step_module_name>/
            record_root: Path | None = None,            # record staging: <record_root>/<step_module_name>/
            cassette_literals: Mapping[str, str] | None = None,   # CassetteConfig.literals for every call (§3.16)
            metadata: Mapping[str, Any] | None = None,  # recorded in run.start; RunRecord.meta = dict(metadata) (§3.14)
            on_event: Callable[[dict], None] | None = None) -> ProcessResult
```
**Agentic-edge seam** (`wynd/runtime/executor/edges.py`, RT-EXEC, complete in M1; `wynd.runtime.edges` imports and
re-exports these and implements them in M5):
```python
@dataclass(frozen=True) class EdgeVerdict: take: bool; reason: str                      # reason ≤ 500 chars
@dataclass(frozen=True) class EdgeCheckCall:
    run_id: str; process: str; process_goal: str | None; edge: str; branch: int; branch_key: str
    source_step: str; target: str; check: str; context: dict[str, Any]; bindings: dict[str, Any]
    lock: EdgeLockEntry; provider: str; model_id: str
@dataclass(frozen=True) class EdgeCheckResult:
    verdict: EdgeVerdict; attempts: int; validation_failures: int; provider: str; tier: str; model_id: str
    usage: Usage; replayed: bool; duration_ms: float
EdgeCheckCause = Literal["validation", "transport", "timeout", "config", "model", "cassette_miss"]
class EdgeCheckError(Exception): cause: EdgeCheckCause; message: str
class EdgeChecker(Protocol):
    def check(self, call: EdgeCheckCall, *, cassette: CassetteConfig, workspace: str, timeout_s: float | None,
              on_event: Callable[[dict], None]) -> EdgeCheckResult: ...   # raises EdgeCheckError
def make_edge_check_call(instance, edge, branch_index, branch, bindings) -> EdgeCheckCall
    # resolves the lock entry (defaults if missing), provider = entry.provider or plan.provider, model_id via
    # resolve_model, context from branch.context or ["previous.outputs"] via assemble_context
```
**Only the executor emits `edge.check`**, from the returned `EdgeCheckResult` or the raised `EdgeCheckError`; it
passes `on_event` (its per-run stamping callback) so worker `model.call` notifications land in the right run. A
checker is stateless per run (`WorkerEdgeChecker(pool, plan)` takes no emitter), so one `Executor` serves concurrent
runs.
Algorithm: `$DRAFTS/02 §7` verbatim, re-expressed over the spec `Scope` (§3.5) and the §3.5 routing rules: per process
instance an `Instance{plan, prefix, parent_span, scope, deadline, history}`; entry binding; `_run_node` builds the
policy (`build_policy` with the branch's `limits.retries`), the context (`assemble_context` over `ContextRef` kinds:
`full_trace` = `{process:{name, goal, inputs}, steps:[{step, run, exit, inputs, outputs, summary}]}` of the instance;
others read the scope; unavailable → `null`), dispatches with the remaining deadline, stamps worker notifications,
writes `.wynd/steps/<path>/<n>/{inputs,outputs}.json` into the run workspace, emits `step.start`/`step.end`, and calls
`scope.complete`; `ProcessStep` nodes recurse with a fresh scope and `prefix = path + "."`; the default/custom error
handler, `finally`, deadlines, workspace retention and run lifecycle (RunRecord create/update incl. `finished_at`,
`usage`, `trace_bytes`, `workspace_bytes` (measured before a workspace is deleted); `run.start`/`run.end`)
exactly as §3.5 and 02 §7.6–§7.10. The executor parses every expression once at construction (syntax error ⇒
`ValueError`). Default provider for the run = `plan.provider`. Branches with `check` call
`edge_checker.check(make_edge_check_call(...), cassette=…, workspace=…, timeout_s=lock.timeout_s, on_event=…)` (M5;
`None` ⇒ `ProcessError(cause="edge_check", message="agentic edges need an EdgeChecker")`); an `EdgeCheckError` ⇒
cause `edge_check` with `detail = {"branch_key", "error_cause": err.cause}`. An `Executor` is reusable and
thread-safe across concurrent `run()` calls (all run state is local).

### 5.5 LLM layer notes (contracts beyond §3.15/§3.16)
- `complete(step, call: AgentCall) -> AgentResult` is a thin wrapper over
  `complete_structured(StructuredCall(unit=call.step_path, instruction=<step docstring>, context, input, adapter,
  output_schema, tools=ToolSet.for_step(step, call, cassettes), policy, cassette, runtime))`; M5 edge checks call
  `complete_structured` with `tools=None`. Both loops, retry/restart rules, prompt text and trace events:
  `$DRAFTS/03 §6` verbatim.
- MCP at run time: connect and verify every snapshot before the first model call (skipped in replay); the model always
  sees the **snapshot's** tool definitions (stable prompts and cassette keys); mismatch message per `$DRAFTS/03 §10.5`;
  a new connection per step run, closed in `finally`; for claude-code, MCP tools are proxied through the in-process
  `wynd` server (never `--mcp-config`, which would put auth headers on argv).
- Every `StepFailure` carries the running `usage`, so failed runs still account for cost.
- `CassetteMissError` is never caught as a tool failure: `ToolSet` and the claude-code bridge re-raise it (the bridge
  aborts the harness), and the loop maps it to `StepFailure("cassette_miss", <NO_RECORDING text>)` (§3.9).

### 5.6 M5 agentic edges (`edges.py`, EDGE-RT)
Adopt `$DRAFTS/08 §4.4–§4.5` over the M1 seam of §5.4 (`EdgeVerdict`, `EdgeCheckCall`, `EdgeCheckResult`,
`EdgeCheckCause`, `EdgeCheckError`, `EdgeChecker`, `make_edge_check_call` are imported from
`wynd.runtime.executor.edges` and re-exported; EDGE-RT never redefines them). EDGE-RT adds: `EDGE_VERDICT_SCHEMA`,
`VERIFIER_INSTRUCTION` (verbatim), `run_edge_check(call, cassette, workspace, on_event) -> EdgeCheckResult`
(worker-side via `complete_structured`, no tools/MCP, `unit=f"edge:{branch_key}"`; raises `EdgeCheckError`),
`handle_edge_check(params: dict) -> dict` (worker RPC handler; errors → JSON-RPC `-32001` with `data.cause`; it has no
emit argument: it sends its `model.call` notifications with `wynd.runtime.worker.server.notify(event)`, which delivers
only while a request is in flight, and reports failure by raising `wynd.runtime.executor.edges.EdgeCheckError(cause,
message)`, which the worker server (RT-WORKER) turns into `-32001` with `data = {"cause", "message"}`), and
`WorkerEdgeChecker(pool: WorkerPool, plan: RunPlan)` implementing `EdgeChecker` (dispatches
`pool.call(plan.edge_venvs[f"{pid}:{branch_key}"], "edge.check", …, on_event=on_event, timeout=timeout_s)`, maps
`-32001` to `EdgeCheckError` and a timeout to cause `timeout`; it emits nothing). The provider of a check is
`EdgeLockEntry.provider or RunPlan.provider` — the ROOT's default, even for branches inside a child process.
Cassettes for edge checks: `<process dir>/cassettes/edges/` (replay/record via the executor's
`cassette_root`/`record_root`).

### 5.7 Tests (`packages/runtime/tests/`, offline)
Adopt `$DRAFTS/02 §13` (step interface, middleware, shell, summary, run_step, lint, http, plan, worker protocol with
real `sys.executable` subprocess workers, pool, executor routing/errors/nested, trace, storage, ids) and
`$DRAFTS/03 §16` (checks, prompt, schema, model loop, agent loop, provider registry, anthropic with fake HttpClient,
claude-code with real SDK types and injected `query_fn`, fake provider, tool decorator, toolset, builtins, MCP client
against `fake_server` over HTTP JSON/SSE and stdio, snapshot/verify, cassette key, cassettes record→replay→miss,
`run_step` e2e with the fake provider, supervisor API with a stub executor, supervisor integration with real
workers, run API contract snapshot, client). Changes: shell tests follow the §5.2 method API; the executor tests use
the spec `Scope`, `$ignore`, `finally` with `with`, and a fake `EdgeChecker`; `test_edges.py` + `test_executor_agentic.py`
(`$DRAFTS/08 §4.8`) belong to EDGE-RT. Live (`@live`): claude-code structured union, tool + continuation (no tool
re-call, no files left in the projects dir), MCP proxy, auth error with a bad token; anthropic live (skipped without
`ANTHROPIC_API_KEY`). A `tests/test_leaf_runtime.py` (owned by RT-STEP) asserts `wynd.runtime` imports no
`wynd.process/compiler/controller` and no third-party package other than pydantic/yaml/lark (claude_agent_sdk only
inside `providers/claude_code.py` function bodies).
Cases this plan adds (each in the owning unit's files, §14): **RT-STEP** — lint `L003` error for
`SESSION = requests.Session()`, `SEEN = dict()`, `CACHE = collections.OrderedDict()`; `L004` for `@lru_cache`,
`@functools.cache`, `cached = lru_cache(maxsize=1)(f)`; `L005` for `class X(DeterministicStep): seen = []` and a
helper class with a dict attribute, and **not** for `tools/context/mcp/exit_codes` or pydantic model fields;
`self.runtime.http` without `network` → cause `config`; a ShellStep running a console script installed by its
dependency set (PATH prepend). **RT-WORKER** — `init` with a drifted `interface_hash`/`context`/`kind`/`exit_codes`
→ `{"ok": false}` with the drift message and runs resolve with cause `import`; `@live`
`test_worker_claude_code_live`: a tiny AgenticStep fixture through `WorkerPool` with `PlanVenv.python =
sys.executable`, `provider: claude-code`, record mode → valid exit, a `model.call` event, a written cassette (proves
the stdio claim and the SDK coexist before M1-INT). **RT-CASSETTE** (wrappers, keys, store) — record with the
default tiers, remap `cheap` in a `MemRegistry`, replay → `CassetteMissError`; `CassetteConfig.literals` makes a
`{tmp}` path in the input key-stable across two temp dirs; `CassetteAgentProvider` replay re-executes `local`
tool_use entries in order. **RT-LOOP** (`test_loop_cassettes_e2e.py`, sub-wave 2b) — a missing tool-call recording
(network tool, runtime loop) resolves the step with cause `cassette_miss`, never `tool` and never an error result to
the model; a step example expecting `exit: error` whose miss is in a tool call still fails through `run_step`; the
same `workspace_write` side effect happens in replay under `ScriptedModelProvider` (runtime loop) and
`ScriptedAgentProvider` (re-execution). **RT-PROVIDERS** —
`check_provider`; `@live test_tier_parity_live` (§3.15). **RT-STORAGE** — `EnvRegistry` reads
`WYND_REGISTRY_JSON` and refuses writes.

---

## 6. Package `wynd.process` (`packages/process`, wynd-process, AGPL-3.0-or-later) — never inside an image

Workspace loading, graph validation, venv planning, local runs, the test runner, env manifest, git primitives, job
types, build artefacts, image builder, base images, bake. Dependencies: spec, runtime, pyyaml. External executables
checked at point of use by `_proc.require_tool(name)` (`ToolMissing` with an install hint): `git` everywhere, `uv`
(`WYND_UV`) for venvs/build/bake, `docker` only in `BuildxImageBuilder`. Entry points: `wynd.image_builders`
(`buildx = wynd.process.build.imagebuilder:BuildxImageBuilder`), `wynd.artefact_stores`
(`local = wynd.process.artefacts:LocalArtefactStore`). Package data: `templates/*`.

### 6.1 Modules
| Path (`src/wynd/process/`) | Responsibility | Unit |
|---|---|---|
| `__init__.py` | lazily re-exports (§0 rule 8) `find_workspace_root, load_workspace, Workspace, LoadedProcess, ResolvedStep, StepPackage, ResolvedInterface, validate, validate_process, ValidationReport, plan_local, run_local, run_tests, tests_status, assemble_env_manifest, compile_state, CompileState, reference_closure, closure_head, dirty_paths, integrate, IntegrationResult, JobKind, JobStatus, JobRecord, JobContext, JobOutcome, JobUsage, JobRunner, open_artefact_store, ArtefactStore, BuildInfo, ImageRegistryEntry` (`validate`/`validate_process`/`ValidationReport` come from the `validation` subpackage, so the attribute `wynd.process.validate` is the function and never shadows a subpackage) | W0 |
| `errors.py` | `CODES: dict[str, tuple[Severity, str]]` (§3.2 process codes, templates from `$DRAFTS/04 §4.12`), `WyndProcessError`, `LoadError`, `ProcessNotFound`, `DesignPhase(step_ids)`, `ValidationFailed(report)`, `ToolMissing`, `GitError`, `ResolutionError` | PROC-WS |
| `_proc.py` | `run(args, *, cwd, env=None, log=None, check=True, input=None) -> str` (streams lines to `log`), `require_tool(name) -> str` | PROC-WS |
| `workspace.py` | `Tree` protocol, `WorkingTree`, `CommitTree` (§3.19), `ProcessEntry`, `StepEntry`, `Workspace`, `load_workspace(root, tree=None)`, `find_workspace_root(start=None)` (`WYND_WORKSPACE` else `wynd.spec.workspace.find_workspace_root(start or cwd)`) — discovery `$DRAFTS/04 §3.3`, root rules → spec `E-ROOTS` + `E103–E106`, ids §3.1 | PROC-WS |
| `loader.py` | `ResolvedInterface`, `StepPackage`, `ResolvedStep`, `LoadedProcess`, `Workspace.load_process` — resolution/phase/interface choice `$DRAFTS/04 §3.6`, cycles `§3.7` | PROC-WS |
| `hashing.py` | `tree_hash`, `step_hash`, `process_hash` (§3.19) | PROC-WS |
| `validation/__init__.py` | `ValidationReport`, `validate(lp, *, providers=None, stats=None)`, `validate_process(ws, pid, **kw)`, `format_diagnostic` | PROC-VAL |
| `validation/structure.py` | `E204` (exit not on the source interface), `E208` (unrouted non-error exit; `$ignore` counts as routed), `E219` | PROC-VAL |
| `validation/reach.py` | `E209` (BFS from entry, on_error, finally) | PROC-VAL |
| `validation/cycles.py` | Tarjan SCC; fill `max_traversals = DEFAULT_MAX_TRAVERSALS` on every branch whose endpoints share an SCC (in memory only, never written to YAML); `I201` | PROC-VAL |
| `validation/bindings.py` | `E210` entry ↔ `process.inputs` by name, `E211` `$exit` binds exactly the declared fields, `E212` `with:` keys ⊆ target Input and covers required, `E215` finally inputs ⊆ `process.inputs ∪ {run_id, exit}` (when no `with`), `E223` every required Input field of the `on_error` step is a `ProcessError` field name (name level, M1; type assignability is added by M3 typecheck) | PROC-VAL |
| `validation/dataflow.py` | exit lattice with `NOT_RUN`, branch-level guard atoms from `when:` (`ExitIs`, `ExitIsNot`, `HasRun`, `NotRun`), fixpoint, `WHEN_STATE`/`WITH_STATE` per branch (`$DRAFTS/04 §4.8` verbatim; intra-expression and/or/if refinement is **not** implemented); plus `FINAL_STATE` = the join of every reachable IN/WITH state and the state at every error-handler entry, where every alias may additionally be `NOT_RUN` or `error` — `finally[].with` sites are checked against it (so only `default()`/`coalesce()`-guarded step refs pass); produces the `TypedSite` list | PROC-VAL |
| `validation/exprcheck.py` | `TypedSite` (below); per-site `TypeEnv` from the dataflow state → `wynd.spec.expr.check_references`; downgrade field-level findings to warnings (suffix ` [provisional: interface inferred from examples]`) for `precise=False` interfaces; `check_expr_at(ws, pid, doc: dict, protos: dict, loc, expr) -> ExprCheckResult{errors, warnings, scope: list[str]}` for the web (dataflow over the in-memory doc) ; records `env_refs` | PROC-VAL |
| `validation/rules.py` | `W203–W205` (child declares a different `env.base`/`provider`/`latency`), `W206` (agentic step using an AgentProvider in a `latency: fast` root), `W207` (unknown provider), `W128`, `W129`, `I130`, `W202`, `E128`, and runtime lint `L001–L005` (errors) over every non-test `.py` file of each compiled step package in the closure | PROC-VAL |
| `validation/typecheck.py` | M3 `check_types(lp: LoadedProcess, sites: Sequence[TypedSite]) -> list[Diagnostic]`: `infer_type` per site (or `site.src_schema` for synthetic sites); `check_assignable(src, site.dst_schema)` for `with:`→target Input, `$exit`→process outputs, entry inputs, on_error Input (`ProcessError` schema) and handler exits → `E-TYPE-ASSIGN`/`W-TYPE-ASSIGN`/`W-TYPE-NULL`/`E-TYPE-OP`. **W0 stub has exactly this signature and returns `[]`.** | PROC-TYPES (M3) |
| `validation/agentic.py` | M5 `check_agentic_edges(lp, edges_lock) -> list[Diagnostic]` (`$DRAFTS/08 §4.3` codes, verbatim). **W0 stub returns `[]`.** | EDGE-PROC (M5) |
| `fragments.py` | `StepEnv`, `MergedEnv`, `merge_process_fragments(lp, providers=None) -> MergedEnv` (raises `DesignPhase`; provider deps added to agentic steps only; children's steps included with the ROOT's provider) — `$DRAFTS/04 §6.1` | PROC-ENV |
| `venvs.py` | `RuntimeSource`, `detect_runtime_source(environ)`, `VenvGroup`, `venv_groups(env)`, `local_venv_id(group, runtime)`, `ensure_local_venv(group, *, venv_root, runtime, log) -> str`, `step_python(requirements, *, venv_root, runtime=None, log=None) -> Path`, `sync_interfaces(ws, pid, *, venv_root, log=None) -> list[str]` — `$DRAFTS/04 §6.2–§6.4` | PROC-ENV |
| `plan.py` | `build_plan(lp, report, *, mode, venv_root, venvs: list[PlanVenv], ws_root: Path \| None) -> RunPlan`, `plan_local(ws, pid, *, venv_root=None, runtime=None, providers=None, log=None) -> RunPlan`, `assign_edge_venvs(plan) -> tuple[dict[str, str], list[PlanVenv]]` (rule §6.4: the branch → venv map and any venvs it had to add; `plan_local` and `prepare_build_job` both call it before venvs are created/resolved) | PROC-ENV |
| `local.py` | `run_local(ws, pid, inputs, *, env, stores, run_id=None, on_event=None, cassette_mode="live", cassette_root=None, record_root=None, cassette_literals=None, metadata=None, venv_root=None, log=None) -> ProcessResult` (plan_local → `WorkerPool(plan, env=env)` → `Executor(plan, pool, stores, env=env, edge_checker=WorkerEdgeChecker(pool, plan))` → `run`; pool closed in `finally`). Relative paths in inputs are NOT resolved here. | PROC-ENV |
| `testing.py` | §3.20 test runner (`TestCase`, `SuiteResult`, `TestReport`, `run_tests`, `run_step_suite`, `run_process_examples`, `tests_status`, `run_test_live_job`) | PROC-ENV |
| `envmanifest.py` | `assemble_env_manifest(ws, pid, registry=None, *, commit=None, providers=None) -> EnvManifest` (sources `$DRAFTS/04 §10.2`: step `fragment.vars`, MCP `auth_env`, provider fragments (vars + groups; `used_by` incl. the agentic steps and agentic branches), `env.*` references from edge expressions (`ValidationReport.env_refs`; description from `process.env.vars` or `"Referenced by edge expression."`), `STORAGE_ENV`, `SUPERVISOR_ENV` (§3.17), `WYND_HOME` (optional), and the user-registry vars of §3.10: `WYND_REGISTRY_JSON` (group `user-registry`, present iff `registry_snapshot` is non-empty) and `WYND_MCP_<NAME>_URL` per http server); `registry_snapshot(lp, registry) -> dict` (§3.10) | PROC-ENV |
| `git.py` | every git primitive (subprocess `git -C <ws_root>`, workspace-relative pathspecs): `git`, `toplevel`, `prefix`, `rev_parse`, `current_branch`, `is_ancestor`, `merge_base`, `reference_closure`, `closure_head`, `dirty_paths`, `count_touching`, `log_paths`, `add_worktree`, `remove_worktree`, `worktree_for_branch`, `commit_paths(checkout_ws, message, paths=None, *, trailers={})` (job worktrees; identity fallback "Wynd <wynd@localhost>"), `commit_only(ws_root, paths, message)` (main worktree design commits: `git add -A -- paths; git commit --only -m msg -- paths`, `None` if nothing to commit), `merge_ff_only`, `update_ref(cwd, ref, new, old=None)`, `result_branch(kind, pid, job_id)`, `set_branch`, `delete_branch`, `commits_touching`, `IntegrationResult`, `integrate` (§3.18) | PROC-GIT |
| `jobs.py` | §3.18 types (`JobKind`, `JobStatus`, `TERMINAL`, `JobRecord`, `JobUsage`, `JobContext`, `JobOutcome`, `JobHandler`, `JobRunner`; written complete by W0) + `new_job_record(kind, ref, inputs, *, ws_root, runner, handler) -> JobRecord` (resolves full sha, `base_commit`, `target_branch = inputs["target_branch"]` for commit-producing kinds (`ValueError` if missing; never reads a branch), `workspace_rel`), `wait_for(runner, job_id, *, poll_s=0.5, timeout_s=None)` | PROC-GIT |
| `compile.py` | `StepCompileState`, `CompileState{design, reasons, steps, process_hash}`, `compile_state(ws, pid)` (reads through `ws`'s Tree: callers pass a `CommitTree` workspace for per-commit status) (`$DRAFTS/04 §14`; a stale or missing edges-lock entry for an agentic branch also makes the process design) | PROC-GIT |
| `artefacts.py` | `BuildInfo`, `ArtefactStore` protocol (`put_build`, `get_build`, `list_builds`, `local_dir`), `LocalArtefactStore(state_dir)` at `.wynd/build/<pid>/<commit>/`, `open_artefact_store(state_dir, environ)` (`WYND_ARTEFACT_STORE`, entry points) — `$DRAFTS/04 §11`; `ImageRegistryEntry` (§3.14; section `registries`) | PROC-GIT |
| `build/__init__.py` | re-exports | W0 |
| `build/job.py` (`Prepared`, `prepare_build_job(ctx) -> Prepared`, `finalize_build_job(ctx) -> JobOutcome`, `run_build_job(ctx) -> JobOutcome`), `build/resolve.py` (`Resolver`, `UvResolver`), `build/wheels.py` (`stage_step`, `build_step_wheel`), `build/variant.py` (`choose_base`), `build/dockerfile.py` (`render_dockerfile(lock)`), `build/lockfile.py` (`make_process_lock`), `build/imagebuilder.py` (`ImageBuildRequest`, `ImageBuildResult`, `ImageBuilder`, `BuildxImageBuilder`, `open_image_builder`), `base.py` (`base_image_ref`, `render_base_dockerfile`, `build_base`, `publish_base`, `ensure_base`), `templates/base.Dockerfile` | §6.5 | PROC-BUILD (M2) |
| `bake.py`, `templates/bake_main.py` | `run_bake_job(ctx)`, `bake_process(...)` — `$DRAFTS/04 §8.9` verbatim (stdlib zipapp + extract-once bootstrap; bakeable iff no `system:`, no shell steps, one resolvable environment) | PROC-BAKE (M2) |
| `edges_lock.py` | M5 `sync_edge_lock(process_dir, doc) -> tuple[EdgesLock, bool]` (`$DRAFTS/08 §4.1`). **W0 stub returns `(EdgesLock(), False)`.** | EDGE-PROC (M5) |
| `optimise.py`, `latency.py` | M5 optimise analysis (`$DRAFTS/08 §3.3–§3.7` models and algorithms; reads §3.13 events). **W0 stub of `latency_warnings` returns `[]`.** | OPT-PROC (M5) |

### 6.2 Key data (loader output)
```python
@dataclass(frozen=True)
class ResolvedInterface:
    interface: Interface | None           # spec Interface (outputs include the exit const); None when unknown
    source: Literal["lock", "proto", "examples", "process", "none"]
    precise: bool                         # False for "examples" (field-level findings become warnings)
@dataclass
class StepPackage:
    id: str; dir: str                     # step id; workspace-relative package dir
    proto_path: str | None; proto: ProtoStep | None; proto_hash: str | None
    lock: StepLock | None; phase: Literal["compiled", "design"]; stale: bool; interface: ResolvedInterface
@dataclass
class ResolvedStep:                       # one entry of process.yaml `steps:`
    name: str; use: str; ref_kind: Literal["local", "root", "process"]
    package: StepPackage | None           # None for process refs
    child: str | None                     # child process id for process refs
@dataclass
class LoadedProcess:
    id: str; dir: str; path: str          # workspace-relative
    doc: ProcessDoc; source: SourceMap
    steps: dict[str, ResolvedStep]
    children: dict[str, "LoadedProcess"]  # memoised per Workspace (diamonds load once)
    diagnostics: list[Diagnostic]
    def closure_processes(self) -> dict[str, "LoadedProcess"]
    def closure_packages(self) -> dict[str, StepPackage]
class ValidationReport(BaseModel):
    process: str; diagnostics: list[Diagnostic]          # sorted (process, file, line, code)
    normalized: dict[str, ProcessDoc]                    # per closure process: to-lists, max_traversals filled, after-else dropped
    env_refs: dict[str, list[str]]                       # env var -> ["edge:<pid>:<branch_key>.<field>", …]
    @property
    def ok(self) -> bool                                 # no severity == "error"
@dataclass(frozen=True)
class TypedSite:                                         # wynd.process.validation.exprcheck; produced by the dataflow walk
    process: str; loc: Loc                               # document path of the value/expression
    expr: str | None                                     # None for synthetic sites (entry, on_error, handler exits)
    env: TypeEnv                                         # exit-aware scope at that site (WHEN/WITH/FINAL state)
    target: Literal["when", "with", "limit", "exit", "entry", "on_error", "handler_exit", "finally"]
    dst_schema: dict | None                              # schema the value must be assignable to (None: condition/limit)
    src_schema: dict | None = None                       # synthetic sites: the source schema instead of inferring expr
```
Interface choice (first match): compiled & not stale & lock interface → `lock`; proto declares inputs and outputs →
`proto` (`interface_from_fields`); proto present → `examples` (union of example keys, all types `{}`); compiled lock
without interface → `none` (`W129`). A `process:<id>` reference uses the child's `ProcessDoc.interface()` (`process`).
Every step implicitly has the `error` exit with `STEP_ERROR_SCHEMA`.

### 6.3 Validation pass order (`validate(lp)`)
Per closure process, memoised: (1) loader diagnostics + spec `check_process_doc`; (2) structure (E204, E208, E219);
(3) reachability (E209); (4) cycles (fill + I201); (5) bindings (E210, E211, E212, E215, E223); (6) dataflow +
expression walk (`E-REF-*`, `E-EXPR-*`), producing `sites: list[TypedSite]`, then
`validation.typecheck.check_types(lp, sites)` (M3; `[]` before); (7) child/provider rules (W203–W207), runtime lint
(`L001–L005`) and `validation.agentic.check_agentic_edges` (M5); (8) when `stats` is given,
`latency.latency_warnings` (M5). Closure-wide on the root: W206. I201 is reported per branch key (§3.1). The M1
dogfood (no branch names) validates with exactly two `I201` infos (`validate.done[1]`, `fix.done[0]`) and nothing
else; the M5 dogfood (branches named `save`, `fix`) with exactly `validate.done[fix]` and `fix.done[0]`.

### 6.4 Venvs and the run plan
- Effective requirements of a compiled step: `(lock.locked_deps or lock.fragment.deps)` plus, for agentic steps,
  `provider_info(effective provider).env_fragment.deps`. Steps with an identical normalised set share one venv
  (`dependency_set_hash`); the builder owns assignment; steps and hooks cannot choose a venv.
- Local venvs: `<ws>/.wynd/venvs/<local_venv_id>/` created with `uv venv --relocatable --python 3.12 --no-project` +
  `uv pip install <runtime.install_args()> <requirements> "pytest>=8,<10"` into a temp dir, marker `wynd-venv.json`
  written last, atomic rename (`$DRAFTS/04 §6.4`). `RuntimeSource`: editable `packages/spec` + `packages/runtime` when
  running from this monorepo (detected from `direct_url.json` or `WYND_RUNTIME_SOURCE=<dir>`), else pinned
  `wynd-spec==V wynd-runtime==V`. Jobs use the **user's** `<workspace_root>/.wynd/venvs` (shared across jobs).
- `build_plan` local: `PlanVenv(id=<local id>, steps)`, `PlanStep.package_dir` absolute, `PlanProcess.dir` absolute,
  `venv_root=<ws>/.wynd/venvs`. Image: venv id = group key, `package_dir=None`, `dir=None`,
  `venv_root=/opt/wynd/venvs`. `PlanProcess.edges_lock` = `load_edges_lock(<process dir>/edges.lock.yaml)`.
- **Edge venv rule (M5):** for each agentic branch with effective provider `P` (`EdgeLockEntry.provider or
  RunPlan.provider`): the first venv (plan order) whose requirements include `P`'s fragment deps; otherwise add a venv
  with exactly `P`'s fragment. Recorded in `RunPlan.edge_venvs["<pid>:<branch_key>"]`. `assign_edge_venvs` runs
  inside `plan_local` **and** inside `prepare_build_job` before host-side resolution, so every venv id in
  `edge_venvs` appears in `RunPlan.venvs`, `ProcessLock.venvs` and the Dockerfile. Implemented by PROC-ENV in M1 (no
  agentic edges exist before M5); goldens include a deterministic-only process with one agentic edge (local plan and
  image lock/Dockerfile).

### 6.5 Builder (M2; SPEC §4, §6.4, §6.5, §8)
Adopt `$DRAFTS/04 §8.1–§8.8` and `§9` verbatim with these changes:
- **Split for Kubernetes:** `prepare_build_job(ctx) -> Prepared` (a pydantic model, stored as `artefacts["prepared"]`
  between phases, §3.18) does validation, design check, closure-HEAD `C`, test gate
  (`runs.get_test_result(C, f"process:{pid}:{process_hash}")` passed — `run_tests` covers the step suites and the
  process examples of **every closure process**, so a child whose own examples fail cannot build into its parent —
  else `run_tests(mode="replay")` and record; failure ⇒ `failed: "tests fail on <C>"`), base choice,
  `assign_edge_venvs` (§6.4), host-side resolution (`uv pip compile --universal`), step wheels, env manifest,
  `process.lock.yaml`, `Dockerfile`, and writes the complete build context to `ctx.scratch/"ctx"` (or
  `$WYND_BUILD_CONTEXT_DIR` when set) plus `image.ref` (the full target reference). `run_build_job(ctx)` = prepare →
  `open_image_builder().build(...)` (writes `buildkit-metadata.json` into the context dir) → `finalize_build_job(ctx)`
  (reads `containerimage.digest`, pushes if requested, `artefacts.put_build`, returns the outcome with
  `artefacts = {commit, image, image_digest, build_dir, pushed, tests: {passed, failed, total, source: "ran"|"registry"},
  sizes: {build_dir_bytes, image_bytes (from `docker image inspect` or the pushed manifest; null if unknown),
  wheels_bytes}}`).
- **Step wheels:** stage `<stage>/<module>/wynd_steps/<step_module_name>/` = the package's tracked `*.py` files except
  `test_*.py` + `wynd-step.json` (`{schema:1, step, entrypoint, kind, proto_hash, step_hash, requirements, effects,
  tools, mcp}` — **no provider/tier/thinking**) + a generated `pyproject.toml` (`name = wynd-step-<…>`, version
  `0.1.0`, `packages = ["wynd_steps"]`, no dependencies); `uv build --wheel`. Wheels install with `--no-deps`.
- **process.lock.yaml** is the §3.11 `ProcessLock` (no timestamps). Tag `wynd/<slug(pid)>:<C[:12]>` (`slug`, §3.1). Labels:
  `dev.wynd.process`, `dev.wynd.commit`, `dev.wynd.base-version`, `dev.wynd.base-variant`, `dev.wynd.run-api-port=8080`,
  `dev.wynd.env-manifest` (compact JSON of `process.env.yaml`), `org.opencontainers.image.revision`.
- **Base image** env: `PYTHONUNBUFFERED=1 UV_PYTHON_DOWNLOADS=never UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
  UV_CACHE_DIR=/var/cache/uv WYND_BASE_VERSION WYND_BASE_VARIANT WYND_DATA_DIR=/var/lib/wynd
  WYND_HOME=/var/lib/wynd/home WYND_REGISTRY=env WYND_PLAN=/opt/wynd/process/process.lock.yaml
  WYND_ENV_MANIFEST=/opt/wynd/process/process.env.yaml WYND_PORT=8080`; non-root user `wynd` (uid 10001) with a writable
  `/home/wynd` (Claude Code writes `~/.claude*`); `ENTRYPOINT ["/opt/wynd/supervisor/bin/wynd-supervisor"]`,
  `CMD ["serve"]`. `UV_VERSION = "0.10.7"`; `python:3.12-slim-trixie` / `python:3.12-alpine3.22`.
- **Registry push** uses the `registries` user-registry section: `build/job.py` and `base.py` read
  `registry.get("registries", name)` and validate it with `wynd.process.artefacts.ImageRegistryEntry` (§3.14; process
  never imports the controller); an unknown name fails the job before building. `wynd base publish` pushes
  `linux/amd64,linux/arm64`.
- `ImageBuilder` backends by `WYND_IMAGE_BUILDER` (entry points `wynd.image_builders`); local = `docker buildx build`
  (BuildKit, docker driver). The kube package does not register an image builder: its build Jobs run `prepare`, a
  rootless BuildKit init container, then `finalize` (§11).

### 6.6 Tests (`packages/process/tests/`)
Adopt `$DRAFTS/04 §18` (fixtures `make_repo`, `FakeProviders`, `FakeResolver`, `FakeImageBuilder`, `MemRegistry`;
workspace/loader/hashing incl. `WorkingTree` vs `CommitTree` blob-id equality with an LFS-filtered cassette when
git-lfs is installed; validator golden cases per code + the clean dogfood (two `I201`); dataflow (branch-level guards
only), cycles (50-shuffle order independence), fragments, runtime source, venvs (fake `_proc.run` + one real offline
`uv` venv with `UV_OFFLINE=1`), plan goldens (local vs image differ only in mode/venv_root/paths), testing (real local
venv, JUnit parsing, "no tests collected", drift detection, registry keys, `match_outputs` via runtime, `{tmp}` and
relative paths), env manifest golden + `check_env` modes, builder goldens (Dockerfile, base Dockerfiles, lock byte-
identical for the same commit, wheel contents without tier), build job with fakes, bake, git closure/HEAD/dirty,
integrate (7 cases incl. per-commit rule and revert), jobs). Package `tests/conftest.py` is owned by PROC-WS; other
process units put helpers in `tests/support/<unit>_*.py` (e.g. `support/proc_git_harness.py`). Every fixture that
copies a workspace links `<ws>/.wynd/venvs` to the root conftest's session-scoped `shared_venvs` dir and sets
`UV_OFFLINE=1` (venv ids are content hashes and creation is an atomic rename, so sharing is safe). Cases this plan
adds: **PROC-VAL** — `E223` (handler requiring `ticket_id`); a `finally[].with` golden where an unguarded
`steps.x.outputs.f` errors and `default(steps.x.outputs.f, "")` passes (`FINAL_STATE`); `L003–L005` findings surface
as validator errors on a fixture step package. **PROC-ENV** — a process example whose only miss sits behind an
`X.error` edge, and one expecting `exit: error`, both fail with the `NO_RECORDING` text; `run_tests` on a parent whose
child's own example fails reports failure (child suite included); two runs with different `{tmp}` dirs hit the same
cassette keys; manifest golden incl. `SUPERVISOR_ENV`, `WYND_REGISTRY_JSON` (MCP + tier override in a `MemRegistry`)
and `WYND_MCP_<NAME>_URL`; `plan_local` golden with an added edge venv. **PROC-GIT** — `dirty_paths(ws, None)` sees
untracked and modified files anywhere in the workspace dir but not `.wynd/` or ignored files; `result_branch` table
incl. `ValueError` for build/bake; `new_job_record` without `target_branch` for compile → `ValueError`.
**PROC-BUILD** — the build gate fails when a child process's example fails at `C`; image lock/Dockerfile golden with
an agentic edge on a deterministic-only process. `@docker`: `build_base("0.1.0", ["slim"])`, full dogfood build,
`check-plan` in the image, non-root user, labels, no `.env` in the image.

---

## 7. Package `wynd.compiler` (`packages/compiler`, wynd-compiler, AGPL-3.0-or-later) — M3

Adopt `$DRAFTS/05` (session, per-step pipeline, compile decision rules 1–4 with precedence 4 → (1,3) → 2, split,
examples/proposals, schema inference, attempts, tool selection and MCP narrowing, lock construction, generated test
format, prompts **verbatim** §14, report, offline and live tests) with these reconciliations:
1. **Package layout and lock** exactly §3.6 (flat module `<name>.py`, entrypoint `<name>:<Class>`, spec `StepLock`
   with `compiled` as an opaque dict validated by the compiler's own `CompiledInfo` model, `retries =
   DEFAULT_RETRIES[kind]`). Class name = PascalCase of the package name; split halves `<name>`/`<name>_agentic`.
2. **No generated process test file.** The process-level phase runs `wynd.process.testing.run_process_examples(…,
   mode="record")` into a staging dir, promotes into `<process dir>/cassettes/`, then re-runs in replay; a failing
   process example fails the job (no revision), branch still published with the report.
3. **Job context** is §3.18: `run_compile_job(ctx: JobContext) -> JobOutcome`. Mapping of 05's names:
   `ctx.checkout → ctx.workspace`, `ctx.worktree → ctx.worktree`, `ctx.ref → ctx.job.base_commit` (session base),
   `ctx.run_registry → ctx.runs`, `ctx.user_registry → ctx.registry`, `ctx.state_dir → ctx.state_dir`,
   `ctx.commit(msg) → ctx.commit(msg, None)`. Scratch = `ctx.scratch / "compile"`. Resume = the **same job requeued**
   (§3.18): the requeued attempt's checkout is at `job.result_commit` (the WIP commit), `ctx.session` holds the
   session, `ctx.inputs["answers"]` is cumulative, `ctx.inputs["accept_proposals"]` optional. `done` squashes onto
   `job.base_commit` (`git reset --soft <base>` + `ctx.commit`) and returns `JobOutcome(commit=<sha>)`; the harness
   publishes `wynd/compile/<pid>/<job-id>`. After the final commit, `run_tests(mode="replay", commit=<closure head of the
   new commit>, runs=ctx.runs, …)` records results so `wynd build` can reuse them.
4. **Session id** = the job id. Question ids are readable and deterministic (`extract.example1`, `fix.clarify1`,
   child processes prefixed `<child-pid>:`), each with a fingerprint.
5. **Compiler LLM**: `ProviderLLM` over `load_provider(os.environ.get("WYND_COMPILER_PROVIDER", "claude-code"),
   registry=ctx.registry)` with `provider_tiers(...)`; AgentProvider calls use raw mode (`AgentRequest(instruction=
   <system>, prompt=<user>, context={}, input={}, tools=[], mcp_servers=[], builtin_tools=[], output_schema=<the plain
   schema>, workspace=ctx.scratch/"llm", model_id=tiers[tier], thinking=…)`) — the compiler has **no** `strict()`:
   the provider's `wrap_output_schema` is the only strictness transform (§3.15); tier/thinking per call kind table `$DRAFTS/05 §13.1`
   (strong for codegen/decide/proposals, standard for schema/revise_example). Offline tests use `ScriptedLLM` keyed by
   `(kind, node, n)`.
6. **Dependencies for attempts**: `step_python` = `wynd.process.venvs.step_python`, `lock_requirements` =
   `UvResolver().compile(deps, universal=True, python_version="3.12")` minus wynd-spec/runtime, `run_step_suite` and
   `run_process_examples` from `wynd.process.testing`, `describe` = `python -m wynd.runtime.describe <pkg> <entrypoint>`
   in the step venv, `list_mcp_tools` = `wynd.runtime.mcp.list_tools`. Attempt tests run with
   `WYND_CASSETTE_MODE=record|replay`, `WYND_CASSETTE_RECORD_DIR=<scratch>/attempts/<node>/<n>/cassettes`,
   `WYND_DEFAULT_PROVIDER=<root provider>`, `WYND_EVENTS_FILE=<scratch>/…/events.jsonl`.
7. **M5 hook:** after a process's steps are final, call `wynd.process.edges_lock.sync_edge_lock(process_dir, doc)`;
   write `edges.lock.yaml` when changed; the process-level record phase then records edge checks too.
8. **CLI flag** is `--accept-proposals` (answers every `example_proposal` with `accept`; clarifications have no
   default, so the CLI still stops with exit 4).
9. **Cassette size warning:** sum of a step's `cassettes/` > `ws.config.cassette_warn_mb` ⇒ report warning (SPEC §6.5).
10. **Session API names are SPEC §9's:** `CompileSession.state`, `.pending_questions`, `.answer(question_id, text)`,
    `.next(env: CompileEnv) -> SessionState` (public), plus `to_json()`/`from_json()`. 05's `run(env)` is renamed
    `next` (a method named `next` does not shadow the builtin); `run` is not exported.
11. **`astcheck`** runs `wynd.runtime.lint.check_step_module` on every generated module and rejects any `L` finding
    (they are all errors, §5.1) exactly like its other checks (a revision is requested with the finding text).

### 7.1 Modules (owners)
| Unit | Files (`src/wynd/compiler/` unless noted) |
|---|---|
| W0 | `__init__.py` (lazily re-exports `run_compile_job, CompileSession, SessionData, SessionState, Question, Answer, SessionEvent, CompileStep, CompileOptions, CompileReport, ScriptedLLM, RecordingLLM, FakeJobContext`). Stub names for the other modules come from `$DRAFTS/05 §4, §6.3, §7, §13.2–§13.5` |
| CMP-A | `jobs.py` (`run_compile_job`), `session.py`, `gitops.py` (`restore_paths`, `squash_to`), `report.py` (`CompileReport`, `render_commit_message`); tests `tests/conftest.py`, `test_session.py`, `test_jobs.py`, `tests/live/test_compile_live.py`; repo-level `examples/invoices/tests/test_compile_from_proto.py` (M3 acceptance, `@live`) |
| CMP-B | `pipeline.py` (`CompileEnv`, `CompileDeps`, `compile_closure`, `compile_node`, `NodeOutcome`), `decision.py`, `split.py`, `yamledit.py`, `examples.py`; tests `test_decision.py`, `test_split.py`, `test_yamledit.py`, `test_examples.py`, `test_pipeline_offline.py`, `test_dogfood_offline.py`; fixtures `tests/fixtures/ws_mini/**`, `tests/fixtures/code/**` (module sources returned by scripted `write_*` calls), `tests/fixtures/scripts/dogfood.yaml` |
| CMP-C | `schemas.py`, `testgen.py`, `codegen.py`, `astcheck.py`, `tools.py`, `lockfile.py` (incl. `CompiledInfo`, `CompiledSplit`), `attempts.py`; tests `test_schemas.py`, `test_testgen.py`, `test_codegen.py`, `test_astcheck.py`, `test_tools.py`, `test_lockfile.py`, `test_attempts.py`; `tests/fixtures/golden/**` |
| CMP-D | `llm.py` (`CompilerLLM`, `ProviderLLM`, `MemoLLM`, `default_llm`), `calls.py`, `prompts/__init__.py` (`load(name)` via `importlib.resources`, `render(sections)`; transferred from W0), `prompts/*.md`, `testing.py` (`ScriptedLLM`, `RecordingLLM`, `FakeJobContext`); tests `test_llm.py`, `test_prompts.py`, `tests/live/test_llm_calls_live.py`, `tests/fixtures/scripts/*` except `dogfood.yaml` |
Shared test fixture workspace `tests/fixtures/ws_mini/` (process `mini`, `provider: fake`, stdlib-only steps, nodes
normalise/classify/parse/shout/store per `$DRAFTS/05 §16.2`) is owned by CMP-B; `tests/conftest.py` by CMP-A.
`test_pipeline_offline.py` and `test_dogfood_offline.py` need all four units, so they are **not** in any CMP unit's
Accept: they are the 7a barrier (§14), run once all four are done, with failures fixed by the owning unit.

### 7.2 Acceptance (M3)
Offline: `test_pipeline_offline.py` six scenarios of `$DRAFTS/05 §16.2` (straight through; questions and resume with
the **same job requeued**; clarification; skip-and-preserve incl. split removal; user working tree untouched;
integration failure) and `test_dogfood_offline.py` (copy `examples/invoices` into a temp git repo, set
`provider: fake` in the copy's `process.yaml`, delete `steps/`, compile with `tests/fixtures/scripts/dogfood.yaml` — a
`ScriptedLLM` whose `write_*` responses are the M1 hand-written modules (`tests/fixtures/code/**`) — and with
`WYND_FAKE_PROVIDER_SCRIPT` = the sample's `examples/invoices/tests/fixtures/fake_provider.json`). **Pass criteria:**
every step's kind equals the M1 kind; each generated module is byte-identical to its scripted response;
`step.lock.yaml` equals the M1 lock except `compiled` and `locked_deps`; the generated step tests pass in replay;
process examples 1–5 pass. Live (`@live`, M3-INT runs it): `$DRAFTS/05 §16.4` steps
1–8 in a temp copy of `examples/invoices` (extract may be agentic **or** split; fix agentic; read/validate/save/escalate
deterministic; `wynd test` passes in replay with live calls made impossible (§14 M1-INT item 2); re-compile skips
everything; appending one example recompiles only that step).

---

## 8. Package `wynd.controller` (`packages/controller`, wynd-controller, AGPL-3.0-or-later)

A library: the CLI imports it and runs it in-process; `wynd serve-api` serves the same object over FastAPI (SPEC §5).
Neither client imports Docker, the executor or the compiler. `Controller.open(root=None, *, environ=None,
load_dotenv=True)` picks every backend by env var/entry point (`$DRAFTS/06 §5.1` steps 1–9, with the storage call
`stores_from_env(env, data_dir=root/".wynd")`, artefacts `open_artefact_store(state_dir, env)`, runner
`open_job_runner(env.get("WYND_JOB_RUNNER", "subprocess"), **kw)`). **Serving and triggers are lazy:**
`ControllerContext.serving` and `.triggers` are `functools.cached_property`s that call `open_serving_backend(env.get(
"WYND_SERVING_BACKEND", "docker"), **kw)` / `open_trigger_backend(env.get("WYND_TRIGGER_BACKEND", "scheduler"),
**kw)` on first access; `Controller.open` never calls them (they are CTL-REL code, stubs until M4). `Controller.
__init__` constructs every service as `Service(ctx, self)`; the W0 stubs of all controller service classes accept
`(ctx, ctl)` and store them, so `Controller.open` works from M1 even though M4 service methods still raise
`NotImplementedError`. `meta()` reports `llm.ready`/`llm.message` from `wynd.runtime.providers.check_provider(<default
provider>)`. **Backend factory convention** (entry-point groups `wynd.job_runners`, `wynd.serving_backends`,
`wynd.trigger_backends`): `factory(*, env: Mapping[str, str], workspace_root: Path, state_dir: Path, stores: Stores,
handlers: Mapping[str, str] | None = None) -> Backend`. Entry points: `wynd.job_runners` `inprocess =
wynd.controller.jobs.inprocess:InProcessJobRunner`, `subprocess = wynd.controller.jobs.subproc:SubprocessJobRunner`;
`wynd.serving_backends` `docker = wynd.controller.releases.serving:DockerServing`; `wynd.trigger_backends`
`scheduler = wynd.controller.releases.triggers:SchedulerTriggers`. Wheel artifact `src/wynd/controller/web_dist/**`.
**`ControllerContext` fields** (frozen dataclass; `$DRAFTS/06 §5.1` with the changes this section forces; declared by
W0): `root: Path`, `state_dir: Path`, `subdir: str`, `env: Mapping[str, str]` (what serving/triggers are selected
from), `stores: Stores`, `artefacts: ArtefactStore`, `docs: DocStore`, `runner: JobRunner`, `git_lock: GitLock`
(`GitLock(state_dir/"locks"/"git.lock")`; there is no controller `Git` class, all git work calls `wynd.process.git`),
`load_provider: Callable[[str], object]`, `clock: Callable[[], datetime]`; plus `workspace(tree=None) -> Workspace` and
the lazy `serving`/`triggers` properties.

### 8.1 Modules
| Path (`src/wynd/controller/`) | Responsibility (design source) | Unit |
|---|---|---|
| `__init__.py` | lazily re-exports `Controller`, `ControllerContext`, errors, `__version__` | W0 |
| `runs/__init__.py`, `jobs/__init__.py`, `releases/__init__.py`, `chat/__init__.py` | empty | W0 |
| `controller.py` | `ControllerContext` (lazy `serving`/`triggers`, §8), `Controller` (services: `processes, design, jobs, runs, env, serve, uploads, registries, releases, chats`), `open`, `meta`, `close` (`$DRAFTS/06 §5.1`) | CTL-CORE |
| `errors.py` | `WyndError` hierarchy with `code/http/exit` (`$DRAFTS/06 §5.2` verbatim), incl. `ValidationFailed` (mapped from `wynd.process.errors.ValidationFailed`, 422/1), `DirtyTree` (409/3), `DetachedHead` (409/3), `EnvMissing` (`env_missing`, 412/3) | CTL-CORE |
| `models.py` | M1/M2 web DTOs (§3.21 amendments 10–13: `Run`, `Usage`, `JobUsage`-typed `Job`, `Integration`, `BuildResult`, `ProcessStatus`, `ValidationReportDTO`, `EnvCheckDTO`, `ProviderInfoDTO`, `Issue`, …) and controller models (`$DRAFTS/06 §10.4`), pydantic; written complete by W0. M3/M4 DTOs are in `api/models_web.py` | CTL-CORE |
| `store.py` | `DocStore` protocol + `FileDocStore(state_dir/"controller")` (`$DRAFTS/06 §10.2`) | CTL-CORE |
| `workspace.py` | `init_workspace(path, *, commit=True) -> InitResult`, `new_process_files(pid, *, goal) -> dict[str, str]` (templates `$DRAFTS/06 §5.4`; `.gitignore` lines `.wynd/`, `.env`, `__pycache__/`, `.pytest_cache/`; `.gitattributes` cassette LFS line) | CTL-CORE |
| `processes.py` | `ProcessService`: `list, get, new, status, validate, validate_all, steps, step_catalog, interface, builds, read_file, step_source, history, test, sync_interfaces, check_expr` (`$DRAFTS/06 §5.4`); StepInfo/ProcessInterface DTOs built from `LoadedProcess` (`source` mapping `lock→"compiled"`, `proto→"declared"`, `examples→"inferred"`, `process→"process"`, `none→null`; `kind: TraceStepKind`) | CTL-CORE |
| `status.py` | `derive_status(ctx, ws, pid) -> ProcessStatus` — closure HEAD `H` (at the working tree's `HEAD`); **every per-commit input is read at `H` through `load_workspace(root, CommitTree(root, H))`**: design = `compile_state(ws_at_H, pid).design`; compiled = not design and `tests_status(runs, ws_at_H, pid, H)["status"] == "passed"`; built = `artefacts.get_build(pid, H)`; released = enabled `Release` documents read from `ctx.docs`, each evaluated at its own commit X (`CommitTree(root, X)`), with `behind = count_touching(X, H, closure)`; working-tree dirt is reported only in the separate `dirty` field (paths from `dirty_paths(ws_root, closure)`) and never changes a flag. Never stored. | CTL-CORE |
| `envfile.py`, `envcheck.py` | `.env` parsing/loading; `EnvService.resolve(extra_files=())` (precedence: `os.environ` > extra files > `root/.env` > `registry.secrets()`), `resolve_image(pid, extra_files=()) -> dict` (= `resolve()` plus `WYND_REGISTRY_JSON` = compact JSON of `registry_snapshot(lp, registry)` when non-empty, §3.10; used for every image/release run and container), `manifest(pid, commit=None)` (build manifest if built else `assemble_env_manifest`), `check(pid, *, extra_files=(), mode="local") -> EnvCheckDTO`, `check_release(release)` | CTL-CORE |
| `registries.py` | `RegistryService`: MCP (`McpServerEntry`), providers (`ProviderEntry` merge; kind from entry point; exactly cheap/standard/strong after merge; DTO `ProviderInfoDTO`), image registries (`wynd.process.artefacts.ImageRegistryEntry`, one default); `remove_*` reports `referenced_by`; `oauth_start/oauth_complete/refresh_tokens` delegate to `oauth.py` | CTL-CORE |
| `runs/service.py` | `RunService`: `run(pid, inputs, *, target, on_event, trigger)` (blocking), `start(req, *, trigger)` (thread), `get`, `list`, `events(run_id, since)`, `follow`. **Env gate:** before any local or image run, `ctl.env.check(pid, mode="local"\|"image")` (image/release targets: against the build's manifest and `resolve_image`); errors ⇒ raise `EnvMissing` (CLI exit 3, HTTP 412) and create no `RunRecord`. Local target = `wynd.process.local.run_local(ws, pid, inputs, env=ctl.env.resolve(), stores=ctx.stores, metadata={"trigger", "release_id", "target"}, …)`; image/release targets call `runs/image.py`. The `Run` DTO is the `RunRecord` projection (§3.21 amendment 11) | CTL-CORE |
| `runs/tree.py` | `render_tree(tree, *, full=False) -> str` (format `$DRAFTS/06 §5.10`; M5 `edge.check` line format `$DRAFTS/08 §4.7`) | CTL-CORE |
| `git.py` | `GitLock` (`threading.RLock` + `fcntl.flock` on `.wynd/locks/git.lock`; a context manager); every main-worktree mutation and worktree add/remove holds it (CTL-CORE's `workspace.py`/`processes.py` code against this one-line contract); all git work calls `wynd.process.git` | CTL-JOBS |
| `jobs/records.py`, `jobs/handlers.py` (`DEFAULT_HANDLERS`, `PHASE_HANDLERS`, `resolve_handler`), `jobs/checkout.py` (`Checkout`, `CheckoutBackend`, `WorktreeCheckout`, `CloneCheckout`), `jobs/harness.py` (`execute_job(job_id, *, phase=…)`, `JobCancelled`, §3.18), `jobs/inprocess.py`, `jobs/subproc.py`, `jobs/worker.py` (`python -m wynd.controller.jobs.worker --workspace P --job ID [--checkout worktree\|clone] [--remote URL] [--phase all\|prepare\|finalize]`), `jobs/runners.py` (`open_job_runner`, `pid_alive`), `jobs/service.py` (`JobService`: `submit, submit_compile, submit_test_live, submit_build, submit_bake, answer, get, list, logs, cancel, wait, integrate`; `answer` delegates to `compile_view.apply_answers`, §3.18), `jobs/integrate.py` (wrapper around `wynd.process.git.integrate` + remote fetch + test-result re-key + record update) | §3.18; `$DRAFTS/06 §5.8–§5.9, §6` (subprocess runner included in M1) | CTL-JOBS |
| `runs/image.py`, `runs/mirror.py`, `docker.py`, `serving.py` (`ServeService.serve/acquire/stop/list`), `base.py` (`build_base`, `publish_base` wrappers; `VersionMismatch` unless `version == wynd.runtime.__version__`) | `$DRAFTS/06 §5.10, §5.12, §5.15` with: path-typed inputs sent as run-API `files` by the §3.21 amendment 7 rule; container env = `EnvService.resolve_image(pid)` filtered to the manifest's vars **minus the host-side storage selectors (`STORAGE_ENV`, incl. `WYND_HOME`, `WYND_REGISTRY`) and `WYND_HOST`/`WYND_PORT`** (`serving.container_env`; passing the host's values would override the base image's `WYND_REGISTRY=env`), plus `WYND_REGISTRY_JSON` whenever the current snapshot is non-empty (so registry changes made after the build still reach the container, and `WYND_RUN_API_TOKEN` is passed too; `RunApiClient(token=env["WYND_RUN_API_TOKEN"])`); `runs/image.py`, `ServeService` and `ReleaseService` take an injectable `run_api_client: Callable[[str], RunApiClient] = RunApiClient`; container records `.wynd/serve/<container>.json` carry `started_at`/`stopped_at` (uptime, SPEC §15); container names `wynd-<slug(pid)>-<commit[:7]>` | CTL-M2 |
| `compile_view.py` | `session_dto(session) -> CompileSession`, `answer_text(q, a) -> str`, `apply_answers(job, answers) -> tuple[dict, bool]` (uses `wynd.compiler.CompileSession.from_json(...).answer(...)`; returns the new session JSON and whether it is `ready`); W0 stub raises `NotImplementedError` | CTL-M3 |
| `design.py` (`DesignService.get/save/commit/scope/lock/unlock/locked_by`), `search.py`, `uploads.py` | `$DRAFTS/06 §5.6–§5.7`, `$DRAFTS/07 §12.3` (design scope; revision `sha256:`; YAML via `yaml_to_json`/`dump_yaml`; commits via `process.git.commit_only` so a commit never spans two processes) | CTL-DESIGN (M4) |
| `oauth.py` | MCP OAuth (RFC 9728 → 8414 discovery, RFC 7591 dynamic registration, PKCE S256; tokens stored with `Registry.put_secret` as `WYND_MCP_<NAME>_TOKEN` (`NAME = name.upper().replace("-", "_")`); entry gets `auth_env`, `headers {"Authorization": "Bearer ${env:WYND_MCP_<NAME>_TOKEN}"}` and `oauth`; `refresh_tokens()` before runs/serve/env check/scheduler ticks) — `$DRAFTS/06 §5.13` | CTL-OAUTH (M4) |
| `releases/store.py`, `releases/cron.py` (`CronExpr`), `releases/service.py` (`ReleaseService`), `releases/scheduler.py` (`Scheduler`), `releases/serving.py` (`ServingBackend`, `DockerServing`, `open_serving_backend`), `releases/triggers.py` (`TriggerBackend`, `SchedulerTriggers`, `open_trigger_backend`) | `$DRAFTS/06 §5.14` verbatim except: `ServingBackend.ensure(release, manifest: EnvManifest, env: Mapping[str, str]) -> str` where `env` is built with `wynd.controller.serving.container_env(manifest, env)` from `EnvService.resolve_image` (incl. `WYND_REGISTRY_JSON`, `WYND_RUN_API_TOKEN`); no mounts; `trigger(release_id, inputs, *, source)`; fire records carry `started_at`/`finished_at` and the serving container's `started_at`/`stopped_at` are kept per release (SPEC §15) | CTL-REL (M4) |
| `chat/store.py`, `chat/events.py`, `chat/prompt.py`, `chat/tools.py`, `chat/engine.py` (`ChatService`) | `$DRAFTS/06 §7` verbatim; tools built with `wynd.runtime.tools.handles_for`; provider `WYND_CHAT_PROVIDER` (default claude-code) at tier `WYND_CHAT_TIER` (default standard); `builtin_tools=[]`; `cancel=turn.cancelled` | CTL-CHAT (M4) |
| `api/__init__.py` (`create_app`, `serve`), `api/app.py`, `api/sse.py`, `api/models_web.py` (M3/M4 web DTOs, written complete by W0), `api/routes_meta.py`, `api/routes_processes.py`, `api/routes_jobs.py`, `api/routes_runs.py`, `api/routes_chats.py`, `api/routes_releases.py`, `api/routes_registries.py`, `api/static.py` | §3.21; `$DRAFTS/06 §8` | CTL-API (M4) |
| `optimise.py` | `optimise_report`, `submit_optimise`, `run_optimise_job(ctx) -> JobOutcome`, `router: APIRouter` with `GET/POST /api/processes/{pid}/optimise` (W0 stub: `router = APIRouter()`, empty; mounted by `api/app.py` before the catch-all) — `$DRAFTS/08 §3.7–§3.8` adapted to §3.18 (`ctx.commit` instead of `ctx.git`; tests via `wynd.process.testing`) | OPT-CTL (M5) |

**Job submit preconditions** (`JobService.submit`): the process loads and validates without errors
(`ValidationFailed`); `dirty_paths(ws_root, None)` empty — the whole workspace directory (`DirtyTree`, §3.19);
commit-producing kinds require a current branch (`DetachedHead`) and use `ref = HEAD` of that branch with
`inputs["target_branch"]` = that branch; `build`/`bake` use `ref = closure_head`. With `WYND_GIT_REMOTE` set, push the
target branch before submitting and fetch result branches before integrating (configuration, not an environment
branch).

### 8.2 Tests (`packages/controller/tests/`)
Adopt `$DRAFTS/06 §11` (fixture workspace `ws_basic` + `subdir_workspace`; fakes: `FakeRunner`, `FakeServing`,
`FakeTriggers`, `FakeProvider`, `FakeDocker`, `FakeClock`, `httpx.MockTransport` for OAuth only; unit test files per
module owner; API tests with `TestClient(create_app(ctl, scheduler=False))`; `test_web_contract.py` validating every
`packages/web/src/api/fixtures/*.json` against its model per `index.json`, run in sub-wave 9b; `test_static_mount.py`;
`test_no_env_branches.py`). The run API is faked with `FakeRunApiClient` (`tests/support/ctl_m2_runapi.py`) passed
through the `run_api_client` injectable — the stdlib `RunApiClient` cannot be intercepted by `httpx.MockTransport`.
`tests/conftest.py` is owned by CTL-CORE; other controller units add helpers under `tests/support/<unit>_*.py`.
Cases this plan adds: **CTL-CORE** — a local run with `RECORDS_DIR` unset raises `EnvMissing` and creates no
`RunRecord`; an uncommitted edit to `process.yaml` changes `dirty` but not `design`/`compiled` (status reads
`CommitTree`). **CTL-M2** — a `.wynd/uploads` path and a workspace file path both become `files` + `{"$file"}`; a
non-existent path passes through. `@docker` (`test_docker_e2e.py`, CTL-M2; offline and deterministic — no
credentials): builds the **fake-provider dogfood variant** (temp git copy of `examples/invoices` with `provider:
fake`, `WYND_FAKE_PROVIDER_SCRIPT` inline JSON from `examples/invoices/tests/fixtures/fake_provider.json`), serves it,
runs examples 1–5 with `--image` and asserts identical `(step, exit, outputs)` sequences to `run --local` of the same
copy, a second run without Docker calls; plus a fixture process with one agentic step using an http MCP server served
by `mcp/fake_server.py` on the host (`WYND_MCP_<NAME>_URL=http://host.docker.internal:<port>/mcp`, registry entry
reaching the container only through `WYND_REGISTRY_JSON`; the run succeeds only if the runtime connects and verifies
the MCP snapshot before the fake model call, and fails `env-check` at startup when `WYND_REGISTRY_JSON` is withheld);
release manual + webhook are added in M4-INT (CTL-REL helper). `@live @docker` (`test_docker_claude_code_live`, CTL-M2): one claude-code agentic call inside a served image
with `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` from the env or `examples/invoices/.env`; skipped with the reason
`no claude-code credential for containers (set CLAUDE_CODE_OAUTH_TOKEN; see docs/providers.md)` when neither is set.
`@live`: chat turn edits only `process.yaml` in one commit.

---

## 9. Package `wynd.cli` (`packages/cli`, wynd-cli, AGPL-3.0-or-later)

`wynd = wynd.cli.main:main` (typer). Commands, flags, output rules and exit codes: §3.22; behaviour per
`$DRAFTS/06 §9` (compile uses `--accept-proposals`). Each `commands/<group>.py` exposes
`register(app: typer.Typer) -> None`; `main.py` calls every module's `register` (W0 stubs register nothing). W0's
`main.py` stub already defines `app = typer.Typer()` with a root `@app.callback()` holding `--workspace/-C` and
`--version` (verified: a Typer app with no command and no callback fails `--help`), so `uv run wynd --help` works
before any command exists.
| File (`src/wynd/cli/`) | Commands | Unit |
|---|---|---|
| `__init__.py`, `commands/__init__.py` (empty) | – | W0 |
| `main.py`, `context.py` (`CliState`, `get_controller` lazy), `output.py`, `inputs.py` (`parse_inputs` with path absolutising via `ctl.processes.interface(pid)`, `parse_answers`) | root app, `--workspace/-C`, `--version`, error mapping | CLI-M1 |
| `commands/workspace.py` | `init`, `new`, `validate [--sync-interfaces]`, `status` | CLI-M1 |
| `commands/test.py` | `test`, `test --live` (job + wait + integrate) | CLI-M1 |
| `commands/run.py` | `run --local\|--image`, `trace` | CLI-M1 |
| `commands/env.py` | `env check` | CLI-M1 |
| `commands/registries.py` | `mcp`, `provider`, `registry` add/list/remove | CLI-M1 |
| `commands/jobs.py` | `jobs list/show/logs/answer/cancel/integrate` | CLI-M1 |
| `commands/build.py` | `build`, `bake`, `base build/publish` | CLI-M2 |
| `commands/serve.py` | `serve` | CLI-M2 |
| `commands/compile.py` | `compile` | CLI-M3 |
| `commands/api.py`, `commands/release.py`, `commands/search.py` | `serve-api`, `release …`, `search` | CLI-M4 |
| `commands/optimise.py` | `optimise` | OPT-CTL (M5) |
Tests (`packages/cli/tests/`, `typer.testing.CliRunner`, `get_controller` monkeypatched to a real `Controller` with
fake backends (the controller exists by sub-wave 3c); `test_cli_imports.py` AST rule §3.22); `tests/conftest.py`
owned by CLI-M1; CLI-M2/M3/M4 and OPT-CTL put helpers in `packages/cli/tests/support/<unit>_*.py` and never edit the
conftest.

---

## 10. Package `web` (`packages/web`, npm, AGPL-3.0-or-later) — M4

Adopt `$DRAFTS/07` in full (stack and versions §3.2; Vite config writing the bundle to
`packages/controller/src/wynd/controller/web_dist/`; dev proxy to `127.0.0.1:8780`; URL state, stores, query cache;
typed API client; SSE; theming with tokens on `:root` + dark overrides; accessibility; graph editor over raw JSON docs
with path-preserving ops; own layered layout; expression lexer/rename; inspector; autosave (800 ms debounce, 5 s max
wait) and commit boundaries (blur, hidden, process switch, before job/chat/integrate, unload) — never per keystroke,
never two processes; chats with the "acting on" chip and design lock; job cards with compile session, answers,
integration; releases UI; run panel with SSE trace tree; Markdown subset; tests with vitest + jsdom + RTL, no
Playwright), with these amendments:
1. `src/api/types.ts` = §3.21 amendments 1–15: `Issue.severity` includes `"info"`, `Issue.file: string | null`;
   path fields detected by `format === "path"`; `JobKind` five kinds; `Integration` = `IntegrationResult` (mode incl.
   `"pr_branch"`, `reason`, `pr_url`); `Run.status` = `queued|running|succeeded|failed`, `Run.finished_at`;
   `Usage`/`JobUsage` per amendment 10; `BuildResult.build_dir`; `ProcessStatus.tests` `passed|failed|unknown`;
   `Meta.latency = ["fast","normal"]`; `StepInfo.kind` = `TraceStepKind`; `TraceEvent = {v, seq, ts, run_id, type,
   [k]: Json}` with narrowing helpers (the trace tree reducer in `src/model/trace.ts` consumes
   `step.start`/`step.end`/`edge.taken`/`edge.check`/`model.call`/`tool.call`/`step.log`/`run.start`/`run.end`,
   parent/child via `span`/`parent` and the `step` path); `ProcessError` per §3.8; file reads via
   `/api/processes/{id}/files?path=`; `POST /api/providers` body `{name, tiers?}`.
2. `processDoc.parseTarget` understands `$ignore` (rendered as a muted terminal node `x:$ignore`, one per process) and
   the M5 branch fields `check`/`context` (edited in `BranchCard`: "Check (agentic)" textarea + context chips; shown
   only when the edge `kind` is `agentic`; `meta.edge_kinds` lists both kinds from M4). `is_else` = no `when` and no
   `check`.
3. Uploads: `POST /api/uploads` returns a path usable for every target (the controller forwards files for image runs).
4. Add `src/components/registries/RegistriesDialog.tsx` (WEB-OPS): lists MCP servers and providers, adds an MCP server
   (url, auth env), starts OAuth (`POST …/oauth/start` → `window.location.assign(authorize_url)`), removes entries;
   opened from a header "Settings" button.
5. No process-level test UI; node positions in `localStorage["wynd.layout.<root>.<pid>"]` only (never in YAML).
**WEB-SCAFFOLD** (sub-wave 9a, the only web unit in it) writes: `package.json` and `package-lock.json` with **exact**
pins (versions per `$DRAFTS/07 §3.2`, resolved from the npm registry at that time), `tsconfig*.json`,
`vite.config.ts`, the vitest config, `index.html`, `src/test/*` (setup, render helpers, fake fetch/SSE),
`src/api/types.ts` (amendment 1), `src/api/fixtures/*.json` + `index.json` (one fixture per DTO, used by
`test_web_contract.py`), and a **stub for every file in `$DRAFTS/07 §16`** exporting its final names and props types
(components render `null`, functions throw `Error("not implemented")`); it runs `npm --prefix packages/web ci` once
and its Accept is `npm --prefix packages/web run typecheck` on the stubs. After that nobody runs `npm ci`,
`npm install` or `npm run build`; package files are frozen.
Owners from sub-wave 9b (files per `$DRAFTS/07 §16`; stubs transferred from WEB-SCAFFOLD): **WEB-CORE** (`index.html`,
`src/main.tsx`, `src/App.tsx`, `src/styles/app.css`, `src/api/*` except `types.ts`, `src/api/fixtures/*` (transferred),
`src/state/{store,query,url,connection,toasts,theme}.ts`, shell/common components), **WEB-GRAPH**
(`src/model/{json,processDoc,protoDoc,expr,cycles,layout,graph,schemaText,values,issues}.ts`,
`src/state/{design,openProcess}.ts`, `components/{graph,inspector,process-settings,yaml,examples}/*`, incl.
`ValueInput`), **WEB-CHAT** (`src/state/{chat,jobs}.ts`, `components/chat/*`), **WEB-OPS**
(`src/model/{status,trace}.ts`, `components/{process,runs,releases,registries}/*`). `src/api/types.ts` is frozen after
9a (a needed change goes to the lead). Cross-unit imports (`App.tsx` mounting other units' components, OPS's
`SchemaForm` using GRAPH's `ValueInput`, OPS's `ProcessActions` calling `state/design.ts`, the header opening
`RegistriesDialog`) resolve against the stubs' exported names and props. Each owner appends CSS under its prefix in a
file it owns: `src/styles/{app,graph,chat,ops}.css` (WEB-CORE imports all four). A unit's Accept is
`npm --prefix packages/web exec -- vitest run <its own test files>`; whole-project `typecheck`, `test`, `check` and
`build` (which writes `web_dist`) run only in M4-INT. Scripts: `dev`, `build`, `typecheck`, `test`, `check`.

---

## 11. Package `wynd.kube` (`packages/kube`, wynd-kube, AGPL-3.0-or-later) + `deploy/` — post-M5

Adopt `$DRAFTS/08 §5` (config, httpx REST client + `FakeKubeClient`, names, env mapping, Job/CronJob/Release/install
renderers with goldens, runner, triggers, serving, checkout, fire, CLI, toolchain image, tests incl. `@kube` kind e2e)
with these reconciliations:
1. Factories follow §8's convention: `KubeJobRunner.from_env`, `KubeServingBackend.from_env`,
   `KubeTriggerBackend.from_env` all accept `(*, env, workspace_root, state_dir, stores, handlers=None)`; records via
   `stores.runs` (`create/update/get`) as `JobRecord` dicts (§3.18); job ids from `new_id("job")`.
2. Job pods run `python -m wynd.controller.jobs.worker --workspace /work/repo/<subdir> --job <id> --checkout clone
   --remote $WYND_KUBE_GIT_URL [--phase prepare|finalize]` (not `jobmain`); the `checkout` init container runs
   `wynd-kube checkout`. Build Jobs: `prepare` (writes the context to `$WYND_BUILD_CONTEXT_DIR`), rootless
   `buildctl-daemonless.sh` init container, `finalize` — i.e. `execute_job(id, phase="prepare")` then
   `execute_job(id, phase="finalize")` (§3.18 `PHASE_HANDLERS`), sharing an `emptyDir` mounted at
   `$WYND_BUILD_CONTEXT_DIR` in all three containers.
3. Controller port **8780** everywhere (`WYND_KUBE_CONTROLLER_URL=http://wynd-controller:8780`, probes
   `/api/health`).
4. `fire` calls `POST {controller}/api/releases/{id}/trigger` with `{"source": "schedule"}` and the bearer token.
5. `KubeServingBackend.ensure(release, manifest, env)`: secret vars come from the per-process Secret
   `wynd-p-<slug(process)>` (never from `env`); non-secret values from `env` — including `WYND_REGISTRY_JSON` (§3.10)
   and any `WYND_MCP_<NAME>_URL` — go into the release ConfigMap; returns `http://<release name>.<ns>.svc:8080`.
   `status(release)` and `remove(release_id)` per the §8 protocol. Pod start/stop times are read back into the
   release's serving record (uptime, SPEC §15).
6. Toolchain image `deploy/wynd.Dockerfile` (web build stage + `uv sync --locked --no-dev --all-packages
   --no-editable`); `claude-agent-sdk` arrives through `wynd-compiler`'s dependency (no extra `uv pip install`).
Module owner: **KUBE** (all of `packages/kube/**` except the W0 `__init__.py` files, `deploy/**`,
`docs/cluster-install.md`). W0 stubs every module of `$DRAFTS/08 §5.1` and writes an empty `__init__.py` for each kube
subpackage listed there.

---

## 12. Sample workspace `examples/invoices/` (the dogfood; SPEC §5.1, §12)

Adopt `$DRAFTS/08 §6` (corrected process: `provider: claude-code`, no `latency: fast`; `extract` also outputs
`supplier`; `validate` returns structured `errors: list[{field, code, message, fixable}]` and always a normalised
`record` with `key`; `fix` also receives `invoice_text`; `escalate` receives `queue_dir: env.ESCALATIONS_DIR` and
`run_id: run.id`; env vars `RECORDS_DIR`, `REVIEW_DIR`, `ESCALATIONS_DIR`; six proto-steps verbatim; five sample PDFs
generated by the stdlib `tools/make_sample_pdfs.py` verbatim, plus the M5 `umbrella_proforma_88.pdf`; hand-written
step modules verbatim; process examples with `env:` and `{tmp}`) with these reconciliations:
1. Step packages follow §3.6 exactly (`step.lock.yaml` in the spec `StepLock` shape; `entrypoint: <name>:<Class>`;
   agentic locks `tier: cheap, thinking: low, retries: {run: 2, validation: 2, tool: 1}`; `fragment.deps` and
   `locked_deps` (`pypdf==6.19.0` for read_pdf); `interface` = the `interface` field of `uv run python -m
   wynd.runtime.describe <pkg dir> <entrypoint>` (run from the repo root; identical to what `wynd validate
   --sync-interfaces` writes); `proto_hash` = `wynd.spec.proto_hash(load_proto_step(<proto>))` — both **real values
   written by SAMPLE-STEPS** in sub-wave 3a, never null placeholders (a null `proto_hash` next to a proto makes the step
   design-phase, which breaks planning)).
2. Step tests use `load_step(Path(__file__).parent)` + `run_step` + `expect` (§3.20), one `test_example_<n>` per proto
   example (path examples resolve against the process dir; `{tmp}` via `tmp_path`; `exit: error` example for a missing
   PDF), plus `test_ticket_contents` in `test_escalate_to_human.py`.
3. **No `tests/test_process.py` / `test_side_effects.py`.** Process examples 1–5 (6 in M5) run from `process.yaml`
   through `wynd test`; process cassettes live at `processes/process_supplier_invoice/cassettes/`.
4. `process.yaml` declares `env.vars` for `RECORDS_DIR`, `REVIEW_DIR`, `ESCALATIONS_DIR` with descriptions (they feed
   the manifest and type as non-null strings in M3 checks, keeping `wynd validate` warning-free). The M1
   `process.yaml` has **no** branch `name:`s. `.env.example` as in 08 §6.3 plus a commented
   `# CLAUDE_CODE_OAUTH_TOKEN=` line ("dev key from `claude setup-token`; needed only for image runs"); `.env` is
   git-ignored; `wynd.yaml` has no `kind:` key (§3.3); `LICENSE` AGPL-3.0-or-later (copied from
   `LICENSES/AGPL-3.0-or-later.txt`); `README.md` with the quickstart commands.
5. Acceptance tests owned by the sample: `tools/test_make_sample_pdfs.py` (drift: PDFs byte-identical, proto example
   texts equal `text_of(name)`, writer rejects blank lines/non-cp1252), `tests/fixtures/fake_provider.json`
   (SAMPLE-STEPS: a `$DRAFTS/03 §14.5` fake-provider script answering extract/fix for examples 1–5 — the same
   records the live model is expected to return), `tests/test_offline_e2e.py` (M1-INT; default suite, offline and
   deterministic: copies the workspace into a temp git repo, sets `provider: fake` and `WYND_FAKE_PROVIDER_SCRIPT`,
   runs `wynd validate` and `wynd run --local` for examples 1–5 through the CLI and asserts exits and outputs; the
   same variant feeds the `@docker` parity test and CMP-B's `test_dogfood_offline.py`), `tests/test_live_smoke.py`
   (`@live`: `wynd run --local` on example 1 exits `done`), `tests/test_compile_from_proto.py` (`@live`, M3; owned by
   CMP-A), `tests/test_optimise_live.py` (`@live`, M5; owned by OPT-CTL).
6. M5 changes (SAMPLE-M5): `validate.done` becomes `kind: agentic` with `name: save`, `check:` and
   `context: [steps.read.outputs]` on the save branch and `name: fix` on the fix branch (the escalate else stays
   unnamed; branch keys `validate.done[save]`, `validate.done[fix]`, `validate.done[2]`); example 6 (umbrella pro forma →
   `needs_review`); `edges.lock.yaml` written by `wynd compile`; process cassettes re-recorded with `wynd test --live`.

---

## 13. Docs, repo infrastructure, CI

- **W0:** root `.gitignore` (`$DRAFTS/08 §8.1` + `packages/controller/src/wynd/controller/web_dist/` + `.env` at any
  depth), `.gitattributes` (`**/cassettes/** filter=lfs diff=lfs merge=lfs -text`, `*.pdf binary`), `LICENSE` notice,
  `LICENSES/Apache-2.0.txt` and `LICENSES/AGPL-3.0-or-later.txt` **downloaded verbatim** (`curl
  https://www.apache.org/licenses/LICENSE-2.0.txt`, `curl https://www.gnu.org/licenses/agpl-3.0.txt`), per-package
  `LICENSE` copies (spec/runtime Apache; process/compiler/controller/cli/web/kube AGPL; `examples/invoices/LICENSE` is
  copied from `LICENSES/AGPL-3.0-or-later.txt` by SAMPLE-DATA), root `conftest.py`, `tests/test_skeleton.py` (no
  `packages/*/src/wynd/__init__.py`; every module in this plan imports; every W0-complete data type has exactly the
  field names of this plan's code blocks).
- **Root `conftest.py`** (W0): the `$DRAFTS/08 §8.3` gate for `live`/`docker`/`kube` + marker registration; an
  **autouse isolation fixture** applied only to items under the root `testpaths` (so step suites run by `wynd test`,
  which need `WYND_CASSETTE_*`, are unaffected): `WYND_HOME` → a tmp dir; delete `WYND_WORKSPACE`, `WYND_DATA_DIR`,
  `WYND_CASSETTE_MODE`, `WYND_CASSETTE_RECORD_DIR`, `WYND_CASSETTE_LITERALS`, `WYND_EVENTS_FILE`,
  `WYND_REGISTRY_JSON`; set `WYND_JOB_RUNNER=inprocess`, `WYND_REGISTRY=file`, `GIT_AUTHOR_NAME/EMAIL` and
  `GIT_COMMITTER_NAME/EMAIL` (`Wynd Test <test@wynd.invalid>`), `GIT_CONFIG_NOSYSTEM=1`; remove `ANTHROPIC_API_KEY` and
  `CLAUDE_CODE_OAUTH_TOKEN` unless the item is `@live`; `Controller.open(load_dotenv=…)` in tests only ever sees tmp
  workspaces. A session-scoped `shared_venvs` fixture returns `tmp_path_factory.getbasetemp() / "venvs"`; workspace-copy
  fixtures symlink `<ws>/.wynd/venvs` to it and set `UV_OFFLINE=1` (§6.6).
- **DOCS (post-M5):** `README.md` (`$DRAFTS/08 §7.1`, incl. prior art from SPEC §16, licences, telemetry: none
  collected), `docs/architecture.md`, `docs/run-api.md` (covers every route of §3.17; hygiene test checks it against
  `wynd.runtime.supervisor.http.ROUTES`), `docs/providers.md` (claude-code auth three ways incl. the container dev key
  `CLAUDE_CODE_OAUTH_TOKEN` from `claude setup-token`; tier defaults; the tier parity rule and its `@live` check
  (§3.15); cassette keys include the resolved model id, so remapping a tier misses with `NO_RECORDING` and needs
  `wynd test --live`), `docs/testing.md`, `docs/agentic-edges.md`, `docs/optimise.md`; `tests/test_repo_hygiene.py`
  (`$DRAFTS/08 §8.5`: cassettes are LFS pointers, licence files byte-identical, namespace rule, gitignore, doc links,
  run-API doc coverage); `.github/workflows/ci.yml` (`$DRAFTS/08 §9`: jobs `python` (offline: `uv sync --locked
  --all-packages`, `uv run pytest -q`, then in `examples/invoices`: `wynd validate`, `wynd env check`, `wynd test`),
  `web` (`npm ci`, `typecheck`, `test`, `build`), `docker` (push to main / label / dispatch; the offline fake-provider
  `-m docker` suite, no secrets needed), `kube` (**push to main** and dispatch: kind from a CI action — not a Python
  dependency — then `WYND_KUBE=1 uv run pytest -m kube`), so the cluster path is tested on the same trigger as the
  laptop path (SPEC §15)).
- **Root pytest config** (W0, in root `pyproject.toml`): `addopts = ["-ra", "--import-mode=importlib"]`,
  `testpaths = ["packages/spec/tests", "packages/runtime/tests", "packages/process/tests", "packages/compiler/tests",
  "packages/controller/tests", "packages/cli/tests", "packages/kube/tests", "tests", "examples/invoices/tools",
  "examples/invoices/tests"]`, `pythonpath = ["packages/runtime/tests", "packages/process/tests",
  "packages/compiler/tests", "packages/controller/tests", "packages/cli/tests", "packages/kube/tests"]` (for
  `support.<unit>_*` helpers, §0 rule 10), `norecursedirs = ["steps", "node_modules", ".wynd", ".venv", "fixtures",
  "design"]`. Step and process tests of workspaces run only under `wynd test` (they need step venvs), except where a
  unit's Accept runs a deterministic step dir explicitly from the repo root.

## 14. Work breakdown (waves → units)

Rules: units in one (sub-)wave run in parallel with **disjoint files**; a (sub-)wave starts when the previous one's
units are done and the lead has committed its checkpoint (§0 rule 9); integration waves run alone. "Owns" lists every
file/dir the unit may create or edit (tests included). "May import" is the set of `wynd.*` packages its code may
import (dependency direction §2). "Accept" are the commands that must pass before the unit is done (all offline
unless marked); they name the unit's **own test files** (never `-k`), written with shell brace expansion where
convenient (`R=packages/runtime/tests`, `P=packages/process/tests`, `C=packages/controller/tests`,
`K=packages/compiler/tests` below). Every unit also keeps `uv run pytest tests/test_skeleton.py` green, keeps every
file it owns importable (§0 rule 8) and adds no dependency.

### Wave 0 — skeleton (three steps)
**W0-ROOT** (alone). Owns: root `pyproject.toml` (virtual workspace, members `packages/*`, `exclude =
["packages/web"]`, `[project] dependencies` = all seven members and `[tool.uv.sources]` `{ workspace = true }` for each
(§1.2), dev group §1.3, pytest config §13), `.python-version` (`3.12`), `uv.lock`, `conftest.py` (§13), `.gitignore`,
`.gitattributes`, `LICENSE`, `LICENSES/*`, `packages/{spec,runtime,process,compiler,controller,cli,kube}/pyproject.toml`
(version `0.1.0`, `license`, `license-files = ["LICENSE"]`, `hatchling>=1.27`, `packages = ["src/wynd"]`, dependencies
§1.3, scripts and entry points §5/§6/§8/§9/§11 incl. `registry.env`, controller wheel `artifacts`), `packages/*/LICENSE`
(incl. `packages/web/LICENSE`), `packages/*/README.md`, and **every W0 `__init__.py`**: the lazily re-exporting package
inits (`wynd/{spec,spec/expr,runtime,process,process/build,compiler,controller,cli,kube}/__init__.py`, module
`__getattr__` over the names in §4.2, §5.1, §6.1, §7.1, §8.1, §9, §11) and the empty subpackage inits (runtime
`worker/`; controller `runs/`, `jobs/`, `releases/`, `chat/`; cli `commands/`; every kube subpackage of
`$DRAFTS/08 §5.1`). Checks that `docs/design/` holds the eight drafts and the spikes (§ preamble). Accept: `uv lock`;
`uv sync --all-packages`; then plain `uv sync` leaves every member installed (`uv run python -c "import wynd.spec,
wynd.runtime, wynd.process, wynd.compiler, wynd.controller, wynd.cli, wynd.kube"`).

**W0-SPEC, W0-RUNTIME, W0-PROCESS, W0-COMPILER, W0-CONTROLLER (incl. cli), W0-KUBE** (parallel; each owns
`packages/<pkg>/src/wynd/<pkg>/**` except W0-ROOT's inits). Each writes a **stub for every module path** of its
package in §4.1, §5.1, §6.1, §7.1, §8.1, §9, §11 (public names; bodies `raise NotImplementedError("PLAN §x")`). Where
this plan does not list a module's names, the name source is: compiler `$DRAFTS/05 §4, §6.3, §7, §13.2–§13.5`;
controller M4 modules `$DRAFTS/06 §5.6–§5.14, §7, §8`; kube `$DRAFTS/08 §5.1`. **Complete definitions (not stubs)**,
copied mechanically from this plan's code blocks and the cited draft sections:
- spec: `base.py` constants/aliases; `errors.py` (`Diagnostic`, `SpecError`, `format_loc`); every model and dataclass in
  `records.py`, `lockfiles.py` (incl. `TraceStepKind`, `HARNESS_BUILTINS`), `plan.py`, `fragments.py`,
  `env_manifest.py` (`EnvManifest`, `EnvCheck`), `interface.py` (`Interface`), `workspace.py` (`WorkspaceConfig`,
  `StepRoot`, `UseRef`, layout constants, `RESERVED_PROCESS_SEGMENTS`), `process_doc.py`, `proto_step.py` (fields
  only; SPEC-CORE adds validators/normalisation), `expr/scope.py` dataclass fields;
- runtime: `usage.py` (`Usage`, `ModelInfo`), `errors.py` exception classes, `policy.py` (`ExecPolicy`,
  `CassetteConfig`), `worker/protocol.py`, `providers/types.py` (all dataclasses, `ProviderError`, protocols),
  `providers/scripted.py` (`ScriptedModelProvider`, `ScriptedAgentProvider`), `agentic/errors.py` (exception classes;
  `ProviderError` re-exported), `storage/base.py` protocols, `storage/models.py` (`RunRecord`, `TestResult`),
  `executor/edges.py` seam types (§5.4), `executor/engine.py` `ProcessResult`, `supervisor/schema.py` `SUPERVISOR_ENV`;
  `edges.py` stub re-exports the seam and `WorkerEdgeChecker.__init__(pool, plan)` stores its args;
- process: `jobs.py` types (§3.18 incl. `JobUsage`), `loader.py` §6.2 dataclasses, `validation/__init__.py`
  `ValidationReport`, `validation/exprcheck.py` `TypedSite`, `git.py` `IntegrationResult`, `artefacts.py` (`BuildInfo`,
  `ImageRegistryEntry`), `testing.py` (`TestCase`, `SuiteResult`, `TestReport`); hook stubs that return values:
  `validation.typecheck.check_types(lp, sites) → []`, `validation.agentic.check_agentic_edges → []`,
  `latency.latency_warnings → []`, `edges_lock.sync_edge_lock → (EdgesLock(), False)`;
- controller: `models.py` and `api/models_web.py` DTOs (§3.21); `errors.py` (the whole hierarchy with its
  `code/http/exit` values, `$DRAFTS/06 §5.2` + the §8.1 overrides; `WyndError.__init__` stores `message`, `details`,
  `hint`), so CTL-JOBS can raise them while CTL-CORE runs in parallel; `controller.py` `ControllerContext` field
  declarations (§8); every service class stub accepts `(ctx, ctl)` and stores them; `optimise.router = APIRouter()`; `compile_view.apply_answers` and `releases.*.open_*_backend` stubs
  raise `NotImplementedError` (never called by `Controller.open`, §8);
- cli: `main.py` with `app = typer.Typer()` + root callback (§9); every `commands/*.register(app)` is a no-op.
Accept (each): `uv run python -c "import <every module of the package>"`.

**W0-CHECK** (alone). Owns `tests/test_skeleton.py` (imports every module named in this plan; no
`packages/*/src/wynd/__init__.py`; the W0-complete types have exactly this plan's field names). Accept:
`uv run pytest tests/test_skeleton.py`; `uv run wynd --help`; `uv run --isolated --package wynd-spec --with pytest
python -c "import wynd.spec"`. The lead then commits the W0 checkpoint (incl. `docs/PLAN.md`, `docs/design/`).

### Wave 1 — M1 foundations
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **SPEC-CORE** | `packages/spec/src/wynd/spec/*.py` except `__init__.py`; `packages/spec/tests/**` except `tests/expr/**` | spec | §3.1–§3.4, §3.6–§3.11, §3.19 (spec hashing), §4 | `uv run pytest packages/spec/tests --ignore=packages/spec/tests/expr`; `uv run --isolated --package wynd-spec --with pytest pytest packages/spec/tests/test_leaf.py` |
| **SPEC-EXPR** | `packages/spec/src/wynd/spec/expr/{grammar,nodes,errors,scope,evaluator,analysis}.py`; `packages/spec/tests/expr/**` except `test_infer.py` | spec | §3.5 (grammar, evaluator, scope, analysis; start from `docs/design/spikes/expr/`) | `uv run pytest packages/spec/tests/expr --ignore=packages/spec/tests/expr/test_infer.py` (01 §12.1–§12.4 tables, thread test, perf smoke) |
| **RT-STORAGE** | `packages/runtime/src/wynd/runtime/{ids.py,storage/*.py}`; `$R/{test_ids,test_storage_local}.py` | spec, runtime | §3.14 incl. `EnvRegistry`, `new_id` | `uv run pytest $R/{test_ids,test_storage_local}.py` (incl. concurrent `update` from two threads and two processes, entry-point override, `WYND_HOME` fallback, secrets file mode 0600, `registry.env`) |
| **SAMPLE-DATA** | `examples/invoices/{wynd.yaml,.env.example,README.md,LICENSE,tools/**}`, `examples/invoices/processes/process_supplier_invoice/{process.yaml,proto/**,examples/*.pdf}` | none (stdlib + pyyaml in tests) | §12 data (M1 state of `process.yaml`: no agentic edge, no branch names, examples 1–5) | `uv run pytest examples/invoices/tools` |

### Wave 2a — M1 runtime leaves and loader
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **RT-STEP** | runtime `step.py, interface.py, errors.py, usage.py, policy.py, handle.py, http.py, shell.py, summary.py, middleware.py, testing.py, lint.py, describe.py`; tests `$R/{test_step_interface,test_middleware,test_handle,test_shell_step,test_summary,test_run_step,test_lint,test_http,test_describe,test_leaf_runtime}.py`, `$R/fixtures/steps/**`, `$R/support/rt_step_*.py` | spec, runtime | §5.2, §3.9 (non-agentic rows), §3.20 runtime helpers, §5.7 RT-STEP cases | `uv run pytest $R/{test_step_interface,test_middleware,test_handle,test_shell_step,test_summary,test_run_step,test_lint,test_http,test_describe,test_leaf_runtime}.py` (agentic path via a monkeypatched `complete`) |
| **RT-PROVIDERS** | runtime `providers/*.py` (incl. `__init__.py`); tests `$R/llm/{test_provider_registry,test_anthropic,test_claude_code,test_fake_provider,test_providers_live}.py` | spec, runtime | §3.15 incl. `check_provider` | `uv run pytest $R/llm/{test_provider_registry,test_anthropic,test_claude_code,test_fake_provider}.py`; live (`WYND_LIVE=1 uv run pytest $R/llm/test_providers_live.py`): structured union, tool + continuation, MCP proxy, auth error, tier parity |
| **RT-TOOLS** | runtime `tools/*.py`, `mcp/*.py`; tests `$R/llm/{test_tool_decorator,test_toolset,test_builtins,test_mcp_client,test_mcp_snapshot}.py` | spec, runtime | `$DRAFTS/03 §9–§10`, `handles_for`, `shell.allow`, `ToolHandle.local` | `uv run pytest $R/llm/{test_tool_decorator,test_toolset,test_builtins,test_mcp_client,test_mcp_snapshot}.py` |
| **RT-CASSETTE** | runtime `cassettes/*.py`; tests `$R/llm/{test_cassette_key,test_cassettes}.py` | spec, runtime | §3.16 | `uv run pytest $R/llm/{test_cassette_key,test_cassettes}.py` (record → promote → replay → edited input misses with the exact text; tier remap misses; literals; LFS pointer error) |
| **PROC-WS** | process `errors.py, _proc.py, workspace.py, loader.py, hashing.py`; tests `$P/{conftest,test_workspace,test_refs,test_loader,test_hashing}.py`, `$P/fixtures/workspaces/**` | spec, runtime (types only), process | §3.1, §3.19 (Trees, hashes), §6.2 | `uv run pytest $P/{test_workspace,test_loader,test_hashing,test_refs}.py` (all E1xx fixtures; subdir workspace; WorkingTree/CommitTree equality) |

### Wave 2b — M1 runtime composites
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **RT-WORKER** | runtime `worker/*.py` except `__init__.py`; tests `$R/{test_worker_protocol,test_pool,test_plan_consumption,test_worker_live}.py` | spec, runtime | §3.12, §5.3, snapshot verification §3.6 | `uv run pytest $R/{test_worker_protocol,test_pool,test_plan_consumption}.py` (real `sys.executable` workers running real `run_chain`; two `read` packages in one worker; stdout pollution; crash/timeout respawn; drift); live `WYND_LIVE=1 uv run pytest $R/test_worker_live.py` |
| **RT-EXEC** | runtime `trace.py`, `executor/*.py` (incl. `__init__.py`); tests `$R/{conftest,test_executor_routing,test_executor_errors,test_executor_nested,test_executor_edge_seam,test_trace}.py` | spec, runtime | §3.5 routing, §3.13, §5.4 incl. the edge seam | `uv run pytest $R/{test_executor_routing,test_executor_errors,test_executor_nested,test_executor_edge_seam,test_trace}.py` (02 §13 cases incl. `$ignore`, finally `with`, fake `EdgeChecker` + `edge.check` emission, deadlines, workspace retention, golden JSONL) |
| **RT-LOOP** | runtime `agentic/*.py` (incl. `__init__.py`); tests `$R/llm/{test_checks,test_prompt,test_schema,test_model_loop,test_agent_loop,test_loop_cassettes_e2e}.py` | spec, runtime | §5.5, §3.9 agentic rows | `uv run pytest $R/llm/{test_checks,test_prompt,test_schema,test_model_loop,test_agent_loop,test_loop_cassettes_e2e}.py` (scripted providers; real tools and cassettes) |

### Wave 3a — M1 process
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **PROC-VAL** | process `validation/{__init__,structure,reach,cycles,bindings,dataflow,exprcheck,rules}.py`; tests `$P/validator/**`, `$P/{test_dataflow,test_cycles,test_codes,test_exprcheck}.py` | spec, runtime (provider_info, lint), process | §6.3 (passes 1–7; calls the typecheck/agentic/latency hooks) | `uv run pytest $P/validator $P/{test_dataflow,test_cycles,test_codes,test_exprcheck}.py`; the dogfood fixture copy yields exactly two `I201` |
| **PROC-ENV** | process `fragments.py, venvs.py, plan.py, local.py, testing.py, envmanifest.py`; tests `$P/{test_fragments,test_runtime_source,test_venvs,test_plan,test_testing,test_envmanifest,test_local}.py`, `$P/support/proc_env_*.py` | spec, runtime, process | §3.20, §6.4, `run_local`, `assemble_env_manifest`, `registry_snapshot`, `sync_interfaces` (refreshes `interface`, `context`, `shell.exit_codes` from `describe`) | `UV_OFFLINE=1 uv run pytest $P/{test_fragments,test_runtime_source,test_venvs,test_plan,test_testing,test_envmanifest,test_local}.py` (one real offline venv; a tiny two-step deterministic workspace runs end to end through `run_local` with real workers; `validate` doubled where PROC-VAL is not needed) |
| **PROC-GIT** | process `git.py, jobs.py, compile.py, artefacts.py`; tests `$P/{test_git_closure,test_git_head,test_git_dirty,test_integrate,test_jobs,test_compile_state,test_artefacts}.py`, `$P/support/proc_git_harness.py` | spec, runtime, process | §3.18 types + `integrate`, §3.19 closure/HEAD/dirty, `compile_state`, artefact store, `ImageRegistryEntry` | `uv run pytest $P/{test_git_closure,test_git_head,test_git_dirty,test_integrate,test_jobs,test_compile_state,test_artefacts}.py` (04 §18.6 integrate cases 1–7 in temp repos) |
| **SPEC-TYPES** | spec `expr/infer.py`; `packages/spec/tests/expr/test_infer.py` | spec | §4.1 infer row | `uv run pytest packages/spec/tests/expr/test_infer.py` |
| **SAMPLE-STEPS** | `examples/invoices/processes/process_supplier_invoice/steps/**` (six packages: `pyproject.toml`, `step.lock.yaml` with **real** `proto_hash` and `interface` (§12 item 1), module, tests; no cassettes); `examples/invoices/tests/fixtures/fake_provider.json` | runtime (step API) | §12 items 1–2, 5 (fake script) | from the repo root: (1) `uv run python -c` script loading every sample YAML with `load_process`/`load_proto_step`/`load_step_lock` → no errors, and every lock's `proto_hash`/`interface` equal to freshly computed values; (2) `check_step_module` reports no findings on the six modules; (3) `uv run pytest -p no:cacheprovider examples/invoices/processes/process_supplier_invoice/steps/{read_pdf,validate_fields,save_record,escalate_to_human}` passes in the root env (pypdf is in the dev group). Never run `uv` with a step dir as cwd. |

### Wave 3b — M1 controller
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **CTL-CORE** | controller `controller.py, errors.py, models.py, store.py, workspace.py, processes.py, status.py, envfile.py, envcheck.py, registries.py, runs/{service,tree}.py`; tests `$C/conftest.py`, `$C/fixtures/ws_basic/**`, `$C/{test_controller_open,test_workspace,test_store,test_processes,test_status,test_env,test_registries,test_runs_local,test_render_tree,test_no_env_branches}.py` | spec, runtime, process, controller (compiler handlers by string only) | §8 (M1 part), §3.21 DTOs, env gate | `uv run pytest $C/{test_controller_open,test_workspace,test_store,test_processes,test_status,test_env,test_registries,test_runs_local,test_render_tree,test_no_env_branches}.py` |
| **CTL-JOBS** | controller `git.py`, `jobs/*.py` except `__init__.py`; tests `$C/{test_git,test_jobs_inprocess,test_jobs_subprocess,test_harness_phases,test_job_service,test_integrate}.py`, `$C/support/ctl_jobs_*.py` | spec, runtime, process, controller | §3.18 harness/runners/service/integration, §8.1 jobs rows | `uv run pytest $C/{test_git,test_jobs_inprocess,test_jobs_subprocess,test_harness_phases,test_job_service,test_integrate}.py` (`apply_answers` faked) |

### Wave 3c — M1 CLI
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **CLI-M1** | cli `main.py, context.py, output.py, inputs.py, commands/{workspace,test,run,env,registries,jobs}.py`; `packages/cli/tests/conftest.py`, `packages/cli/tests/test_m1_*.py` | controller (+ typer, yaml) | §9 (M1 rows), §3.22 | `uv run pytest packages/cli/tests/test_m1_*.py` (CliRunner + real `Controller` with fake backends; exit-code table; `--json`; import rule) |

### Wave 4 — **M1-INT** (alone)
Owns: `packages/spec/tests/test_examples_workspace.py`, `examples/invoices/tests/{test_offline_e2e,test_live_smoke}.py`,
recorded cassettes under `examples/invoices/**/cassettes/`, and fix-ups anywhere (coordinated with the lead). Steps:
(1) `uv sync --all-packages`; `uv run pytest` (whole repo, offline) green, **including `test_offline_e2e.py`** — the
first CLI → controller → `run_local` → workers → executor run of the dogfood is deterministic (fake provider);
(2) verify the sample locks: `uv run wynd validate process_supplier_invoice --sync-interfaces` in `examples/invoices`
changes nothing (`git diff --exit-code examples/invoices`); the lead commits; (3) `uv run wynd test
process_supplier_invoice --live` (test_live job via the subprocess runner; records step cassettes for extract/fix and
process cassettes; the CLI fast-forwards `feat/wynd-v1`). Then the **M1 acceptance (SPEC §12 M1)**:
1. `uv run wynd validate process_supplier_invoice` → exit 0, no errors or warnings (two `I201` infos:
   `validate.done[1]`, `fix.done[0]`).
2. `ANTHROPIC_API_KEY=sk-invalid-replay-check WYND_EVENTS_FILE=<tmp>/events.jsonl uv run wynd test
   process_supplier_invoice` → all step suites and process examples 1–5 pass, and every `model.call` line in
   `events.jsonl` has `"cassette": "replay"` (`ANTHROPIC_API_KEY` outranks the OAuth token and the Keychain login
   (§1.4), so any live call would fail with 401; HOME is left alone).
3. With `.env` from `.env.example`: `uv run wynd run process_supplier_invoice --local --input
   pdf_path=processes/process_supplier_invoice/examples/acme_inv_1042.pdf` → exit `done`, record under `RECORDS_DIR`
   (live, uses the Claude subscription); `uv run wynd trace <run_id>` renders the tree incl. the model line.
4. `uv run wynd env check process_supplier_invoice` → ok locally (claude-code group is a warning, not an error).
5. `WYND_LIVE=1 uv run pytest examples/invoices/tests/test_live_smoke.py`.

### Wave 5a — M2 builder, bake, supervisor
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **PROC-BUILD** | process `build/{job,resolve,wheels,variant,dockerfile,lockfile,imagebuilder}.py`, `base.py`, `templates/base.Dockerfile`; tests `$P/{test_dockerfile,test_base_dockerfile,test_variant,test_wheels,test_build_job,test_docker_build}.py`, `$P/golden/**` | spec, runtime, process | §6.5 | `uv run pytest $P/{test_dockerfile,test_base_dockerfile,test_variant,test_wheels,test_build_job}.py`; `WYND_DOCKER=1 uv run pytest $P/test_docker_build.py` |
| **PROC-BAKE** | process `bake.py`, `templates/bake_main.py`; runtime `bake.py`; tests `$P/test_bake.py`, `$R/test_bake_main.py` | spec, runtime, process | `$DRAFTS/04 §8.9` | `UV_OFFLINE=1 uv run pytest $P/test_bake.py $R/test_bake_main.py` |
| **RT-SUPERVISOR** | runtime `supervisor/**` (incl. `__init__.py`); tests `$R/llm/{test_supervisor_api,test_supervisor_integration,test_run_api_contract,test_run_api_client}.py` | spec, runtime | §3.17 | `uv run pytest $R/llm/{test_supervisor_api,test_supervisor_integration,test_run_api_contract,test_run_api_client}.py` |

### Wave 5b — M2 controller and CLI
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **CTL-M2** | controller `runs/{image,mirror}.py, docker.py, serving.py, base.py`; tests `$C/{test_runs_image,test_serving,test_base_wrappers,test_docker_e2e,test_docker_claude_code_live}.py`, `$C/support/ctl_m2_*.py` (incl. `ctl_m2_runapi.py`, the fake-variant builder helper) | spec, runtime, process, controller | §8 M2 rows, §3.21 amendment 7 | `uv run pytest $C/{test_runs_image,test_serving,test_base_wrappers}.py` |
| **CLI-M2** | cli `commands/{build,serve}.py`; tests `packages/cli/tests/test_build_serve.py` | controller | §9 M2 rows | `uv run pytest packages/cli/tests/test_build_serve.py` |

### Wave 6 — **M2-INT** (alone)
**Preflight (credential).** Look for `CLAUDE_CODE_OAUTH_TOKEN` (or `ANTHROPIC_API_KEY`) in the environment, then in the
git-ignored `examples/invoices/.env` (the lead asked the user for this dev key at the start of the run, §17 R11).
Accept (SPEC §12 M2; `WYND_DOCKER=1`), **token-free part** — the wave is done (and later waves start) when these pass:
1. `uv run wynd base build 0.1.0 --variant slim`.
2. `uv run wynd build process_supplier_invoice` (clean workspace; the test gate reuses the recorded result; building
   needs no credential) → image `wynd/process_supplier_invoice:<commit12>` (`slug` keeps `_`) and
   `.wynd/build/process_supplier_invoice/<commit>/` with `Dockerfile`, `process.lock.yaml`, `process.env.yaml`, `dist/`.
3. The fake-provider dogfood variant (temp git copy, §8.2): build, `wynd serve <image> -d` → ready; `wynd run --image`
   of examples 1–5 → the expected exits; `(step, exit, outputs)` sequences identical to `run --local` of the same copy;
   a second `run --image` makes no Docker call and completes in seconds (report wall time).
4. `WYND_DOCKER=1 uv run pytest -m docker` green (the claude-code container test skips with its reason when no token).
5. `wynd bake` on a pure-deterministic fixture process produces a `.pyz` that runs.
**Token-conditional part** (runs iff the preflight found a credential): `uv run wynd serve <dogfood image> -d --env-file
examples/invoices/.env` → ready; `uv run wynd run process_supplier_invoice --image --input
pdf_path=processes/process_supplier_invoice/examples/acme_inv_1042.pdf` → `done` with step sequence `read, extract,
validate, save` (the M1 local run's sequence for example 1); `WYND_LIVE=1 WYND_DOCKER=1 uv run pytest
$C/test_docker_claude_code_live.py`. SPEC §12 M2's "the M1 process runs inside its image in seconds" is reported as
**met** only if this part passed; without a credential it is recorded as **BLOCKED (dev key)** and named in the final
report, never marked passed.

### Wave 7a — M3 compiler and type checks
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **PROC-TYPES** | process `validation/typecheck.py`; tests `$P/typecheck/**`, `$P/test_typecheck.py` | spec, runtime, process | §6.1 typecheck row | `uv run pytest $P/typecheck $P/test_typecheck.py` |
| **CMP-A** | §7.1 CMP-A row | spec, runtime, process, compiler | §7 | `uv run pytest $K/{test_session,test_jobs}.py` |
| **CMP-B** | §7.1 CMP-B row | spec, runtime, process, compiler | §7 | `uv run pytest $K/{test_decision,test_split,test_yamledit,test_examples}.py` |
| **CMP-C** | §7.1 CMP-C row | spec, runtime, process, compiler | §7 | `uv run pytest $K/{test_schemas,test_testgen,test_codegen,test_astcheck,test_tools,test_lockfile,test_attempts}.py` |
| **CMP-D** | §7.1 CMP-D row | spec, runtime, process, compiler | §7 | `uv run pytest $K/{test_llm,test_prompts}.py` |
**7a barrier** (lead, after all 7a units): `uv run pytest $K/{test_pipeline_offline,test_dogfood_offline}.py`; each
failure is fixed by the unit that owns the failing module.

### Wave 7b — M3 controller and CLI
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **CTL-M3** | controller `compile_view.py`; tests `$C/{test_compile_view,test_job_answers}.py` | spec, runtime, process, compiler, controller | §8 compile_view row | `uv run pytest $C/{test_compile_view,test_job_answers}.py` |
| **CLI-M3** | cli `commands/compile.py`; tests `packages/cli/tests/test_compile_cmd.py` | controller | §9 compile row (questions block, exit 4/5) | `uv run pytest packages/cli/tests/test_compile_cmd.py` |

### Wave 8 — **M3-INT** (alone)
Accept (SPEC §12 M3 "`wynd compile` produces the M1 process from proto-steps alone"): offline compiler suites green;
`WYND_LIVE=1 uv run pytest examples/invoices/tests/test_compile_from_proto.py` (the `$DRAFTS/05 §16.4` sequence in a
temp copy: validate → `wynd compile process_supplier_invoice --accept-proposals` → exit 0 and fast-forward →
kinds as expected → `wynd test` green in replay with `ANTHROPIC_API_KEY=sk-invalid-replay-check` and every
`model.call` event `cassette == "replay"` (as M1-INT item 2) → second compile skips everything → one added example
recompiles only that step). Record wall time and cost from the compile report.

### Wave 9a — M4 services and web scaffold
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **CTL-DESIGN** | controller `design.py, search.py, uploads.py`; tests `$C/{test_design,test_design_roundtrip,test_search,test_uploads}.py` | spec, process, controller | §8 design row | `uv run pytest $C/{test_design,test_design_roundtrip,test_search,test_uploads}.py` |
| **CTL-CHAT** | controller `chat/*.py` except `__init__.py`; tests `$C/{test_chat,test_chat_live}.py` | spec, runtime, process, controller | §8 chat row | `uv run pytest $C/test_chat.py` |
| **CTL-REL** | controller `releases/*.py` except `__init__.py`; tests `$C/{test_cron,test_scheduler,test_releases}.py`, `$C/support/ctl_rel_*.py` | spec, runtime, process, controller | §8 releases row | `uv run pytest $C/{test_cron,test_scheduler,test_releases}.py` |
| **CTL-OAUTH** | controller `oauth.py`; tests `$C/test_oauth.py` | runtime, controller (+ httpx) | §8 oauth row | `uv run pytest $C/test_oauth.py` |
| **WEB-SCAFFOLD** | `packages/web/**` package files, configs, `src/test/*`, `src/api/types.ts`, `src/api/fixtures/*`, stubs of every `$DRAFTS/07 §16` file (§10) | HTTP contract only | §10 scaffold | `npm --prefix packages/web ci`; `npm --prefix packages/web run typecheck` |

### Wave 9b — M4 API, CLI, web
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **CTL-API** | controller `api/*.py` (incl. `__init__.py`; `models_web.py` transferred from W0); tests `$C/test_api_*.py`, `$C/{test_web_contract,test_static_mount}.py` | controller (+ fastapi, uvicorn, httpx) | §3.21 | `uv run pytest $C/test_api_*.py $C/{test_web_contract,test_static_mount}.py` |
| **CLI-M4** | cli `commands/{api,release,search}.py`; tests `packages/cli/tests/test_m4_cmds.py` | controller | §9 M4 rows | `uv run pytest packages/cli/tests/test_m4_cmds.py` |
| **WEB-CORE**, **WEB-GRAPH**, **WEB-CHAT**, **WEB-OPS** | §10 owner lists (+ colocated `*.test.ts(x)` of their files) | HTTP contract only | §10 | `npm --prefix packages/web exec -- vitest run <the unit's own test files>` (no `npm ci`, no whole-project checks) |

### Wave 10 — **M4-INT** (alone)
Accept (SPEC §12 M4): `npm --prefix packages/web run check` and `npm --prefix packages/web run build` (whole project,
first time); `uv run pytest packages/controller packages/cli` incl. `test_web_contract.py`. **Every write below runs
against a temporary `git clone` of the repo** (`serve-api` pointed at `<clone>/examples/invoices`), which is deleted
afterwards; `git diff --exit-code examples/invoices` in the real tree must hold at the end. In the clone: `uv run wynd
serve-api` then HTTP smoke via `curl`: `/api/health`, `/api/meta` (`default_provider: claude-code`, `llm.ready` equal
to `check_provider("claude-code")`), `/api/processes?q=invoice` (status flags), `GET/POST
/api/processes/process_supplier_invoice/design` (save + commit `design(process_supplier_invoice): …` touching only that
process), `/api/expressions/validate` (typo → error with span), `POST /api/runs` local + run SSE ends with `end`,
`POST /api/releases` for the **fake-variant image** (token-free) + `/trigger` + `/hooks/releases/{id}` with a secret,
`GET /` serves the bundle; add the release manual + webhook cases to `test_docker_e2e.py` and run `WYND_DOCKER=1 uv run
pytest $C/test_docker_e2e.py`. Live (`WYND_LIVE=1`, in the clone): one chat turn that edits the loop limit and commits
once. Token-conditional: a release of the real dogfood image fired once (else BLOCKED (dev key)). The `$DRAFTS/07 §17.6`
checklist is executed in a browser if available (claude-in-chrome), else recorded as manual.

### Wave 11a — M5 optimise analysis and agentic edges
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **OPT-PROC** | process `optimise.py, latency.py`; tests `$P/optimise/**`, `$P/test_latency.py` | spec, runtime, process | `$DRAFTS/08 §3.1–§3.6` over §3.13 events parsed with `wynd.runtime.trace.parse_event` and `Usage` (`step.end.kind/attempts/validation_failures/usage/model/replayed`, `model.call`, `edge.check`, `run.end`) | `uv run pytest $P/optimise $P/test_latency.py` |
| **EDGE-PROC** | process `edges_lock.py, validation/agentic.py`; tests `$P/{test_validate_agentic,test_edges_lock_sync}.py` | spec, runtime, process | §3.7, `$DRAFTS/08 §4.1, §4.3` | `uv run pytest $P/{test_validate_agentic,test_edges_lock_sync}.py` |
| **EDGE-RT** | runtime `edges.py`; tests `$R/{test_edges,test_executor_agentic}.py` | spec, runtime | §5.6 | `uv run pytest $R/{test_edges,test_executor_agentic}.py` (incl. a child process declaring a different `provider:` — its branch still uses `RunPlan.provider`) |
| **SAMPLE-M5** | transferred: `examples/invoices/tools/make_sample_pdfs.py`, `processes/process_supplier_invoice/{process.yaml,examples/umbrella_proforma_88.pdf}`, `examples/invoices/tests/fixtures/fake_provider.json` (adds the example-6 check verdict) | – | §12 item 6 | `uv run pytest examples/invoices/tools` |

### Wave 11b — M5 optimise job
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **OPT-CTL** | controller `optimise.py`; cli `commands/optimise.py`; `examples/invoices/tests/test_optimise_live.py`; tests `$C/test_optimise_job.py`, `packages/cli/tests/test_optimise_cmd.py` | process, controller (cli: controller) | `$DRAFTS/08 §3.7–§3.8` via §3.18 | `uv run pytest $C/test_optimise_job.py packages/cli/tests/test_optimise_cmd.py` |

### Wave 12 — **M5-INT** (alone)
Accept (SPEC §12 M5): commit; `uv run wynd compile process_supplier_invoice` writes `edges.lock.yaml` (only the
edges lock + process cassettes change; steps are skipped); `uv run wynd test process_supplier_invoice --live` then
`wynd test` in replay passes examples 1–6 (example 6 → `needs_review` via a negative check); `wynd trace` shows the
`check: NOT TAKEN` line; `uv run wynd optimise process_supplier_invoice` prints a report (R0 keeps with few runs);
offline optimise/edge suites green.

### Wave 13 — post-M5 Kubernetes, docs, CI
| Unit | Owns | May import | Implements | Accept |
|---|---|---|---|---|
| **KUBE** | `packages/kube/src/wynd/kube/*` (except W0 `__init__.py` files), `packages/kube/tests/**`, `deploy/**`, `docs/cluster-install.md` | spec, runtime, process, controller (+ httpx, pyyaml, typer) | §11 | `uv run pytest packages/kube/tests` (goldens, MockTransport client, FakeKubeClient runner/triggers/serving, checkout against a local bare repo, fire against a stub server); `uv run wynd-kube render install --image registry.example/wynd:0.1.0 --git-url https://git.example/acme/ops.git` equals the golden |
| **DOCS** | `README.md`, `docs/{architecture,run-api,providers,testing,agentic-edges,optimise}.md`, `tests/test_repo_hygiene.py`, `.github/workflows/ci.yml` | – | §13 | `uv run pytest tests/test_repo_hygiene.py`; `uv run python -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"` |

### Wave 14 — **FINAL-INT** (alone)
Accept, against the **M5 state** of the dogfood (the M1-INT expectations are superseded where M5 changed them):
1. `uv run pytest -q` (everything offline) green; `npm --prefix packages/web run check`; the CI `python` job's commands
   pass locally in `examples/invoices`.
2. `uv run wynd validate process_supplier_invoice` → exit 0 with exactly two `I201` infos, `validate.done[fix]` and
   `fix.done[0]`, and **no** `W-AGENTIC-*`, `W-EDGE-LOCK-*` or other warnings.
3. `ANTHROPIC_API_KEY=sk-invalid-replay-check WYND_EVENTS_FILE=… uv run wynd test process_supplier_invoice` → all step
   suites and process examples **1–6** pass, every `model.call` event is `cassette: replay`.
4. `uv run wynd trace <run id of example 6>` contains the `check: NOT TAKEN` line for `validate.done[save]`.
5. `WYND_DOCKER=1 uv run pytest -m docker` (fake-variant image path; claude-code container test runs iff a dev key
   exists, else BLOCKED (dev key)); `docker buildx build -f deploy/wynd.Dockerfile .` succeeds.
6. `@kube` e2e: skipped locally (no cluster). The CI `kube` job (push to main / dispatch) must have run green once;
   if it has not (the branch is not yet on main and nobody dispatched it), the final report lists SPEC §15 "cluster
   install tested to the same standard" as **unmet pending CI**.
7. The final report lists every BLOCKED (dev key) item; `git status` clean after the lead's final commit.

### 14.1 Unit → spec milestone map
M1: W1–W4 · M2: W5–W6 · M3: W7–W8 · M4: W9–W10 · M5: W11–W12 · post-M5 (Kubernetes + docs/CI): W13–W14.
Deliberately earlier than SPEC §12: the subprocess JobRunner and `wynd test --live` (with integration) ship in M1 so
the dogfood cassettes can be recorded as a job; the agentic-edge schema fields, `EdgesLock` model, the complete
executor edge seam (`wynd.runtime.executor.edges`) and the edge-venv rule ship in M1, so M5 adds files and fills
W0 stubs (`edges.py`, `edges_lock.py`, `validation/agentic.py`, `optimise.py`, `latency.py`) but edits no file
an earlier wave implemented.

---

## 15. Interpretations (decisions where SPEC is ambiguous or the drafts disagreed)

1. **`use:` has exactly three forms** (SPEC §5.1 lists three; §13's "four" is a typo).
2. **Outputs flat vs nested**: nested iff `exits:` is declared, or a `done` key exists and every value is a mapping
   (`$DRAFTS/01 §5.3`).
3. **Every YAML string under `when`/`with`/`limits`/`finally[].with` is an expression**; literal text needs inner
   quotes; YAML 1.2 booleans (`yes/no/on/off` are strings).
4. **Expressions**: `and`/`or` return booleans; `not in` and unary minus exist; member/index access on null or a
   missing key yields null; ordering/arithmetic on null raises (→ process error handler, cause `expression_error`);
   `when:` uses truthiness; `default()`'s first argument and `coalesce()` arguments are *guarded* for exit-aware checks.
5. **`previous`** = the most recently completed step of this process instance (during edge resolution: the edge's
   source step); null at the entry step.
6. **Exit-aware reference checks** use a forward dataflow over "possible latest exits ∪ not-run" with branch-level guard
   refinement from simple `when:` atoms only (no intra-expression refinement).
7. **Explicit ignore** is `to: $ignore` (only as the single unconditioned branch); taking it routes to the process error
   handler (cause `ignored_exit`). `$exit.error` is not routable.
8. **`max_traversals`** is filled (10) in memory on every branch whose endpoints share a strongly connected component,
   reported as info `I201`, never written to YAML; `N` allows N takes.
9. **`finally`** items are a step key (inputs by name from `process.inputs ∪ {run_id, exit}`) or `{step, with}`; their
   exits are not routed and their errors never change the process exit. **`on_error`** is bound by name from the
   `ProcessError`; its exit `x` becomes `$exit.x`; an error inside it ends with `$exit.error` (no recursion).
10. **Timeouts** (`limits.timeout`, seconds) route to the process error handler (cause `timeout`), enforced by killing
    the worker; they bound all attempts of the target (a whole child for a ProcessStep node).
11. **Retry policy** has three counters (`run`, `validation`, `tool`); defaults deterministic/shell 0/0/0, agentic
    `run=2` (transport restarts before any tool call), `validation=2`, `tool=1` (idempotent tools only). The spec gives
    no restart count; 2 with backoff 1 s/4 s is chosen.
12. **Dirty-tree refusal covers the whole workspace directory** for build, bake, compile, test --live and optimise
    --apply (untracked-but-not-ignored files count; `.wynd/` and ignored files do not) — the whole repo in the SPEC
    case where the workspace is the repo root. The closure scope is used only to decide whether `wynd test` results
    may be recorded.
13. **Step error payload carries the bound inputs** (`StepError.inputs`) so an `X.error` edge can re-bind them (the
    compiler's two-step split uses `steps.X.outputs.inputs.<f>`).
14. **Edges are "compiled" by parsing once at plan load and evaluating with the spec evaluator** — no generated edge
    code, no `eval`.
15. **Interface and context snapshots live in `step.lock.yaml`** so validation never imports step code; the worker
    verifies them at import; `wynd validate --sync-interfaces` refreshes them for hand-written steps.
16. **Step package import path is `wynd_steps.<step_module_name(step_id)>`** in both modes (mounted locally, staged into
    the wheel in images), so same-named packages never collide and no module-uniqueness rule is needed.
17. **Names match paths**: process `name` = last id segment; proto `name` = file stem (process-local) or package dir
    (step root, `<pkg>/proto.yaml`).
18. **`latency`** takes `fast` or `normal` (absent = normal); only `fast` warns. The sample drops `latency: fast`
    because the default provider is an AgentProvider.
19. **Default provider `claude-code`** (lead decision); tier defaults haiku/sonnet/opus live in the provider class and
    the user registry, never in lockfiles; `wynd compile` never writes `provider`.
20. **Env alternatives**: `one_of` groups with `modes`; the claude-code auth group is required only in `image` mode
    (locally the logged-in CLI suffices).
21. **`system:` packages** are used verbatim with apt-get/apk; the Alpine fallback triggers on `requires: glibc` or a
    failing binary-only musllinux resolve, recorded in `process.lock.yaml`.
22. **`exit_codes`** is accepted on any proto (the compiler picks the kind); default `{0: done, "*": error}`. ShellSteps
    are explicit code: `command()` returns argv (never a shell string), `outputs()` maps the result.
23. **Example conventions**: relative `path` values resolve against the process dir; `{tmp}` is a per-example temp dir;
    process examples may carry `env:`; `exit: error` examples are allowed.
24. **Process examples are run directly from `process.yaml`** by `wynd test` (no generated process test file, no pytest
    in the driver environment); comparison is subset match with float tolerance.
25. **Summaries** are deterministic projections (200-char cap); `note` is the agent's last free text.
26. **Context entries**: `process.goal`, `process.inputs`, `previous.summary`, `previous.outputs`,
    `steps.<k>.outputs[.f]`, `steps.<k>.summary`, `full_trace` (= this instance's history); unavailable → null.
27. **No `ui` in `process.yaml`**: node positions are per-browser (`localStorage`); comments in YAML are lost when the web
    saves a document (the compiler edits YAML surgically to keep comments).
28. **Agentic edges (M5)**: `kind: agentic` stays on the edge; branches may carry `check:` (prose) and `context:`;
    `when:` stays an expression and is evaluated first (no model call when false); the else is the first branch with
    neither `when` nor `check`; a negative verdict falls through; a verifier failure routes to the error handler
    (cause `edge_check`); knobs live in `edges.lock.yaml` keyed by branch key; checks run in a venv worker (`edge.check`).
29. **Worker crash** is the step's `error` exit (cause `worker_crash`); a JSON-RPC fault likewise.
30. **Workspace retention**: kept iff the top-level instance ended in its error handler (default or custom).
31. **Top-level inputs** are validated at the boundary (`InvalidProcessInputs`, HTTP 422) before any run record exists;
    child inputs failing validation are the ProcessStep node's `error` exit (`input_validation`).
32. **Entry binding** passes only the process inputs the entry Input declares.
33. **`AgenticStep.run` is never called**; a concrete body is a definition error.
34. **Declared `effects` are recorded, with one enforcement point**: `self.runtime.http` refuses (cause `config`) in a
    step whose effects lack `network`. Everything else is recorded, not enforced, in v1 (plain Docker, no sandbox).
35. **One worker per venv serves one step run at a time**; concurrent runs interleave at step granularity.
36. **`self.runtime.cache`** is per step id per worker lifetime (lost on restart).
37. **AgentProviders also expose `tiers()`**; `AgentRequest.mcp_servers` is informational; MCP tools are proxied
    through the in-process server (secrets stay off argv).
38. **Only a tool that raises is a tool failure**; argument errors, `ToolInputError` and MCP `isError` go back to the
    model.
39. **Cassette keys include the resolved model id** (with provider, tier and thinking): a tier remap is a request
    change and misses with the standard `NO_RECORDING` error. Timestamps are normalised by regex; run workspace,
    example tmp dirs, process dir and workspace root by literal replacement.
40. **Replay of tools**: network/MCP tool calls are recorded and replayed with model calls; filesystem/shell/pure tools
    execute; an AgentProvider run replays as a whole and then re-executes its recorded local tool calls in order, so
    side effects are the same for both provider kinds. A miss anywhere is cause `cassette_miss` and fails the test.
41. **Recording writes to a caller-supplied staging dir** ("the run workspace" of SPEC §7.1 for our purposes) and is
    promoted into the source package only after the tests pass.
42. **Compile decisions**: schema inference is presented, never asked; rule 3 "most" = more than half handled, zero
    wrong answers, ≥1 deferral; one escalation cheap → standard, then a clarification; splits only for process-local
    steps with no external references; hand-written steps are untouched unless their proto hash changes.
43. **Answering resubmits the same job** (`requeue`, same id and branch); answers are cumulative in the job inputs.
44. **Commit-producing jobs fork from the current branch tip**; build/bake use the closure HEAD; test results and builds
    are keyed by the closure HEAD and copied to the new commit after an auto-rebase.
45. **Integration** uses the per-commit rule over the union closure (base, target tip, branch tip); the user's checkout
    is only ever fast-forwarded; `pr_branch` leaves the branch and prints a `gh pr create` hint (no PR is opened).
46. **"BuildKit or kaniko, never a Docker socket"**: locally `docker buildx build` (BuildKit via the docker driver, no
    socket inside any job container); in clusters, rootless `buildctl` in an init container.
47. **Bake** = stdlib zipapp + extract-once bootstrap (pydantic-core cannot load from a zip); only for processes with
    no `system:` packages, no shell steps and one resolvable environment.
48. **Host-side resolution** pins every venv in `process.lock.yaml`; images install with `--no-deps`; local venvs add
    pytest and editable spec/runtime, images never do.
49. **SPEC §4.1's "`wynd serve` as the entrypoint"** is the in-image `wynd-supervisor serve`; CLI `wynd serve <image>`
    runs that image with Docker and waits for readiness.
50. **`wynd base build|publish`** are plain functions (no workspace git ref), not jobs.
51. **Triggers**: the default scheduler runs only inside `wynd serve-api`; at-most-once firing; ≤5 min catch-up; every
    release can also be run manually; webhooks use `secret_env`, else the API token.
52. **Chats** replay persisted history as context (no SDK session resume); write tools are exactly edit design, edit
    proto, start compile, start build, bound to the message's `acting_on` process.
53. **Design auto-commit** stages only the process's design scope (`git commit --only`), so a commit never spans two
    processes; a shared step-root proto is committed under whichever referencing process commits first.
54. **MCP OAuth** (SPEC §3.8 "or the equivalent OAuth flow in the web UI") is implemented in the controller with a small
    web dialog; tokens are user-registry secrets referenced as env vars.
55. **Optimise** changes tiers only, applies only to process-local steps and the process's own agentic branches,
    uses non-replayed calls as evidence and live tests as the quality gate (`$DRAFTS/08 §1`).
56. **Kubernetes**: REST via httpx; cluster storage = the local FS backends on an RWX PVC; jobs clone from and push to a
    git remote; namespace Pod Security `privileged` for rootless BuildKit.
57. **git-lfs is recommended, not required**; LFS pointer files in `cassettes/` fail replay loudly.
58. **`processes/<name>/build/` mirror** (SPEC §5.1 "if wanted") is not implemented.
59. **Telemetry**: none is collected; Claude Code runs with `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`.
60. **The claude-code env fragment has deps only** (the SDK wheel bundles the glibc CLI binary; no Node) and
    `requires: glibc`.
61. **A workspace may be a subdirectory of its git repository** (the dogfood lives at `examples/invoices`).
62. **Status is derived at the closure HEAD on every request**, reading the commit (`CommitTree`), never the working
    tree; a stale or missing edge-lock entry counts as design.
63. **The `fake` provider ships in the Apache runtime wheel** as a deliberate, test-only deviation from SPEC §3.9's
    "runtime ships anthropic and claude-code": it is inert unless a process or `WYND_DEFAULT_PROVIDER` selects
    `fake`, has no dependencies and no network I/O, and it is what makes image mode, the docker suite, M2/M4
    acceptance and the kube e2e verifiable offline and without credentials (a separate dev-only package would have to
    be resolved into image venvs, which the builder deliberately cannot do).
64. **Harness built-ins are limited** to `Read`, `Glob`, `Grep`, `WebFetch`, `WebSearch` in v1 (`E-LOCK`): local mode,
    tests and compile attempts run the harness on the host, and SPEC §3.8 allows model-written code only inside the
    process container.
65. **The `shell` builtin needs an executable allowlist** (`shell.allow(...)`), snapshotted in the lock; interpreters
    and shells are refused, so the runtime loop never executes model-written code (SPEC §3.8).
66. **User-registry data reaches images as an env var** (`WYND_REGISTRY_JSON`, read by the `registry.env` backend the
    base image selects), listed in the manifest with the MCP URLs; no `$WYND_HOME` mount exists.
67. **Child processes' examples are part of the parent's tests** (they run in the parent's image), and the build gate
    therefore covers them.
68. **Runs are gated by `wynd env check`** (SPEC §6.5 "Gate: wynd env check before run"): a local or image run with
    missing required vars is refused before any run record exists (`EnvMissing`).
69. **Compile session API uses SPEC §9's names** (`next`, not `run`).
70. **YAML date literals in `with:`** (`WithValue` includes `date | datetime`; `$DRAFTS/01 §6.4`'s alias omits them,
    but §3.3 makes non-string scalars literals and a bare date parses as `datetime.date`). SPEC-CORE's `ProcessDoc`
    normalisation rewrites every date/datetime under `with:`/`finally[].with` into the quoted expression string of its
    ISO text (`2026-10-01` → `"'2026-10-01'"`), so the normalised model holds no date objects and a JSON round trip of
    `RunPlan`/`process.lock.yaml` cannot turn the literal into arithmetic.

---

## 16. Spec coverage

| SPEC | Clause | Plan | Units |
|---|---|---|---|
| §1 | graph authoring without programming; testable/reproducible; deterministic first; modular steps; fast path (warm, ms step-to-step) | §3.3, §3.6, §7 (rule 1 first), §5.3 (warm workers), §3.17 (warm container), OPT latency warnings | all |
| §2 | non-goals: no global graph, no router model, agentic edges schema-only in v1 (built in M5), no WASM, single-user, sequential only | §3.3 (`kind`), §3.5 (sequential state machine), §3.21 (optional token, single user) | SPEC-CORE, RT-EXEC |
| §3.1 | step = Input/discriminated Output/pre/run/post; per-run hooks; no module state; `self.runtime.cache`; examples not in steps | §3.4, §5.2, §3.20, lint L001–L005 (errors; validator + compiler astcheck) | RT-STEP, SPEC-CORE, PROC-VAL, CMP-C |
| §3.2 | Deterministic/Agentic/Shell/ProcessStep; child in same container; child env.base/provider/latency ignored + warning; nested trace paths and counters | §5.2, §5.4, §6.3 W203–W205, §6.4 (closure fragments), §3.13 | RT-STEP, RT-EXEC, PROC-VAL, PROC-ENV |
| §3.3 | process graph; `entry:` binding by name; no `$entry` | §3.3, §3.5, E210 | SPEC-CORE, PROC-VAL, RT-EXEC |
| §3.4 | edges: from/to branches, if/elif/else, `with`/`limits` on branch, `max_traversals` on every cycle branch, `kind` | §3.3, §3.5, §6.1 cycles | SPEC-CORE, PROC-VAL, RT-EXEC |
| §3.4.1 | expression language, references, counters, literals, operators, inline conditionals, builtins; Lark; staged checking (M1 refs, M3 types) | §3.5, §4.1, §6.1 exprcheck/typecheck | SPEC-EXPR, SPEC-TYPES, PROC-VAL, PROC-TYPES |
| §3.5 | implicit `error` exit; routing failures → handler; default handler + ProcessError + workspace kept; retry defaults; agentic retry continues conversation; `on_error`; `finally`; ProcessStep error propagation | §3.5, §3.8, §3.9, §5.4, §5.5 | SPEC-CORE, RT-EXEC, RT-STEP, RT-LOOP |
| §3.6 | middleware chain 1–6; env/cwd snapshot; deterministic summary | §5.2 middleware, §3.8 Summary | RT-STEP |
| §3.7 | pull-based structured context | §4.1 context.py, §5.4 `assemble_context` | SPEC-CORE, RT-EXEC |
| §3.8 | `@tool`, shared library, MCP with `allow`, effects/idempotent/env, compile-time snapshot + live verification, env references, global MCP registry (+OAuth), in-process MCP for AgentProviders | §3.14, §3.15, §5.1 tools/mcp, §5.5, §8 oauth | RT-TOOLS, RT-PROVIDERS, CTL-CORE, CTL-OAUTH, CMP-C |
| §3.9 | ModelProvider/AgentProvider; tiers in the user registry; per-step provider with process default; provider env fragments; cassettes at the provider boundary; entry points; anthropic + claude-code; usage incl. latency; AgentProvider latency warning | §3.15, §3.16, §3.10, W206 | RT-PROVIDERS, RT-LOOP, RT-CASSETTE, PROC-VAL |
| §4 | process = image boundary; one Dockerfile; slim default, alpine opt-in with fallback; fragments; one venv per dep set; builder owns assignment; spec+runtime in every venv; worker per venv; local mode same scheme; versioned wynd-base; nothing undeclared installed; env manifest; env check; workspace per run; warm pools; secrets as references | §3.10, §3.11, §3.12, §6.4, §6.5, §3.14, §8 serving | PROC-ENV, PROC-BUILD, RT-WORKER, RT-STORAGE, CTL-M2 |
| §4.1 | image runs as a pod with the supervisor; run API contract; manifest → Secrets/ConfigMaps; env check as init container; storage backends | §3.17, §11, §3.14 | RT-SUPERVISOR, KUBE |
| §5 | monorepo layout; dependency direction; controller as library + service | §2, §8, §9, §1.3 | W0, all |
| §5.1 | `wynd.yaml` roots; discovery by marker; identity by path; leaves don't nest; `use:` forms; parent hash includes children; cycles are errors; build outputs never tracked | §3.1, §3.3, §3.19, §6.1 workspace/loader | SPEC-CORE, PROC-WS |
| §6.1 | proto-step schema; schemas inferred from examples | §3.3, §3.4, §7 | SPEC-CORE, CMP-B/C |
| §6.2 | process schema; `$exit.<name>`; outputs bound by terminating `with` | §3.3, E211 | SPEC-CORE, PROC-VAL |
| §6.3 | compiled step source package; `step.lock.yaml`; results in RunRegistry; wheel without tier | §3.6, §3.20, §6.5 | SPEC-CORE, CMP-C, PROC-BUILD |
| §6.4 | build outputs under `.wynd/build/<process>/<commit>/` | §3.24, §6.5 | PROC-GIT (artefacts), PROC-BUILD |
| §6.5 | Design → Compile → Build; compile output = commit on a branch; integrate ff/auto-rebase/PR; build refuses dirty tree, records commit, requires passing tests; cassettes in git-lfs with size warning; `test --live` job → branch | §3.18, §3.19, §3.20, §6.5, §7 | PROC-GIT, PROC-ENV, PROC-BUILD, CMP-A, CTL-JOBS |
| §6.6 | jobs vs runs; JobRunner interface in process, impls in controller (in-process, subprocess, Kubernetes); jobs on a checkout at a ref; worktrees under `.wynd/jobs`; BuildKit; registry; submit+poll; serialisable compile session | §3.18, §8 jobs, §11 | PROC-GIT, CTL-JOBS, CMP-A, KUBE |
| §7 | Step/AgenticStep API; `run_step` helper; replay-tested agentic steps; cassettes keyed on the full normalised request; explicit miss error; compile records live | §5.2, §3.16, §3.20 | RT-STEP, RT-CASSETTE, CMP-C |
| §7.1 | WorkspaceStore, TraceSink, RunRegistry (runs+jobs), Registry; env-selected backends; no home-dir assumptions except `WYND_HOME` | §3.14 | RT-STORAGE |
| §8 | loader and graph validation list; executor in runtime with local/image modes and `mode` in trace; JSONL trace; builder + bake; supervisor | §6, §5.4, §3.13, §6.5, §3.17 | PROC-*, RT-EXEC, RT-SUPERVISOR, PROC-BAKE |
| §9 | compile decision rules 1–4; tests from examples; generate-test-revise; example proposal; schema inference; session API; tool selection; model tiering recorded | §7 | CMP-A..D |
| §10 | CLI commands | §3.22, §9 | CLI-M1..M4, OPT-CTL |
| §11 | web: selector, decoupled chats + acting-on chip, graph editor, auto-commit, compile chat, build button, derived status per commit, search, releases + triggers, run panel | §10, §8 design/chat/releases/status | WEB-*, CTL-* |
| §12 | milestones M1–M5 + post-M5 Kubernetes; dogfood | §14, §12 | all |
| §13 | decisions (all) | honoured throughout; §15 records the readings | – |
| §14 | open questions: auto-rebase rule; conservative `max_traversals` | §3.18 (per-commit rule as stated), I201 as info | PROC-GIT, PROC-VAL |
| §15 | licences; no privileged code paths; cluster deploy first-class (kind e2e in CI on push to main); usage observable — model usage per provider/tier in traces (`Usage`) and jobs (`JobUsage`), job compute (`duration_ms`/`cpu_ms`), serving-container uptime (`started_at`/`stopped_at` in serve and release records), storage (`RunRecord.trace_bytes`/`workspace_bytes`, build `artefacts.sizes`); telemetry off | §13 licences + CI, §0 rules, §11, §3.13/§3.14/§3.18 usage, §8.1 serving/releases, §15 item 59 | W0, KUBE, DOCS, CTL-M2, CTL-REL, PROC-BUILD |
| §16 | prior art cited in README | §13 DOCS | DOCS |
| §17 | plan first; small increments; runtime dependency-light; tests everywhere; sample workspace is the integration test | this plan; §14 integration waves | all |

---

## 17. Risks and unresolved concerns (for the lead)

- **R1 — `claude-agent-sdk` as a real dependency of `wynd-compiler`.** The fixed decision declares it only in the
  claude-code provider's env fragment. But the compiler (compile jobs) and the controller chat run the default
  `claude-code` provider **in their own interpreter**, so the SDK must be importable there. This plan adds
  `claude-agent-sdk>=0.2.157,<0.3` to `wynd-compiler`'s dependencies (controller/cli get it transitively) and to the
  root dev group (offline tests use real SDK types). `wynd-runtime` still never depends on it. Needs lead sign-off.
- **R2 — Drafts are normative detail (resolved).** Non-contract detail (prompt texts, UI behaviour, cron grammar,
  golden manifests) is adopted from `$DRAFTS`, now copied into the repo at `docs/design/` (with the spikes) and
  committed with the W0 checkpoint, so every agent can read it and nothing depends on the session scratchpad.
- **R3 — Live spend and nondeterminism.** M1-INT (recording cassettes), M3-INT (compiling the dogfood: estimated 20–40
  minutes and several dollars of strong-tier calls through Claude Code) and M5-INT use the user's Claude subscription
  and its rate limits; live re-recordings can fail nondeterministically (the test_live job keeps passing suites only).
- **R4 — Git in the shared tree.** Integration units commit on `feat/wynd-v1`; jobs create worktrees of the whole wynd
  repo under `examples/invoices/.wynd/jobs/` and fast-forward the checked-out branch. No other unit may run git
  mutations in the shared tree while an integration unit runs.
- **R5 — Wave 0 is large and mechanical** (≈250 stub modules, the complete data types, 7 pyprojects with every entry
  point). It is split into W0-ROOT → six parallel per-package stub units → W0-CHECK, with name sources cited per
  package; `test_skeleton.py` imports every module and checks the data types' field names.
- **R6 — Kubernetes cannot be exercised end-to-end on this machine** (no cluster; kind not installed). Locally,
  correctness rests on goldens and fake-client tests; the `@kube` kind e2e runs in CI on push to main (and dispatch).
  Until that job has run green once, FINAL-INT reports the SPEC §15 cluster-parity item as unmet pending CI.
- **R7 — claude-agent-sdk 0.2.x churn and image size** (~100 MB per image with an agentic step); the `anthropic`
  ModelProvider path is unit-tested only (no API key on this machine).
- **R8 — Web toolchain versions** (Vite 8, TypeScript 7, Vitest 5, React 19.3) were checked by the web draft on
  2026-09-22; WEB-CORE pins exact versions from the npm registry at install time.
- **R9 — MCP OAuth** is included (SPEC says CLI `--auth-env` *or* web OAuth); it adds `Registry.put_secret` and a
  small web dialog. If the lead prefers the minimal reading, drop CTL-OAUTH and the dialog; nothing else depends on it.
- **R10 — Design comments are lost** when the web saves YAML (accepted); the compiler's surgical edits keep them.
- **R11 — The dev key is the one user input the run needs.** No `CLAUDE_CODE_OAUTH_TOKEN`/`ANTHROPIC_API_KEY` is set
  on this machine and the local login lives in the macOS Keychain, which a container cannot read, so the claude-code
  auth group (required in image mode) cannot be satisfied inside an image without it. **At the start of the run the
  lead asks the user once** for a `claude setup-token` value, to be placed in the git-ignored
  `examples/invoices/.env` as `CLAUDE_CODE_OAUTH_TOKEN` (listed in `.env.example`). Nothing else in the build waits on
  the user: every image-mode acceptance (M2, M4 releases, `-m docker`) is verified with the fake-provider dogfood
  variant, and only the claude-code-inside-a-container checks (M2-INT/M4-INT/FINAL-INT token-conditional parts,
  `test_docker_claude_code_live`) depend on the key. If it never arrives, those checks are reported as
  **BLOCKED (dev key)** in the final report — never as passed.

---

## Review log

Review round 3 (spec-coverage, interface-consistency and buildability lenses): every blocker and major finding was
applied, and every minor finding was applied as well. The lines below record the findings that were rejected or
resolved differently from the reviewer's suggested fix, with the reason.

- **`fake` provider moved to a dev-only package:** rejected. The provider stays in `wynd-runtime` as a deliberate
  test-only deviation (§15 item 63). The offline image-mode acceptance that replaces the missing container credential
  needs it inside images, and the builder cannot resolve a non-PyPI dev package into image venvs.
- **User registry in images via a `$WYND_HOME` mount / required `WYND_HOME`:** resolved with the reviewers' alternative
  instead. The `registry.env` backend reads `WYND_REGISTRY_JSON`, and the manifest lists it together with the MCP URLs
  and `WYND_MCP_<NAME>_URL` (§3.10). The base image already sets `WYND_HOME`, so a "required `WYND_HOME`" could never
  fail `env-check`. An env var also reaches docker and kube identically, with no mounts and no `ServingBackend`
  signature change.
- **on_error input rule as `E216`:** implemented as **`E223`**. `E216` already has a different meaning in
  `$DRAFTS/04 §4.12` (invalid expression, dropped here), and reusing it would corrupt the adopted template table.
- **Dev key requested "before wave 5":** the key is requested **at the start of the run** instead (§17 R11), so no
  mid-run check-in exists. The run never waits on it: token-dependent checks become BLOCKED (dev key).
- **M2 "not accepted" without the key vs. "verify image mode without it":** both are honoured. The wave completes on the
  token-free fake-variant checks, and the SPEC §12 M2 line for the real dogfood is reported as met only with the key,
  otherwise as BLOCKED (dev key).
- **Moving `open_serving_backend`/`open_trigger_backend` into a CTL-M1 module:** the other offered fix was taken
  instead. `ControllerContext.serving`/`.triggers` are lazy `cached_property`s (§8), so no file moves.
- **Dropping `llm.ready` from M4-INT:** rejected. `check_provider(name)` is defined in RT-PROVIDERS (§3.15) and M4-INT
  checks it.
- **`validate` subpackage clash:** the rename option was taken (`wynd/process/validation/`). The public function
  `wynd.process.validate` keeps its SPEC-facing name.
- **Two different `EdgeChecker.check` signatures proposed:** they are merged into one:
  `check(call, *, cassette, workspace, timeout_s, on_event) -> EdgeCheckResult`, which raises `EdgeCheckError`. Only
  the executor emits `edge.check`.
- **`EdgeCheckError` causes:** extended beyond `validation|transport|timeout` with `config`, `model` and
  `cassette_miss`. This lets a missed edge-check recording fail process examples like any other miss (§3.20).
- **Drafts copy as a W0 deliverable:** the architect copied `docs/design/` while revising this plan, because deferring
  the copy risked the scratchpad being cleaned first. W0-ROOT only verifies the copy and the W0 checkpoint commits it.
- **Sub-wave 3a split:** kept as the reviewer proposed. PROC-ENV stays in 3a next to PROC-VAL and uses a `validate`
  double, rather than adding a fourth M1 sub-wave.
- **`test_offline_e2e.py` owner:** M1-INT, which the finding allowed. SAMPLE-STEPS owns its fake-provider script.
- **SAMPLE-STEPS acceptance runs only the deterministic step tests.** The agentic steps have no cassettes until M1-INT
  records them.
- **CI `kube` job "must be seen green by FINAL-INT":** FINAL-INT cannot push to `main` on its own authority. It
  reports the SPEC §15 cluster-parity item as unmet pending CI unless the job has run green (§14 Wave 14 item 6).
- **Shell builtin:** the allowlist option was taken (`shell.allow(...)` + `ToolSnapshot.allow`), not the option of
  excluding `shell` from the loop.
- **AgentProvider replay asymmetry:** the re-execution option was taken (`ToolHandle.local`), not the option of
  documenting the asymmetry.
- **M3-INT live fix-ups (W8):** (1) a proposed example that expects outputs from a fixture file no existing example
  of the step uses is dropped with a warning (`examples.proposal_problem`): the model sees fixture names only, so its
  outputs are a guess `--accept-proposals` would make a permanent, unsatisfiable test. (2) An agent run aborted by a
  `ToolFailure` is recorded as `{"tool_failure": <message>}` and replays by raising it (`cassettes/wrap.py`), so an
  agentic step's `error` examples replay. (3) The `decide` prompt (`$DRAFTS/05 §14`, both copies) says that reading a
  file whose path is an input is not `external_data`.
- **M3-INT result (W8):** `test_compile_from_proto.py` green live: compile wall 579 s, compiler cost $1.67, 18 calls
  (claude-code provider); the whole test 719 s.
- **W8 close-out:** the unseen-fixture rule is keyed by (input field, path), so a file shown through one path input
  does not count as shown for another. Not changed: interactive corrections (`_correction`) skip that rule, since the
  human's answer supplies the outputs. Rerun live after the change: compile wall 534 s, cost $1.65, 19 calls; whole
  test 679 s.
