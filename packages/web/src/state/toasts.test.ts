import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MAX_TOASTS, TOAST_MS, clearToasts, dismissToast, toast, useToasts } from "./toasts";

beforeEach(() => {
  vi.useFakeTimers();
  clearToasts();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("toasts", () => {
  it("adds toasts with unique ids, level info by default, and dismisses by id", () => {
    const { result } = renderHook(() => useToasts());
    const run = vi.fn();
    let a = 0;
    let b = 0;
    act(() => {
      a = toast("Saved");
      b = toast("Job failed", { level: "error", action: { label: "Open chat", run } });
    });
    expect(a).not.toBe(b);
    expect(result.current).toEqual([
      { id: a, level: "info", text: "Saved", action: null },
      { id: b, level: "error", text: "Job failed", action: { label: "Open chat", run } },
    ]);
    act(() => dismissToast(a));
    expect(result.current.map((t) => t.id)).toEqual([b]);
  });

  it("info and warnings disappear after TOAST_MS; errors stay", () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      toast("info");
      toast("warning", { level: "warning" });
      toast("error", { level: "error" });
    });
    act(() => vi.advanceTimersByTime(TOAST_MS - 1));
    expect(result.current).toHaveLength(3);
    act(() => vi.advanceTimersByTime(1));
    expect(result.current.map((t) => t.text)).toEqual(["error"]);
  });

  it(`keeps the newest ${MAX_TOASTS}`, () => {
    const { result } = renderHook(() => useToasts());
    act(() => {
      for (let i = 1; i <= MAX_TOASTS + 2; i++) toast(`t${i}`, { level: "error" });
    });
    expect(result.current.map((t) => t.text)).toEqual(["t3", "t4", "t5", "t6", "t7"]);
  });
});
