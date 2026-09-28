# 03 — wynd.runtime LLM layer: providers, agentic loop, tools, MCP, cassettes, supervisor + run API

Status: draft for the synthesizer. Area: **runtime-llm**. All modules are in `packages/runtime` (Apache-2.0). Dependencies: `wynd-spec` plus the stdlib only. `claude-agent-sdk` is imported lazily and declared only in the `claude-code` provider's env fragment.

This draft plugs into the seams defined in `02-runtime-core.md`: `complete(step, AgentCall) -> AgentResult`, `Usage`, `ModelInfo`, `ExecPolicy`, `CassetteConfig`, `StepFailure`, `RuntimeHandle`, `HttpClient`, `Registry`, the `model.call`/`tool.call` trace events, `Executor`, `WorkerPool`, and `RunPlan`. It also covers the requirements that `05-compiler.md` (C-R1..C-R13) and the controller draft (`06-parts`, R2 and the run API) placed on this area. Where the drafts disagree, §15 lists the item for reconciliation.

Contents
0. Summary of decisions
1. Spike log (claude-agent-sdk 0.2.157, run live with haiku on this machine)
2. Spec clauses this area owns (coverage audit)
3. Interpretations
4. Module and file list with owners and milestones
5. Provider layer: types, protocols, registration, resolution, env fragments
6. The agentic loop: `complete()`, prompt, output schema, retry and restart rules
7. The `claude-code` AgentProvider (concrete, on claude-agent-sdk)
8. The `anthropic` ModelProvider (stdlib urllib)
9. Tools: `@tool`, `env()`, ToolSet, retry policy, builtin library
10. MCP: declaration, registry entry, stdlib client, discovery and snapshot, verification
11. Cassettes: key, normalisation, file format, modes, promotion
12. Context entries and summaries (semantics; implemented by runtime-core)
13. Supervisor and run API (public contract)
14. Formats owned here (examples)
15. Interfaces consumed / provided, and items to reconcile
16. Test plan
17. Dogfood reference (hand-written agentic step)
18. Risks

---

## 0. Summary of decisions

- **Two provider protocols, exactly as in 3.9.** `ModelProvider.generate` covers a single turn; the runtime runs the loop. `AgentProvider.run` hands the whole loop to a harness. Both have `name` and `tiers()`. Request and response types are frozen dataclasses in `wynd.runtime.providers.types`. Per-call usage reuses runtime-core's `Usage` (calls=1) and adds `model_id`, `startup_ms` and `cost_basis` on the response.
- **Registration** uses the entry-point group `wynd.providers`. `wynd-runtime` registers `claude-code`, `anthropic` and `fake`. A provider class carries its metadata as class attributes (`kind`, `default_tiers`, `env_fragment`, `env_vars`), so the builder and validator can read them without instantiating the class or importing the SDK.
- **Tier to model mapping.** The effective mapping is the provider's built-in `default_tiers` overlaid with the registry `providers` entry. It never appears in a lockfile. Defaults:
  - `claude-code`: `cheap→haiku`, `standard→sonnet`, `strong→opus`.
  - `anthropic`: `cheap→claude-haiku-4-5`, `standard→claude-sonnet-5`, `strong→claude-opus-5`.
- **Cassette key.** The key covers the tier-level identity `(provider, tier, thinking)`, not the concrete `model_id`. Replay therefore works in CI with no registry and no credentials. The recorded `model_id` is stored alongside for information.
- **The default provider is `claude-code`.** It drives Claude Code through `claude-agent-sdk`, whose platform wheels bundle a native `claude` binary: glibc on Linux, so no Node is needed. Settings:
  - Step tools go through one **in-process SDK MCP server** named `wynd`.
  - Built-ins are disabled with `tools=[]` unless the lockfile lists `builtin_tools`.
  - Isolation: `setting_sources=[]`, `strict_mcp_config=True`, `permission_mode="dontAsk"`.
  - Structured output uses `output_format={"type":"json_schema","schema": <wrapped>}`, with the result read from `ResultMessage.structured_output`.
  - Auth: the logged-in CLI locally, `CLAUDE_CODE_OAUTH_TOKEN` (dev key) or `ANTHROPIC_API_KEY` in containers.
- **Output schema envelope.** The spike found that Claude Code (and the Messages API) reject a top-level `anyOf`/`oneOf`, and a discriminated union always produces one. Both providers therefore send `{"type":"object","properties":{"output": <union>},"required":["output"],"additionalProperties":false}` (with `$defs` hoisted to the root) and unwrap `output`. The runtime validates the unwrapped value with the discriminated `TypeAdapter`.
- **A validation retry continues the conversation.**
  - ModelProvider: the runtime appends a user message with the errors and calls `generate` again with the full history.
  - AgentProvider: it calls `run` again with `continuation=Continuation(session, message)`.
  - claude-code implements continuation **statelessly**. The first call mirrors the transcript into a per-call `SessionStore`, and the local session file is then deleted. The retry passes the transcript back with `resume=<session_id>`, and the SDK materialises it into a temp `CLAUDE_CONFIG_DIR`, copying auth (including the macOS keychain), then cleans up. This was verified live: same session id, no tool re-call, and nothing left in `~/.claude`.
- **Restarts and error exits.** A transport failure before any tool call restarts the whole loop (at most 2 restarts). A transport failure after a tool call gives the `error` exit (cause `transport`). A tool that *raises* gives the `error` exit (cause `tool`). A tool that rejects bad arguments (`ToolInputError` or argument validation) returns an `is_error` result to the model.
- **Tools.**
  - `@tool` builds an argument model with `pydantic.create_model` from the signature.
  - Tools declare `effects`, `idempotent` and `env`.
  - The builtin library is `http_get`, `web_search` (Brave Search API, `BRAVE_API_KEY`), `workspace_read`, `workspace_write`, `shell` and `now`.
  - A tool is retried once, and only when it is idempotent and the error is a transient network error.
- **MCP.**
  - `McpServer(name, allow=[...])`; `allow` is required and non-empty.
  - Registry entries hold a url or command, headers with `${env:NAME}` references, and `auth_env`.
  - The stdlib client speaks streamable HTTP (JSON or SSE bodies) and stdio. It was verified against the official `mcp` 2.x server in all three modes and against a stdlib fake.
  - Compile-time discovery snapshots the allowed tools into the lockfile. At run time the runtime connects, lists the tools and compares `(name, input_schema)` hashes, failing loudly on a mismatch.
  - The model always sees the **snapshot's** tool definitions, which keeps cassette keys stable.
  - For claude-code, MCP tools are **proxied through the in-process `wynd` server** rather than passed to the CLI as `--mcp-config`. Native pass-through was verified to work, but it would put auth headers on the CLI's argv.
- **Cassettes.** The three modes come from runtime-core's `CassetteConfig`: `live`, `record` and `replay`.
  - Each entry is one JSON file named by the request key, in the package's `cassettes/` directory.
  - The key is SHA-256 over canonical JSON after normalisation. Normalisation replaces the run workspace path with `<workspace>`, `run.id` with `<run.id>`, and ISO-8601 timestamps with `<datetime>`.
  - In the runtime loop, network and MCP tool calls are also recorded and replayed. Workspace and shell tools execute for real.
  - A miss raises `CassetteMissError("no recording for this request — re-record with `wynd test --live`")`.
- **Supervisor.**
  - The console script is `wynd-supervisor` (`serve`, `env-check`, `schema`). It serves a stdlib `ThreadingHTTPServer` on port 8080.
  - At boot it loads the image `RunPlan`, starts the `WorkerPool` (one warm worker per venv) and builds an `Executor`. Runs execute in a bounded thread pool and interleave at step granularity on the per-venv worker locks.
  - The run API is versioned under `/v1`: `POST /v1/runs`, `GET /v1/runs[/{id}[/outputs|/events]]` (events are SSE), `GET /v1/info`, `GET /healthz` and `GET /readyz`.
  - A small file-upload field (`files` + `{"$file": name}` input values) lets image runs and cluster triggers pass PDFs.
  - The contract is exported as a JSON Schema, and a test pins it.

---

## 1. Spike log (claude-agent-sdk 0.2.157, bundled CLI 2.1.277, model `haiku` = claude-haiku-4-5-20251001)

The spikes live in `scratchpad/spikes/`, outside the repo, in a uv venv on Python 3.12. The live calls were few and cost about $0.005 each. Every session file the spikes created under `~/.claude/projects` was deleted afterwards.

