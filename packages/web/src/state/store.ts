// Tiny external store + selector hook (`$DRAFTS/07 §4.3`). Stub from WEB-SCAFFOLD; WEB-CORE implements it.

export interface Store<T> {
  get(): T;
  set(next: T | ((prev: T) => T)): void;
  subscribe(fn: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  throw new Error("not implemented");
}

/** useSyncExternalStore; `select` must return a stable reference (a slice), never a freshly built object. */
export function useStore<T, S>(store: Store<T>, select: (s: T) => S): S {
  throw new Error("not implemented");
}
