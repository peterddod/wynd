// Typed API client (`$DRAFTS/07 §4.4`; routes PLAN §3.21, `$DRAFTS/07 §12.1`, `$DRAFTS/06 §8.3`).
// `ApiError` and the `Api` surface are final: tests and every other unit code against them.
import { reportNetworkError } from "../state/connection";
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
  const headers: Record<string, string> = { Accept: "application/json" };
  if (body !== undefined) headers["Content-Type"] = "application/json";
  return send<T>(path, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
    signal: init?.signal,
    keepalive: init?.keepalive,
  });
}

/** fetch + error mapping; a 2xx with an empty body (204) resolves to undefined. */
async function send<T>(path: string, init: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch (err) {
    if (!(err instanceof TypeError)) throw err;          // an AbortError stays what it is
    reportNetworkError();
    throw new ApiError(0, "network", `Can't reach wynd serve-api (${err.message})`);
  }
  const payload = parseBody(await res.text());
  if (res.ok) return payload as T;
  throw errorFrom(res.status, res.statusText, payload);
}

function parseBody(text: string): unknown {
  if (text === "") return undefined;
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function errorFrom(status: number, statusText: string, payload: unknown): ApiError {
  const fallback = status === 404 ? "not_found" : `http_${status}`;
  const text = statusText === "" ? `HTTP ${status}` : statusText;
  if (isObject(payload) && isObject(payload.error)) {
    const { code, message, details } = payload.error;
    return new ApiError(
      status, typeof code === "string" ? code : fallback, typeof message === "string" ? message : text, details ?? null,
    );
  }
  if (isObject(payload) && Array.isArray(payload.detail)) {           // FastAPI request validation
    const first: unknown = payload.detail[0];
    const message = isObject(first) && typeof first.msg === "string" ? first.msg : "invalid request";
    return new ApiError(status, status === 422 ? "invalid" : fallback, message, payload.detail);
  }
  if (isObject(payload) && typeof payload.detail === "string") return new ApiError(status, fallback, payload.detail);
  return new ApiError(status, fallback, text, payload ?? null);
}

/** "finance/invoices" -> each segment encodeURIComponent'd, slashes kept. */
export function pidPath(pid: string): string {
  return pid.split("/").map(encodeURIComponent).join("/");
}

type Params = Record<string, string | number | boolean | null | undefined>;

/** `?a=1&b=x`; null/undefined parameters are omitted. */
function query(params: Params): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null) search.set(key, String(value));
  }
  const text = search.toString();
  return text === "" ? "" : `?${text}`;
}

/** Header values must be ISO-8859-1: other file names are sent percent-encoded. */
function headerSafe(name: string): string {
  return /^[\x20-\x7e]*$/.test(name) ? name : encodeURIComponent(name);
}

function processPath(pid: string, suffix = ""): string {
  return `/api/processes/${pidPath(pid)}${suffix}`;
}

function id(value: string): string {
  return encodeURIComponent(value);
}

export function createApi(): Api {
  return {
    health: () => request("GET", "/api/health"),
    meta: () => request("GET", "/api/meta"),
    processes: {
      list: (q, flags) =>
        request("GET", `/api/processes${query({ q: q.trim() === "" ? null : q, flags: flags.length === 0 ? null : flags.join(",") })}`),
      get: (pid) => request("GET", processPath(pid)),
      create: (body) => request("POST", "/api/processes", body),
      design: (pid) => request("GET", processPath(pid, "/design")),
      save: (pid, body, init) => request("POST", processPath(pid, "/design"), body, { keepalive: init?.keepalive }),
      compile: (pid) => request("POST", processPath(pid, "/compile"), {}),
      build: (pid) => request("POST", processPath(pid, "/build"), {}),
      builds: (pid) => request("GET", processPath(pid, "/builds")),
      iface: (pid, commit) => request("GET", processPath(pid, "/interface") + query({ commit })),
    },
    expressions: { validate: (body) => request("POST", "/api/expressions/validate", body) },
    steps: { list: () => request("GET", "/api/steps") },
    providers: {
      list: () => request("GET", "/api/providers"),
      add: (body) => request("POST", "/api/providers", body),
      remove: (name) => request("DELETE", `/api/providers/${id(name)}`),
    },
    registries: {
      mcp: {
        list: () => request("GET", "/api/registries/mcp"),
        add: (entry) => request("POST", "/api/registries/mcp", entry),
        remove: (name) => request("DELETE", `/api/registries/mcp/${id(name)}`),
        oauthStart: (name, body) => request("POST", `/api/registries/mcp/${id(name)}/oauth/start`, body),
      },
    },
    jobs: {
      list: (p) => request("GET", `/api/jobs${query({ process_id: p.process_id, active: p.active })}`),
      get: (job) => request("GET", `/api/jobs/${id(job)}`),
      logs: (job, offset) => request("GET", `/api/jobs/${id(job)}/logs${query({ offset })}`),
      answer: (job, body) => request("POST", `/api/jobs/${id(job)}/answers`, body),
      integrate: (job) => request("POST", `/api/jobs/${id(job)}/integrate`, {}),
      cancel: (job) => request("POST", `/api/jobs/${id(job)}/cancel`, {}),
    },
    chats: {
      list: () => request("GET", "/api/chats"),
      create: (body) => request("POST", "/api/chats", body),
      get: (chat) => request("GET", `/api/chats/${id(chat)}`),
      update: (chat, body) => request("PATCH", `/api/chats/${id(chat)}`, body),
      remove: (chat) => request("DELETE", `/api/chats/${id(chat)}`),
      send: (chat, body) => request("POST", `/api/chats/${id(chat)}/messages`, body),
      cancel: (chat) => request("POST", `/api/chats/${id(chat)}/cancel`, {}),
      eventsUrl: (chat, since) => `/api/chats/${id(chat)}/events${query({ since })}`,
    },
    runs: {
      create: (body) => request("POST", "/api/runs", body),
      list: (p) => request("GET", `/api/runs${query({ process_id: p.process_id, release_id: p.release_id, limit: p.limit })}`),
      get: (run) => request("GET", `/api/runs/${id(run)}`),
      eventsUrl: (run, since = 0) => `/api/runs/${id(run)}/events${query({ since })}`,
    },
    uploads: {
      put: (file) =>
        send("/api/uploads", {
          method: "POST",
          headers: {
            Accept: "application/json",
            "Content-Type": file.type === "" ? "application/octet-stream" : file.type,
            "X-Wynd-Filename": headerSafe(file.name),
          },
          body: file,
        }),
    },
    releases: {
      list: (pid) => request("GET", `/api/releases${query({ process_id: pid })}`),
      create: (body) => request("POST", "/api/releases", body),
      update: (release, body) => request("PATCH", `/api/releases/${id(release)}`, body),
      remove: (release) => request("DELETE", `/api/releases/${id(release)}`),
      trigger: (release, inputs) => request("POST", `/api/releases/${id(release)}/trigger`, { inputs }),
      envCheck: (release) => request("GET", `/api/releases/${id(release)}/env-check`),
    },
  };
}
