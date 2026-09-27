import { act, render, renderHook, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { resetConnection, useConnection } from "./connection";
import { getQueryData, invalidate, resetQueryCache, setQueryData, useQuery, type QueryOpts } from "./query";

/** A fetcher whose calls resolve only when the test says so. */
function deferred<T>() {
  const pending: { resolve(v: T): void; reject(e: unknown): void }[] = [];
  const fn = vi.fn(() => new Promise<T>((resolve, reject) => pending.push({ resolve, reject })));
  return {
    fn,
    async resolve(value: T): Promise<void> {
      await act(async () => pending.shift()?.resolve(value));
    },
    async reject(err: unknown): Promise<void> {
      await act(async () => pending.shift()?.reject(err));
    },
  };
}

function Show({ k, fetcher, opts }: { k: string; fetcher: () => Promise<string>; opts?: QueryOpts<string> }): ReactElement {
  const q = useQuery(k, fetcher, opts);
  return <p data-testid={k}>{q.loading ? "loading" : ""}|{q.data ?? "-"}|{q.error?.code ?? ""}</p>;
}

function text(key: string): string {
  return screen.getByTestId(key).textContent ?? "";
}

async function flush(): Promise<void> {
  await act(async () => {});
}

beforeEach(() => {
  resetQueryCache();
});

afterEach(() => {
  vi.useRealTimers();
  resetConnection();
});

describe("useQuery", () => {
  it("loads, then shows the data", async () => {
    const d = deferred<string>();
    render(<Show k="meta" fetcher={d.fn} />);
    expect(text("meta")).toBe("loading|-|");
    await d.resolve("m1");
    expect(text("meta")).toBe("|m1|");
    expect(getQueryData("meta")).toBe("m1");
  });

  it("de-duplicates requests for one key across components", async () => {
    const d = deferred<string>();
    render(
      <>
        <Show k="steps" fetcher={d.fn} />
        <div>
          <Show k="steps" fetcher={d.fn} />
        </div>
      </>,
    );
    expect(d.fn).toHaveBeenCalledOnce();
    await d.resolve("s");
    expect(screen.getAllByText("|s|")).toHaveLength(2);
  });

  it("does not fetch while disabled; fetches once enabled", async () => {
    const d = deferred<string>();
    const view = render(<Show k="iface:p" fetcher={d.fn} opts={{ enabled: false }} />);
    expect(d.fn).not.toHaveBeenCalled();
    expect(text("iface:p")).toBe("|-|");
    view.rerender(<Show k="iface:p" fetcher={d.fn} opts={{ enabled: true }} />);
    expect(d.fn).toHaveBeenCalledOnce();
    await d.resolve("i");
    expect(text("iface:p")).toBe("|i|");
  });

  it("an error is an ApiError; reload retries with the latest fetcher", async () => {
    const d = deferred<string>();
    const { result } = renderHook(() => useQuery("builds:p", d.fn));
    await d.reject(new ApiError(500, "internal", "boom"));
    expect(result.current.error).toMatchObject({ status: 500, code: "internal" });
    expect(result.current.loading).toBe(false);
    const reloaded = act(() => result.current.reload());
    await d.resolve("b");
    await reloaded;
    expect(result.current).toMatchObject({ data: "b", error: null, loading: false });
  });

  it("a non-ApiError rejection becomes client_error (status -1) and never takes the connection offline", async () => {
    const connection = renderHook(() => useConnection());
    const { result } = renderHook(() => useQuery("x", () => Promise.reject(new Error("not implemented"))));
    await flush();
    expect(result.current.error).toMatchObject({ status: -1, code: "client_error", message: "not implemented" });
    expect(connection.result.current.online).toBe(true);
  });

  it("a network failure reports the connection offline", async () => {
    const connection = renderHook(() => useConnection());
    renderHook(() => useQuery("y", () => Promise.reject(new ApiError(0, "network", "down"))));
    await flush();
    expect(connection.result.current.online).toBe(false);
  });

  it("setQueryData data is shown without a request; getQueryData reads it", async () => {
    setQueryData("job:j1", "preset");
    const fetcher = vi.fn(async () => "fetched");
    render(<Show k="job:j1" fetcher={fetcher} />);
    expect(text("job:j1")).toBe("|preset|");
    expect(fetcher).not.toHaveBeenCalled();
    act(() => setQueryData("job:j1", "pushed"));
    expect(text("job:j1")).toBe("|pushed|");
    expect(getQueryData("job:j1")).toBe("pushed");
    expect(getQueryData("job:other")).toBeUndefined();
  });

  it("a remount shows the cached data and refetches it", async () => {
    let n = 0;
    const fetcher = vi.fn(async () => `v${++n}`);
    const first = render(<Show k="runs:p" fetcher={fetcher} />);
    await flush();
    expect(text("runs:p")).toBe("|v1|");
    first.unmount();
    render(<Show k="runs:p" fetcher={fetcher} />);
    expect(text("runs:p")).toBe("loading|v1|");
    await flush();
    expect(text("runs:p")).toBe("|v2|");
  });

  it("the response of a request abandoned by unmounting is dropped", async () => {
    const d = deferred<string>();
    const first = render(<Show k="chats" fetcher={d.fn} />);
    first.unmount();
    const again = deferred<string>();
    render(<Show k="chats" fetcher={again.fn} />);
    expect(again.fn).toHaveBeenCalledOnce();
    await d.resolve("stale");
    expect(text("chats")).toBe("loading|-|");
    await again.resolve("fresh");
    expect(text("chats")).toBe("|fresh|");
  });
});

describe("invalidate", () => {
  it("refetches mounted queries under the prefix and leaves the others alone", async () => {
    const list = vi.fn(async () => "list");
    const one = vi.fn(async () => "one");
    const meta = vi.fn(async () => "meta");
    render(
      <>
        <Show k="processes?q=inv" fetcher={list} />
        <Show k="processes:p" fetcher={one} />
        <Show k="meta" fetcher={meta} />
      </>,
    );
    await flush();
    act(() => invalidate("processes"));
    await flush();
    expect(list).toHaveBeenCalledTimes(2);
    expect(one).toHaveBeenCalledTimes(2);
    expect(meta).toHaveBeenCalledTimes(1);
    act(() => invalidate(""));
    await flush();
    expect(meta).toHaveBeenCalledTimes(2);
  });

  it("an unmounted query becomes stale: refetched when next mounted", async () => {
    setQueryData("builds:p", "old");
    act(() => invalidate("builds"));
    const fetcher = vi.fn(async () => "new");
    render(<Show k="builds:p" fetcher={fetcher} />);
    expect(text("builds:p")).toBe("loading|old|");
    await flush();
    expect(fetcher).toHaveBeenCalledOnce();
    expect(text("builds:p")).toBe("|new|");
  });

  it("while a request is in flight: one more request after it settles", async () => {
    const d = deferred<string>();
    render(<Show k="jobs:active" fetcher={d.fn} />);
    act(() => invalidate("jobs"));
    expect(d.fn).toHaveBeenCalledOnce();
    await d.resolve("before");
    expect(d.fn).toHaveBeenCalledTimes(2);
    await d.resolve("after");
    expect(text("jobs:active")).toBe("|after|");
  });
});

describe("polling", () => {
  it("polls at pollMs and stops when pollMs(data) returns null", async () => {
    vi.useFakeTimers();
    const statuses = ["running", "running", "succeeded"];
    const fetcher = vi.fn(async () => statuses.shift() ?? "succeeded");
    const pollMs = (s: string | undefined): number | null => (s === "succeeded" ? null : 1000);
    render(<Show k="job:j" fetcher={fetcher} opts={{ pollMs }} />);
    await flush();
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(999));
    expect(fetcher).toHaveBeenCalledTimes(1);
    await act(() => vi.advanceTimersByTimeAsync(1));
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(fetcher).toHaveBeenCalledTimes(3);
    expect(text("job:j")).toBe("|succeeded|");
    await act(() => vi.advanceTimersByTimeAsync(10_000));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });

  it("the shortest interval of the mounted queries wins; unmounting stops the timer", async () => {
    vi.useFakeTimers();
    const fetcher = vi.fn(async () => "d");
    const view = render(
      <>
        <Show k="jobs" fetcher={fetcher} opts={{ pollMs: 15_000 }} />
        <Show k="jobs" fetcher={fetcher} opts={{ pollMs: 2000 }} />
      </>,
    );
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(fetcher).toHaveBeenCalledTimes(2);
    view.unmount();
    await act(() => vi.advanceTimersByTimeAsync(60_000));
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("pauses while the document is hidden and resumes when it is visible again", async () => {
    vi.useFakeTimers();
    const hidden = vi.spyOn(document, "hidden", "get").mockReturnValue(true);
    const fetcher = vi.fn(async () => "d");
    render(<Show k="runs" fetcher={fetcher} opts={{ pollMs: 3000 }} />);
    await flush();
    await act(() => vi.advanceTimersByTimeAsync(30_000));
    expect(fetcher).toHaveBeenCalledTimes(1);
    hidden.mockReturnValue(false);
    await act(async () => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
    await act(() => vi.advanceTimersByTimeAsync(3000));
    expect(fetcher).toHaveBeenCalledTimes(3);
  });
});

describe("focus", () => {
  it("refetches mounted queries on window focus unless refetchOnFocus is false", async () => {
    const a = vi.fn(async () => "a");
    const b = vi.fn(async () => "b");
    render(
      <>
        <Show k="a" fetcher={a} />
        <Show k="b" fetcher={b} opts={{ refetchOnFocus: false }} />
      </>,
    );
    await flush();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    expect(a).toHaveBeenCalledTimes(2);
    expect(b).toHaveBeenCalledTimes(1);
  });
});
