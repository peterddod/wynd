// Web DTOs: the controller HTTP contract (PLAN §3.21 amendments 1-15; `$DRAFTS/07 §12.2` as amended).
// Each type mirrors its pydantic model in `wynd.controller.models` / `wynd.controller.api.models_web`, field for
// field and under the same name; fields marked "controller addition" extend the contract and may be absent.
// Timestamps are ISO-8601 strings. Frozen after sub-wave 9a: a needed change goes to the lead.

export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type JsonObject = { [k: string]: Json };
export type Loc = (string | number)[];

export type StatusFlag = "design" | "compiled" | "built" | "released";
export type StepPhase = "design" | "compiled" | "handwritten" | "missing";
export type InterfaceSource = "declared" | "inferred" | "compiled" | "process";
export type TraceStepKind = "deterministic" | "agentic" | "shell" | "process";
export type TriggerKind = "manual" | "schedule" | "webhook";
export type ReleaseState = "starting" | "serving" | "stopped" | "error";
export type RunTrigger = TriggerKind | "api";
export type Tier = "cheap" | "standard" | "strong";
export type Severity = "error" | "warning" | "info";

/** Error envelope of every non-2xx response (`$DRAFTS/07 §12.0`). */
export interface ErrorBody {
  error: { code: string; message: string; details: Json; hint?: string | null };
}

// --- interfaces (JSON Schemas as pydantic emits them; path-typed fields carry `format: "path"`) --------------------

