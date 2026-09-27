// Test doubles of the WEB-CORE modules WEB-GRAPH's code imports (state/store, query, url, toasts, connection, theme and
// common/Dialog). They were written in the same sub-wave, so WEB-GRAPH's tests replace them with these, following
// each module's contract (PLAN §0 rule 3):
//   vi.mock("../../state/store", () => import("../graph/testDoubles"));
// Nothing in the app imports this file.
import { useEffect, useState, useSyncExternalStore, type ReactElement, type ReactNode } from "react";
import type { ApiError } from "../../api/client";

// --- state/store ------------------------------------------------------------------------------------------------------

export interface Store<T> {
  get(): T;
  set(next: T | ((prev: T) => T)): void;
  subscribe(fn: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let value = initial;
  const subs = new Set<() => void>();
  return {
    get: () => value,
    set(next) {
      const v = typeof next === "function" ? (next as (prev: T) => T)(value) : next;
      if (Object.is(v, value)) return;
      value = v;
      for (const fn of [...subs]) fn();
    },
    subscribe(fn) {
      subs.add(fn);
      return () => subs.delete(fn);
    },
  };
}

export function useStore<T, S>(store: Store<T>, select: (s: T) => S): S {
  return useSyncExternalStore(store.subscribe, () => select(store.get()), () => select(store.get()));
}

// --- state/query ------------------------------------------------------------------------------------------------------

export interface QueryResult<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload(): Promise<void>;
}

const queryData = new Map<string, unknown>();
/** Every `invalidate(prefix)` call, in order. */
export const invalidated: string[] = [];

export function useQuery<T>(key: string, fetcher: () => Promise<T>, opts?: { enabled?: boolean }): QueryResult<T> {
  const enabled = opts?.enabled ?? true;
  const [state, setState] = useState<{ data: T | undefined; error: ApiError | null; loading: boolean }>(
    { data: queryData.get(key) as T | undefined, error: null, loading: enabled });
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (!enabled) return;
    let live = true;
    fetcher().then(
      (data) => {
        queryData.set(key, data);
        if (live) setState({ data, error: null, loading: false });
      },
      (error: ApiError) => live && setState((s) => ({ ...s, error, loading: false })),
    );
    return () => {
      live = false;
    };
  }, [key, enabled, tick]);
  return { ...state, reload: async () => setTick((t) => t + 1) };
}

export function setQueryData<T>(key: string, data: T): void {
  queryData.set(key, data);
}

export function getQueryData<T>(key: string): T | undefined {
  return queryData.get(key) as T | undefined;
}

export function invalidate(prefix: string): void {
  invalidated.push(prefix);
}

// --- state/url --------------------------------------------------------------------------------------------------------

export type Tab = "graph" | "process" | "yaml" | "runs" | "releases";

export interface UrlState {
  process: string | null;
  chat: string | null;
  tab: Tab;
  sel: string | null;
  run: string | null;
}

export type SetUrl = (patch: Partial<UrlState>, mode?: "push" | "replace") => void;

const urlSubs = new Set<() => void>();
let urlCache: { search: string; state: UrlState } | null = null;

export function readUrl(search: string = window.location.search): UrlState {
  const p = new URLSearchParams(search);
  return {
    process: p.get("process"),
    chat: p.get("chat"),
    tab: (p.get("tab") as Tab | null) ?? "graph",
    sel: p.get("sel"),
    run: p.get("run"),
  };
}

function currentUrl(): UrlState {
  const search = window.location.search;
  if (urlCache === null || urlCache.search !== search) urlCache = { search, state: readUrl(search) };
  return urlCache.state;
}

export const setUrl: SetUrl = (patch, mode) => {
  const next = { ...currentUrl(), ...patch };
  const params = new URLSearchParams();
  for (const key of ["process", "chat", "tab", "sel", "run"] as const) {
    const v = next[key];
    if (v !== null && !(key === "tab" && v === "graph")) params.set(key, v);
  }
  const query = params.toString();
  const push = mode === "push" || (mode === undefined && ("process" in patch || "chat" in patch));
  window.history[push ? "pushState" : "replaceState"](null, "", `/${query === "" ? "" : `?${query}`}`);
  for (const fn of [...urlSubs]) fn();
};

export function useUrlState(): [UrlState, SetUrl] {
  const state = useSyncExternalStore((fn) => {
    urlSubs.add(fn);
    return () => urlSubs.delete(fn);
  }, currentUrl);
  return [state, setUrl];
}

// --- state/toasts -----------------------------------------------------------------------------------------------------

export type ToastLevel = "info" | "warning" | "error";
export interface ToastAction {
  label: string;
  run(): void;
}
export interface Toast {
  id: number;
  level: ToastLevel;
  text: string;
  action: ToastAction | null;
}

/** Every toast shown, in order. */
export const toasts: Toast[] = [];

export function toast(text: string, opts?: { level?: ToastLevel; action?: ToastAction }): number {
  const id = toasts.length + 1;
  toasts.push({ id, level: opts?.level ?? "info", text, action: opts?.action ?? null });
  return id;
}

export function dismissToast(_id: number): void {}

export function useToasts(): Toast[] {
  return toasts;
}

// --- state/connection -------------------------------------------------------------------------------------------------

export const BACKOFF_MS: readonly number[] = [1000, 2000, 5000, 10000];
const reconnects = new Set<() => void>();

export function reportNetworkError(): void {}

export function onReconnect(fn: () => void): () => void {
  reconnects.add(fn);
  return () => reconnects.delete(fn);
}

/** Test hook: the controller became reachable again. */
export function fireReconnect(): void {
  for (const fn of [...reconnects]) fn();
}

export function useConnection(): { online: boolean; retryInMs: number | null } {
  return { online: true, retryInMs: null };
}

// --- state/theme ------------------------------------------------------------------------------------------------------

export type Theme = "system" | "light" | "dark";
export const THEME_KEY = "wynd.theme";
export const readTheme = (): Theme => "system";
export const applyTheme = (_theme: Theme): void => {};
export const nextTheme = (t: Theme): Theme => (t === "system" ? "light" : t === "light" ? "dark" : "system");
export function useTheme(): [Theme, (next: Theme) => void] {
  return ["system", () => {}];
}

// --- components/common/Dialog -----------------------------------------------------------------------------------------

export interface DialogProps {
  open: boolean;
  title: string;
  onClose(): void;
  children?: ReactNode;
  actions?: ReactNode;
  className?: string;
}

export function Dialog(props: DialogProps): ReactElement | null {
  if (!props.open) return null;
  return (
    <div role="dialog" aria-label={props.title} className={props.className}>
      <h2>{props.title}</h2>
      {props.children}
      <div>{props.actions}</div>
    </div>
  );
}

/** Clears the recorded calls between tests. */
export function resetDoubles(): void {
  invalidated.length = 0;
  toasts.length = 0;
  queryData.clear();
  urlCache = null;
}
