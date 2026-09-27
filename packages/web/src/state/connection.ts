// Controller reachability (`$DRAFTS/07 §4.6`): after a network failure, back off on GET /api/health (1 s, 2 s, 5 s,
// 10 s cap); on recovery run `invalidate("")` and notify subscribers (the design session retries its save).
// Stub from WEB-SCAFFOLD; WEB-CORE implements it.

export interface ConnectionState {
  online: boolean;
  retryInMs: number | null;   // next health probe while offline
}

export const BACKOFF_MS: readonly number[] = [1000, 2000, 5000, 10000];

/** Called by the client on a fetch TypeError (ApiError status 0). */
export function reportNetworkError(): void {
  throw new Error("not implemented");
}

/** Subscribes to offline -> online transitions; returns unsubscribe. */
export function onReconnect(fn: () => void): () => void {
  throw new Error("not implemented");
}

export function useConnection(): ConnectionState {
  throw new Error("not implemented");
}
