// Test-only doubles for the WEB-OPS component tests; no app module imports this file. The modules these components
// use from WEB-CORE (query cache, SSE, URL state, toasts, common components) and WEB-GRAPH (openProcess, ValueInput)
// are written in the same sub-wave, so the tests run against small doubles of their contracts (PLAN §0 rule 3). A
// test installs one with `vi.mock("<module>", () => import("<this file>").then((m) => m.<name>Module))` and calls
// `resetDoubles()` in `beforeEach`.
import { useEffect, useReducer, useRef, useSyncExternalStore, type ReactElement } from "react";
import { vi } from "vitest";
import type { ApiError } from "../../api/client";
import type { SseOptions } from "../../api/sse";
import type { CopyButtonProps } from "../common/CopyButton";
import type { DialogProps } from "../common/Dialog";
import type { ErrorBoxProps } from "../common/ErrorBox";
import type { JsonViewProps } from "../common/JsonView";
import type { ValueInputProps } from "../examples/ValueInput";
import type { QueryOpts, QueryResult } from "../../state/query";
import type { SetUrl, Tab, UrlState } from "../../state/url";

// --- state/query: a keyed cache; mount fetches once, `invalidate` refetches mounted keys, `pollMs` re-arms ----------

interface Entry {
  data: unknown;
  error: ApiError | null;
  loading: boolean;
  fetcher: () => Promise<unknown>;
  subs: Set<() => void>;
  inflight: Promise<void> | null;
}

const cache = new Map<string, Entry>();

function notify(e: Entry): void {
  for (const fn of [...e.subs]) fn();
}

function load(key: string): Promise<void> {
  const e = cache.get(key);
  if (e === undefined) return Promise.resolve();
  if (e.inflight !== null) return e.inflight;
  e.loading = true;
  notify(e);
  e.inflight = e.fetcher().then(
    (data) => {
      e.data = data;
      e.error = null;
    },
    (err: unknown) => {
      e.error = err as ApiError;
    },
  ).finally(() => {
    e.loading = false;
    e.inflight = null;
    notify(e);
  });
  return e.inflight;
}

function useQuery<T>(key: string, fetcher: () => Promise<T>, opts: QueryOpts<T> = {}): QueryResult<T> {
  const enabled = opts.enabled !== false;
  const [, rerender] = useReducer((n: number) => n + 1, 0);
  const latest = useRef(fetcher);
  latest.current = fetcher;
  useEffect(() => {
    if (!enabled) return;
    let e = cache.get(key);
    if (e === undefined) {
      e = { data: undefined, error: null, loading: false, fetcher: () => latest.current(), subs: new Set(), inflight: null };
      cache.set(key, e);
    }
    e.fetcher = () => latest.current();
    e.subs.add(rerender);
    if (e.data === undefined && e.error === null) void load(key);
    const entry = e;
    return () => {
      entry.subs.delete(rerender);
    };
  }, [key, enabled]);
  const e = enabled ? cache.get(key) : undefined;
  const data = e?.data as T | undefined;
  const poll = opts.pollMs;
  const ms = typeof poll === "function" ? poll(data) : poll ?? null;
  useEffect(() => {
    if (!enabled || ms === null) return;
    const timer = setTimeout(() => void load(key), ms);
    return () => clearTimeout(timer);
  }, [key, enabled, ms, data]);
  return { data, error: e?.error ?? null, loading: enabled && (e === undefined || e.loading), reload: () => load(key) };
}

export const queryModule = {
  useQuery,
  setQueryData<T>(key: string, data: T): void {
    const e = cache.get(key);
    if (e === undefined) {
      cache.set(key, { data, error: null, loading: false, fetcher: async () => data, subs: new Set(), inflight: null });
      return;
    }
    e.data = data;
    notify(e);
  },
  getQueryData<T>(key: string): T | undefined {
    return cache.get(key)?.data as T | undefined;
  },
  invalidate(prefix: string): void {
    invalidated.push(prefix);
    for (const [key, e] of cache) {
      if (!key.startsWith(prefix)) continue;
      if (e.subs.size > 0) void load(key);
      else cache.delete(key);
    }
  },
};

/** Every `invalidate` prefix, in order. */
export const invalidated: string[] = [];

// --- api/sse: named events over the global EventSource (FakeEventSource in tests); `end` closes -------------------