| # | Question | Result |
|---|---|---|
| a | Structured output | `ClaudeAgentOptions(output_format={"type":"json_schema","schema": S})` becomes `--json-schema`. The result arrives in `ResultMessage.structured_output` (already a dict) and as text in `ResultMessage.result`. Claude Code implements it as a `StructuredOutput` tool whose `input_schema` is `S`. A top-level `anyOf` gave **`API Error: 400 tools.0.custom.input_schema.type: Field required`**. Adding `"type":"object"` beside `anyOf` gave **`input_schema does not support oneOf, allOf, or anyOf at the top level`**. Wrapping the union under `properties.output` works, including pydantic `$defs`/`$ref`, `format: date`, and nested models. The discriminated `TypeAdapter` then validated `Done(... due_date=date(2026,10,1))` and `NotAnInvoice()`. |
| b | Python tools via in-process MCP | `create_sdk_mcp_server("wynd", tools=[sdk_tool])` with `@tool(name, desc, <json-schema dict>)` and an async handler. The model sees the tool as `mcp__wynd__lookup_rate`. It called the tool with `{'currency': 'USD'}` and then called `StructuredOutput`. A JSON-schema dict with `type` and `properties` is passed through unchanged. |
| c | Restricting built-ins | `tools=[]` gives an init tool list of `['StructuredOutput', 'mcp__wynd__lookup_rate']` only. `allowed_tools=["mcp__wynd__…"]` with `permission_mode="dontAsk"` means no prompts, and anything not listed is denied. `disallowed_tools` removes tools from the context entirely. |
| d | Usage, cost and duration | `ResultMessage`: `usage` (per-call input/output tokens), `model_usage` (per model: `inputTokens`, `outputTokens`, `cacheRead/CreationInputTokens`, `costUSD`, `costBasis: "list"`), `total_cost_usd`, `duration_ms`, `duration_api_ms`, `num_turns`, `session_id`. `model_usage` also counts side calls such as the title generator (for example `usage.input_tokens=3013` against `model_usage 3922`). **On resume, `model_usage` and `total_cost_usd` are cumulative for the session** (0.00272 then 0.004657), while `usage` is per call. Per-call cost is therefore the delta against the previous response. Startup was about 600 ms to the `init` SystemMessage; a small structured haiku call took about 3.1–4.6 s wall. |
| e | Continue after a validation failure | `resume=<session_id>` with a new prompt continues the same session: the same session id, context kept (it upper-cased the earlier title), and **no tool re-call**. The stateless variant also works: `session_store=<store holding the transcript>`, the local file deleted with `delete_session(sid, directory=cwd)`, then `resume=sid` with a store preloaded with the captured entries (22 entries, then 35 after the retry). The SDK materialised the session into a temp `CLAUDE_CONFIG_DIR` and removed it afterwards. |
| f | cwd | `cwd=<workspace>`, echoed in `init.data["cwd"]`. Sessions are stored under `<config>/projects/<project_key_for_directory(cwd)>/<sid>.jsonl`. `delete_session` leaves an empty project directory behind, so the provider also runs `rmdir`. Setting `CLAUDE_CONFIG_DIR` per run **breaks local subscription auth** ("Not logged in · Please run /login"), because macOS keeps the token in the keychain under a service name derived from the config dir. It is therefore not used. |
| g | External MCP over HTTP with headers | `mcp_servers={"github":{"type":"http","url":…,"headers":{"Authorization":"Bearer …"}}}` against a stdlib server: the CLI sent `server/discover`, received method-not-found, then did `initialize` → `notifications/initialized` → `tools/list` → `tools/call`, and the tool result came back correctly. However, the SDK puts the whole config, headers included, into `--mcp-config <json>` on the CLI's argv, so it is visible in `ps`. That is why we proxy MCP tools instead (§10.6). |
| h | Error types | A missing CLI raises `CLINotFoundError` (a subclass of `CLIConnectionError`). A bad OAuth token raises `ResultError(api_error_status=401, terminal_reason="api_error", result="Failed to authenticate. API Error: 401 OAuth access token is invalid.")`, and a bad API key gives the same with "API key is invalid". A logged-out CLI returns `result="Not logged in · Please run /login"` with `api_error_status=None`. A 400 from the API raises `ResultError("API Error: 400 …")`. Other process failures raise `ProcessError(exit_code, stderr)`, `CLIConnectionError` or `CLIJSONDecodeError`. Max turns raises `ResultError(subtype="error_max_turns")` (per the SDK docstring; not run live). |
| i | Bundled CLI in the wheels | PyPI 0.2.157 has wheels for `manylinux_2_17_x86_64` and `aarch64` (about 103 MB each) and `macosx_11_0_arm64`/`x86_64` (about 93–97 MB), plus a 0.3 MB sdist. **There is no musllinux wheel.** The Linux wheel's `_bundled/claude` is a 234 MB ELF x86-64 binary, dynamically linked against glibc only (`libc`, `libm`, `libdl`, `libpthread`, `librt`, `ld-linux`; max `GLIBC_2.9`), with no libstdc++ and no Node. The SDK depends on `mcp<3,>=1.23`, `anyio`, `jsonschema`, and so on. |
| — | Env vars present in the binary | `CLAUDE_CODE_OAUTH_TOKEN`, `CLAUDE_AGENT_SDK_CLIENT_APP`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC`, `DISABLE_AUTOUPDATER`, `DISABLE_TELEMETRY` (checked with `strings`). The SDK strips `CLAUDECODE` from the child env; a nested run from inside a Claude Code session worked. |
| — | stdlib MCP client | The prototype `mcp_client_stdlib.py` (~130 lines) completed `initialize`/`tools/list`/`tools/call` against the stdlib fake with Bearer auth (a wrong token gives `HTTP 401`), against the official `mcp` 2.2.0 `MCPServer` over streamable HTTP in both SSE and `json_response=True` modes, and over stdio. It negotiated protocol version `2025-11-25`. |

The working snippets from the spikes appear inline in §7, §10.4 and §10.5.

---

## 2. Spec clauses this area owns (coverage audit)

| Clause | What this area provides |
|---|---|
| 3.2 AgenticStep bullet | NOOA checks (docstring is the instruction, `run` is `...`, `@tool` methods are the only callable methods); `tools` and `mcp` declarations; structured output enforced to `Output`; `context` declared (assembled by runtime-core) |
| 3.5 agentic bullets | 2 validation retries that continue the conversation; no restart after a tool call; a full restart only for transport errors before the first tool call; a transport error after a tool call gives the `error` exit; a tool failure gives the `error` exit; tools retried once on transient network errors, and only if `idempotent: true` |
| 3.6 stages 2 and 4 (agentic side), stage 5 `note` | `complete()` consumes the assembled context, validates and retries, and returns the `note` |
| 3.7 | Semantics of the context entries (§12); prompt rendering of the context |
| 3.8 all bullets | `@tool`, `wynd.runtime.tools` builtins, `McpServer(allow=)`, effects/idempotent/env, the snapshot plus run-time verification, `env()` references resolved at run time, the MCP registry entry format, `@tool` methods callable directly, the runtime loop limited to declared tools, the harness allowed inside the container only with tools over in-process MCP |
| 3.9 all bullets | Both protocols; lockfile-driven resolution with the process default; tiers in the registry; providers contribute env fragments; cassettes wrap `generate`/`run`; entry points `wynd.providers`; `anthropic` and `claude-code` shipped; `usage` with tokens, cost and per-call latency; AgentProvider startup recorded |
| 4 (env fragments, env manifest) | Provider fragments and env var declarations (`provider_info`); MCP and builtin tool env declarations (§14) |
| 4 (warm pools) | The supervisor keeps workers warm across runs; the second run never touches Docker |
| 4.1 run API bullet | The run API contract (§13): submit, stream trace events, fetch outputs, health/readiness; `wynd-supervisor env-check` for the init container |
| 5 ("keep runtime deps minimal") | stdlib only; the SDK is lazy and in the fragment |
| 7 AgenticStep block, cassette bullets | `complete()`; cassettes keyed on the full request with volatile fields normalised; explicit miss text; recorded live by compile and `--live` |
| 7.1 WorkspaceStore bullet (cassettes) | Record into the run workspace (`CassetteConfig.record_dir`), `promote()` into `steps/<name>/cassettes/`, replay only from the source package |
| 7.1 Registry bullet | Entry schemas for the `providers` and `mcp` sections (§14.1, §14.2) |
| 8 Supervisor bullet | `wynd.runtime.supervisor` |
| 9 tool selection / tiering | Discovery (`list_tools`, `snapshot`), `BUILTINS`, `provider_info`, `resolve_model`; model/cost/attempts recorded per call |
| 10 `wynd serve`, `run --image`, `mcp`/`provider` registries | `RunApiClient` (stdlib) and the registry entry formats; the CLI drives Docker |
| 12 M1 / M2 | M1: providers, loop, tools, MCP, cassettes. M2: supervisor and run API schema |
| 13 decisions | Two provider interfaces; tier mapping only in the registry; agentic retries continue the conversation; tools and MCP in v1; every secret an env var; MCP registered once per user |
| 15 usage observable / no privileged paths | Every call emits `model.call` with usage and cost; there is no `if <environment>` code (auth differences come from the SDK's own env/keychain resolution) |

---

## 3. Interpretations

1. **AgentProvider also has `tiers()`.** 3.9 shows it only on ModelProvider, but tier mapping applies to both kinds.
2. **`AgentRequest.mcp_servers` is informational for claude-code.** 3.9 lists "mcp servers" as a field. The runtime has already connected to them, verified the snapshots, and turned their allowed tools into entries in `AgentRequest.tools`, which are exposed over the in-process server. The field carries `{name, allow}` so a provider can annotate its transcript. A future provider that wants native MCP could use it, but none does in v1. This keeps MCP secrets out of argv, gives one verification path, and makes tracing and retry uniform.
3. **Harness built-ins are a lockfile field** (`agentic.builtin_tools`, default `[]`), not a step class attribute. They are provider-specific, and the spec puts provider knobs in the lockfile.
4. **Only a tool that raises is a "tool failure".** 3.5 says a tool failure resolves the step to `error`. We read that as a tool raising after its retry policy. Argument-validation failures, `ToolInputError`, and MCP `isError: true` results are returned to the model as error results; they are recoverable model mistakes or server-reported outcomes. `http_get` returns 4xx/5xx statuses as results and raises only when the connection or read fails.
5. **Transport restarts are fixed at 2**, with backoff of 1 s and then 4 s. The spec gives no count. `max_turns` is an optional lockfile field (default 25).
6. **The cassette key is tier-level**: `(provider, tier, thinking)` plus the request content, but not `model_id`.
7. **Replay of tools.** In the runtime-owned loop, tool calls whose tool declares `network`, and all MCP tools, are recorded and replayed with the model calls. `filesystem`/`shell`/pure tools always execute, because the workspace must end up in the same state. An AgentProvider is replayed as a whole `run()`, so no tool executes, and workspace side effects of a replayed harness run do not happen. Steps whose value lies in side effects should say so in their outputs.
8. **Timestamp normalisation is regex based.** Only full ISO-8601 datetimes with a `T`, seconds and a zone designator are replaced, which is what `now()` produces. Plain dates such as `2026-10-01` are never touched. Two test inputs that differ only in such a timestamp would share a recording.
9. **Every model call is recorded into the trace** as a `model.call` event. The event carries the canonical request (with messages as a delta against the previous call, to stay linear) and the response. Cassette *files* are written only in `record` mode.
10. **A concrete `run` body on an `AgenticStep` is a definition error.** "`run` with a `...` body is completed by the loop", and deterministic pre-processing belongs in a `DeterministicStep` (deterministic first).
11. **Context entries** accept exactly: `process.goal`, `process.inputs`, `previous.summary`, `previous.outputs`, `steps.<n>.outputs`, `steps.<n>.summary`, `full_trace`. The extra forms mirror the expression references in 3.4.1.
12. **Run API concurrency.** Runs execute concurrently up to `WYND_MAX_CONCURRENT_RUNS` (default 4) and interleave at step granularity, because each venv has one worker (runtime-core's `WorkerPool` lock). Beyond that they queue up to `WYND_MAX_QUEUED_RUNS`, after which the API returns 429.
13. **File inputs over the run API.** Image runs cannot see the caller's disk, so `POST /v1/runs` accepts `files` (base64). Any input value `{"$file": "<name>"}` is replaced by the absolute path of the uploaded file. This is an optional part of the contract.
14. **Raw mode for non-step callers.** The compiler and the controller chat set `AgentRequest.prompt`. Then `instruction` is used verbatim as the system prompt and `prompt` as the user message, with no step preamble and no context/input rendering (C-R1).

---

## 4. Module and file list with owners and milestones

Owner tags:
- **LLM-P**: providers.
- **LLM-L**: the loop.
- **LLM-T**: tools and MCP.
- **LLM-C**: cassettes.
- **LLM-S**: supervisor and run API.

One agent per tag can build in parallel against this doc. All paths are under `packages/runtime/`.

```
src/wynd/runtime/
  agentic/__init__.py        LLM-L  M1  re-export complete, check_agentic_class, describe_agentic
  agentic/loop.py            LLM-L  M1  complete(step, call) -> AgentResult; ModelLoop, AgentLoop
  agentic/checks.py          LLM-L  M1  check_agentic_class(cls), is_ellipsis_body(fn), describe_agentic(cls)
  agentic/prompt.py          LLM-L  M1  SYSTEM_PREAMBLE, render_prompt, RETRY_* templates, format_validation_errors
  agentic/schema.py          LLM-L  M1  wrap_output_schema, unwrap_output, strict_compatible
  agentic/errors.py          LLM-L  M1  ProviderError, ToolFailure, ToolInputError, OutputValidationFailed,
                                        AgentLoopError, McpSnapshotMismatch, McpConfigError, CassetteMissError
  providers/__init__.py      LLM-P  M1  DEFAULT_PROVIDER, ProviderInfo, provider_info, available_providers,
                                        load_provider, resolve_model, provider_tiers, register_for_tests
  providers/types.py         LLM-P  M1  Thinking, Tier, Message, ToolSchema, ToolCall, ToolHandle, ToolResult,
                                        GenerateRequest/Response, AgentRequest/Response, Continuation,
                                        McpServerRef, ModelProvider, AgentProvider (Protocols)
  providers/claude_code.py   LLM-P  M1  ClaudeCodeProvider (lazy claude_agent_sdk)
  providers/anthropic.py     LLM-P  M1  AnthropicProvider, PRICING, request/response mapping
  providers/fake.py          LLM-P  M1  FakeProvider ("fake" entry point, WYND_FAKE_PROVIDER_SCRIPT)
  providers/scripted.py      LLM-P  M1  ScriptedModelProvider, ScriptedAgentProvider (unit-test fakes)
  tools/__init__.py          LLM-T  M1  tool, env, ToolSpec, BUILTINS, http_get, web_search, workspace_read,
                                        workspace_write, shell, now, ToolInputError (re-export)
  tools/decorator.py         LLM-T  M1  tool(), ToolDecl, tool_spec(fn, source), step_tools(cls)
  tools/toolset.py           LLM-T  M1  ToolSet (invoke, retry policy, trace, replay), is_transient, current_runtime
  tools/builtins.py          LLM-T  M1  the six builtin tools + result models
  mcp/__init__.py            LLM-T  M1  McpServer, McpServerEntry, McpToolSpec, list_tools, snapshot, verify
  mcp/client.py              LLM-T  M1  McpClient, HttpTransport, StdioTransport, McpError, McpAuthError
  mcp/snapshot.py            LLM-T  M1  schema_sha256, snapshot(), verify(), McpSnapshotTool
  mcp/entry.py               LLM-T  M1  McpServerEntry (pydantic), resolve_env_refs, open_client(entry, env)
  mcp/fake_server.py         LLM-T  M1  stdlib fake MCP server (HTTP JSON/SSE + stdio); python -m wynd.runtime.mcp.fake_server
  cassettes/__init__.py      LLM-C  M1  CassetteSession, CassetteMissError (re-export), promote, NO_RECORDING
  cassettes/key.py           LLM-C  M1  canonical_json, Normaliser, request_key
  cassettes/store.py         LLM-C  M1  CassetteEntry, read_entry, write_entry, find
  cassettes/wrap.py          LLM-C  M1  CassetteModelProvider, CassetteAgentProvider, tool replay hooks
  supervisor/__init__.py     LLM-S  M2  (empty)
  supervisor/__main__.py     LLM-S  M2  -> main.main()
  supervisor/main.py         LLM-S  M2  console script `wynd-supervisor` (serve | env-check | schema)
  supervisor/schema.py       LLM-S  M2  run API pydantic models + export_json_schema()
  supervisor/runs.py         LLM-S  M2  RunManager (queue, state, event buffers, retention, drain)
  supervisor/http.py         LLM-S  M2  Handler(BaseHTTPRequestHandler), routes, SSE writer, auth
  supervisor/files.py        LLM-S  M2  materialise_files(inputs, files, dir)
  supervisor/client.py       LLM-S  M2  RunApiClient (stdlib urllib + SSE parser)
  supervisor/run_api.schema.json  LLM-S M2  committed contract snapshot (generated; test pins it)
tests/llm/                   (see §16; each test file carries the owner tag of the module it tests)
```

pyproject fragment this area adds to `packages/runtime/pyproject.toml` (the file is shared with runtime-core):

```toml
[project.scripts]
wynd-supervisor = "wynd.runtime.supervisor.main:main"

[project.entry-points."wynd.providers"]
claude-code = "wynd.runtime.providers.claude_code:ClaudeCodeProvider"
anthropic   = "wynd.runtime.providers.anthropic:AnthropicProvider"
fake        = "wynd.runtime.providers.fake:FakeProvider"
```

Root dev dependency group (a **justified dev-only dependency**): `claude-agent-sdk>=0.2.157,<0.3`. Offline unit tests of `ClaudeCodeProvider` build real SDK message dataclasses and inject a fake `query` function, so no CLI is spawned. The live tests need the SDK anyway. It is never a runtime dependency.

---

## 5. Provider layer

### 5.1 Types — `wynd/runtime/providers/types.py`

```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal, Protocol
from wynd.runtime.usage import Usage          # runtime-core: input/output/cache tokens, cost_usd, latency_ms, calls

Tier = Literal["cheap", "standard", "strong"]
Thinking = Literal["none", "low", "medium", "high"]
Effect = Literal["network", "filesystem", "shell"]
Message = dict[str, Any]
# Neutral, Anthropic-shaped message: {"role": "user"|"assistant", "content": [block, ...]}.
# Blocks the runtime creates: {"type":"text","text"}, {"type":"tool_result","tool_use_id","content": str,"is_error": bool}.
# Blocks providers return (appended verbatim, never inspected by the runtime except tool_use):
#   {"type":"text"}, {"type":"tool_use","id","name","input"}, and provider-opaque blocks (e.g. "thinking" with "signature").

@dataclass(frozen=True)
class ToolSchema:
    name: str                       # ^[A-Za-z0-9_-]{1,64}$
    description: str
    input_schema: dict[str, Any]    # JSON Schema, type: object

@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]

@dataclass(frozen=True)
class ToolResult:
    text: str                       # what the model sees
    is_error: bool = False

@dataclass(frozen=True)
class ToolHandle:                   # what an AgentProvider receives: schema + a callable into the runtime's ToolSet
    name: str
    description: str
    input_schema: dict[str, Any]
    invoke: Callable[[dict[str, Any]], ToolResult]   # raises ToolFailure (tool raised) -> provider must abort

@dataclass(frozen=True)
class McpServerRef:
    name: str
    allow: tuple[str, ...]

@dataclass(frozen=True)
class GenerateRequest:
    model_id: str
    thinking: Thinking
    system: str
    messages: list[Message]
    tools: list[ToolSchema]
    output_schema: dict[str, Any] | None     # UNWRAPPED Output JSON schema; None = free text (compiler/chat only)

@dataclass(frozen=True)
class GenerateResponse:
    message: Message                          # assistant message to append verbatim (keeps thinking blocks/signatures)
    text: str                                 # concatenated text blocks, "" if none
    tool_calls: list[ToolCall]
    structured_output: Any | None             # UNWRAPPED; None if absent or unparseable
    stop: Literal["end", "tool_calls", "max_tokens", "refusal"]
    usage: Usage                              # calls=1, latency_ms = this call
    model_id: str                             # concrete model that served it (from the response)
    cost_basis: Literal["api", "list"] | None = None
    raw: dict[str, Any] | None = None         # provider-native body; never written to cassettes

@dataclass(frozen=True)
class Continuation:
    session: dict[str, Any]                   # opaque: AgentResponse.session of the previous call
    message: str                              # the validation-error message (or the user's next chat turn)

@dataclass(frozen=True)
class AgentRequest:
    model_id: str
    thinking: Thinking
    instruction: str                          # step docstring (raw mode: verbatim system prompt)
    context: dict[str, Any]                   # assembled context ({} if none)
    input: dict[str, Any]                     # Input.model_dump(mode="json")
    output_schema: dict[str, Any] | None      # UNWRAPPED Output schema; None = free text (chat)
    tools: list[ToolHandle]                   # @tool methods + builtins + allowed MCP tools (proxied)
    mcp_servers: list[McpServerRef]           # informational (Interpretation 2)
    workspace: Path                           # harness cwd
    max_turns: int = 25
    builtin_tools: list[str] | None = None    # harness built-ins; None = provider default ([] for claude-code)
    continuation: Continuation | None = None
    prompt: str | None = None                 # raw mode (Interpretation 14)
    on_event: Callable[[dict[str, Any]], None] | None = None   # streaming for chat UIs; NOT part of cassette key
    # on_event shapes: {"type":"text","text"}, {"type":"tool_use","id","name","input"},
    #                  {"type":"tool_result","id","is_error","content"}; tool names without "mcp__wynd__"

@dataclass(frozen=True)
class AgentResponse:
    structured_output: Any | None             # UNWRAPPED; None if the harness produced none
    usage: Usage                              # this call only (deltas on resumed sessions)
    model_id: str
    transcript: list[dict[str, Any]]          # compact: text / tool_use / tool_result items (as on_event shapes)
    session: dict[str, Any]                   # opaque continuation state
    note: str = ""                            # last free text before the structured output
    tool_calls: int = 0                       # harness tool invocations in this call (in-process + built-ins)
    startup_ms: float | None = None           # time to harness ready (claude-code: init SystemMessage)
    cost_basis: Literal["api", "list"] | None = None

class ModelProvider(Protocol):
    name: str
    def generate(self, req: GenerateRequest) -> GenerateResponse: ...
    def tiers(self) -> dict[str, str]: ...

class AgentProvider(Protocol):
    name: str
    def run(self, req: AgentRequest) -> AgentResponse: ...
    def tiers(self) -> dict[str, str]: ...
```

`ProviderError` lives in `agentic/errors.py` and is re-exported from `providers`:

```python
ErrorKind = Literal["transport", "auth", "unavailable", "invalid_request", "refusal", "max_turns"]

class ProviderError(Exception):
    def __init__(self, message: str, *, kind: ErrorKind, retryable: bool,
                 tool_called: bool = False, status: int | None = None) -> None: ...
    # retryable is True only for kind == "transport"
```

### 5.2 Provider classes, entry points and metadata — `wynd/runtime/providers/__init__.py`

A provider class is registered in `wynd.providers` and exposes these class attributes. They can be read without instantiating the class and without importing any SDK.

```python
class ProviderClass(Protocol):
    name: ClassVar[str]
    kind: ClassVar[Literal["model", "agent"]]
    default_tiers: ClassVar[dict[str, str]]
    env_fragment: ClassVar[EnvFragment]          # wynd.spec EnvFragment(deps, system, requires)
    env_vars: ClassVar[list[EnvVarDecl]]         # wynd.spec EnvVarDecl (see §14.4)
    def __init__(self, tiers: Mapping[str, str]) -> None: ...

@dataclass(frozen=True)
class ProviderInfo:
    name: str
    kind: Literal["model", "agent"]
    default_tiers: dict[str, str]
    env_fragment: EnvFragment
    env_vars: list[EnvVarDecl]

