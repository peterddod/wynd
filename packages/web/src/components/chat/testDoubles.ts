// Test-only doubles for the chat unit's tests; no app module imports this file. The WEB-CORE modules the chat unit
// uses (query cache, SSE, URL state, toasts) are written in the same sub-wave, so the tests run against small doubles
// of their contracts (PLAN §0 rule 3), as they do for the WEB-GRAPH/WEB-OPS pieces a job card mounts. A test file
// installs one with `vi.mock("<module>", () => import("<this file>").then((m) => m.<name>Module))` and calls
// `resetDoubles()` in `beforeEach`.
import { createElement, useEffect, useSyncExternalStore, type ChangeEvent, type ReactElement } from "react";
import { vi, type Mock } from "vitest";
import type { ApiError } from "../../api/client";
import type { SseOptions } from "../../api/sse";
import type { Interface, Json, JsonSchema, StepInfo } from "../../api/types";
import type { CommitReason, DesignSession, DesignState } from "../../state/design";
import type { QueryOpts, QueryResult } from "../../state/query";
import type { Store } from "../../state/store";
import type { Toast, ToastAction, ToastLevel } from "../../state/toasts";
import type { SetUrl, Tab, UrlState } from "../../state/url";
import type { ExampleCardProps } from "../examples/ExampleCard";
import type { NewReleaseDialogProps } from "../releases/NewReleaseDialog";

// --- state/query: an in-memory cache; nothing polls by itself (`queries.refetch` is one poll tick) ----------------

interface Entry {
  data: unknown;
  error: ApiError | null;
  loading: boolean;
  version: number;
  fetcher: (() => Promise<unknown>) | null;
  opts: QueryOpts<unknown> | undefined;
  listeners: Set<() => void>;
}

const entries = new Map<string, Entry>();

function entryOf(key: string): Entry {
  let entry = entries.get(key);
  if (entry === undefined) {
    entry = { data: undefined, error: null, loading: false, version: 0, fetcher: null, opts: undefined, listeners: new Set() };
    entries.set(key, entry);
  }
  return entry;
}

function notify(entry: Entry): void {
  entry.version += 1;
  for (const fn of [...entry.listeners]) fn();
}

async function load(key: string): Promise<void> {
  const entry = entryOf(key);
  if (entry.fetcher === null) return;
  entry.loading = true;
  notify(entry);
  try {
    entry.data = await entry.fetcher();
    entry.error = null;
  } catch (err) {
    entry.error = err as ApiError;
  }
  entry.loading = false;
  notify(entry);
}

function useQuery<T>(key: string, fetcher: () => Promise<T>, opts: QueryOpts<T> = {}): QueryResult<T> {
  const entry = entryOf(key);
  entry.fetcher = fetcher;
  entry.opts = opts as QueryOpts<unknown>;
  useSyncExternalStore(
    (fn) => {
      entry.listeners.add(fn);
      return () => void entry.listeners.delete(fn);
    },
    () => entry.version,
  );
  const enabled = opts.enabled !== false;
  useEffect(() => {
    if (enabled && entry.data === undefined && !entry.loading) void load(key);
  }, [key, enabled, entry]);
  return { data: entry.data as T | undefined, error: entry.error, loading: entry.loading, reload: () => load(key) };
}

export const queries = {
  /** One poll tick of `key`: fetch it again now. */
  refetch: (key: string): Promise<void> => load(key),
  /** The options of the latest `useQuery(key, …)` render. */
  opts: <T>(key: string): QueryOpts<T> | undefined => entries.get(key)?.opts as QueryOpts<T> | undefined,
  data: <T>(key: string): T | undefined => entries.get(key)?.data as T | undefined,
  /** Every `invalidate(prefix)` call, in order. */
  invalidated: [] as string[],
};

export const queryModule = {
  useQuery,
  setQueryData<T>(key: string, data: T): void {
    const entry = entryOf(key);
    entry.data = data;
    notify(entry);
  },
  getQueryData<T>(key: string): T | undefined {
    return entries.get(key)?.data as T | undefined;
  },
  invalidate(prefix: string): void {
    queries.invalidated.push(prefix);
    for (const [key, entry] of entries) {
      if (key.startsWith(prefix) && entry.listeners.size > 0) void load(key);
    }
  },
};

// --- api/sse: the contract over the global EventSource (FakeEventSource in tests) ----------------------------------

export const sseModule = {
  openEventStream(url: string, opts: SseOptions): () => void {
    const source = new EventSource(url);
    const close = (): void => source.close();
    for (const [name, handler] of Object.entries(opts.events)) {
      source.addEventListener(name, (ev) => {
        const msg = ev as MessageEvent<string>;
        let data: unknown;
        try {
          data = JSON.parse(msg.data);
        } catch {
          return;
        }
        handler(data, msg.lastEventId === "" ? null : msg.lastEventId);
        if (name === "end") close();
      });
    }
    source.onopen = () => opts.onOpen?.();
    source.onerror = () => opts.onError?.();
    return close;
  },
  setEventSourceImpl(): void {},
};

// --- state/url: the query string of the jsdom location ------------------------------------------------------------

const TABS: readonly Tab[] = ["graph", "process", "yaml", "runs", "releases"];
const URL_KEYS = ["process", "chat", "tab", "sel", "run"] as const;
const urlListeners = new Set<() => void>();
let urlCache: { search: string; state: UrlState } | null = null;

function parseUrl(search: string): UrlState {
  const params = new URLSearchParams(search);
  const tab = params.get("tab");
  return {
    process: params.get("process"),
    chat: params.get("chat"),
    tab: TABS.find((t) => t === tab) ?? "graph",
    sel: params.get("sel"),
    run: params.get("run"),
  };
}

