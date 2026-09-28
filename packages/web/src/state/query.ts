// Server-state cache (`$DRAFTS/07 §4.3`; canonical keys in its table): one entry per key, requests de-duplicated,
// mounted queries refetched on window focus, one polling timer per key paused while the document is hidden.
// An entry nobody has mounted keeps its data but is stale: the next mount shows it and refetches.
import { useCallback, useEffect, useRef, useSyncExternalStore } from "react";
import { ApiError } from "../api/client";
import { reportNetworkError } from "./connection";

export interface QueryResult<T> {
  data: T | undefined;
  error: ApiError | null;
  loading: boolean;
  reload(): Promise<void>;
}

export interface QueryOpts<T> {
  enabled?: boolean;                                            // default true
  pollMs?: number | ((data: T | undefined) => number | null);  // null = stop polling
  refetchOnFocus?: boolean;                                     // default true
}

interface Snapshot {
  data: unknown;
  error: ApiError | null;
  fetching: boolean;
}

interface Mount {
  fetcher: () => Promise<unknown>;
  opts: QueryOpts<unknown>;
}

interface Entry {
  snap: Snapshot;
  stale: boolean;                          // refetch on the next mount
  promise: Promise<void> | null;           // the request in flight
  gen: number;                             // a settled request whose gen differs was abandoned
  again: boolean;                          // invalidated while in flight: fetch once more when it settles
  listeners: Set<() => void>;
  mounts: Set<Mount>;
  timer: ReturnType<typeof setTimeout> | null;
  pollPaused: boolean;                     // a poll fell due while hidden
}

const cache = new Map<string, Entry>();
let listening = false;

function entryFor(key: string): Entry {
  let e = cache.get(key);
  if (e === undefined) {
    e = {
      snap: { data: undefined, error: null, fetching: false }, stale: false, promise: null, gen: 0, again: false,
      listeners: new Set(), mounts: new Set(), timer: null, pollPaused: false,
    };
    cache.set(key, e);
  }
  return e;
}

function update(e: Entry, patch: Partial<Snapshot>): void {
  e.snap = { ...e.snap, ...patch };
  for (const fn of [...e.listeners]) fn();
}

function enabledMounts(e: Entry): Mount[] {
  return [...e.mounts].filter((m) => m.opts.enabled !== false);
}

/** Non-ApiError rejections are client-side bugs: status -1, never mistaken for a network failure (status 0). */
function toApiError(err: unknown): ApiError {
  if (err instanceof ApiError) return err;
  return new ApiError(-1, "client_error", err instanceof Error ? err.message : String(err));
}

function fetchEntry(e: Entry): Promise<void> {
  if (e.promise !== null) return e.promise;
  const mount = enabledMounts(e).at(-1);
  if (mount === undefined) {
    e.stale = true;
    return Promise.resolve();
  }
  clearTimer(e);
  e.pollPaused = false;
  const gen = ++e.gen;
  update(e, { fetching: true });
  e.promise = settle(e, mount.fetcher, gen);
  return e.promise;
}

async function settle(e: Entry, fetcher: () => Promise<unknown>, gen: number): Promise<void> {
  let patch: Partial<Snapshot>;
  try {
    patch = { data: await fetcher(), error: null };
  } catch (err) {
    const error = toApiError(err);
    if (error.status === 0) reportNetworkError();
    patch = { error };
  }
  if (gen !== e.gen) return;
  e.promise = null;
  if (patch.error === null) e.stale = false;
  update(e, { ...patch, fetching: false });
  if (e.again) {
    e.again = false;
    void fetchEntry(e);
    return;
  }
  schedulePoll(e);
}

function clearTimer(e: Entry): void {
  if (e.timer !== null) clearTimeout(e.timer);
  e.timer = null;
}

function pollInterval(e: Entry): number | null {
  let min: number | null = null;
  for (const m of enabledMounts(e)) {
    const poll = m.opts.pollMs;
    const ms = typeof poll === "function" ? poll(e.snap.data) : poll;
    if (ms === undefined || ms === null) continue;
    if (min === null || ms < min) min = ms;
  }
  return min;
}

function schedulePoll(e: Entry): void {
  clearTimer(e);
  if (e.promise !== null) return;          // rescheduled when it settles
  const ms = pollInterval(e);
  if (ms === null) return;
  e.timer = setTimeout(() => {
    e.timer = null;
    if (document.hidden) {
      e.pollPaused = true;
      return;
    }
    void fetchEntry(e);
  }, ms);
}

/** The last mount went away: stop polling, abandon the request in flight, refetch on the next mount. */
function retire(e: Entry): void {
  clearTimer(e);
  e.stale = true;
  e.again = false;
  e.pollPaused = false;
  if (e.promise === null) return;
  e.gen += 1;
  e.promise = null;
  update(e, { fetching: false });
}

function onFocus(): void {
  for (const e of cache.values()) {
    if (enabledMounts(e).some((m) => m.opts.refetchOnFocus !== false)) void fetchEntry(e);
  }
}

function onVisibility(): void {
  if (document.hidden) return;
  for (const e of cache.values()) {
    if (e.pollPaused) void fetchEntry(e);
  }
}

function listen(): void {
  if (listening) return;
  listening = true;
  window.addEventListener("focus", onFocus);
  document.addEventListener("visibilitychange", onVisibility);
}

export function useQuery<T>(key: string, fetcher: () => Promise<T>, opts?: QueryOpts<T>): QueryResult<T> {
  const options = (opts ?? {}) as QueryOpts<unknown>;
  const enabled = options.enabled !== false;
  const mount = useRef<Mount>({ fetcher, opts: options });
  mount.current.fetcher = fetcher;         // the latest closure and options serve every refetch
  mount.current.opts = options;

  const subscribe = useCallback((fn: () => void) => {
    const e = entryFor(key);
    e.listeners.add(fn);
    return () => {
      e.listeners.delete(fn);
    };
  }, [key]);
  const snap = useSyncExternalStore(subscribe, () => entryFor(key).snap);

  useEffect(() => {
    const e = entryFor(key);
    const m = mount.current;
    e.mounts.add(m);
    listen();
    if (enabled && (e.snap.data === undefined || e.snap.error !== null || e.stale)) void fetchEntry(e);
    else schedulePoll(e);
    return () => {
      e.mounts.delete(m);
      if (e.mounts.size === 0) retire(e);
      else schedulePoll(e);
    };
  }, [key, enabled]);

  const reload = useCallback(() => fetchEntry(entryFor(key)), [key]);
  return {
    data: snap.data as T | undefined,
    error: snap.error,
    loading: snap.fetching || (enabled && snap.data === undefined && snap.error === null),
    reload,
  };
}

export function setQueryData<T>(key: string, data: T): void {
  const e = entryFor(key);
  e.stale = false;
  update(e, { data, error: null });
}

export function getQueryData<T>(key: string): T | undefined {
  return cache.get(key)?.snap.data as T | undefined;
}

/** Refetches mounted queries whose key starts with `prefix`; the others become stale (refetched when next mounted). */
export function invalidate(prefix: string): void {
  for (const [key, e] of cache) {
    if (!key.startsWith(prefix)) continue;
    if (e.promise !== null) {
      e.again = true;
      continue;
    }
    if (enabledMounts(e).length > 0) void fetchEntry(e);
    else e.stale = true;
  }
}

/** Drops every entry and timer (tests). */
export function resetQueryCache(): void {
  for (const e of cache.values()) {
    clearTimer(e);
    e.gen += 1;
  }
  cache.clear();
}