DEFAULT_PROVIDER = "claude-code"

def available_providers() -> dict[str, Literal["model", "agent"]]: ...
    # {ep.name: ep.load().kind for ep in entry_points(group="wynd.providers")} ∪ test overrides
def provider_info(name: str) -> ProviderInfo: ...                    # KeyError(name) if unknown
def provider_tiers(name: str, registry: Registry | None) -> dict[str, str]: ...
    # {**info.default_tiers, **ProviderEntry.model_validate(registry.get("providers", name) or {}).tiers}
def resolve_model(provider: str, tier: str, registry: Registry | None) -> str: ...
    # tiers[tier]; unknown provider -> StepFailure("config", f"provider {provider!r} is not installed ...");
    # missing tier -> StepFailure("config", f"provider {provider!r} has no model for tier {tier!r}; "
    #                             f"run `wynd provider add {provider} --tier {tier}=<model>`")
def load_provider(name: str, *, registry: Registry | None = None) -> ModelProvider | AgentProvider: ...
    # cls(tiers=provider_tiers(name, registry)); cheap, stateless; created per step run
def register_for_tests(name: str, cls: type) -> Callable[[], None]: ...  # test seam; returns an undo function
```

Loading:

```python
def _provider_class(name: str) -> type:
    if name in _TEST_OVERRIDES:
        return _TEST_OVERRIDES[name]
    eps = importlib.metadata.entry_points(group="wynd.providers", name=name)
    if not eps:
        raise KeyError(name)
    return next(iter(eps)).load()
```

### 5.3 Metadata of the shipped providers

| | `claude-code` | `anthropic` | `fake` |
|---|---|---|---|
| `kind` | agent | model | agent |
| `default_tiers` | `{cheap: haiku, standard: sonnet, strong: opus}` | `{cheap: claude-haiku-4-5, standard: claude-sonnet-5, strong: claude-opus-5}` | `{cheap: fake, standard: fake, strong: fake}` |
| `env_fragment.deps` | `["claude-agent-sdk>=0.2.157,<0.3"]` | `[]` | `[]` |
| `env_fragment.system` | `[]` | `[]` | `[]` |
| `env_fragment.requires` | `glibc` (no musllinux wheel; the bundled binary is glibc-linked) | `null` | `null` |
| `env_vars` | `CLAUDE_CODE_OAUTH_TOKEN` (secret, `one_of: claude-code-auth`), `ANTHROPIC_API_KEY` (secret, `one_of: claude-code-auth`) | `ANTHROPIC_API_KEY` (secret, required), `ANTHROPIC_BASE_URL` (optional, default `https://api.anthropic.com`) | `WYND_FAKE_PROVIDER_SCRIPT` (optional) |

`one_of` groups: the env check requires at least one member of the group. Per the process draft, it enforces this in image mode only, because locally the logged-in `claude` CLI supplies auth.

The comparable-strength rule is met: both providers map `cheap` to Haiku 4.5, `standard` to the current Sonnet and `strong` to the current Opus.

### 5.4 How builders and venv planners pick up provider fragments

Owner: process/builder. This area only supplies `provider_info()`.

```
effective_provider(step) = lock.agentic.provider or ROOT process.yaml provider or DEFAULT_PROVIDER   # children use the root's (3.2)
effective_fragment(step) = step.fragment  ∪  (provider_info(effective_provider(step)).env_fragment  if lock.kind == "agentic")
    deps: union (dedupe by normalised name; step specifier wins), system: union, requires: "glibc" if any
venv groups = group steps by dep_set_hash(effective_fragment.deps locked)                                  # builder owns assignment
env manifest += provider_info(p).env_vars for each p used by ≥1 agentic step (used_by = those steps)
```

On macOS, local mode installs the macOS wheel (bundled Mach-O `claude`). `requires: glibc` only affects the choice of image base.

### 5.5 Resolution at step-run time

This happens executor-side in runtime-core's `build_policy` (C7). The worker receives a resolved `ExecPolicy`:

```
provider = lock.agentic.provider or ctx.default_provider                # root process.yaml provider or $WYND_DEFAULT_PROVIDER or "claude-code"
tier     = lock.agentic.tier or "cheap"
thinking = lock.agentic.thinking or "low"
model_id = resolve_model(provider, tier, registry)                      # registry == WYND_HOME registry (ConfigMap in a cluster)
```

In the worker, `complete()` builds the provider with `load_provider(policy.provider, registry=None)`. The `model_id` has already been resolved, so the instance only needs its name and the default tiers.

---

## 6. The agentic loop — `wynd/runtime/agentic/`

### 6.1 Class checks (NOOA) — `agentic/checks.py`

Runtime-core's `AgenticStep.__init_subclass__` calls `check_agentic_class(cls)`, which is a light import (ast and inspect only).

```python
def is_ellipsis_body(fn: Callable) -> bool:
    """True iff the function body is `...`, optionally preceded by a docstring. Uses inspect.getsource + ast
    (verified: `def run(self, input: Input) -> Output: ...` -> True; docstring + `...` -> True; `return 1` -> False)."""
    node = ast.parse(textwrap.dedent(inspect.getsource(fn))).body[0]
    body = node.body
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
            and isinstance(body[0].value.value, str):
        body = body[1:]
    return (len(body) == 1 and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant) and body[0].value.value is Ellipsis)

def check_agentic_class(cls: type) -> None:          # raises StepDefinitionError (runtime-core) with all problems
    # 1. inspect.getdoc(cls) is a non-empty str, and not inherited from AgenticStep  -> "the class docstring is the instruction"
    # 2. if "run" in cls.__dict__: is_ellipsis_body(cls.run) else inherited (fine)
    #    concrete body -> "AgenticStep.run must be `...` (completed by the loop); put deterministic logic in a
    #                     DeterministicStep or a @tool method"
    #    getsource fails (OSError/TypeError) -> "cannot read the source of run(); ship .py sources in the wheel"
    # 3. tools: list of functions each carrying __wynd_tool__ (decorated with @tool)
    # 4. mcp: list[McpServer]; server names unique
    # 5. context: list[str]; each matches CONTEXT_ENTRY_RE (§12)
    # 6. tool names unique across @tool methods, `tools`, and "<server>__<tool>" for every allowed MCP tool
    # 7. a @tool method must not be named run/pre/post

def describe_agentic(cls: type) -> dict[str, Any]: ...
# {"instruction": str, "context": [...], "tools": [ToolSpec.describe()...], "mcp": [{"name","allow"}],
#  "completed_by_loop": True}
# used by the worker's `describe` (runtime-core) -> compiler, validator, web
```

### 6.2 Entry point — `agentic/loop.py`

```python
def complete(step: AgenticStep, call: AgentCall) -> AgentResult:
    policy, rt = call.policy, call.runtime
    info = _provider_info_or_fail(policy.provider)            # StepFailure("config") if unknown
    cassettes = CassetteSession(call.cassette, provider=policy.provider, tier=policy.tier, thinking=policy.thinking,
                                package=step_package_of(step), step_path=call.step_path,
                                normaliser=Normaliser.for_run(rt.run_id, rt.workspace), trace=rt.trace)
    provider = cassettes.wrap(lambda: load_provider(policy.provider), kind=info.kind)   # real provider built lazily
    tools = ToolSet.for_step(step, call, cassettes)           # binds @tool methods, `tools`, MCP snapshots
    try:
        if call.cassette.mode != "replay":
            tools.connect_mcp()                               # connect + verify every snapshot before any model call
        loop = ModelLoop if info.kind == "model" else AgentLoop
        return loop(step, call, provider, tools).run()
    except CassetteMissError as e:   raise StepFailure("cassette_miss", str(e), usage=..., attempts=...)
    except McpSnapshotMismatch as e: raise StepFailure("config", str(e), ...)
    except McpConfigError as e:      raise StepFailure("config", str(e), ...)
    except ToolFailure as e:         raise StepFailure("tool", str(e), ...)
    except OutputValidationFailed as e: raise StepFailure("output_validation", str(e), ...)
    except ProviderError as e:       raise StepFailure(_CAUSE[e.kind], str(e), ...)
    except AgentLoopError as e:      raise StepFailure("model", str(e), ...)   # max_turns exceeded in runtime loop
    finally:
        tools.close()

_CAUSE = {"transport": "transport", "auth": "config", "unavailable": "config",
          "invalid_request": "model", "refusal": "model", "max_turns": "model"}
```

Every `StepFailure` carries `usage` (the running total) and `attempts`, so failed runs still account for cost (15). Runtime-core is asked to add `model` and `cassette_miss` to the `StepFailure` causes (§15).

`AgentResult(output, note, usage, attempts, model=ModelInfo(provider, model_id, tier, thinking))`. `model_id` is the concrete id reported by the last response.

### 6.3 Prompt — `agentic/prompt.py` (exact text)

```python
SYSTEM_PREAMBLE = """\
You are one step in an automated, tested process. No human is available to answer questions.
Carry out the task below using only the tools provided, then reply with the step's structured output.
The `exit` field selects the outcome: choose the exit that matches what happened and fill in exactly that exit's fields.

# Task
"""

def render_prompt(instruction: str, context: Mapping[str, Any], input: Mapping[str, Any],
                  *, raw_prompt: str | None = None) -> tuple[str, str]:
    """-> (system, user). Deterministic: JSON with sort_keys, indent=2, ensure_ascii=False, default=str."""
    if raw_prompt is not None:
        return instruction, raw_prompt                          # raw mode (compiler, chat)
    system = SYSTEM_PREAMBLE + inspect.cleandoc(instruction)
    parts = []
    if context:
        parts.append("# Context\n```json\n" + _j(context) + "\n```")
    parts.append("# Input\n```json\n" + _j(input) + "\n```")
    return system, "\n\n".join(parts)

RETRY_VALIDATION = """\
Your structured output did not validate against the step's Output schema:
{errors}
Reply again with corrected structured output. Only call tools again if you need information you do not have."""
RETRY_NO_OUTPUT = "You replied without structured output. Reply with the step's structured output now."
RETRY_TRUNCATED = ("Your previous reply was cut off at the output token limit. "
                   "Reply again, more concisely, with the step's structured output.")

def format_validation_errors(err: ValidationError, limit: int = 20) -> str:
    # one line per error: "- output.<loc joined by '.'>: <msg> (got <repr(input)[:80]>)"; at most `limit` lines
```

Both provider kinds use `render_prompt`. Swapping provider therefore does not change the prompt (3.9).

### 6.4 Output schema — `agentic/schema.py`

Runtime-core's `StepInterface` supplies the discriminated `TypeAdapter` and `output_json_schema()`. For a union that is `oneOf` + `discriminator` + `$defs`; for a single model it is a plain object. This module owns the provider-facing transformation.

```python
DROP = {"discriminator", "title", "default", "examples", "minimum", "maximum", "exclusiveMinimum",
        "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern", "minItems", "maxItems",
        "uniqueItems", "minProperties", "maxProperties"}
FORMATS = {"date-time", "time", "date", "duration", "email", "hostname", "uri", "ipv4", "ipv6", "uuid"}

def wrap_output_schema(schema: dict) -> dict:
    """Provider envelope (verified live with claude-code, spike 7):
       {"type":"object","properties":{"output": clean(schema minus $defs)},"required":["output"],
        "additionalProperties":false, "$defs": {k: clean(v)}}   ($defs only if present; $ref paths stay valid)."""

def _clean(node):   # recursive
    # dict:  drop keys in DROP; rename "oneOf" -> "anyOf"; drop "format" not in FORMATS;
    #        "properties": clean each VALUE (property names are never dropped, e.g. a field called "title");
    #        if node has "properties": set additionalProperties=false and required=list(properties)   # every field
    #        requested; the spec never makes fields optional for another exit, and `exit` must be present for
    #        the discriminator even though pydantic gives it a default
    # list:  clean each item
    # other: unchanged

def unwrap_output(value: Any) -> Any:
    return value["output"] if isinstance(value, dict) and set(value) == {"output"} else value

def strict_compatible(wrapped: dict) -> bool:
    """False if any object node has no `properties` (free-form dict[str, X] / `object`) — the Messages API's strict
    structured outputs need additionalProperties:false on every object. Used by the anthropic provider (§8.3)."""
```

### 6.5 ModelProvider loop (runtime-owned) — exact

```python
TRANSPORT_RESTARTS = 2
BACKOFF_S = (1.0, 4.0)

class ModelLoop:
    def run(self) -> AgentResult:
        system, user = render_prompt(step_instruction(step), call.context or {}, call.input.model_dump(mode="json"))
        schema = call.interface.output_json_schema()
        restarts = 0
        while True:                                                # restart loop
            messages = [{"role": "user", "content": [{"type": "text", "text": user}]}]
            attempt, failures, note = 1, 0, ""
            try:
                for turn in range(policy.max_turns):
                    reason = "initial" if turn == 0 else ...       # "tool_results" | "validation_retry"
                    resp = provider.generate(GenerateRequest(policy.model_id, policy.thinking, system,
                                                             messages, tools.schemas(), schema))
                    usage += resp.usage
                    if resp.text.strip() and resp.structured_output is None: note = resp.text.strip()[:500]
                    if resp.stop == "refusal":
                        emit_model_call(resp, outcome="refusal"); raise ProviderError("model refused", kind="refusal", retryable=False)
                    if resp.stop == "max_tokens":                  # truncated: drop the partial assistant turn
                        emit_model_call(resp, outcome="truncated")
                        failures += 1; attempt += 1
                        if failures > policy.retries.validation: raise OutputValidationFailed("output truncated")
                        messages[-1]["content"].append({"type": "text", "text": RETRY_TRUNCATED})   # keeps alternation
                        continue
                    messages.append(resp.message)
                    if resp.tool_calls:
                        emit_model_call(resp, outcome="tool_calls")
                        results = [tools.invoke(tc.name, tc.arguments) for tc in resp.tool_calls]   # sequential, in order
                        messages.append({"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": tc.id, "content": r.text, "is_error": r.is_error}
                            for tc, r in zip(resp.tool_calls, results)]})
                        continue
                    if resp.structured_output is None:
                        problem, text = "no_output", RETRY_NO_OUTPUT
                    else:
                        try:
                            out = call.interface.output_adapter.validate_python(resp.structured_output)
                            emit_model_call(resp, outcome="valid")
                            return AgentResult(out, note, usage, attempt, model_info(resp.model_id))
                        except ValidationError as e:
                            problem, text = "invalid", RETRY_VALIDATION.format(errors=format_validation_errors(e))
                    emit_model_call(resp, outcome=problem, errors=text)
                    failures += 1; attempt += 1
                    if failures > policy.retries.validation:
                        raise OutputValidationFailed(text)
                    messages.append({"role": "user", "content": [{"type": "text", "text": text}]})   # CONTINUE
                raise AgentLoopError(f"agent loop exceeded max_turns={policy.max_turns}")
            except ProviderError as e:
                if e.retryable and tools.calls == 0 and restarts < TRANSPORT_RESTARTS:
                    time.sleep(BACKOFF_S[restarts]); restarts += 1
                    continue                                        # FULL restart: fresh messages
                raise                                               # after a tool call -> error exit (cause transport)
```

- `tools.calls` counts every invocation, including replayed and argument-rejected ones. Once the model has *asked* for a tool, a restart could repeat side effects, so the count must include those.
- Tool execution raising `ToolFailure` is not caught here; it propagates to `complete()` and becomes `StepFailure("tool")`.
- The sleep uses `time.sleep`, which tests patch.

### 6.6 AgentProvider loop — exact