function currentUrl(): UrlState {
  const search = window.location.search;
  if (urlCache === null || urlCache.search !== search) urlCache = { search, state: parseUrl(search) };
  return urlCache.state;
}

const setUrl: SetUrl = (patch, mode) => {
  urls.calls.push({ patch, mode });
  const next = { ...currentUrl(), ...patch };
  const params = new URLSearchParams();
  for (const key of URL_KEYS) {
    const value = next[key];
    if (value !== null && !(key === "tab" && value === "graph")) params.set(key, value);
  }
  const query = params.toString();
  window.history.replaceState(null, "", `/${query === "" ? "" : `?${query}`}`);
  for (const fn of [...urlListeners]) fn();
};

export const urls = {
  current: currentUrl,
  set: setUrl,
  calls: [] as { patch: Partial<UrlState>; mode: "push" | "replace" | undefined }[],
};

export const urlModule = {
  readUrl: (search: string = window.location.search): UrlState => parseUrl(search),
  useUrlState(): [UrlState, SetUrl] {
    const state = useSyncExternalStore((fn) => {
      urlListeners.add(fn);
      return () => void urlListeners.delete(fn);
    }, currentUrl);
    return [state, setUrl];
  },
};

// --- state/toasts --------------------------------------------------------------------------------------------------

export const toasts: { text: string; level: ToastLevel | undefined; action: ToastAction | undefined }[] = [];

export const toastsModule = {
  toast(text: string, opts: { level?: ToastLevel; action?: ToastAction } = {}): number {
    toasts.push({ text, level: opts.level, action: opts.action });
    return toasts.length;
  },
  dismissToast(): void {},
  useToasts: (): Toast[] => [],
};

// --- model/values, model/schemaText (WEB-GRAPH): recognisable deterministic text -----------------------------------

export const valuesModule = {
  parseLoose: (text: string): Json => text,
  formatLoose: (v: Json): string => (typeof v === "string" ? v : JSON.stringify(v)),
  parseTyped: (text: string): { ok: true; value: Json } => ({ ok: true, value: text }),
  exampleSentence: (ex: Json): string => `sentence ${JSON.stringify(ex)}`,
};

function fieldNames(s: JsonSchema | null | undefined): string {
  return Object.keys(s?.properties ?? {}).join(", ");
}

export const schemaTextModule = {
  describe: (): string => "a value",
  describeFields: fieldNames,
  stepSentence: (iface: Interface): string => `takes ${fieldNames(iface.inputs)}`,
};

// --- ExampleCard (WEB-GRAPH), NewReleaseDialog (WEB-OPS) ------------------------------------------------------------

function ExampleCardDouble(props: ExampleCardProps): ReactElement {
  const json = JSON.stringify(props.example);
  const { onChange } = props;
  if (onChange === undefined) {
    return createElement("pre", { "data-testid": "example-card" }, `${json} | exits: ${props.exits.join(",")}`);
  }
  return createElement("textarea", {
    "aria-label": "Example JSON",
    defaultValue: json,
    onChange: (e: ChangeEvent<HTMLTextAreaElement>) => {
      try {
        onChange(JSON.parse(e.target.value) as Json);
      } catch {
        // mid-edit JSON: keep the last valid value
      }
    },
  });
}

export const exampleCardModule = { ExampleCard: ExampleCardDouble };

export const newReleaseDialogModule = {
  NewReleaseDialog(props: NewReleaseDialogProps): ReactElement | null {
    if (!props.open) return null;
    return createElement(
      "div",
      { role: "dialog", "aria-label": "New release" },
      `release ${props.processId} at ${props.commit ?? "HEAD"}`,
      createElement("button", { type: "button", onClick: props.onClose }, "Close"),
    );
  },
};

// --- the design session ------------------------------------------------------------------------------------------

export interface FakeDesign extends DesignSession {
  commit: Mock<DesignSession["commit"]>;
  lock: Mock<DesignSession["lock"]>;
  unlock: Mock<DesignSession["unlock"]>;
  reload: Mock<DesignSession["reload"]>;
}

/** A design session with `processId` open (null: none). `log` receives "commit:<reason>" for ordering checks. */
export function fakeDesign(
  processId: string | null,
  opts: { steps?: Record<string, StepInfo>; log?: string[] } = {},
): FakeDesign {
  let state = processId === null ? null : ({ processId, steps: opts.steps ?? {} } as unknown as DesignState);
  const listeners = new Set<() => void>();
  const store: Store<DesignState | null> = {
    get: () => state,
    set(next) {
      state = typeof next === "function" ? next(state) : next;
      for (const fn of [...listeners]) fn();
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => void listeners.delete(fn);
    },
  };
  const design = {
    store,
    open: vi.fn(async () => undefined),
    apply: vi.fn(),
    flush: vi.fn(async () => undefined),
    commit: vi.fn(async (reason: CommitReason) => {
      opts.log?.push(`commit:${reason}`);
      return null;
    }),
    reload: vi.fn(async () => undefined),
    resolveConflict: vi.fn(async () => undefined),
    lock: vi.fn(),
    unlock: vi.fn(),
    processDoc: vi.fn(() => null),
    dirtyProtoDocs: vi.fn(() => ({})),
    close: vi.fn(),
  };
  return design as FakeDesign;
}

export function resetDoubles(): void {
  entries.clear();
  queries.invalidated.length = 0;
  toasts.length = 0;
  urls.calls.length = 0;
  urlCache = null;
}
