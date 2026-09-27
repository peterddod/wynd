// Server-state cache (`$DRAFTS/07 §4.3`; canonical keys in its table). Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ApiError } from "../api/client";

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

export function useQuery<T>(key: string, fetcher: () => Promise<T>, opts?: QueryOpts<T>): QueryResult<T> {
  throw new Error("not implemented");
}

export function setQueryData<T>(key: string, data: T): void {
  throw new Error("not implemented");
}

export function getQueryData<T>(key: string): T | undefined {
  throw new Error("not implemented");
}

/** Refetches mounted queries whose key starts with `prefix`; drops the others. */
export function invalidate(prefix: string): void {
  throw new Error("not implemented");
}