```python
class AgentLoop:
    def run(self) -> AgentResult:
        restarts = 0
        while True:
            continuation, attempt, failures, harness_calls = None, 1, 0, 0
            try:
                while True:
                    resp = provider.run(AgentRequest(
                        model_id=policy.model_id, thinking=policy.thinking, instruction=step_instruction(step),
                        context=call.context or {}, input=call.input.model_dump(mode="json"),
                        output_schema=call.interface.output_json_schema(), tools=tools.handles(),
                        mcp_servers=tools.mcp_refs(), workspace=rt.workspace, max_turns=policy.max_turns,
                        builtin_tools=list(policy.builtin_tools), continuation=continuation))
                    usage += resp.usage; harness_calls += resp.tool_calls
                    tools.trace_harness_calls(resp.transcript)      # tool.call events for built-ins (source "harness")
                    if resp.structured_output is None:
                        problem, text = "no_output", RETRY_NO_OUTPUT
                    else:
                        try:
                            out = call.interface.output_adapter.validate_python(resp.structured_output)
                            emit_model_call(resp, outcome="valid")
                            return AgentResult(out, resp.note, usage, attempt, model_info(resp.model_id))
                        except ValidationError as e:
                            problem, text = "invalid", RETRY_VALIDATION.format(errors=format_validation_errors(e))
                    emit_model_call(resp, outcome=problem, errors=text)
                    failures += 1; attempt += 1
                    if failures > policy.retries.validation:
                        raise OutputValidationFailed(text)
                    continuation = Continuation(session=resp.session, message=text)   # CONTINUE the conversation
            except ProviderError as e:
                called = e.tool_called or tools.calls > 0 or harness_calls > 0
                if e.retryable and not called and restarts < TRANSPORT_RESTARTS:
                    time.sleep(BACKOFF_S[restarts]); restarts += 1
                    continue
                raise
```

### 6.7 Decision table (3.5 as implemented)

| Situation | Result |
|---|---|
| Output fails validation (or none, or truncated); fewer than `retries.validation` failures so far | Continue the same conversation with the error message |
| Output fails validation; retries exhausted | `error` exit, cause `output_validation`, attempts = 1 + retries |
| Transport error (429/5xx/529, connection, CLI crash); no tool called in this loop | Full restart, at most 2, backoff 1 s then 4 s |
| Transport error after any tool call (including earlier attempts) | `error` exit, cause `transport` |
| Auth error, CLI missing | `error` exit, cause `config` (no restart) |
| Refusal, invalid request (400), provider max_turns | `error` exit, cause `model` |
| Runtime loop exceeds `max_turns` | `error` exit, cause `model` |
| Tool raises (after its retries) | `error` exit, cause `tool` (claude-code aborts the harness first) |
| Tool argument validation fails / `ToolInputError` / MCP `isError` | Error result returned to the model; the loop continues |
| MCP snapshot mismatch, missing MCP env var | `error` exit, cause `config`, before any model call |
| Cassette miss in replay | `error` exit, cause `cassette_miss`; `run_step` re-raises `CassetteMissError` |

### 6.8 Trace events emitted by the loop

These use runtime-core's envelope via `rt.trace.emit(type, **fields)`.

`model.call` (one per `generate`/`run`):

```json
{"type":"model.call","step":"extract","provider":"claude-code","kind":"agent","tier":"cheap","thinking":"low",
 "model_id":"claude-haiku-4-5-20251001","n":1,"attempt":1,"restart":0,"reason":"initial",
 "outcome":"valid","errors":null,
 "request_hash":"9f2c0b6a51d34e7f8a0c1b2d3e4f5a6b",
 "request":{"instruction":"Given the text of a supplier invoice, …","context":{"process.goal":"…"},
            "input":{"invoice_text":"INVOICE INV-1042 …"},"output_schema":{"…":"…"},"tools":[],"mcp":[],
            "builtin_tools":[],"max_turns":25,"continuation":null},
 "messages_new":null,
 "response":{"structured_output":{"exit":"done","invoice_number":"INV-1042","total":1200.5,"currency":"GBP","due_date":"2026-10-01"},
             "note":"","transcript":[{"type":"tool_use","id":"t1","name":"StructuredOutput","input":{"…":"…"}}]},
 "usage":{"input_tokens":2120,"output_tokens":120,"cache_read_tokens":0,"cache_write_tokens":0,"cost_usd":0.00272,"latency_ms":3144.0,"calls":1},
 "startup_ms":612.0,"cost_basis":"list","cassette":"live","cassette_file":null}
```

- For `kind: "generate"`, `request` omits `messages` and `messages_new` holds the messages appended since the previous `model.call` of this attempt (all of them on the first call). The trace therefore stays linear, and the full request can be reconstructed.
- `outcome` is one of `tool_calls`, `valid`, `invalid`, `no_output`, `truncated`, `refusal` or `error`.

`tool.call` (one per invocation; for claude-code built-ins, reconstructed from the transcript):

```json
{"type":"tool.call","step":"triage","tool":"github__get_issue","source":"mcp:github","effects":["network"],
 "idempotent":true,"args":{"number":42},"result":"{\"number\":42,\"state\":\"open\",\"title\":\"Login page broken\"}",
 "ok":true,"is_error":false,"error":null,"tries":1,"latency_ms":41.2,"replayed":false}
```

`source` is one of `builtin`, `method`, `mcp:<server>` or `harness`. `args` and `result` are truncated to 4 kB each.

---

## 7. The `claude-code` AgentProvider — `wynd/runtime/providers/claude_code.py`

```python
from __future__ import annotations
import asyncio, contextlib, logging, os, time
from pathlib import Path
from typing import Any, Callable, ClassVar, Mapping

from wynd.runtime import __version__
from wynd.runtime.agentic.errors import ProviderError, ToolFailure
from wynd.runtime.agentic.prompt import render_prompt
from wynd.runtime.agentic.schema import unwrap_output, wrap_output_schema
from wynd.runtime.providers.types import AgentRequest, AgentResponse, ToolHandle
from wynd.runtime.usage import Usage

log = logging.getLogger("wynd.provider.claude_code")
SERVER = "wynd"
EFFORT = {"none": "low", "low": "low", "medium": "medium", "high": "high"}
BUILTIN_EFFECTS = {"Read": ["filesystem"], "Glob": ["filesystem"], "Grep": ["filesystem"], "Edit": ["filesystem"],
                   "Write": ["filesystem"], "NotebookEdit": ["filesystem"], "Bash": ["shell"],
                   "WebFetch": ["network"], "WebSearch": ["network"]}   # compiler records these in the lock's effects

def _sdk():
    try:
        import claude_agent_sdk
    except ImportError as e:
        raise ProviderError("claude-code provider needs claude-agent-sdk in this venv; it is installed from the "
                            "provider's env fragment (is the step's venv built from the effective fragment?)",
                            kind="unavailable", retryable=False) from e
    return claude_agent_sdk

class _TranscriptStore:
    """Minimal duck-typed SessionStore (append + load only): captures the main transcript for stateless resume."""
    def __init__(self, entries: list[dict] | None = None) -> None:
        self.entries: list[dict] = list(entries or [])
    async def append(self, key, entries) -> None:
        if key.get("subpath") is None:
            self.entries.extend(entries)
    async def load(self, key):
        return list(self.entries) if key.get("subpath") is None and self.entries else None

class _State:
    calls = 0
    failure: ToolFailure | None = None

class ClaudeCodeProvider:
    name: ClassVar[str] = "claude-code"
    kind: ClassVar[str] = "agent"
    default_tiers: ClassVar[dict[str, str]] = {"cheap": "haiku", "standard": "sonnet", "strong": "opus"}
    env_fragment = EnvFragment(deps=["claude-agent-sdk>=0.2.157,<0.3"], system=[], requires="glibc")
    env_vars = [
        EnvVarDecl(name="CLAUDE_CODE_OAUTH_TOKEN", secret=True, required=False, one_of="claude-code-auth",
                   description="Claude Code subscription token (dev key, `claude setup-token`). Locally the logged-in "
                               "`claude` CLI is used when neither this nor ANTHROPIC_API_KEY is set."),
        EnvVarDecl(name="ANTHROPIC_API_KEY", secret=True, required=False, one_of="claude-code-auth",
                   description="Anthropic API key; alternative to CLAUDE_CODE_OAUTH_TOKEN (takes precedence if both are set)."),
    ]

    def __init__(self, tiers: Mapping[str, str], *, query_fn: Callable | None = None) -> None:
        self._tiers, self._query_fn = dict(tiers), query_fn      # query_fn: test seam (fake async iterator)

    def tiers(self) -> dict[str, str]:
        return dict(self._tiers)

    def run(self, req: AgentRequest) -> AgentResponse:
        return asyncio.run(self._run(req))        # worker threads have no running loop; one loop per call

    async def _run(self, req: AgentRequest) -> AgentResponse:
        sdk = _sdk()
        query = self._query_fn or sdk.query
        state = _State()
        prev = req.continuation.session if req.continuation else None
        store = _TranscriptStore(prev["entries"] if prev else None)
        system, user = render_prompt(req.instruction, req.context, req.input, raw_prompt=req.prompt)
        builtins = list(req.builtin_tools or [])
        opts = sdk.ClaudeAgentOptions(
            model=req.model_id,                                      # alias (haiku) or full id
            system_prompt=({"type": "preset", "preset": "claude_code", "append": system} if builtins else system),
            tools=builtins,                                          # [] -> no built-ins (spike c)
            mcp_servers=({SERVER: sdk.create_sdk_mcp_server(SERVER, tools=[_bridge(sdk, h, state, req.on_event)
                                                                           for h in req.tools])}
                         if req.tools else {}),
            allowed_tools=[f"mcp__{SERVER}__{h.name}" for h in req.tools] + builtins,
            strict_mcp_config=True,                                  # ignore .mcp.json, claude.ai connectors, plugins
            setting_sources=[],                                      # ignore ~/.claude settings, CLAUDE.md, hooks
            permission_mode="dontAsk",                               # never prompt; unlisted tools denied
            output_format=({"type": "json_schema", "schema": wrap_output_schema(req.output_schema)}
                           if req.output_schema is not None else None),
            cwd=str(req.workspace),
            max_turns=req.max_turns,
            effort=EFFORT[req.thinking],                             # explicit: never inherit CLAUDE_EFFORT from a parent
            resume=prev["session_id"] if prev else None,
            session_store=store,
            env={"CLAUDE_AGENT_SDK_CLIENT_APP": f"wynd/{__version__}",
                 "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"},  # no autoupdate/telemetry/error reporting (15)
            stderr=lambda line: log.debug("claude: %s", line),
        )
        t0 = time.monotonic()
        startup_ms = session_id = result = None
        model_id, note, transcript, builtin_calls = req.model_id, "", [], 0
        try:
            async for m in query(prompt=req.continuation.message if prev else user, options=opts):
                if state.failure:
                    break                                            # a tool raised: abort the harness (closes the CLI)
                if isinstance(m, sdk.SystemMessage) and m.subtype == "init":
                    startup_ms = (time.monotonic() - t0) * 1000
                    session_id = m.data.get("session_id"); model_id = m.data.get("model", model_id)
                elif isinstance(m, sdk.AssistantMessage):
                    for b in m.content:
                        if isinstance(b, sdk.TextBlock) and b.text.strip():
                            note = b.text.strip()[:500]; _push(transcript, req.on_event, {"type": "text", "text": b.text})
                        elif isinstance(b, sdk.ToolUseBlock) and b.name != "StructuredOutput":
                            if not b.name.startswith(f"mcp__{SERVER}__"):
                                builtin_calls += 1
                            _push(transcript, req.on_event, {"type": "tool_use", "id": b.id,
                                  "name": b.name.removeprefix(f"mcp__{SERVER}__"), "input": b.input})
                elif isinstance(m, sdk.UserMessage):
                    for b in m.content if isinstance(m.content, list) else []:
                        if isinstance(b, sdk.ToolResultBlock):
                            _push(transcript, req.on_event, {"type": "tool_result", "id": b.tool_use_id,
                                  "is_error": bool(b.is_error), "content": _text(b.content)[:4000]})
                elif isinstance(m, sdk.ResultMessage):
                    result = m
        except sdk.CLINotFoundError as e:
            raise ProviderError(f"Claude Code CLI not found: {e}", kind="unavailable", retryable=False)
        except sdk.ResultError as e:
            raise _classify(e, tool_called=state.calls + builtin_calls > 0) from e
        except (sdk.CLIConnectionError, sdk.ProcessError, sdk.CLIJSONDecodeError, sdk.ClaudeSDKError) as e:
            raise ProviderError(f"claude-code transport failure: {e}", kind="transport", retryable=True,
                                tool_called=state.calls + builtin_calls > 0) from e
        finally:
            if session_id:
                _cleanup_local_session(sdk, session_id, req.workspace)
        if state.failure:
            raise state.failure
        if result is None:
            raise ProviderError("claude-code ended without a result", kind="transport", retryable=True,
                                tool_called=state.calls + builtin_calls > 0)
        usage, cum, basis = _usage_delta(result, prev, latency_ms=(time.monotonic() - t0) * 1000)
        out = result.structured_output
        return AgentResponse(
            structured_output=unwrap_output(out) if out is not None else None,
            usage=usage, model_id=model_id, transcript=transcript,
            session={"session_id": result.session_id, "entries": store.entries, "cum": cum},
            note=note if out is not None else (result.result or note)[:500],
            tool_calls=state.calls + builtin_calls, startup_ms=startup_ms, cost_basis=basis)
```

Helpers (exact behaviour):

```python
def _bridge(sdk, h: ToolHandle, state: _State, on_event):
    @sdk.tool(h.name, h.description, h.input_schema)          # dict with type+properties is passed through (spike b)
    async def handler(args: dict) -> dict:
        if state.failure:
            return {"content": [{"type": "text", "text": "step aborted"}], "is_error": True}
        state.calls += 1
        try:
            r = await asyncio.to_thread(h.invoke, args)       # sync ToolSet.invoke: validation, retries, trace, replay
        except ToolFailure as e:
            state.failure = e                                 # the consumer loop breaks on the next message
            return {"content": [{"type": "text", "text": f"tool failed: {e}; the step is aborting"}], "is_error": True}
        return {"content": [{"type": "text", "text": r.text}], "is_error": r.is_error}
    return handler

def _classify(e, *, tool_called: bool) -> ProviderError:
    status, text = e.api_error_status, (e.result or str(e))
    if e.subtype == "error_max_turns":
        return ProviderError(text, kind="max_turns", retryable=False, tool_called=tool_called)
    if status in (401, 403) or text.startswith("Not logged in"):
        return ProviderError("claude-code is not authenticated: log in with `claude` (local) or set "
                             "CLAUDE_CODE_OAUTH_TOKEN (`claude setup-token`) or ANTHROPIC_API_KEY. " + text,
                             kind="auth", retryable=False, status=status)
    if status in (400, 404, 413, 422):
        return ProviderError(text, kind="invalid_request", retryable=False, status=status, tool_called=tool_called)
    return ProviderError(text, kind="transport", retryable=True, status=status, tool_called=tool_called)
    # 429/5xx/529/None+api_error/error_during_execution -> transport

def _usage_delta(result, prev, *, latency_ms: float) -> tuple[Usage, dict, str | None]:
    mu = result.model_usage or {}
    tot = {"in": sum(v.get("inputTokens", 0) for v in mu.values()),
           "out": sum(v.get("outputTokens", 0) for v in mu.values()),
           "cr": sum(v.get("cacheReadInputTokens", 0) for v in mu.values()),
           "cw": sum(v.get("cacheCreationInputTokens", 0) for v in mu.values()),
           "cost": result.total_cost_usd}
    base = prev["cum"] if prev else {"in": 0, "out": 0, "cr": 0, "cw": 0, "cost": 0.0}   # cumulative on resume (spike d)
    cost = None if tot["cost"] is None else round(tot["cost"] - (base["cost"] or 0.0), 8)
    basis = next((v.get("costBasis") for v in mu.values() if v.get("costBasis")), None)
    return (Usage(input_tokens=tot["in"] - base["in"], output_tokens=tot["out"] - base["out"],
                  cache_read_tokens=tot["cr"] - base["cr"], cache_write_tokens=tot["cw"] - base["cw"],
                  cost_usd=cost, latency_ms=latency_ms, calls=1),
            tot, "list" if basis == "list" else ("api" if basis else None))

def _cleanup_local_session(sdk, session_id: str, cwd: Path) -> None:
    with contextlib.suppress(FileNotFoundError, ValueError):
        sdk.delete_session(session_id, directory=str(cwd))
    projects = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "projects"
    with contextlib.suppress(OSError):
        (projects / sdk.project_key_for_directory(str(cwd))).rmdir()   # only if empty
```

