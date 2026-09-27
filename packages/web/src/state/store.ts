// Tiny external store + selector hook (`$DRAFTS/07 §4.3`).
import { useSyncExternalStore } from "react";

export interface Store<T> {
  get(): T;
  set(next: T | ((prev: T) => T)): void;
  subscribe(fn: () => void): () => void;
}

export function createStore<T>(initial: T): Store<T> {
  let state = initial;
  const listeners = new Set<() => void>();
  return {
    get: () => state,
    set(next) {
      const value = typeof next === "function" ? (next as (prev: T) => T)(state) : next;
      if (Object.is(value, state)) return;
      state = value;
      for (const fn of [...listeners]) fn();
    },
    subscribe(fn) {
      listeners.add(fn);
      return () => {
        listeners.delete(fn);
      };
    },
  };
}

/** useSyncExternalStore; `select` must return a stable reference (a slice), never a freshly built object. */
export function useStore<T, S>(store: Store<T>, select: (s: T) => S): S {
  const read = (): S => select(store.get());
  return useSyncExternalStore(store.subscribe, read, read);
}
