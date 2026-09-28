// A fake `Api` for tests (`$DRAFTS/07 §17.1`): every method is a `vi.fn` whose default implementation answers from
// the shared fixtures (fresh copies), like a controller serving the dogfood workspace. `overrides` replace
// implementations by path; `calls` lists every invocation across all methods in call order.
import { vi, type Mock } from "vitest";
import { ApiError, type Api } from "../api/client";
import type { Job, Json, ProcessSummary, Release, Run } from "../api/types";
import { fixture } from "./fixtures";

export interface FakeCall {
  method: string;                    // e.g. "processes.save"
  args: unknown[];
}

export type DeepPartial<T> = {
  [K in keyof T]?: T[K] extends (...args: never[]) => unknown ? T[K] : DeepPartial<T[K]>;
};

export type FakeApi = Api & { readonly calls: FakeCall[] };

export function createFakeApi(overrides: DeepPartial<Api> = {}): FakeApi {
  const fns: [string, Mock][] = [];
  const api = wrap(defaults(), overrides, "", fns) as Api;
  return Object.defineProperty(api, "calls", {
    get: (): FakeCall[] =>
      fns
        .flatMap(([method, fn]) => fn.mock.calls.map((args, i) => ({ method, args, order: fn.mock.invocationCallOrder[i] ?? 0 })))
        .sort((a, b) => a.order - b.order)
        .map(({ method, args }) => ({ method, args })),
  }) as FakeApi;
}

function wrap(base: object, over: object | undefined, prefix: string, fns: [string, Mock][]): object {
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(base)) {
    const method = prefix === "" ? key : `${prefix}.${key}`;
    const given = over === undefined ? undefined : (over as Record<string, unknown>)[key];
    if (typeof value === "function") {
      const fn = vi.fn((typeof given === "function" ? given : value) as (...args: unknown[]) => unknown);
      fns.push([method, fn]);
      out[key] = fn;
      continue;
    }
    out[key] = wrap(value as object, given as object | undefined, method, fns);
  }
  return out;
}

function notFound(what: string): ApiError {
  return new ApiError(404, "not_found", `${what} not found`);
}

function jobs(): Job[] {
  return [fixture("jobCompileAwaiting"), fixture("jobCompileIntegrated"), fixture("jobBuildSucceeded")];
}

/** Active per `$DRAFTS/07 §9.9`: queued/running/awaiting_input, or a succeeded compile/test_live not yet integrated. */
function listedAsActive(j: Job): boolean {
  if (j.status === "queued" || j.status === "running" || j.status === "awaiting_input") return true;
  return j.status === "succeeded" && j.integration === null && (j.kind === "compile" || j.kind === "test_live");
}

function findOr404<T extends { id: string }>(items: T[], id: string, what: string): T {
  const found = items.find((x) => x.id === id);
  if (found === undefined) throw notFound(`${what} '${id}'`);
  return found;
}

function newProcess(id: string, goal: string | null | undefined): ProcessSummary {
  const base = fixture("processesList").find((p) => p.id === "process_supplier_invoice") as ProcessSummary;
  return { ...base, id, name: id.split("/").at(-1) ?? id, goal: goal ?? null, path: `processes/${id}`, matches: [] };
}

function releaseRun(release: Release, inputs: Record<string, Json>): Run {
  const run = fixture("runFinished");
  return {
    ...run, id: "run_20260927T120000000_0a0b0c", commit: release.commit, mode: "image", inputs,
    target: { kind: "release", release_id: release.id }, release_id: release.id, trigger: "manual",
    status: "running", exit: null, outputs: null, finished_at: null, duration_ms: null, usage: null,
  };
}