Notes:
- **Auth needs no code.** It is the CLI's own precedence: `ANTHROPIC_API_KEY` over `CLAUDE_CODE_OAUTH_TOKEN` over the keychain or `~/.claude` login. The worker inherits the process env (runtime-core pool), so no `if environment:` branch exists anywhere.
- **Container prerequisites** (wynd-base, owned by the builder):
  - The runtime user must have a writable `$HOME`, where Claude Code writes `~/.claude.json` and `~/.claude/projects/...`.
  - `CLAUDE_CONFIG_DIR` may be pointed at an ephemeral directory with env auth. This is optional and goes in the manifest description.
- **Per-run hygiene.** The session JSONL is deleted after every call and the empty project dir is removed. Resumes use the SDK's temporary materialised config. The spikes confirmed no per-cwd entries were added to `~/.claude.json`.
- **Latency.** Startup is about 0.6 s and a small haiku structured call about 3–5 s end to end. This is recorded as `startup_ms` and `latency_ms`, and it backs the validator's `latency: fast` warning (W206).
- **Raw mode** (`req.prompt` set: compiler, chat): the system prompt is `instruction` verbatim. `output_schema=None` means free text in `result.result` and `structured_output=None`. The loop is not used; callers call `run()` directly and use `continuation` for follow-up turns (chat).

---

## 8. The `anthropic` ModelProvider — `wynd/runtime/providers/anthropic.py`

### 8.1 Class

```python
class AnthropicProvider:
    name: ClassVar[str] = "anthropic"
    kind: ClassVar[str] = "model"
    default_tiers: ClassVar[dict[str, str]] = {"cheap": "claude-haiku-4-5", "standard": "claude-sonnet-5",
                                               "strong": "claude-opus-5"}
    env_fragment = EnvFragment(deps=[], system=[], requires=None)
    env_vars = [EnvVarDecl(name="ANTHROPIC_API_KEY", secret=True, required=True, description="Anthropic API key"),
                EnvVarDecl(name="ANTHROPIC_BASE_URL", secret=False, required=False,
                           description="Messages API base URL (default https://api.anthropic.com)")]
    MAX_TOKENS = 16000

    def __init__(self, tiers: Mapping[str, str], *, http: HttpClient | None = None,
                 env: Mapping[str, str] | None = None) -> None: ...
        # http: runtime-core HttpClient (test seam: any object with .post(url, json=, headers=, timeout=) -> HttpResponse)
    def tiers(self) -> dict[str, str]: ...
    def generate(self, req: GenerateRequest) -> GenerateResponse: ...
```

### 8.2 Request mapping (`POST {base}/v1/messages`)

Headers: `x-api-key: $ANTHROPIC_API_KEY`, `anthropic-version: 2023-06-01`, `content-type: application/json`. The timeout is 600 s. The request is not streamed, since `max_tokens=16000` stays well under the timeout.

```python
body = {
  "model": req.model_id,
  "max_tokens": MAX_TOKENS,
  "system": req.system,
  "messages": req.messages,                       # neutral format == Messages API format
  **({"tools": [{"name": t.name, "description": t.description, "input_schema": t.input_schema}
                for t in req.tools]} if req.tools else {}),
  **thinking_params(req.model_id, req.thinking),  # below
}
if req.output_schema is not None:
    wrapped = wrap_output_schema(req.output_schema)
    if strict_compatible(wrapped):
        body.setdefault("output_config", {})["format"] = {"type": "json_schema", "schema": wrapped}
    else:   # parse-and-retry mechanism (3.9 "provider's concern"): schema in the system prompt, JSON parsed from text
        body["system"] += ("\n\nWhen you are finished, reply with only a JSON object matching this JSON Schema:\n"
                           + json.dumps(wrapped, sort_keys=True))
```

Thinking mapping. The table below is a small set of model-family rules, and `startswith` matching on `model_id` selects the row:

| model family | none | low | medium | high |
|---|---|---|---|---|
| `claude-haiku-4-5` (effort unsupported; budget thinking) | no `thinking` | no `thinking` | `thinking={"type":"enabled","budget_tokens":2048}` | `thinking={"type":"enabled","budget_tokens":8192}` |
| everything else (`claude-sonnet-5`, `claude-opus-5`, `claude-opus-5-5`, `claude-opus-4-6..8`, `claude-sonnet-4-6`, `claude-fable-*`) | `output_config.effort="low"`, no `thinking` | `thinking={"type":"adaptive"}`, `effort="low"` | adaptive + `effort="medium"` | adaptive + `effort="high"` |

`effort` goes under `output_config` and is merged with `format`. The request never sends `budget_tokens` to 4.6+ models, never sends `{"type":"disabled"}` (it returns a 400 on Opus 5.5 and Fable), and never forces `tool_choice`, which Opus 5.5 and Fable 5.1 reject.

### 8.3 Response mapping

```python
content = body["content"]
tool_calls = [ToolCall(b["id"], b["name"], b["input"]) for b in content if b["type"] == "tool_use"]
text = "".join(b["text"] for b in content if b["type"] == "text")
stop = {"end_turn": "end", "stop_sequence": "end", "tool_use": "tool_calls",
        "max_tokens": "max_tokens", "refusal": "refusal"}.get(body["stop_reason"], "end")
structured = None
if stop == "end" and req.output_schema is not None and not tool_calls:
    with contextlib.suppress(ValueError):
        structured = unwrap_output(json.loads(_strip_fences(text)))   # output_config.format guarantees JSON text
u = body["usage"]   # input_tokens excludes cache tokens
usage = Usage(input_tokens=u["input_tokens"], output_tokens=u["output_tokens"],
              cache_read_tokens=u.get("cache_read_input_tokens") or 0,
              cache_write_tokens=u.get("cache_creation_input_tokens") or 0,
              cost_usd=cost(body["model"], u), latency_ms=elapsed_ms, calls=1)
return GenerateResponse(message={"role": "assistant", "content": content}, text=text, tool_calls=tool_calls,
                        structured_output=structured, stop=stop, usage=usage, model_id=body["model"],
                        cost_basis="api", raw=body)
```

The assistant `content` is appended **verbatim**, including `thinking` blocks and their `signature`, because the API requires them unchanged when the conversation continues.

Errors: the HTTP status maps to a `ProviderError` kind. The provider does **no internal retries**; restarts are the loop's decision (§6.7).

