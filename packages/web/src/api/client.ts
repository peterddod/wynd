// Typed API client (`$DRAFTS/07 §4.4`; routes PLAN §3.21). Stub from WEB-SCAFFOLD; WEB-CORE implements it.
// `ApiError` and the `Api` surface are final: tests and every other unit code against them.
import type {
  AnswerRequest, Build, ChatSnapshot, ChatSummary, CreateChatRequest, CreateProcessRequest, CreateReleaseRequest,
  CreateRunRequest, DesignDoc, EnvCheckDTO, ExprCheck, ExprCheckRequest, Job, JobWithChat, Json, LogChunk,
  McpServerEntry, Meta, OAuthStart, OAuthStartRequest, ProcessInterface, ProcessSummary, ProviderAddRequest,
  ProviderInfoDTO, Release, ReleasePatch, RemoveResult, RenameChatRequest, Run, SaveRequest, SaveResult,
  SendMessageRequest, SendMessageResult, StatusFlag, StepCatalogEntry, UploadResult,
} from "./types";

/** Non-2xx `{"error": {code, message, details}}` -> (status, code); FastAPI 422 -> "invalid"; network -> (0, "network"). */
export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly details: unknown;

  constructor(status: number, code: string, message: string, details: unknown = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.details = details;
  }
}

export type Method = "GET" | "POST" | "PATCH" | "DELETE";

export interface RequestOptions {
  signal?: AbortSignal;
  keepalive?: boolean;
}

/** New MCP server: `name` and `transport` are required, the rest default server-side. */
export type McpServerInput = Pick<McpServerEntry, "name" | "transport"> & Partial<McpServerEntry>;

/** The whole client surface; each method is one route of PLAN §3.21. */
export interface Api {
  health(): Promise<{ ok: true }>;
  meta(): Promise<Meta>;
  processes: {
    list(q: string, flags: StatusFlag[]): Promise<{ processes: ProcessSummary[] }>;
    get(pid: string): Promise<ProcessSummary>;
    create(body: CreateProcessRequest): Promise<ProcessSummary>;
    design(pid: string): Promise<DesignDoc>;
    save(pid: string, body: SaveRequest, init?: { keepalive?: boolean }): Promise<SaveResult>;
    compile(pid: string): Promise<JobWithChat>;
    build(pid: string): Promise<JobWithChat>;
    builds(pid: string): Promise<{ builds: Build[] }>;
    iface(pid: string, commit?: string): Promise<ProcessInterface>;
  };
  expressions: { validate(body: ExprCheckRequest): Promise<ExprCheck> };
  steps: { list(): Promise<{ steps: StepCatalogEntry[] }> };
  providers: {
    list(): Promise<{ providers: ProviderInfoDTO[] }>;
    add(body: ProviderAddRequest): Promise<ProviderInfoDTO>;
    remove(name: string): Promise<RemoveResult>;
  };
  registries: {
    mcp: {
      list(): Promise<{ items: McpServerEntry[] }>;
      add(entry: McpServerInput): Promise<McpServerEntry>;
      remove(name: string): Promise<RemoveResult>;
      oauthStart(name: string, body: OAuthStartRequest): Promise<OAuthStart>;
    };
  };
  jobs: {
    list(p: { process_id?: string; active?: boolean }): Promise<{ jobs: Job[] }>;
    get(id: string): Promise<Job>;
    logs(id: string, offset: number): Promise<LogChunk>;
    answer(id: string, body: AnswerRequest): Promise<Job>;
    integrate(id: string): Promise<Job>;
    cancel(id: string): Promise<Job>;
  };
  chats: {
    list(): Promise<{ chats: ChatSummary[] }>;
    create(body: CreateChatRequest): Promise<ChatSummary>;
    get(id: string): Promise<ChatSnapshot>;
    update(id: string, body: RenameChatRequest): Promise<ChatSummary>;
    remove(id: string): Promise<void>;
    send(id: string, body: SendMessageRequest): Promise<SendMessageResult>;
    cancel(id: string): Promise<{ ok: true }>;
    eventsUrl(id: string, since: number): string;
  };
  runs: {
    create(body: CreateRunRequest): Promise<Run>;
    list(p: { process_id?: string; release_id?: string; limit?: number }): Promise<{ runs: Run[] }>;
    get(id: string): Promise<Run>;
    eventsUrl(id: string, since?: number): string;
  };
  uploads: { put(file: File): Promise<UploadResult> };
  releases: {
    list(pid: string): Promise<{ releases: Release[] }>;
    create(body: CreateReleaseRequest): Promise<Release>;
    update(id: string, body: ReleasePatch): Promise<Release>;
    remove(id: string): Promise<void>;
    trigger(id: string, inputs: Record<string, Json>): Promise<Run>;
    envCheck(id: string): Promise<EnvCheckDTO>;
  };
}

export async function request<T>(method: Method, path: string, body?: unknown, init?: RequestOptions): Promise<T> {
  throw new Error("not implemented");
}

/** "finance/invoices" -> each segment encodeURIComponent'd, slashes kept. */
export function pidPath(pid: string): string {
  throw new Error("not implemented");
}

export function createApi(): Api {
  throw new Error("not implemented");
}