export const sseModule = {
  openEventStream(url: string, opts: SseOptions): () => void {
    const source = new globalThis.EventSource(url);
    for (const [name, handler] of Object.entries(opts.events)) {
      source.addEventListener(name, (ev) => {
        const msg = ev as MessageEvent<string>;
        handler(JSON.parse(msg.data), msg.lastEventId === "" ? null : msg.lastEventId);
        if (name === "end") source.close();
      });
    }
    source.addEventListener("open", () => opts.onOpen?.());
    source.addEventListener("error", () => opts.onError?.());
    return () => source.close();
  },
  setEventSourceImpl(): void {},
};

// --- state/url: the query string of window.location; every setter call notifies every hook ------------------------

const urlSubs = new Set<() => void>();
const TABS: Tab[] = ["graph", "process", "yaml", "runs", "releases"];

function readUrl(search: string = window.location.search): UrlState {
  const params = new URLSearchParams(search);
  const tab = params.get("tab");
  return {
    process: params.get("process"),
    chat: params.get("chat"),
    tab: tab !== null && (TABS as string[]).includes(tab) ? (tab as Tab) : "graph",
    sel: params.get("sel"),
    run: params.get("run"),
  };
}

const setUrl: SetUrl = (patch) => {
  const next = { ...readUrl(), ...patch };
  const params = new URLSearchParams();
  for (const key of ["process", "chat", "tab", "sel", "run"] as const) {
    const value = next[key];
    if (value !== null && !(key === "tab" && value === "graph")) params.set(key, value);
  }
  const query = params.toString();
  window.history.replaceState(null, "", query === "" ? "/" : `/?${query}`);
  for (const fn of [...urlSubs]) fn();
};

let urlSnapshot: { search: string; state: UrlState } | null = null;

function urlState(): UrlState {
  if (urlSnapshot === null || urlSnapshot.search !== window.location.search) {
    urlSnapshot = { search: window.location.search, state: readUrl() };
  }
  return urlSnapshot.state;
}

export const urlModule = {
  readUrl,
  setUrl,
  useUrlState(): [UrlState, SetUrl] {
    const state = useSyncExternalStore((fn) => {
      urlSubs.add(fn);
      return () => void urlSubs.delete(fn);
    }, urlState);
    return [state, setUrl];
  },
};

// --- state/openProcess and state/toasts: spies ---------------------------------------------------------------------

export const openProcessSpy = vi.fn(async (_next: string | null): Promise<void> => {});
export const openProcessModule = {
  useOpenProcess: () => openProcessSpy,
  openProcess: async (_deps: unknown, next: string | null) => openProcessSpy(next),
  showSwitchBlocked(): void {},
};

export const toastSpy = vi.fn((_text: string, _opts?: unknown): number => 1);
export const toastsModule = { toast: toastSpy, dismissToast(): void {}, useToasts: () => [] };

// --- common components and ValueInput -----------------------------------------------------------------------------

export const dialogModule = {
  Dialog({ open, title, children, actions }: DialogProps): ReactElement | null {
    if (!open) return null;
    return (
      <div role="dialog" aria-label={title}>
        <h2>{title}</h2>
        {children}
        <div>{actions}</div>
      </div>
    );
  },
};

export const jsonViewModule = {
  JsonView({ value, label }: JsonViewProps): ReactElement {
    return <pre aria-label={label}>{value === undefined ? "" : JSON.stringify(value)}</pre>;
  },
};

export const errorBoxModule = {
  ErrorBox({ error, onRetry }: ErrorBoxProps): ReactElement | null {
    if (error === null) return null;
    return (
      <div role="alert">
        {error.message}
        {onRetry !== undefined && <button type="button" onClick={onRetry}>Retry</button>}
      </div>
    );
  },
};

export const copyButtonModule = {
  CopyButton({ text, label }: CopyButtonProps): ReactElement {
    return <button type="button" aria-label={label ?? "Copy"} data-copy={text}>Copy</button>;
  },
};

/** Props of every ValueInput render, latest last. */
export const valueInputs: ValueInputProps[] = [];

export const valueInputModule = {
  ValueInput(props: ValueInputProps): ReactElement {
    valueInputs.push(props);
    const text = props.value === undefined ? "" : typeof props.value === "string" ? props.value : JSON.stringify(props.value);
    return (
      <input
        aria-label={props.label}
        aria-required={props.required === true}
        value={text}
        onChange={(e) => props.onChange(e.target.value === "" ? undefined : e.target.value)}
      />
    );
  },
};

export function resetDoubles(): void {
  cache.clear();
  invalidated.length = 0;
  valueInputs.length = 0;
  urlSnapshot = null;
  openProcessSpy.mockClear();
  toastSpy.mockClear();
}