export interface JsonSchema {
  type?: string | string[];
  format?: string;
  const?: Json;
  enum?: Json[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  anyOf?: JsonSchema[];
  oneOf?: JsonSchema[];
  allOf?: JsonSchema[];
  $ref?: string;
  $defs?: Record<string, JsonSchema>;
  additionalProperties?: boolean | JsonSchema;
  description?: string;
  title?: string;
  default?: Json;
}

export interface ExitSchema {
  name: string;
  schema: JsonSchema | null;
}

export interface Interface {
  inputs: JsonSchema | null;             // object schema
  exits: ExitSchema[];                   // declared exits; the implicit `error` is excluded
  source: InterfaceSource | null;        // null: not declared (the compiler will infer it)
}

export interface InterfaceExample {
  inputs: Record<string, Json>;
  outputs: Json;
  exit: string;
}

export interface ProcessInterface {
  commit: string | null;
  inputs: JsonSchema | null;
  outputs: Record<string, JsonSchema | null>;   // by exit
  examples: InterfaceExample[];
}

// --- meta -------------------------------------------------------------------------------------------------------------

export interface WorkspaceInfo {
  root: string;
  branch: string | null;                 // null when HEAD is detached
  head: string | null;                   // null before the first commit
  process_roots: string[];
  step_roots: Record<string, string>;
}

export interface LlmStatus {
  provider: string;
  ready: boolean;
  message: string;                       // check_provider(provider)[1]
}

export interface Meta {
  version: string;
  workspace: WorkspaceInfo;
  proto_types: string[];
  bases: string[];
  latency: string[];                     // ["fast", "normal"]
  edge_kinds: string[];                  // ["deterministic", "agentic"]
  expr_functions: string[];
  limit_fields: string[];                // ["max_traversals", "timeout", "retries"]
  default_max_traversals: number;
  default_provider: string;
  llm: LlmStatus;
}

// --- validation and env -----------------------------------------------------------------------------------------------

/** `Diagnostic.to_json()` subset (amendment 1). `loc` is a path into the normalised document of `file`. */
export interface Issue {
  severity: Severity;
  code: string;
  message: string;
  file: string | null;
  loc: Loc;
  span: [number, number] | null;         // 0-based [start, end) inside an expression
}

export interface ValidationReportDTO {
  ok: boolean;
  issues: Issue[];
}

export interface EnvVar {
  name: string;
  description: string;
  secret: boolean;
  required: boolean;
  default: string | null;
  one_of: string | null;
  used_by: string[];
}

export interface EnvCheckDTO {
  ok: boolean;
  missing: string[];                     // E-ENV-MISSING / E-ENV-ONE-OF names
  unbound: string[];                     // W-ENV-ONE-OF names
  issues: Issue[];
}

// --- status and listing -----------------------------------------------------------------------------------------------

export interface HeadInfo {
  commit: string;
  short: string;
  at: string;
  subject: string;
}

export interface ReleaseRef {
  id: string;
  commit: string;
  short: string;
  behind: number;
  trigger: TriggerKind;
  state: ReleaseState;
}

export interface ProcessStatus {
  head: HeadInfo | null;                 // null while the closure has no commit
  design: boolean;
  compiled: boolean;
  built: boolean;
  released: boolean;
  tests: "passed" | "failed" | "unknown";
  design_steps: string[];
  releases: ReleaseRef[];
  dirty?: string[];                      // controller addition: dirty closure paths (never changes a flag)
}

export interface Match {
  field: "id" | "name" | "goal" | "instruction";
  step: string | null;
  snippet: string;
}

export interface ProcessSummary {
  id: string;
  name: string;
  goal: string | null;
  path: string;                          // workspace-relative process dir
  status: ProcessStatus | null;          // null when the process fails to load (see `error`)
  matches: Match[];
  error?: string | null;                 // controller addition: per-process load error
}

// --- steps ------------------------------------------------------------------------------------------------------------

export interface StepLockInfo {
  provider: string | null;
  tier: string | null;
  thinking: string | null;
  effects: string[];
  env: string[];
}

export interface StepInfo {
  name: string;
  use: string;
  ref_kind: "local" | "root" | "process";
  resolved: boolean;
  proto_path: string | null;             // workspace-relative
  source_path: string | null;            // compiled package dir
  kind: TraceStepKind | null;            // null: not compiled
  phase: StepPhase;
  lock: StepLockInfo | null;
  used_by: string[];
  instruction: string | null;
  interface: Interface;
}

export interface StepCatalogEntry {
  use: string;
  root: string;
  path: string;
  kind: TraceStepKind | null;
  phase: StepPhase;
  instruction: string | null;
  used_by: string[];
}

// --- design -----------------------------------------------------------------------------------------------------------

export interface ParseError {
  message: string;
  line: number | null;
  column: number | null;
}

export interface FileDoc {
  path: string;
  revision: string | null;               // "sha256:<hex>"; null: the file does not exist
  doc: Json;
  yaml: string;
}

export interface ProcessFileDoc extends FileDoc {
  parse_error: ParseError | null;        // doc is null when set
}

export interface DesignConventions {
  local_use: string;                     // "{name}" placeholder
  local_proto_path: string;
}

export interface AvailableLocalStep {
  use: string;
  proto_path: string | null;
  source_path: string | null;
  phase: StepPhase;
}

export interface LockOwner {
  chat_id: string;
  turn_id: string;
}

export interface DesignDoc {
  process_id: string;
  head: string;
  process_file: ProcessFileDoc;
  protos: Record<string, FileDoc>;       // by workspace-relative path
  steps: Record<string, StepInfo>;       // by step key
  interface: ProcessInterface;           // of the working tree
  available_local: AvailableLocalStep[];
  conventions: DesignConventions;
  validation: ValidationReportDTO;
  locked_by: LockOwner | null;
}

export type CommitReason =
  | "blur" | "hidden" | "process_switch" | "before_job" | "before_chat" | "before_integrate" | "unload";

export interface SaveWrite {
  path: string;
  base_revision: string | null;          // null: the file must not exist
  doc?: Json;
  delete?: boolean;
}

export interface SaveCommit {
  reason: CommitReason;
  summary: string;
}

export interface SaveRequest {
  writes: SaveWrite[];
  commit: SaveCommit | null;
}

export interface SavedFile {
  path: string;
  revision: string | null;
  yaml: string | null;
}

export interface SavedCommit {
  sha: string;
  message: string;
}

export interface SaveResult {
  files: SavedFile[];
  validation: ValidationReportDTO;
  steps: Record<string, StepInfo>;
  interface: ProcessInterface;
  commit: SavedCommit | null;
  head: string;
}

export interface CreateProcessRequest {
  id: string;
  goal?: string | null;
  root?: string | null;
}

// --- expressions ------------------------------------------------------------------------------------------------------

export interface ExprCheckRequest {
  process_id: string;
  process: Json;                         // the in-flight process document
  protos?: Record<string, Json>;
  loc: Loc;
  expr: string;
  scope: boolean;
}

export interface ExprIssue {
  message: string;
  start: number;
  end: number;
}

export interface ExprCheck {
  ok: boolean;
  errors: ExprIssue[];
  warnings: ExprIssue[];
  scope: string[] | null;                // refs valid at `loc`, when requested
}

// --- usage ------------------------------------------------------------------------------------------------------------

/** PLAN §3.13 `Usage`. */
export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_write_tokens: number;
  cost_usd: number | null;               // null: unknown
  latency_ms: number;                    // sum of per-call latencies
  calls: number;
}