function defaults(): Api {
  let revision = 0;
  return {
    health: async () => ({ ok: true }),
    meta: async () => fixture("meta"),
    processes: {
      list: async (q) => ({ processes: q.trim() === "" ? fixture("processesList") : fixture("processesSearch") }),
      get: async (pid) => findOr404(fixture("processesList"), pid, "process"),
      create: async (body) => newProcess(body.id, body.goal),
      design: async (pid) => {
        const design = fixture("design");
        if (pid !== design.process_id) throw notFound(`process '${pid}'`);
        return design;
      },
      save: async (pid, body) => {
        const result = fixture("saveResult");
        const files = body.writes.map((w) => ({
          path: w.path,
          revision: w.delete === true ? null : `sha256:${(++revision).toString(16).padStart(64, "0")}`,
          yaml: w.delete === true ? null : (result.files[0]?.yaml ?? ""),
        }));
        const commit = body.commit === null ? null : { sha: result.head, message: `design(${pid}): ${body.commit.summary}` };
        return { ...result, files, commit, head: commit === null ? fixture("design").head : commit.sha };
      },
      compile: async () => fixture("jobCompileStarted"),
      build: async () => {
        const started = fixture("jobCompileStarted");
        const build = fixture("jobBuildSucceeded");
        const job: Job = { ...build, status: "queued", started_at: null, finished_at: null, build: null, artefacts: {} };
        return {
          job,
          chat: { ...started.chat, id: build.chat_id ?? started.chat.id, title: `Build ${build.process_id}`,
                  job: { id: job.id, kind: "build", process_id: job.process_id, status: "queued", pending_questions: 0 } },
        };
      },
      builds: async () => ({ builds: fixture("builds") }),
      iface: async (_pid, commit) => {
        const iface = fixture("processInterface");
        return { ...iface, commit: commit ?? null };
      },
    },
    expressions: { validate: async () => fixture("exprCheck") },
    steps: { list: async () => ({ steps: fixture("stepsCatalog") }) },
    providers: {
      list: async () => ({ providers: fixture("providers") }),
      add: async (body) => {
        const base = fixture("providers").find((p) => p.name === body.name) ?? fixture("providers")[0]!;
        return { ...base, name: body.name, tiers: { ...base.tiers, ...body.tiers } };
      },
      remove: async () => ({ removed: true, referenced_by: [] }),
    },
    registries: {
      mcp: {
        list: async () => ({ items: fixture("mcpList") }),
        add: async (entry) => ({
          url: null, headers: {}, command: null, env: {}, auth_env: [], timeout_s: 30, description: "", oauth: null,
          ...entry,
        }),
        remove: async () => ({ removed: true, referenced_by: [] }),
        oauthStart: async () => fixture("oauthStart"),
      },
    },
    jobs: {
      list: async (p) => ({
        jobs: jobs().filter((j) => (p.process_id === undefined || j.process_id === p.process_id)
          && (p.active !== true || listedAsActive(j))),
      }),
      get: async (id) => findOr404(jobs(), id, "job"),
      logs: async () => fixture("jobLogs"),
      answer: async (id) => findOr404(jobs(), id, "job"),
      integrate: async (id) => {
        const job = findOr404(jobs(), id, "job");
        return { ...job, integration: fixture("jobCompileIntegrated").integration };
      },
      cancel: async (id) => ({ ...findOr404(jobs(), id, "job"), status: "cancelled" }),
    },
    chats: {
      list: async () => ({ chats: fixture("chatsList") }),
      create: async (body) => ({
        ...fixture("chatSnapshot").chat, id: "chat_20260927T120000000_0d0e0f", title: body.title ?? "New chat",
        running_turn: null, job: null, usage_totals: null,
      }),
      get: async (id) => {
        const snapshot = fixture("chatSnapshot");
        if (id === snapshot.chat.id) return snapshot;
        return { chat: findOr404(fixture("chatsList"), id, "chat"), items: [], cursor: 0 };
      },
      update: async (id, body) => ({ ...findOr404(fixture("chatsList"), id, "chat"), title: body.title }),
      remove: async () => undefined,
      send: async () => fixture("sendResult"),
      cancel: async () => ({ ok: true }),
      eventsUrl: (id, since) => `/api/chats/${encodeURIComponent(id)}/events?since=${since}`,
    },
    runs: {
      create: async (body) => ({
        ...fixture("runFinished"), id: "run_20260927T120500000_1a1b1c", process_id: body.process_id,
        target: body.target, mode: body.target.kind === "local" ? "local" : "image", inputs: body.inputs,
        status: "running", exit: null, outputs: null, error: null, finished_at: null, duration_ms: null, usage: null,
      }),
      list: async (p) => ({
        runs: fixture("runsList")
          .filter((r) => (p.process_id === undefined || r.process_id === p.process_id)
            && (p.release_id === undefined || r.release_id === p.release_id))
          .slice(0, p.limit ?? 50),
      }),
      get: async (id) => findOr404(fixture("runsList"), id, "run"),
      eventsUrl: (id, since = 0) => `/api/runs/${encodeURIComponent(id)}/events?since=${since}`,
    },
    uploads: { put: async () => fixture("upload") },
    releases: {
      list: async (pid) => ({ releases: fixture("releases").filter((r) => r.process_id === pid) }),
      create: async (body) => ({ ...fixture("releases")[0]!, ...body, short: body.commit.slice(0, 7) }),
      update: async (id, body) => {
        const release = findOr404(fixture("releases"), id, "release");
        return {
          ...release,
          trigger: body.trigger ?? release.trigger,
          env: body.env ?? release.env,
          enabled: body.enabled ?? release.enabled,
        };
      },
      remove: async () => undefined,
      trigger: async (id, inputs) => releaseRun(findOr404(fixture("releases"), id, "release"), inputs),
      envCheck: async () => fixture("envCheck"),
    },
  };
}
