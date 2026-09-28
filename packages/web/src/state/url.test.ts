import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";
import { formatUrl, readUrl, setUrl, useUrlState } from "./url";

beforeEach(() => {
  window.history.replaceState(null, "", "/");
});

describe("readUrl", () => {
  it("defaults: nothing open, graph tab", () => {
    expect(readUrl("")).toEqual({ process: null, chat: null, tab: "graph", sel: null, run: null });
  });

  it("parses every field; an unknown tab falls back to graph; empty values are null", () => {
    expect(readUrl("?process=finance%2Finvoices&chat=c1&tab=runs&sel=b%3A3%3A1&run=r1")).toEqual({
      process: "finance/invoices", chat: "c1", tab: "runs", sel: "b:3:1", run: "r1",
    });
    expect(readUrl("?tab=nope&process=")).toMatchObject({ tab: "graph", process: null });
  });

  it("reads window.location by default", () => {
    window.history.replaceState(null, "", "/?process=p&tab=yaml");
    expect(readUrl()).toMatchObject({ process: "p", tab: "yaml" });
  });
});

describe("formatUrl", () => {
  it("writes fields in order, omitting nulls and the default tab; round-trips through readUrl", () => {
    const state = { process: "finance/invoices", chat: null, tab: "releases" as const, sel: null, run: "r 1" };
    expect(formatUrl(state)).toBe("?process=finance%2Finvoices&tab=releases&run=r+1");
    expect(readUrl(formatUrl(state))).toEqual(state);
    expect(formatUrl(readUrl(""))).toBe("");
  });
});

describe("setUrl", () => {
  it("pushes when process or chat changes and replaces otherwise", () => {
    const start = window.history.length;
    setUrl({ process: "p1" });
    expect(window.history.length).toBe(start + 1);
    setUrl({ tab: "runs", run: "r1" });
    setUrl({ sel: "s:read" });
    expect(window.history.length).toBe(start + 1);
    expect(window.location.search).toBe("?process=p1&tab=runs&sel=s%3Aread&run=r1");
    setUrl({ chat: "c1" });
    expect(window.history.length).toBe(start + 2);
  });

  it("mode overrides the default; undefined leaves a field alone and null clears it", () => {
    const start = window.history.length;
    setUrl({ process: "p1", run: "r1" }, "replace");
    expect(window.history.length).toBe(start);
    setUrl({ tab: "runs" }, "push");
    expect(window.history.length).toBe(start + 1);
    setUrl({ process: "p2", sel: undefined, run: null });
    expect(readUrl()).toEqual({ process: "p2", chat: null, tab: "runs", sel: null, run: null });
  });

  it("does not push a duplicate history entry", () => {
    setUrl({ process: "p1" });
    const length = window.history.length;
    setUrl({ process: "p1" }, "push");
    expect(window.history.length).toBe(length);
  });
});

describe("useUrlState", () => {
  it("every caller sees setUrl changes and back/forward navigation", () => {
    const a = renderHook(() => useUrlState());
    const b = renderHook(() => useUrlState());
    act(() => a.result.current[1]({ process: "p1", tab: "runs" }));
    expect(b.result.current[0]).toMatchObject({ process: "p1", tab: "runs" });
    act(() => {
      window.history.replaceState(null, "", "/?process=p2");
      window.dispatchEvent(new PopStateEvent("popstate"));
    });
    expect(a.result.current[0]).toMatchObject({ process: "p2", tab: "graph" });
  });

  it("returns the same state object until the URL changes", () => {
    const { result, rerender } = renderHook(() => useUrlState());
    const first = result.current[0];
    rerender();
    expect(result.current[0]).toBe(first);
  });
});