/** PLAN §3.18 `JobUsage`: totals over the job plus a split by "<provider>/<tier>". */
export interface JobUsage extends Usage {
  by: Record<string, Usage>;
}

// --- registries -------------------------------------------------------------------------------------------------------

export interface ProviderInfoDTO {
  name: string;
  kind: "model" | "agent";
  tiers: Record<string, string>;
  ready: boolean;
  message: string;
}

export interface ProviderAddRequest {
  name: string;
  tiers?: Partial<Record<Tier, string>>;
}

export interface McpServerEntry {
  name: string;
  transport: "http" | "stdio";
  url: string | null;
  headers: Record<string, string>;       // values may contain ${env:NAME}
  command: string[] | null;
  env: Record<string, string>;
  auth_env: string[];
  timeout_s: number;
  description: string;
  oauth: JsonObject | null;
}

export interface ImageRegistryEntry {
  name: string;
  url: string;
  username_env: string | null;
  password_env: string | null;
  insecure: boolean;
  default: boolean;
}

export interface RemoveResult {
  removed: boolean;
  referenced_by: string[];
}

export interface OAuthStartRequest {
  return_to?: string | null;
}

export interface OAuthStart {
  authorize_url: string;
}

// --- compile session (the compiler SessionData projection, amendment 9) ---------------------------------------------

export interface PassCounts {
  passed: number;
  failed: number;
  total: number;
}

export interface CompileSplit {
  deterministic: string;
  agentic: string;
}

export interface CompileDecision {
  kind: TraceStepKind;
  tier: string | null;
  thinking: string | null;
  reason: string;
  split: CompileSplit | null;            // the two-step fallback
}

export interface CompileStep {
  step: string;
  use: string;
  phase: "pending" | "generating" | "testing" | "revising" | "done" | "skipped" | "failed";
  decision: CompileDecision | null;
  tests: PassCounts | null;
  attempts: number;
  skipped_reason: string | null;
}

export interface ProposedExample {
  inputs: Record<string, Json>;
  outputs: Json;
  exit: string;
}

export interface ClarificationAnswer {
  text: string;
}

export interface ProposalAnswer {
  decision: "confirm" | "correct" | "reject";
  example: Json;
  text: string | null;
}

export interface ClarificationQuestion {
  id: string;
  kind: "clarification";
  step: string | null;
  text: string;
  status: "pending" | "answered";
  answer: ClarificationAnswer | null;
  asked_at: string;
  detail?: string | null;                // controller addition
  default?: string | null;               // controller addition
}

export interface ExampleProposalQuestion {
  id: string;
  kind: "example_proposal";
  step: string;
  text: string;
  proposed: ProposedExample;
  status: "pending" | "answered";
  answer: ProposalAnswer | null;
  asked_at: string;
  detail?: string | null;                // controller addition
  default?: string | null;               // controller addition
}

export type Question = ClarificationQuestion | ExampleProposalQuestion;

export interface CompileEvent {
  at: string;
  type: string;
  step: string | null;
  text: string;
}

export interface CompileSession {
  state: "compiling" | "awaiting_input" | "done" | "failed";
  steps: CompileStep[];
  questions: Question[];
  inferred_schemas: Record<string, Interface>;
  events: CompileEvent[];
}

export interface TextAnswer {
  question_id: string;
  text: string;
}

export interface DecisionAnswer {
  question_id: string;
  decision: "confirm" | "correct" | "reject";
  example?: Json;
  text?: string | null;
}

/** Answer text: confirm -> "accept", reject -> "reject", correct+example -> the example JSON, text -> text. */
export type AnswerRequest = TextAnswer | DecisionAnswer;

// --- jobs -------------------------------------------------------------------------------------------------------------

export type JobKind = "compile" | "test_live" | "build" | "bake" | "optimise";
export type JobStatus = "queued" | "running" | "awaiting_input" | "succeeded" | "failed" | "cancelled";

export interface BuildTests extends PassCounts {
  source: "ran" | "registry";            // "registry": reused result recorded for this commit
}

