import { act, render, renderHook, screen } from "@testing-library/react";
import type { ReactElement } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { BACKOFF_MS, onReconnect, reportNetworkError, resetConnection, setHealthProbe, useConnection } from "./connection";
import { resetQueryCache, useQuery } from "./query";

function Meta({ fetcher }: { fetcher: () => Promise<string> }): ReactElement {
  return <p>{useQuery("meta", fetcher).data ?? "-"}</p>;
}

beforeEach(() => {
  vi.useFakeTimers();
  resetQueryCache();
});

afterEach(() => {
  resetConnection();
  vi.useRealTimers();
});

describe("connection", () => {
  it("is online until a network error, then probes with backoff 1 s, 2 s, 5 s, 10 s, 10 s", async () => {
    const probe = vi.fn(async () => {
      throw new Error("down");
    });
    setHealthProbe(probe);
    const { result } = renderHook(() => useConnection());
    expect(result.current).toEqual({ online: true, retryInMs: null });
    act(() => reportNetworkError());
    expect(result.current).toEqual({ online: false, retryInMs: 1000 });
    const seen: (number | null)[] = [];
    for (const ms of [1000, 2000, 5000, 10000]) {
      await act(() => vi.advanceTimersByTimeAsync(ms - 1));
      expect(probe).toHaveBeenCalledTimes(seen.length);
      await act(() => vi.advanceTimersByTimeAsync(1));
      seen.push(result.current.retryInMs);
    }
    expect(seen).toEqual([2000, 5000, 10000, 10000]);
    expect(BACKOFF_MS).toEqual([1000, 2000, 5000, 10000]);
  });

  it("repeated reports while offline do not restart the backoff", async () => {
    const probe = vi.fn(async () => {
      throw new Error("down");
    });
    setHealthProbe(probe);
    const { result } = renderHook(() => useConnection());
    act(() => reportNetworkError());
    await act(() => vi.advanceTimersByTimeAsync(1000));
    act(() => reportNetworkError());
    expect(result.current.retryInMs).toBe(2000);
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(probe).toHaveBeenCalledTimes(2);
  });

  it("recovers when the probe succeeds: online, mounted queries refetched, reconnect listeners called", async () => {
    let up = false;
    setHealthProbe(async () => {
      if (!up) throw new Error("down");
    });
    const meta = vi.fn(async () => (up ? "fresh" : "stale"));
    render(<Meta fetcher={meta} />);
    await act(async () => {});
    const reconnected = vi.fn();
    const unsubscribe = onReconnect(reconnected);
    const { result } = renderHook(() => useConnection());
    act(() => reportNetworkError());
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(result.current.online).toBe(false);
    up = true;
    await act(() => vi.advanceTimersByTimeAsync(2000));
    expect(result.current).toEqual({ online: true, retryInMs: null });
    expect(reconnected).toHaveBeenCalledOnce();
    expect(meta).toHaveBeenCalledTimes(2);
    expect(screen.getByText("fresh")).toBeTruthy();
    unsubscribe();
    act(() => reportNetworkError());
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(reconnected).toHaveBeenCalledOnce();
  });

  it("the default probe is GET /api/health", async () => {
    const fetchMock = vi.fn(async () => new Response('{"ok":true}', { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    try {
      const { result } = renderHook(() => useConnection());
      act(() => reportNetworkError());
      await act(() => vi.advanceTimersByTimeAsync(1000));
      expect(fetchMock).toHaveBeenCalledWith("/api/health", { headers: { Accept: "application/json" } });
      expect(result.current.online).toBe(true);
    } finally {
      vi.unstubAllGlobals();
    }
  });
});
