// Controller reachability (`$DRAFTS/07 §4.6`): after a network failure, back off on GET /api/health (1 s, 2 s, 5 s,
// 10 s cap); on recovery run `invalidate("")` and notify subscribers (the design session retries its save).
import { invalidate } from "./query";
import { createStore, useStore } from "./store";

export interface ConnectionState {
  online: boolean;
  retryInMs: number | null;   // next health probe while offline
}

export const BACKOFF_MS: readonly number[] = [1000, 2000, 5000, 10000];

const ONLINE: ConnectionState = { online: true, retryInMs: null };

const store = createStore<ConnectionState>(ONLINE);
const reconnectListeners = new Set<() => void>();
let probe: () => Promise<unknown> = probeHealth;
let attempt = 0;
let timer: ReturnType<typeof setTimeout> | null = null;
let generation = 0;           // bumped by resetConnection so a probe in flight is ignored

async function probeHealth(): Promise<void> {
  const res = await fetch("/api/health", { headers: { Accept: "application/json" } });
  if (!res.ok) throw new Error(`GET /api/health: HTTP ${res.status}`);
}

/** The health check used while offline; App sets `() => api.health()`. */
export function setHealthProbe(fn: () => Promise<unknown>): void {
  probe = fn;
}

/** Called by the client on a fetch TypeError (ApiError status 0). */
export function reportNetworkError(): void {
  if (!store.get().online) return;
  attempt = 0;
  scheduleProbe();
}

function scheduleProbe(): void {
  const ms = BACKOFF_MS[Math.min(attempt, BACKOFF_MS.length - 1)] as number;
  store.set({ online: false, retryInMs: ms });
  timer = setTimeout(() => void runProbe(generation), ms);
}

async function runProbe(gen: number): Promise<void> {
  timer = null;
  try {
    await probe();
  } catch {
    if (gen !== generation) return;
    attempt += 1;
    scheduleProbe();
    return;
  }
  if (gen !== generation) return;
  attempt = 0;
  store.set(ONLINE);
  invalidate("");
  for (const fn of [...reconnectListeners]) fn();
}

/** Subscribes to offline -> online transitions; returns unsubscribe. */
export function onReconnect(fn: () => void): () => void {
  reconnectListeners.add(fn);
  return () => {
    reconnectListeners.delete(fn);
  };
}

export function useConnection(): ConnectionState {
  return useStore(store, (s) => s);
}

/** Back to online with no probe pending and the default probe (tests). */
export function resetConnection(): void {
  generation += 1;
  if (timer !== null) clearTimeout(timer);
  timer = null;
  attempt = 0;
  probe = probeHealth;
  store.set(ONLINE);
}
