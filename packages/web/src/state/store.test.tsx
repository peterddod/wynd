import { act, render, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { createStore, useStore, type Store } from "./store";

describe("createStore", () => {
  it("gets, sets values and updaters, and notifies subscribers until they unsubscribe", () => {
    const store = createStore({ n: 1 });
    const fn = vi.fn();
    const unsubscribe = store.subscribe(fn);
    store.set({ n: 2 });
    store.set((prev) => ({ n: prev.n + 1 }));
    expect(store.get()).toEqual({ n: 3 });
    expect(fn).toHaveBeenCalledTimes(2);
    unsubscribe();
    store.set({ n: 4 });
    expect(fn).toHaveBeenCalledTimes(2);
  });

  it("does not notify when the value is the same reference", () => {
    const state = { n: 1 };
    const store = createStore(state);
    const fn = vi.fn();
    store.subscribe(fn);
    store.set(state);
    store.set((prev) => prev);
    expect(fn).not.toHaveBeenCalled();
  });

  it("a subscriber that unsubscribes during notification does not break the others", () => {
    const store = createStore(0);
    const second = vi.fn();
    const unsubscribe = store.subscribe(() => unsubscribe());
    store.subscribe(second);
    store.set(1);
    store.set(2);
    expect(second).toHaveBeenCalledTimes(2);
  });
});

interface State {
  user: { name: string };
  count: number;
}

function Name({ store, onRender }: { store: Store<State>; onRender(): void }): ReactElement {
  const user = useStore(store, (s) => s.user);
  onRender();
  return <p>{user.name}</p>;
}

describe("useStore", () => {
  it("re-renders on change of the selected slice only", () => {
    const store = createStore<State>({ user: { name: "ada" }, count: 0 });
    const onRender = vi.fn();
    render(<Name store={store} onRender={onRender} />);
    expect(screen.getByText("ada")).toBeTruthy();
    const renders = onRender.mock.calls.length;
    act(() => store.set((s) => ({ ...s, count: s.count + 1 })));
    expect(onRender.mock.calls.length).toBe(renders);
    act(() => store.set((s) => ({ ...s, user: { name: "grace" } })));
    expect(screen.getByText("grace")).toBeTruthy();
    expect(onRender.mock.calls.length).toBe(renders + 1);
  });
});