export interface BuildResult {
  commit: string;
  image: string;
  build_dir: string;                     // JobRecord.artefacts.build_dir (amendment 12)
  tests: BuildTests;
  image_digest?: string | null;          // controller addition
  pushed?: boolean;                      // controller addition
}

/** `IntegrationResult` JSON (amendment 12); `pr_url` is always null in v1 (`reason` carries the hint). */
export interface Integration {
  mode: "fast_forward" | "rebased" | "pr_branch" | "noop";
  branch: string;
  target: string;
  head: string | null;
  skipped_commits: number;
  conflicts: string[];
  reason: string | null;
  at: string;
  pr_url: string | null;
}

export interface JobError {
  message: string;
  detail: string | null;
}

export interface Job {
  id: string;
  kind: JobKind;                         // JobRecord.job_kind
  process_id: string;                    // JobRecord.process
  ref: string;
  status: JobStatus;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  branch: string | null;                 // JobRecord.result_branch
  result_commit: string | null;
  session: CompileSession | null;
  build: BuildResult | null;
  integration: Integration | null;
  error: JobError | null;
  usage: JobUsage;
  chat_id: string | null;
  report?: JsonObject | null;            // controller addition
  artefacts?: JsonObject;                // controller addition
}

export interface LogChunk {
  text: string;
  offset: number;                        // next byte offset
  done: boolean;
}

// --- chats ------------------------------------------------------------------------------------------------------------

export interface ChatJob {
  id: string;
  kind: JobKind;
  process_id: string;
  status: JobStatus;
  pending_questions: number;
}

export interface ChatSummary {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  running_turn: string | null;
  job: ChatJob | null;
  usage_totals?: Usage | null;           // controller addition
}

export interface UserItem {
  id: string;
  seq: number;
  type: "user";
  created_at: string;
  text: string;
  acting_on: string | null;
}

export interface AssistantItem {
  id: string;
  seq: number;
  type: "assistant";
  created_at: string;
  turn_id: string;
  text: string;
  status: "streaming" | "done" | "error" | "cancelled";
  error: string | null;
  usage?: Usage | null;                  // controller addition
}

export interface ToolItem {
  id: string;
  seq: number;
  type: "tool";
  created_at: string;
  turn_id: string;
  tool: string;
  args: Json;
  write: boolean;
  acting_on: string | null;
  status: "running" | "ok" | "error";
  summary: string | null;
  duration_ms: number | null;
}

export interface CommitItem {
  id: string;
  seq: number;
  type: "commit";
  created_at: string;
  turn_id: string;
  process_id: string;
  sha: string;
  message: string;
}

export interface JobItem {
  id: string;
  seq: number;
  type: "job";
  created_at: string;
  job_id: string;
  kind: JobKind;
  process_id: string;
}

export interface NoticeItem {
  id: string;
  seq: number;
  type: "notice";
  created_at: string;
  level: "info" | "warning" | "error";
  text: string;
}

export type ChatItem = UserItem | AssistantItem | ToolItem | CommitItem | JobItem | NoticeItem;

export interface ChatSnapshot {
  chat: ChatSummary;
  items: ChatItem[];
  cursor: number;                        // last chat event id this snapshot reflects
}

export interface SendMessageRequest {
  text: string;
  acting_on: string | null;              // the open process, sent with every message
  client_id: string;
}

export interface SendMessageResult {
  turn_id: string;
  item: ChatItem;
}

export interface CreateChatRequest {
  title?: string | null;
}

export interface RenameChatRequest {
  title: string;
}

/** Response of the compile/build buttons: 201 `{job, chat}`. */
export interface JobWithChat {
  job: Job;
  chat: ChatSummary;
}

export type ChatTurnStatus = "running" | "done" | "error" | "cancelled";

/** Chat SSE payloads (`$DRAFTS/07 §12.4`), by event name. */
export interface ChatItemEvent {
  item: ChatItem;
}
export interface ChatDeltaEvent {
  item_id: string;
  text: string;
}
export interface ChatTurnEvent {
  turn_id: string;
  status: ChatTurnStatus;
  acting_on: string | null;
  edited: string[];                      // process ids this turn edited (and committed)
  error: string | null;
}
export type ChatResetEvent = Record<string, never>;

// --- records (PLAN §3.8) ----------------------------------------------------------------------------------------------