| status / condition | kind |
|---|---|
| 401, 403 | `auth` |
| 400, 404, 413, 422 | `invalid_request` (includes the API's `error.message`) |
| 429, 500, 502, 503, 504, 529 | `transport` (retryable) |
| `HttpError(response=None)`: connection refused/reset, DNS, timeout | `transport` (retryable) |
| missing `ANTHROPIC_API_KEY` | `auth` ("set ANTHROPIC_API_KEY") |

Pricing (USD per 1M tokens, input/output). Cache reads cost 0.1× input and cache writes 1.25× input. An unknown model gives `cost_usd=None`. Matching is by prefix, longest first:

```python
PRICING = {"claude-haiku-4-5": (1.0, 5.0), "claude-sonnet-4-6": (3.0, 15.0), "claude-sonnet-5": (2.0, 10.0),
           "claude-opus-4-6": (5.0, 25.0), "claude-opus-4-7": (5.0, 25.0), "claude-opus-4-8": (5.0, 25.0),
           "claude-opus-5-5": (4.0, 20.0), "claude-opus-5": (5.0, 25.0), "claude-fable-5": (10.0, 50.0)}
def cost(model: str, u: dict) -> float | None:
    # (in*pi + out*po + cache_read*pi*0.1 + cache_write*pi*1.25) / 1e6, rounded to 8 dp
```

The Messages API is **not** exercised live here: there is no API key on this machine, only the subscription. The combination of `output_config.format` and `tools` is covered by `@live` test `test_anthropic_live_tools_and_format`. If the API rejects the combination, the fallback already exists: the `strict_compatible=False` path (schema in the prompt, then parse and validate).

---

## 9. Tools — `wynd/runtime/tools/`

### 9.1 Decorator — `tools/decorator.py`

```python
@dataclass(frozen=True)
class ToolDecl:
    name: str | None
    effects: tuple[Effect, ...]
    idempotent: bool
    env: tuple[str, ...]

def tool(fn: Callable | None = None, /, *, name: str | None = None, effects: Iterable[Effect] = (),
         idempotent: bool = False, env: Iterable[str] = ()) -> Callable:
    """`@tool` or `@tool(effects=["network"], idempotent=True, env=["CRM_TOKEN"])`. Works on methods, plain functions
    and closures. Only marks the function (fn.__wynd_tool__ = ToolDecl(...)); the function stays directly callable (3.8)."""

@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str                        # inspect.getdoc(fn) (required: "tool <name> needs a docstring")
    effects: tuple[Effect, ...]
    idempotent: bool
    env: tuple[str, ...]
    source: Literal["builtin", "method", "mcp"]
    server: str | None                      # mcp only
    input_schema: dict[str, Any]            # JSON Schema (from args model, or the MCP snapshot)
    args_model: type[BaseModel] | None      # None for mcp
    returns: TypeAdapter | None             # None for mcp / missing annotation
    def describe(self) -> dict[str, Any]: ...   # {"name","description","effects","idempotent","env","source","input_schema"}

def tool_spec(fn: Callable, source: Literal["builtin", "method"]) -> ToolSpec: ...
    # args model: pydantic.create_model(f"{fn.__name__}_args", __config__=ConfigDict(extra="forbid"),
    #   **{p: (hints.get(p, Any), ... if default is empty else default) for p in signature params except self})
    # parameter descriptions: Annotated[str, Field(description=...)] (verified: lands in the JSON schema)
    # input_schema = args_model.model_json_schema(); returns = TypeAdapter(hints["return"]) if annotated

def step_tools(cls: type) -> list[ToolSpec]: ...   # methods with __wynd_tool__ (source "method") + cls.tools (source "builtin")

def env(name: str) -> str:
    """Secret/config reference resolved at run time (3.8). Raises McpConfigError-like `MissingEnvVar`
    ("<name> is not set; it is listed in process.env.yaml — run `wynd env check`") -> StepFailure("config")."""
```

Result serialisation for the model: a `str` return is used as is. Otherwise the value is serialised with `json.dumps(returns.dump_python(v, mode="json"), ensure_ascii=False, sort_keys=True)`. Output is capped at 100 000 characters with `…[truncated N chars]`.

### 9.2 ToolSet — `tools/toolset.py`

```python
class ToolSet:
    calls: int                                          # every invocation attempt requested by the model
    @classmethod
    def for_step(cls, step: AgenticStep, call: AgentCall, cassettes: CassetteSession) -> "ToolSet": ...
        # binds: method tools -> getattr(step, name) (bound method); builtins -> the function;
        #        MCP: for snap in call.policy.mcp: one ToolSpec per snapshot tool named f"{server}__{tool}",
        #             source "mcp", effects ("network",), idempotent = snap tool idempotent, env = entry.auth_env
    def schemas(self) -> list[ToolSchema]: ...          # ModelProvider
    def handles(self) -> list[ToolHandle]: ...          # AgentProvider (invoke = self.invoke bound by name)
    def mcp_refs(self) -> list[McpServerRef]: ...
    def connect_mcp(self) -> None: ...                  # §10.5; raises McpSnapshotMismatch / McpConfigError
    def invoke(self, name: str, arguments: dict[str, Any]) -> ToolResult: ...
    def trace_harness_calls(self, transcript: list[dict]) -> None: ...   # tool.call source "harness" for built-ins
    def close(self) -> None: ...                        # close MCP clients (stdio: terminate child)

_RUNTIME: ContextVar[RuntimeHandle | None]              # set around each builtin/method invocation
def current_runtime() -> RuntimeHandle: ...             # builtins use it; outside a step run -> RuntimeError
```

`invoke` algorithm:

```
1. spec = by_name.get(name) or return ToolResult(f"unknown tool {name!r}", is_error=True)
2. self.calls += 1
3. validate: args = spec.args_model.model_validate(arguments) (not for MCP: the server validates)
     ValidationError -> return ToolResult("invalid arguments:\n" + errors, is_error=True)     # model error, not a failure
4. replay: if cassettes.mode == "replay" and (spec.source == "mcp" or "network" in spec.effects):
     entry = cassettes.find_tool(name, arguments)  -> CassetteMissError if absent
     emit tool.call(replayed=True); if entry.error: raise ToolFailure(entry.error) else return entry.result
5. tries = 1 + (policy.retries.tool if spec.idempotent else 0)      # 3.5: default tool retries 1, idempotent only
   for i in range(tries):
       try: value = call_with_runtime(spec, args)                    # contextvar bound; method/builtin/MCP call
            result = ToolResult(serialise(spec, value))              # MCP: text from content, is_error from isError
            break
       except ToolInputError as e: result = ToolResult(str(e), is_error=True); break
       except Exception as e:
            if is_transient(e) and i + 1 < tries: sleep(0.5 * 2**i); continue
            emit tool.call(ok=False, error=repr(e)); record failure (record mode)
            raise ToolFailure(f"tool {name} failed: {type(e).__name__}: {e}") from e
6. record mode and (mcp or network): cassettes.write_tool(name, arguments, result)
7. emit tool.call(ok=True, is_error=result.is_error, tries=i+1, ...); return result

is_transient(e) = isinstance(e, (TimeoutError, ConnectionError, TransientToolError))
               or (isinstance(e, HttpError) and e.response is None)                        # runtime-core HttpClient
               or (isinstance(e, HttpError) and e.response.status in (429, 502, 503, 504))
               or (isinstance(e, urllib.error.URLError) and not isinstance(e, urllib.error.HTTPError))
               or (isinstance(e, McpError) and e.transient)
```

### 9.3 Builtin library — `tools/builtins.py`

```python
class HttpGetResult(BaseModel):  status: int; content_type: str; text: str; truncated: bool
class SearchResult(BaseModel):   title: str; url: str; snippet: str
class ShellResult(BaseModel):    exit_code: int; stdout: str; stderr: str

@tool(effects=["network"], idempotent=True)
def http_get(url: str, headers: dict[str, str] | None = None) -> HttpGetResult:
    """Fetch a URL with HTTP GET and return the status, content type and body text (first 200 kB, UTF-8 with replacement)."""
    # uses current_runtime().http.get(url, headers=headers, timeout=30). Every HTTP status (incl. 4xx/5xx) is returned
    # as a result for the model to read. Only transport failures raise (HttpError(response=None): connection, DNS,
    # timeout) -> transient -> ToolSet retries once because http_get is idempotent.

@tool(effects=["network"], idempotent=True, env=["BRAVE_API_KEY"])
def web_search(query: str, count: int = 5) -> list[SearchResult]:
    """Search the web and return the top results (title, url, snippet)."""
    # GET https://api.search.brave.com/res/v1/web/search?q=<query>&count=<1..20>
    # headers: Accept: application/json, X-Subscription-Token: env("BRAVE_API_KEY")
    # map data["web"]["results"][i] -> {title, url, snippet: description}

@tool(effects=["filesystem"], idempotent=True)
def workspace_read(path: str) -> str:
    """Read a UTF-8 text file from this run's workspace. `path` is relative to the workspace root."""
    # p = (ws / path).resolve(); not p.is_relative_to(ws.resolve()) -> ToolInputError("path escapes the workspace")
    # missing -> ToolInputError("no such file"); > 1 MB -> first 1 MB + marker; binary -> ToolInputError

@tool(effects=["filesystem"], idempotent=True)
def workspace_write(path: str, content: str) -> str:
    """Write UTF-8 text to a file in this run's workspace, creating directories; returns the relative path."""

@tool(effects=["shell"])
def shell(command: list[str], timeout_s: int = 60) -> ShellResult:
    """Run a command (argv list, no shell) in this run's workspace; stdout and stderr are truncated to 20 kB each."""
    # subprocess.run(command, cwd=ws, capture_output=True, text=True, timeout=min(timeout_s, 600), shell=False)
    # TimeoutExpired -> ToolInputError("timed out after Ns"); FileNotFoundError -> ToolInputError

@tool
def now() -> str:
    """Return the current UTC time as an ISO-8601 timestamp."""
    # datetime.now(timezone.utc).isoformat(timespec="seconds")  -> "2026-09-22T21:50:01+00:00" (normalised in keys)

BUILTINS: list[ToolSpec] = [tool_spec(f, "builtin") for f in (http_get, web_search, workspace_read,
                                                             workspace_write, shell, now)]
```

**Why Brave for `web_search`:** it is an independent index with a stable JSON REST API; one header of auth (`BRAVE_API_KEY`, which the env manifest carries); a free tier; stdlib-only (`urllib`); and no scraping or ToS risk (DuckDuckGo HTML). It also works for **both** provider kinds, which the Anthropic server-side `web_search` tool does not: that tool exists only on the Messages API, and claude-code has its own `WebSearch` built-in, which steps can enable through `builtin_tools`.

---

## 10. MCP — `wynd/runtime/mcp/`

### 10.1 Declaration

```python
@dataclass(frozen=True)
class McpServer:
    name: str
    allow: tuple[str, ...]
    def __init__(self, name: str, *, allow: Iterable[str]) -> None: ...
    # allow is keyword-only and required; empty -> StepDefinitionError("McpServer(allow=...) must name at least one tool;
    # exposing a server's entire tool surface is not permitted")
```

### 10.2 Registry entry (section `mcp`) — `mcp/entry.py`

```python
class McpServerEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str
    transport: Literal["http", "stdio"]
    url: str | None = None                      # http
    headers: dict[str, str] = {}                # values may contain ${env:NAME}; never literal secrets
    command: list[str] | None = None            # stdio argv
    env: dict[str, str] = {}                    # stdio child env overlay; values may contain ${env:NAME}
    auth_env: list[str] = []                    # every env var referenced (manifest + validation)
    timeout_s: float = 30.0
    description: str = ""

ENV_REF = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
def resolve_env_refs(value: str, env: Mapping[str, str], *, server: str) -> str: ...
    # missing -> McpConfigError(f"MCP server {server!r} needs env var {name} (listed in process.env.yaml)")
def open_client(entry: McpServerEntry, env: Mapping[str, str]) -> McpClient: ...   # resolves refs, initialises
```

Mapping from `wynd mcp add` (the CLI owns the command):
- `wynd mcp add github --url https://api.githubcopilot.com/mcp/ --auth-env GITHUB_TOKEN` produces `transport: http`, `headers: {Authorization: "Bearer ${env:GITHUB_TOKEN}"}`, `auth_env: [GITHUB_TOKEN]`.
- `--header 'X-Api-Key=${env:FOO}'` overrides the default Authorization header.
- `--command "npx -y @modelcontextprotocol/server-filesystem /data"` produces `transport: stdio`, with the command split by `shlex`.

### 10.3 Client — `mcp/client.py` (stdlib; prototype verified, spike 6)

```python
PROTOCOL_VERSION = "2025-11-25"          # initialize-handshake era; servers counter-offer any handshake version
class McpError(Exception):     transient: bool = False
class McpAuthError(McpError):  ...       # HTTP 401/403

class HttpTransport:
    def __init__(self, url: str, headers: dict[str, str], timeout: float) -> None: ...
    def send(self, msg: dict) -> dict | None: ...
    # POST url, Content-Type: application/json, Accept: "application/json, text/event-stream", + headers,
    # + Mcp-Session-Id (captured from any response), + MCP-Protocol-Version (after initialize).
    # Response application/json -> json.loads; text/event-stream -> read "data:" events until the one whose id
    # matches (server->client requests/notifications ignored). Notifications (no id) -> expect 202, return None.
    # HTTPError 401/403 -> McpAuthError(hint auth_env); 429/5xx and URLError/timeout -> McpError(transient=True)
    def close(self) -> None: ...          # DELETE url with Mcp-Session-Id (best effort)

class StdioTransport:
    def __init__(self, command: list[str], env: Mapping[str, str]) -> None: ...
    # Popen(stdin=PIPE, stdout=PIPE, stderr=DEVNULL, text=True, bufsize=1); newline-delimited JSON-RPC;
    # read lines until the response with the matching id; EOF -> McpError("stdio server exited", transient=False)
    def close(self) -> None: ...          # close stdin, terminate, wait 5 s, kill

class McpClient:
    def __init__(self, transport: HttpTransport | StdioTransport) -> None: ...
    def initialize(self) -> dict: ...     # {"protocolVersion":..,"capabilities":{},"clientInfo":{"name":"wynd","version":__version__}}
                                          # then notifications/initialized
    def list_tools(self) -> list[dict]: ...   # follows nextCursor pagination
    def call_tool(self, name: str, arguments: dict) -> dict: ...   # {"content":[...],"isError":bool,"structuredContent"?}
    def close(self) -> None: ...
```

Converting a tool result to text: `text` items become their text; `image` items become `[image omitted]`; `resource` items become `resource.text`, or `[resource <uri>]`. If there are no text items and `structuredContent` is present, the text is `json.dumps(structuredContent)`. `isError` becomes `ToolResult.is_error`.

The server→client features (sampling, elicitation, roots) are not supported. Their requests are ignored, and the tool call then fails or times out.

### 10.4 Discovery and snapshot (compile time) — `mcp/snapshot.py`

```python
@dataclass(frozen=True)
class McpToolSpec:                                   # C-R11
    name: str; description: str; input_schema: dict; annotations: dict

def list_tools(entry: McpServerEntry, env: Mapping[str, str] | None = None) -> list[McpToolSpec]: ...
def schema_sha256(name: str, input_schema: dict) -> str:
    return hashlib.sha256(canonical_json({"name": name, "input_schema": input_schema}).encode()).hexdigest()
def snapshot(server: McpServer, tools: list[McpToolSpec]) -> dict: ...
    # every allow name must exist -> McpDiscoveryError(f"server {server.name!r} has no tool {n!r}; available: [...]")
    # returns the lock block (§14.3), tools in `allow` order, idempotent = annotations.idempotentHint or readOnlyHint
```

### 10.5 Run-time verification — `ToolSet.connect_mcp()`

```
for block in policy.mcp:                                   # lock snapshot + {"server": registry entry dict}
    entry = McpServerEntry.model_validate(block["server"]) # missing registry entry -> build_policy already failed "config"
    client = open_client(entry, os.environ)                # env refs resolved here, at run time (3.8)
    live = {t["name"]: t for t in client.list_tools()}
    problems = []
    for t in block["snapshot"]:
        if t["name"] not in live: problems.append(f"{t['name']}: missing on the server")
        elif schema_sha256(t["name"], live[t["name"]]["inputSchema"]) != t["input_schema_sha256"]:
            problems.append(f"{t['name']}: input schema changed")
    if problems:
        raise McpSnapshotMismatch(f"MCP server {entry.name!r} no longer matches the snapshot in step.lock.yaml:\n"
                                  + "\n".join(f"  - {p}" for p in problems)
                                  + "\nRe-run `wynd compile` to re-snapshot, and review the diff.")
    clients[entry.name] = client
```

Descriptions are not compared (3.8 says names and schemas). The model sees the snapshot's description and schema, which keeps prompts and cassette keys deterministic. A connection is opened per step run and closed in `finally`. Connections are never pooled across runs, so no state or auth leaks between runs. The cost is one handshake per agentic step run. Replay mode never connects.

### 10.6 How MCP tools reach each provider kind

- **ModelProvider.** The runtime loop sends MCP tools as ordinary `ToolSchema`s named `<server>__<tool>`. `ToolSet.invoke` calls `client.call_tool`.
- **claude-code.** They become `ToolHandle`s like every other tool and are exposed over the in-process `wynd` SDK server, so the model sees `mcp__wynd__github__get_issue`. The alternative, `ClaudeAgentOptions(mcp_servers={"github": {"type":"http","url":…,"headers":…}})`, works (spike g) but is **not used**:
  1. Headers would be serialised onto the CLI's argv, visible in `ps`.
  2. It would bypass the runtime's snapshot, `allow` filter, retry policy and `tool.call` trace.
  3. The runtime has already connected in order to verify.

---

## 11. Cassettes — `wynd/runtime/cassettes/`

### 11.1 Modes

Modes come from runtime-core's `CassetteConfig.mode`.

| mode | provider call | tool (network/MCP) call | files | default for |
|---|---|---|---|---|
| `live` | real | real | none; the `model.call` trace event still carries the request and response | executor runs (`wynd run`, supervisor) |
| `record` | real | real | writes an entry per call to `record_dir` | the compile job; `wynd test --live` (`WYND_CASSETTE_MODE=record`) |
| `replay` | from `dir` only | from `dir` only | none | `run_step` / `wynd test` (`WYND_CASSETTE_MODE` default) |

### 11.2 Key — `cassettes/key.py`

```python
def canonical_json(obj: Any) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)

DATETIME_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2})")

class Normaliser:
    def __init__(self, literals: Mapping[str, str]) -> None: ...     # value -> placeholder, applied longest first
    @classmethod
    def for_run(cls, run_id: str, workspace: Path) -> "Normaliser":
        return cls({str(workspace): "<workspace>", os.path.realpath(workspace): "<workspace>", run_id: "<run.id>"})
    def apply(self, text: str) -> str:
        for value in sorted(self._literals, key=len, reverse=True):
            text = text.replace(value, self._literals[value])      # also matches JSON-escaped forms on POSIX
        return DATETIME_RE.sub("<datetime>", text)

def request_key(canonical_request: dict, normaliser: Normaliser) -> str:
    return hashlib.sha256(normaliser.apply(canonical_json(canonical_request)).encode()).hexdigest()
```

Canonical requests (every field that influences model behaviour; nothing volatile):

```python
# generate
{"v": 1, "kind": "generate", "provider": P, "tier": T, "thinking": H,
 "system": req.system, "messages": req.messages, "tools": [asdict(t) for t in req.tools],
 "output_schema": req.output_schema}
# agent (first call of an attempt)
{"v": 1, "kind": "agent", "provider": P, "tier": T, "thinking": H,
 "instruction": req.instruction, "context": req.context, "input": req.input, "prompt": req.prompt,
 "output_schema": req.output_schema, "tools": [{"name","description","input_schema"} for h in req.tools],
 "mcp": [{"name", "allow"}...], "builtin_tools": req.builtin_tools or [], "max_turns": req.max_turns,
 "workspace": str(req.workspace)}                       # normalised to "<workspace>"
# agent continuation: chained on the previous key (the session transcript is volatile, the chain is not)
{"v": 1, "kind": "agent_continue", "previous": <key of previous agent call>, "message": continuation.message}
# tool
{"v": 1, "kind": "tool", "tool": name, "arguments": arguments}
```

`model_id` is excluded (Interpretation 6), and so is `on_event`. The provider name is included, so swapping providers causes misses by design.

### 11.3 Entry format and layout — `cassettes/store.py`

There is one JSON file per entry. The path is `<dir>/<key[:32]>.json`, with no sharding. Directories:
- step-level: `<step package>/cassettes/`
- process-level: `<process dir>/tests/cassettes/<step_package_name>/` (compiler C-P5)
- record: `CassetteConfig.record_dir` = `<workspace>/.wynd/cassettes/<step_package_name>/`

```json
{
  "wynd_cassette": 1,
  "kind": "agent",
  "key": "9f2c0b6a51d34e7f8a0c1b2d3e4f5a6b7c8d9e0f1a2b3c4d5e6f708192a3b4c5",
  "package": "extract_invoice_fields_9c0e4b1a22",
  "step": "extract",
  "provider": "claude-code", "tier": "cheap", "thinking": "low",
  "model_id": "claude-haiku-4-5-20251001",
  "recorded_at": "2026-09-22T21:50:03Z",
  "request": {"v": 1, "kind": "agent", "provider": "claude-code", "tier": "cheap", "thinking": "low",
              "instruction": "Given the text of a supplier invoice, …", "context": {"process.goal": "…"},
              "input": {"invoice_text": "INVOICE INV-1042 …"}, "prompt": null,
              "output_schema": {"…": "…"}, "tools": [], "mcp": [], "builtin_tools": [], "max_turns": 25,
              "workspace": "<workspace>"},
  "response": {"structured_output": {"exit": "done", "invoice_number": "INV-1042", "total": 1200.5,
                                     "currency": "GBP", "due_date": "2026-10-01"},
               "note": "", "tool_calls": 0, "model_id": "claude-haiku-4-5-20251001",
               "usage": {"input_tokens": 2120, "output_tokens": 120, "cache_read_tokens": 0, "cache_write_tokens": 0,
                         "cost_usd": 0.00272, "latency_ms": 3144.0, "calls": 1},
               "transcript": [{"type": "tool_use", "id": "t1", "name": "StructuredOutput", "input": {"…": "…"}}]}
}
```

- `request` is stored **normalised** so that a reviewer can diff it. `response` omits `raw` and `session`.
- For `kind: "tool"`, the `response` is `{"result": {"text", "is_error"}}` or `{"error": "<message>"}`.
- For `kind: "generate"`, it is the GenerateResponse fields minus `raw`, with `message` included verbatim.

### 11.4 Wrappers — `cassettes/wrap.py`

```python
class CassetteSession:
    mode: Literal["live", "record", "replay"]
    def wrap(self, factory: Callable[[], Any], kind: Literal["model", "agent"]) -> "CassetteModelProvider | CassetteAgentProvider": ...
    def find_tool(self, name: str, arguments: dict) -> CassetteEntry: ...
    def write_tool(self, name: str, arguments: dict, result: ToolResult | None, error: str | None = None) -> None: ...

class CassetteModelProvider:                            # implements ModelProvider
    def generate(self, req):
        key = request_key(canonical(req), self.norm)
        if mode == "replay":
            e = find(dir, key) or raise CassetteMissError(NO_RECORDING, key=key, dir=dir, dump=dump_request(...))
            return response_from(e)                     # latency_ms from the recording; cassette="replay" in model.call
        resp = self._inner().generate(req)              # inner built lazily (credentials only needed when live)
        if mode == "record": write_entry(record_dir, key, req, resp)
        return resp
    def tiers(self): ...

class CassetteAgentProvider:                            # implements AgentProvider; same shape around run();
    # continuation key: {"kind":"agent_continue","previous": req.continuation.session["cassette_key"], "message": ...}
    # it stamps session["cassette_key"] = key on every returned AgentResponse (replayed responses carry only that key)
```

The miss error, with the exact text required by the spec:

```python
NO_RECORDING = "no recording for this request — re-record with `wynd test --live`"
class CassetteMissError(Exception):
    def __init__(self, message: str = NO_RECORDING, *, key: str, dir: Path, dump: Path | None) -> None:
        super().__init__(f"{message}\n  key: {key}\n  cassettes: {dir}"
                         + (f"\n  request: {dump}" if dump else ""))
```

On a miss, the normalised request is written to `<workspace>/.wynd/cassettes/misses/<key[:32]>.request.json`. The user can then diff it against the committed entries. Runtime-core's `run_step` re-raises `CassetteMissError` when a step fails with cause `cassette_miss` (§15 R3). Pytest then shows the exact text instead of `exit == "error"`.

### 11.5 Promotion — `cassettes/__init__.py`

```python
def promote(workspace_root: Path, dest: Path, *, package: str | None = None) -> list[Path]:
    """C-R5. Replace dest/*.json with every cassette entry found under workspace_root (recursively, files with
    "wynd_cassette": 1), filtered to entry["package"] == package when given. Returns the written paths.
    Used by the compile job and `wynd test --live` after a passing record run; never touches anything else in dest."""
```

- The size warning (6.5, default 5 MB) is the compile job's job: sum the returned file sizes.
- git-lfs tracking of `cassettes/` is set in `.gitattributes` (docs area): `**/cassettes/*.json filter=lfs diff=lfs merge=lfs -text`.

---

## 12. Context entries and summaries (semantics)

Runtime-core implements these (`executor/context.py`, `summary.py`); this section fixes the meaning.

Grammar, validated by `check_agentic_class` and by the process validator (unknown `steps.<n>` → error):

```python
CONTEXT_ENTRY_RE = re.compile(
    r"^(process\.(goal|inputs)|previous\.(summary|outputs)|steps\.[A-Za-z_][A-Za-z0-9_]*\.(outputs|summary)|full_trace)$")
```

| entry | value (JSON) | when unavailable |
|---|---|---|
| `process.goal` | the process `goal` string (the child's own goal inside a `ProcessStep`) | `null` |
| `process.inputs` | validated process inputs of this process instance | — |
| `previous.summary` | `{step, exit, key_outputs, note}` of the immediately preceding completed step run in this instance | `null` (entry step, first run) |
| `previous.outputs` | that run's outputs (without `exit`) | `null` |
| `steps.<n>.outputs` | outputs of the **latest completed run** of node `<n>` in this instance | `null` (not run yet) |
| `steps.<n>.summary` | its summary | `null` |
| `full_trace` | `{"process": {name, goal, inputs}, "steps": [{step, run, exit, inputs, outputs, summary} …]}` in execution order for this instance | — |

The assembled context is a dict keyed by the declared strings. It is rendered into the prompt under `# Context` as sorted JSON (§6.3), and it is part of the cassette key. That makes process-level cassettes more brittle than step-level ones (7).

**Summary** (3.6 stage 5): `{step, exit, key_outputs, note}`. It is derived deterministically by runtime-core's `summarise`; each output field is projected with a 200-character cap and container markers. This area supplies `note`:
- for claude-code: the last non-empty assistant text block before `StructuredOutput`;
- for ModelProvider: the last non-empty text of a response that carried no structured output;
- otherwise `""`. It is truncated to 500 characters.

A model is never called to produce a summary.

---

## 13. Supervisor and run API — `wynd/runtime/supervisor/` (public contract, M2)

### 13.1 Console script

```
wynd-supervisor serve     [--plan PATH] [--host H] [--port P]     # the image ENTRYPOINT
wynd-supervisor env-check [--manifest PATH]                       # init container / startup probe (4.1)
wynd-supervisor schema                                            # print the run API JSON Schema
```

| env | default | meaning |
|---|---|---|
| `WYND_PLAN` | `/opt/wynd/plan.json` (builder, runtime-core §14 item 10) | image `RunPlan` JSON |
| `WYND_ENV_MANIFEST` | `/opt/wynd/process.env.yaml` | used by `env-check` (`wynd.spec.envmanifest.check_env`) |
| `WYND_HOST` / `WYND_PORT` | `0.0.0.0` / `8080` | listen address |
| `WYND_MAX_CONCURRENT_RUNS` | `4` | runs executing at once (they interleave at step granularity) |
| `WYND_MAX_QUEUED_RUNS` | `64` | queued runs beyond concurrency, then 429 |
| `WYND_RUN_RETENTION` | `1000` | finished runs kept in memory for GET/SSE (then 404; the RunRegistry keeps the record) |
| `WYND_RUN_API_TOKEN` | unset | if set, every `/v1/*` request needs `Authorization: Bearer <token>` (health probes are exempt) |
| `WYND_DRAIN_TIMEOUT_S` | `30` | SIGTERM: stop accepting, wait for running runs, stop workers |
| `WYND_MAX_BODY_MB` | `32` | request body cap (files are base64) |

`env-check` exits 0 if the environment satisfies the manifest. Otherwise it prints one line per missing variable (or group) and exits 1.

### 13.2 Boot sequence and concurrency

```
plan = RunPlan.model_validate_json(read(WYND_PLAN))          # mode "image"
stores = stores_from_env()                                   # runtime-core
pool = WorkerPool(plan); pool.start()                        # one warm worker per venv, all step modules pre-imported
executor = Executor(plan, pool, stores)
manager = RunManager(executor, stores, max_concurrent, max_queued, retention)   # ThreadPoolExecutor(max_concurrent)
server = ThreadingHTTPServer((host, port), Handler); ready = True
SIGTERM -> ready = False (readyz 503, POST 503) -> manager.drain(timeout) -> pool.close() -> server.shutdown()
```

- Runs go through `executor.run(inputs, run_id=, cassette_mode="live", on_event=manager.fan_out(run_id))`.
- Workers are warm across runs (4 "warm pools"). Per-run isolation comes from runtime-core: a fresh workspace per run, a fresh step instance per attempt, and env/cwd restored after each run.
- A crashed worker is respawned on the next dispatch (runtime-core §5.5). `readyz` reports `degraded` while any venv's worker is dead, returning 200 so that traffic continues and the next dispatch respawns it.

### 13.3 Routes (all JSON unless SSE; errors are `{"error": {"code", "message", "details"}}`)

| Method and path | Success | Errors |
|---|---|---|
| `GET /healthz` | 200 `{"status":"ok"}` (the HTTP loop is alive) | — |
| `GET /readyz` | 200 `{"status":"ready"\|"degraded","workers":[{"venv","pid","alive"}]}` | 503 `{"status":"starting"\|"draining"}` |
| `GET /v1/info` | 200 `ProcessInfo` | 401 |
| `POST /v1/runs` | 202 `RunCreated` (new) or 200 `RunCreated` (same `run_id` and identical inputs already submitted) | 401; 409 `run_id_conflict`; 413 `too_large`; 422 `invalid_inputs` (`details` = pydantic errors); 429 `queue_full`; 503 `not_ready` |
| `GET /v1/runs?status=&limit=50` | 200 `{"runs":[RunSummary]}`, newest first | 401 |
| `GET /v1/runs/{run_id}?wait=<s≤60>` | 200 `Run` (long-polls until terminal or `wait` elapses) | 401; 404 `not_found` |
| `GET /v1/runs/{run_id}/outputs` | 200 `{"exit","outputs","error"}` | 401; 404; 409 `not_finished` |
| `GET /v1/runs/{run_id}/events?after=<id>` | 200 `text/event-stream` | 401; 404 |

### 13.4 Schemas (`supervisor/schema.py`, pydantic; `wynd-supervisor schema` exports them)

```python
class FileUpload(BaseModel):
    content_base64: str
class RunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    inputs: dict[str, Any]                                  # values may be {"$file": "<name in files>"}
    run_id: str | None = None                               # ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$ (valid_id); idempotency key
    files: dict[str, FileUpload] = {}                       # name: ^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$
    metadata: dict[str, Any] = {}                           # opaque; echoed; recorded in run.start (e.g. {"trigger":"cron","release":"rel_…"})
class Links(BaseModel):
    self: str; events: str; outputs: str
class RunCreated(BaseModel):
    run_id: str; status: Literal["queued", "running", "succeeded", "failed"]; links: Links
class RunStatus = Literal["queued", "running", "succeeded", "failed"]      # failed iff exit == "error"
class UsageTotals(BaseModel):                               # runtime-core Usage totals over the run
    input_tokens: int; output_tokens: int; cache_read_tokens: int; cache_write_tokens: int
    cost_usd: float | None; latency_ms: float; calls: int
class Run(BaseModel):
    api_version: Literal["1"] = "1"
    run_id: str
    process: str                                            # process id
    commit: str | None
    runtime_version: str
    mode: Literal["image", "local"]
    status: RunStatus
    exit: str | None                                        # set when terminal
    outputs: dict[str, Any] | None                          # outputs of that exit (without "exit")
    error: dict[str, Any] | None                            # ProcessError JSON (runtime-core) when exit == "error"
    created_at: datetime; started_at: datetime | None; finished_at: datetime | None
    duration_ms: float | None
    usage: UsageTotals | None
    metadata: dict[str, Any]
    links: Links
class RunSummary(BaseModel):
    run_id: str; status: RunStatus; exit: str | None; created_at: datetime; finished_at: datetime | None
class ProcessInfo(BaseModel):
    api_version: Literal["1"] = "1"
    process: str; name: str; goal: str | None; commit: str | None; runtime_version: str
    inputs_schema: dict[str, Any]                           # JSON Schema of process inputs
    outputs_schema: dict[str, dict[str, Any]]               # per exit
    steps: list[str]
    limits: dict[str, int]                                  # {"max_concurrent_runs","max_queued_runs","max_body_mb"}
class ErrorBody(BaseModel):
    error: dict[str, Any]                                   # {"code": str, "message": str, "details": Any}
```

Examples:

```http
POST /v1/runs
{"inputs": {"pdf_path": {"$file": "inv1.pdf"}}, "files": {"inv1.pdf": {"content_base64": "JVBERi0xLjQK..."}},
 "metadata": {"trigger": "manual"}}

202 Accepted
Location: /v1/runs/run_20260922T215001100_a1b2c3
{"run_id": "run_20260922T215001100_a1b2c3", "status": "queued",
 "links": {"self": "/v1/runs/run_20260922T215001100_a1b2c3",
           "events": "/v1/runs/run_20260922T215001100_a1b2c3/events",
           "outputs": "/v1/runs/run_20260922T215001100_a1b2c3/outputs"}}
```

```json
{"api_version":"1","run_id":"run_20260922T215001100_a1b2c3","process":"process_supplier_invoice","commit":"764ca6e",
 "runtime_version":"0.1.0","mode":"image","status":"succeeded","exit":"done",
 "outputs":{"record":{"invoice_number":"INV-1042","total":1200.5,"currency":"GBP","due_date":"2026-10-01"}},
 "error":null,"created_at":"2026-09-22T21:50:01.100Z","started_at":"2026-09-22T21:50:01.102Z",
 "finished_at":"2026-09-22T21:50:05.310Z","duration_ms":4208.0,
 "usage":{"input_tokens":2120,"output_tokens":120,"cache_read_tokens":0,"cache_write_tokens":0,"cost_usd":0.00272,"latency_ms":3144.0,"calls":1},
 "metadata":{"trigger":"manual"},"links":{"self":"…","events":"…","outputs":"…"}}
```

File handling (`supervisor/files.py`):
- Each uploaded file is written to `$WYND_DATA_DIR/uploads/<run_id>/<name>`.
- Every `{"$file": name}` value, at any depth in `inputs`, is replaced by that absolute path string. An unknown name is a 422.
- The upload directory is deleted when the run ends. If the run ends in the process error handler (workspace kept), it is moved into the kept workspace instead.

### 13.5 SSE stream (`/v1/runs/{id}/events`)

```
: connected
id: 0
event: status
data: {"status":"queued"}

id: 1
event: trace
data: {"v":1,"seq":1,"ts":"2026-09-22T21:50:01.100000Z","run_id":"run_…","type":"run.start",…}

id: 2
event: status
data: {"status":"running"}

…
id: 31
event: trace
data: {"v":1,"seq":30,…,"type":"run.end","exit":"done",…}

id: 32
event: end
data: {"status":"succeeded","exit":"done"}

```

- `id` is the index in the run's event buffer, which interleaves `status` and `trace` events. The trace's own `seq` is inside `data`.
- `?after=<id>`, or the `Last-Event-ID` header, resumes after that index. The whole buffer is replayed first, then live events follow, then `end`, and the server closes the connection.
- A `: ping` comment is sent every 15 s.
- The stream is written with `Content-Type: text/event-stream`, `Cache-Control: no-cache`, and connection-close framing (no `Content-Length`). One handler thread per stream blocks on a per-run `threading.Condition`.
- Every `trace` event's `data` is a runtime-core `TraceEvent` serialised as `model_dump(mode="json", by_alias=True)`.

### 13.6 Client — `supervisor/client.py` (stdlib; used by the controller, CLI and triggers)

```python
class RunApiError(Exception):  status: int; code: str; body: dict
class RunApiClient:
    def __init__(self, base_url: str, *, token: str | None = None, timeout: float = 30.0) -> None: ...
    def health(self) -> bool: ...
    def ready(self) -> dict: ...                          # raises RunApiError(503) when not ready
    def wait_ready(self, timeout: float = 120.0, interval: float = 0.25) -> None: ...
    def info(self) -> ProcessInfo: ...
    def submit(self, inputs: dict, *, run_id: str | None = None, files: dict[str, bytes] | None = None,
               metadata: dict | None = None) -> RunCreated: ...
    def get(self, run_id: str, *, wait: float | None = None) -> Run: ...
    def outputs(self, run_id: str) -> dict: ...
    def events(self, run_id: str, *, after: int | None = None) -> Iterator[tuple[int, str, dict]]: ...  # (id, event, data)
    def run(self, inputs: dict, **kw) -> Run: ...        # submit + follow events until end + get
```

How the clients use it (they are owned by the controller and CLI):
- `wynd serve <image>` runs the container with `-p <port>:8080 --env-file .env`, plus `WYND_HOME` mounted read-only when MCP servers or registry tier overrides are needed.
- `wynd run --image` does `acquire` (reusing a warm container through `GET /readyz`, without touching Docker), then `RunApiClient.run(...)`, printing trace events as they stream.
- Path inputs of local files become `files` uploads (`--input pdf_path=@examples/inv1.pdf`).

---

## 14. Formats owned here (examples)

### 14.1 Registry section `providers` (`$WYND_HOME/providers.json`, runtime-core `FileRegistry`)

```json
{"version": 1, "entries": {
  "claude-code": {"tiers": {"cheap": "haiku", "standard": "sonnet", "strong": "opus"}},
  "anthropic":   {"tiers": {"strong": "claude-opus-5-5"}}
}}
```

```python
class ProviderEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tiers: dict[Literal["cheap", "standard", "strong"], str] = {}   # overlays provider default_tiers
```

`wynd provider add claude-code --tier cheap=haiku` merges into `tiers`. `wynd provider list` shows `provider_tiers(name, registry)`, the effective mapping. An entry is optional: the built-in defaults work out of the box.

### 14.2 Registry section `mcp` (`$WYND_HOME/mcp.json`)

```json
{"version": 1, "entries": {
  "github": {"name": "github", "transport": "http", "url": "https://api.githubcopilot.com/mcp/",
             "headers": {"Authorization": "Bearer ${env:GITHUB_TOKEN}"}, "auth_env": ["GITHUB_TOKEN"],
             "timeout_s": 30, "description": "GitHub MCP"},
  "files":  {"name": "files", "transport": "stdio",
             "command": ["npx", "-y", "@modelcontextprotocol/server-filesystem", "/data"], "env": {}, "auth_env": []}
}}
```

### 14.3 `step.lock.yaml` fields this area reads

Spec and compiler own the model; these are the fields and semantics needed. The shape follows the compiler draft 9.2, with the additions marked NEW.

```yaml
kind: agentic
entrypoint: step:ExtractInvoiceFields
agentic:
  provider: null            # null -> process default (root process.yaml `provider`, else claude-code)
  tier: cheap               # cheap | standard | strong  (never a model id)
  thinking: low             # none | low | medium | high
  builtin_tools: []         # NEW: AgentProvider harness built-ins, e.g. [Read, Grep, Glob]; effects per BUILTIN_EFFECTS
  max_turns: 25             # NEW: optional
retries: {validation: 2, tool: 1}     # agentic defaults (3.5); `limits.retry` on a branch overrides field by field
effects: [network]
tools:
  - {name: web_search, source: builtin, effects: [network], idempotent: true, env: [BRAVE_API_KEY]}
  - {name: lookup_customer, source: method, effects: [network], idempotent: false, env: [CRM_TOKEN]}
mcp:
  - server: github
    allow: [get_issue, list_issues]
    snapshot:
      - name: get_issue
        description: Get a GitHub issue by number.
        input_schema: {type: object, properties: {number: {type: integer}}, required: [number]}
        input_schema_sha256: 5d1e0f4a…      # schema_sha256(name, input_schema)
        idempotent: true                   # NEW: annotations.idempotentHint or readOnlyHint
    env: [GITHUB_TOKEN]
env:
  - {name: BRAVE_API_KEY, description: "Brave Search API key (web_search)", used_by: ["tool:web_search"]}
```

`ExecPolicy.mcp` (runtime-core) is `[{**lock_mcp_block, "server": <registry mcp entry dict>}]`.

### 14.4 Env manifest contributions

The builder assembles these through `provider_info()`, `BUILTINS[].env`, the MCP `auth_env` and the supervisor table below.

```yaml
- {name: CLAUDE_CODE_OAUTH_TOKEN, secret: true, required: false, one_of: claude-code-auth, used_by: [extract, fix],
   description: "Claude Code subscription token (dev key, `claude setup-token`)…"}
- {name: ANTHROPIC_API_KEY, secret: true, required: false, one_of: claude-code-auth, used_by: [extract, fix], description: "…"}
- {name: BRAVE_API_KEY, secret: true, required: true, used_by: ["tool:web_search"], description: "…"}
- {name: GITHUB_TOKEN, secret: true, required: true, used_by: ["mcp:github"], description: "auth for MCP server github"}
- {name: WYND_HOME, secret: false, required: true, used_by: ["mcp:github"],
   description: "user registry dir (mcp.json/providers.json); mount the registry ConfigMap here"}
- {name: WYND_RUN_API_TOKEN, secret: true, required: false, description: "if set, the run API requires this bearer token"}
- {name: WYND_MAX_CONCURRENT_RUNS, required: false, description: "…"}   # and the other §13.1 knobs, required: false
```

`WYND_HOME` is required only when a step uses an MCP server, or when the process needs registry tier overrides.

### 14.5 Fake provider script (`WYND_FAKE_PROVIDER_SCRIPT`, C-R13)

```json
{"responses": [
  {"match": {"input": {"invoice_text": "Dear customer, your order has shipped"}}, "output": {"exit": "not_an_invoice"}},
  {"match": {"input": {}}, "output": {"exit": "done", "invoice_number": "INV-1042", "total": 1200.5,
                                     "currency": "GBP", "due_date": "2026-10-01"}, "note": "scripted"}
]}
```

The first entry whose `match.input` is a subset of `req.input` wins; a miss raises `ProviderError(kind="invalid_request")`. Usage is all zeros and `model_id` is `"fake"`. It goes through the cassette wrapper like any provider, so the record → promote → replay path can be tested offline.

---

## 15. Interfaces consumed / provided, and items to reconcile

### 15.1 Consumed

| From | Name | Needed shape |
|---|---|---|
| runtime-core | `AgentCall`, `AgentResult`, `StepFailure`, `StepDefinitionError`, `StepInterface` (`output_adapter`, `output_json_schema()`), `ExecPolicy`, `CassetteConfig`, `Usage` (`__add__`), `ModelInfo`, `RuntimeHandle` (`run_id`, `workspace`, `trace.emit`, `http`, `logger`), `HttpClient`/`HttpError`, `Registry`, `registry_from_env` | As in 02 §3–§4. `Usage` is reused for per-call usage with `calls=1` |
| runtime-core | `Executor`, `ProcessResult`, `InvalidProcessInputs`, `WorkerPool` (`start`, `close`, worker liveness for `/readyz`), `RunPlan`, `TraceEvent`, `stores_from_env`, `new_id`, `valid_id` | Supervisor. **Needs** `WorkerPool.status() -> list[{"venv","pid","alive"}]` |
| spec | `EnvFragment(deps, system, requires)`, `EnvVarDecl(name, description, secret, required, one_of, used_by)`, `StepLock` agentic fields (§14.3), `wynd.spec.envmanifest.check_env(manifest, env) -> list[str]` | `one_of` group on `EnvVarDecl` is NEW |
| process | the image `RunPlan` JSON at `WYND_PLAN`; `process.env.yaml` at `WYND_ENV_MANIFEST`; the effective-fragment merge in §5.4 | |
| docs area | `.gitattributes` lfs rule for `cassettes/` | |

### 15.2 Provided

| To | Provided |
|---|---|
| runtime-core | `agentic.loop.complete`, `agentic.checks.check_agentic_class`, `describe_agentic`, `providers.DEFAULT_PROVIDER`, `resolve_model`, `CONTEXT_ENTRY_RE`, `CassetteMissError`, `NO_RECORDING` |
| process (validator, builder, venvs) | `provider_info(name) -> ProviderInfo` (KeyError if unknown → W207), `available_providers()`, `BUILTIN_EFFECTS` (claude-code), `BUILTINS`, `McpServerEntry` |
| compiler | `load_provider`, `GenerateRequest`/`AgentRequest` (with `prompt` and `thinking`), `wrap_output_schema` (the compiler's `strict()` can reuse `_clean`), `BUILTINS: list[ToolSpec]` (`input_schema`, not `parameters`), `tool_spec`, `step_tools`, `mcp.list_tools(entry)`, `mcp.snapshot(server, tools)`, `schema_sha256`, `cassettes.promote`, the `fake` provider, `describe_agentic` |
| controller / CLI | `load_provider`, `available_providers`, `provider_tiers`, `ProviderEntry`, `McpServerEntry`, `AgentRequest.builtin_tools` and `on_event`, `Continuation` (chat follow-ups), `RunApiClient`, run API schemas, `wynd-supervisor` |
| kube | `wynd-supervisor serve` / `env-check`, `/healthz`, `/readyz`, the run API (`files` upload) |
| web | run API JSON (§13.4) and SSE framing (§13.5) for TypeScript types (via the controller) |
| M5 optimise | `model.call` events with `tier`, `usage.cost_usd`, `attempt`, `outcome`; `AgentResult.attempts` |

### 15.3 Items for the synthesizer

1. **R1 (runtime-core): `StepFailure` causes.** Add `model` (refusal, invalid request, max_turns) and `cassette_miss`. `run_step` must re-raise `CassetteMissError(message)` when `ErrorOutput.cause == "cassette_miss"`, so the spec's exact text reaches pytest. This supersedes 02's "surfaces as `transport`".
2. **R2 (runtime-core + spec): new policy fields.** `ExecPolicy` gains `builtin_tools: list[str] = []` and `max_turns: int = 25`, taken from `StepLock.agentic`. `RetryPolicy` keeps `{validation, tool, exception}`; transport restarts are a runtime constant (2).
3. **R3 (runtime-core): `run_step` provider.** `run_step`'s default provider should honour `WYND_DEFAULT_PROVIDER` (compiler C-R4), which `wynd test` sets from the root `process.yaml`. Otherwise step-level cassettes recorded under a process whose default is `anthropic` would miss.
4. **Trace event names.** runtime-core uses `model.call`/`tool.call`; the compiler (C-R6) and web (C-RT-TRACE) drafts use `model_call`/`tool_call`. I follow runtime-core. The payload fields are defined in §6.8.
5. **Run API paths.** The controller draft assumed `POST /runs` and `GET /runs/{id}/events`. This contract versions them under `/v1/`, adds `/outputs` and `/v1/info`, and returns 422 for invalid inputs (as in runtime-core C11). The health paths match (`/healthz`, `/readyz`) and so does port 8080.
6. **Lock shape.** runtime-core C4 has `mcp: [{name, allow, tools}]`; the compiler has `mcp: [{server, allow, snapshot, env}]`. I adopted the compiler's shape and added `idempotent` per snapshot tool.
7. **`BUILTINS` attribute name.** The compiler expects `.parameters`; this design uses `.input_schema` (the same name as MCP and the Messages API). One of the two must change.
8. **Cassette directories.** Step-level cassettes live in `<pkg>/cassettes/`. Process-level cassettes live in `<process>/tests/cassettes/<step_package_name>/` (compiler C-P5 uses `<process_dir>/tests/cassettes`). Recordings go to runtime-core's `record_dir`.
9. **`EnvVarDecl.one_of`.** This is needed for the `claude-code` auth pair. The process draft plans to enforce it in image mode only.
10. **Compiler dependency.** The compiler and the controller chat run the `claude-code` provider in *their own* venv, so `wynd-compiler` and `wynd-controller` need `claude-agent-sdk>=0.2.157,<0.3` as a real dependency. The runtime never lists it.

---

## 16. Test plan

All tests live under `packages/runtime/tests/llm/`, run offline, and are deterministic unless marked otherwise.

| File | Owner | Covers |
|---|---|---|
| `test_checks.py` | LLM-L | ellipsis detection (plain, docstring+`...`, `pass`, real body); missing docstring; concrete `run` rejected; duplicate tool names across method/builtin/MCP; bad context entry; `describe_agentic` output |
| `test_prompt.py` | LLM-L | golden `render_prompt` output (context present/absent, unicode, dates); raw mode; `format_validation_errors` lines and limit |
| `test_schema.py` | LLM-L | `wrap_output_schema` on a real pydantic union (oneOf→anyOf, `$defs` hoisted, discriminator dropped, `required` = all including `exit`, a property named `title` kept, unsupported format dropped); single-model Output; `unwrap_output`; `strict_compatible` false for `dict[str, Any]` fields |
| `test_model_loop.py` | LLM-L | `ScriptedModelProvider`: tool call → result → valid output; validation retry **continues** (history contains the first answer and the error message); exhaustion → `output_validation` with attempts=3; `no_output` and `max_tokens` paths (alternation preserved); transport before a tool → restart with fresh messages (sleep patched); transport after a tool → `transport`; refusal → `model`; `max_turns` → `model`; tool raising → `tool`; bad args → is_error result; `model.call` events (outcome, attempt, `messages_new` deltas) |
| `test_agent_loop.py` | LLM-L | `ScriptedAgentProvider`: continuation carries `session` and message; restart only when `tool_called` is false in both the provider and the ToolSet; exhaustion; usage summed; `note` from the response |
| `test_provider_registry.py` | LLM-P | entry points resolve `claude-code`/`anthropic`/`fake` (the installed dist); `register_for_tests`; `provider_tiers` overlay; `resolve_model` errors (unknown provider, missing tier) with exact messages; `provider_info` needs no SDK import (asserts `claude_agent_sdk` is not in `sys.modules`) |
| `test_anthropic.py` | LLM-P | fake `HttpClient`: request body goldens per model family (haiku budget vs adaptive+effort; no `budget_tokens` for 4.6+; `output_config.format` wrapped; tools); non-strict schema → prompt path; response parsing (tool_use, JSON text, fenced JSON, max_tokens, refusal); thinking blocks kept verbatim in `message`; status → kind table; cost for known and unknown models; missing key |
| `test_claude_code.py` | LLM-P | real SDK types, injected `query_fn` (async generator of `SystemMessage`/`AssistantMessage`/`UserMessage`/`ResultMessage`): options built (`tools=[]`, `allowed_tools`, strict, `setting_sources=[]`, `dontAsk`, wrapped schema, effort map, cwd, `resume` + store on continuation, preset system prompt when `builtin_tools` is set); the bridge invokes ToolSet in a thread; a tool failure aborts; usage delta with a cumulative `cum`; transcript and `on_event` shapes (prefix stripped); `_classify` for 401 / "Not logged in" / 400 / 529 / `error_max_turns`; `CLINotFoundError` → unavailable; session cleanup calls `delete_session` and rmdir (tmp `CLAUDE_CONFIG_DIR`); missing SDK → unavailable |
| `test_fake_provider.py` | LLM-P | script matching, miss error, zero usage |
| `test_tool_decorator.py` | LLM-T | bare and parameterised `@tool`; closures; schema from signature (defaults, Annotated descriptions, extra=forbid); return serialisation; a missing docstring errors; the method stays directly callable |
| `test_toolset.py` | LLM-T | idempotent+transient retried exactly once; non-idempotent never retried; non-transient not retried; `ToolInputError` → is_error; unknown tool; `current_runtime` bound in builtins; replay of network/MCP tools from the cassette (and a miss); record writes tool entries; `tool.call` events; `trace_harness_calls` |
| `test_builtins.py` | LLM-T | `http_get` against a local `ThreadingHTTPServer` (connection reset then success → one retry; 404/503 returned as results; truncation); `web_search` with a fake HttpClient and Brave JSON; workspace path escape → is_error; `shell` truncation, timeout and no-shell; `now` format matches `DATETIME_RE` |
| `test_mcp_client.py` | LLM-T | `fake_server` over HTTP JSON, HTTP SSE and stdio: initialize, pagination, call, isError, 401 → `McpAuthError`, a dying stdio server |
| `test_mcp_snapshot.py` | LLM-T | `snapshot` filters by `allow` and errors on missing tools; `schema_sha256` is stable under key order; `connect_mcp` mismatch message (changed schema, missing tool) is exact; env-ref resolution and missing-var error; the model sees snapshot descriptions |
| `test_cassette_key.py` | LLM-C | key stability under dict order; the normaliser replaces workspace, realpath, run id and datetimes but not dates; `model_id` is excluded; the provider is included; continuation chaining |
| `test_cassettes.py` | LLM-C | record → replay round trip for generate/agent/tool; replay never builds the inner provider (its factory raises if called); miss → exact `NO_RECORDING` text plus a dumped request; `promote` replaces only `*.json`, filters by package, returns paths |
| `test_loop_cassettes_e2e.py` | LLM-C | `run_step` (runtime-core) with the `fake` provider: record in a tmp workspace → promote → replay passes → edit the Input → replay misses with the exact text |
| `test_supervisor_api.py` | LLM-S | a real `ThreadingHTTPServer` on port 0 with a stub `Executor` (scripted events and results): 202/200 idempotency, 409 conflict, 422 invalid inputs, 413, 429 queue full, 401 token, `?wait` long-poll, `/outputs` 409→200, the SSE framing (ids, `after`, `Last-Event-ID`, end + close, ping with a patched interval), `/readyz` states (starting, ready, degraded, draining), drain on SIGTERM, `files` + `$file` substitution and cleanup |
| `test_supervisor_integration.py` | LLM-S | the real `Executor` + `WorkerPool` (python = `sys.executable`) on a tiny two-step deterministic plan; submit via `RunApiClient.run`; the trace matches the local executor run except for `mode` |
| `test_run_api_contract.py` | LLM-S | `export_json_schema()` equals the committed `run_api.schema.json` (contract drift fails CI) |
| `test_run_api_client.py` | LLM-S | client vs the stub server: errors → `RunApiError`, SSE parsing across chunk boundaries, `wait_ready` |

Live tests are `@pytest.mark.live` and skipped unless `WYND_LIVE=1`. They port spikes 1, 3, 4 and 7 to haiku, each call under $0.01:
- `test_claude_code_live_structured_union` (discriminated union with `$defs` and a date; validates).
- `test_claude_code_live_tool_and_continuation` (in-process tool called once; a forced validation failure continues on the same session with no tool re-call; no files left under the projects dir for the cwd).
- `test_claude_code_live_mcp_proxy` (fake MCP server over HTTP with Bearer auth via the ToolSet proxy).
- `test_claude_code_live_auth_error` (`CLAUDE_CODE_OAUTH_TOKEN=invalid` → `auth`).
- `test_anthropic_live_tools_and_format` (skipped without `ANTHROPIC_API_KEY`).

The docker-marked integration test (supervisor inside the built image) belongs to the builder and kube areas; it uses `RunApiClient`.

---

## 17. Dogfood reference (hand-written agentic step, M1)

`examples/invoices/processes/process_supplier_invoice/steps/extract_invoice_fields/step.py` (owned by the sample area; shown here as the contract):

```python
from datetime import date
from typing import Literal
from pydantic import BaseModel
from wynd.runtime import AgenticStep

class ExtractInvoiceFields(AgenticStep):
    """Given the text of a supplier invoice, extract the invoice number, total, currency and due date.
    If the text is not a supplier invoice, take the not_an_invoice exit."""

    class Input(BaseModel):
        invoice_text: str

    class Done(BaseModel):
        """The text is an invoice; fields extracted."""
        exit: Literal["done"] = "done"
        invoice_number: str
        total: float
        currency: str
        due_date: date

    class NotAnInvoice(BaseModel):
        """The text is not an invoice."""
        exit: Literal["not_an_invoice"] = "not_an_invoice"

    Output = Done | NotAnInvoice
    context = ["process.goal"]

    def run(self, input: Input) -> Output: ...   # completed by the loop
```

`step.lock.yaml` (agentic part): `agentic: {provider: null, tier: cheap, thinking: low, builtin_tools: [], max_turns: 25}`, `retries: {validation: 2, tool: 1}`. `process.yaml` sets `provider: claude-code`. The generated test replays `cassettes/<key>.json`, recorded live with haiku (spike 7 produced exactly this output for the §6.1 example text).

---

## 18. Risks

- **claude-agent-sdk churn.** It is 0.2.x, and the internal options it maps to CLI flags have moved before. The pin is `>=0.2.157,<0.3`. The offline tests use the real SDK types, so a breaking change fails CI rather than production. The stateless resume relies on the documented `session_store` + `resume` materialisation.
- **Image size.** The claude-code wheel adds about 100 MB compressed (a 234 MB binary) to every image that has an agentic step using it. That is accepted per the lead's decision (the claude-code default). Deterministic-only processes pay nothing.
- **AgentProvider latency.** 3–5 s per haiku call (0.6 s of it startup) conflicts with `latency: fast`. The W206 warning covers it, and `anthropic` (a ModelProvider) is the low-latency option.
- **Subscription limits.** The dev key shares the user's Claude subscription rate limits (5-hour and 7-day windows). A `RateLimitEvent` from the SDK is logged at WARNING. A `rejected` status surfaces as a transport `ResultError`, which is restarted at most twice and then fails the step.
- **Messages API `output_config.format` + tools** has not been verified live here (no API key). The fallback path exists and there is a live test.
- **Regex timestamp normalisation** could merge two recordings that differ only in machine timestamps (Interpretation 8).