export type StepErrorCause =
  | "input_validation" | "exception" | "hook" | "output_validation" | "tool" | "transport" | "model" | "config"
  | "cassette_miss" | "shell_exit" | "child_process" | "worker_crash" | "import";

export type ProcessErrorCause =
  | "step_error" | "unrouted_exit" | "ignored_exit" | "no_branch_matched" | "max_traversals" | "timeout"
  | "expression_error" | "invalid_process_outputs" | "edge_check" | "internal";

export interface Summary {
  step: string;
  exit: string;
  key_outputs: Record<string, Json>;
  note: string;
}

export interface StepError {
  exit: "error";
  cause: StepErrorCause;
  message: string;
  type: string | null;
  traceback: string | null;
  inputs: Record<string, Json>;
  partial_outputs: Record<string, Json> | null;
  attempts: number;
  child: ProcessError | null;            // cause "child_process"
}

export interface TracePointer {
  run_id: string;
  uri: string;
  seq: number;                           // seq of the process.error event
}

export interface ProcessError {
  run_id: string;
  process: string;
  step: string | null;                   // full step path
  cause: ProcessErrorCause;
  message: string;
  edge: string | null;                   // edge key, or branch key when branch-specific
  inputs: Record<string, Json>;
  partial_outputs: Record<string, Json> | null;
  step_error: StepError | null;
  handler_error: StepError | null;
  detail: Record<string, Json>;
  trace: TracePointer | null;
  workspace: string | null;              // URI when the workspace was kept
}

// --- runs -------------------------------------------------------------------------------------------------------------

export interface LocalTarget {
  kind: "local";
}
export interface ImageTarget {
  kind: "image";
  commit: string | null;                 // null: the build at the process HEAD
}
export interface ReleaseTarget {
  kind: "release";
  release_id: string;
}
export type RunTarget = LocalTarget | ImageTarget | ReleaseTarget;

export interface CreateRunRequest {
  process_id: string;
  target: LocalTarget | ImageTarget;
  inputs: Record<string, Json>;
}

/** The `RunRecord` projection (amendment 11). */
export interface Run {
  id: string;
  process_id: string;
  commit: string | null;
  mode: "local" | "image";
  target: RunTarget;
  release_id: string | null;
  trigger: RunTrigger;
  status: "queued" | "running" | "succeeded" | "failed";
  inputs: Record<string, Json>;
  exit: string | null;
  outputs: Record<string, Json> | null;
  error: ProcessError | null;
  started_at: string | null;
  finished_at: string | null;
  duration_ms: number | null;
  usage: Usage | null;
}

export interface UploadResult {
  path: string;                          // usable as a `path` input for every run target
}

/** Run SSE `end` payload; `error` is set when the run never produced an event. */
export interface RunEndEvent {
  error?: string;
}

// --- trace events (PLAN §3.13, passed through unchanged; amendment 4) ------------------------------------------------

export interface TraceEvent {
  v: number;
  seq: number;
  ts: string;
  run_id: string;
  type: string;
  [k: string]: Json;
}

export interface StepTimings {
  started_at: string;
  ended_at: string;
  duration_ms: number;
  worker_ms: number | null;
  pre_ms: number | null;
  run_ms: number | null;
  post_ms: number | null;
}

export interface ModelRef {
  provider: string;
  model_id: string;
  tier: string | null;
  thinking: string | null;
}

/** Fields of each known event type, beyond the envelope. */
export interface TraceFields {
  "run.start": {
    process: string; mode: "local" | "image"; inputs: Record<string, Json>; commit: string | null;
    runtime_version: string; workspace: string; cassette: "live" | "record" | "replay"; metadata: Record<string, Json>;
  };
  "step.start": {
    step: string; name: string; process: string; span: number; parent: number | null; id: string;
    kind: TraceStepKind; run: number; role: "node" | "on_error" | "finally"; via: string | null;
    inputs: Record<string, Json>; venv: string | null;
  };
  "step.end": {
    step: string; span: number; parent: number | null; run: number; kind: TraceStepKind; exit: string | null;
    timed_out: boolean; outputs: Record<string, Json> | null; summary: Summary | null; attempts: number;
    validation_failures: number; timings: StepTimings; usage: Usage | null; model: ModelRef | null; replayed: boolean;
  };
  "step.log": {
    step: string; span: number; parent: number | null; level: string; stream: "logger" | "output"; message: string;
  };
  "step.event": { step: string; span: number; parent: number | null; name: string; data: Json };
  "edge.taken": {
    process: string; parent: number | null; from: string; branch: number; name: string | null; to: string;
    kind: string; with: Record<string, Json>; taken: number;
    verdicts?: { branch: number; take: boolean | null; reason: string }[];   // agentic edges only
  };
  "edge.check": {
    process: string; parent: number | null; edge: string; branch: number; branch_key: string; target: string;
    take: boolean | null; reason: string | null; attempts: number; validation_failures: number;
    error_cause: string | null; provider: string; tier: string; model_id: string; usage: Usage | null;
    duration_ms: number; replayed: boolean;
  };
  "model.call": {
    step: string; span: number; parent: number | null; provider: string; kind: "model" | "agent"; tier: string | null;
    thinking: string | null; model_id: string; n: number; attempt: number; restart: number; reason: string;
    outcome: "tool_calls" | "valid" | "invalid" | "no_output" | "truncated" | "refusal" | "error";
    errors: Json; request_hash: string; request: Json; messages_new: Json; response: Json; usage: Usage | null;
    startup_ms: number | null; cost_basis: "api" | "list" | null; cassette: "live" | "record" | "replay";
  };
  "tool.call": {
    step: string; span: number; parent: number | null; tool: string; source: string; effects: string[];
    idempotent: boolean; args: Json; result: Json; ok: boolean; is_error: boolean; error: string | null;
    tries: number; latency_ms: number; replayed: boolean;
  };
  "process.error": { process: string; parent: number | null; error: ProcessError; handler: string };
  "worker.start": {
    step: string; span: number; parent: number | null; venv: string; pid: number; startup_ms: number;
  };
  "run.end": {
    exit: string | null; outputs: Record<string, Json> | null; error: ProcessError | null;
    status: "succeeded" | "failed"; duration_ms: number; workspace_kept: boolean; workspace: string | null;
    usage: Usage | null; finally_errors: Json[];
  };
}

export type TraceType = keyof TraceFields;
export type TraceEventOf<T extends TraceType> = TraceEvent & { type: T } & TraceFields[T];

/** Narrows an event to its type's fields: `if (isTrace(ev, "step.end")) ev.exit`. Unknown types stay envelopes. */
export function isTrace<T extends TraceType>(ev: TraceEvent, type: T): ev is TraceEventOf<T> {
  return ev.type === type;
}

// --- builds and releases ----------------------------------------------------------------------------------------------

export interface Build {
  process_id: string;
  commit: string;
  short: string;
  image: string;
  built_at: string;
  job_id: string | null;
  at_head: boolean;
  behind: number;                        // closure commits between this build and the process HEAD
  env: EnvVar[];
}

export interface ManualTrigger {
  kind: "manual";
}
export interface ScheduleTrigger {
  kind: "schedule";
  cron: string;
  timezone: string | null;               // null: UTC
  inputs: Record<string, Json>;
}
export interface WebhookTrigger {
  kind: "webhook";
  secret_env: string | null;
}
export type Trigger = ManualTrigger | ScheduleTrigger | WebhookTrigger;

export interface ValueBinding {
  value: string;
}
export interface FromEnvBinding {
  from_env: string;
}
export type EnvBinding = ValueBinding | FromEnvBinding;   // secrets are always from_env

export interface Release {
  id: string;
  process_id: string;
  commit: string;
  short: string;
  image: string;
  behind: number;
  trigger: Trigger;
  env: Record<string, EnvBinding>;
  enabled: boolean;
  state: ReleaseState;
  state_detail: string | null;
  created_at: string;
  next_fire_at: string | null;
  webhook_url: string | null;
}

export interface CreateReleaseRequest {
  process_id: string;
  commit: string;
  trigger: Trigger;
  env: Record<string, EnvBinding>;
  enabled: boolean;
}

export interface ReleasePatch {
  trigger?: Trigger | null;
  env?: Record<string, EnvBinding> | null;
  enabled?: boolean | null;
}

/** `POST /api/releases/{id}/trigger` body (amendment 5). */
export interface TriggerRequest {
  inputs?: Record<string, Json> | null;
  source?: "manual" | "schedule";
}

/** `GET /api/processes/{pid}/files?path=` (amendment 14). */
export interface FileContent {
  path: string;
  revision: string;
  content: string;
  size: number;
  truncated: boolean;
}
