import { afterEach, describe, expect, it, vi } from "vitest";
import { clearOverrides, layoutKey, loadOverrides, renameOverride, saveOverride } from "./positions";

const ROOT = "/home/dev/invoices";
const PID = "finance/invoices";

afterEach(() => {
  vi.restoreAllMocks();
});

describe("position overrides", () => {
  it("saves, migrates on rename and clears per workspace and process", () => {
    expect(layoutKey(ROOT, PID)).toBe("wynd.layout./home/dev/invoices.finance/invoices");
    saveOverride(ROOT, PID, "s:read", { x: 10, y: 20 });
    saveOverride(ROOT, PID, "x:done", { x: 1, y: 2 });
    expect(loadOverrides(ROOT, PID)).toEqual({ "s:read": { x: 10, y: 20 }, "x:done": { x: 1, y: 2 } });
    expect(loadOverrides(ROOT, "other")).toEqual({});
    renameOverride(ROOT, PID, "read", "load");
    expect(loadOverrides(ROOT, PID)).toEqual({ "x:done": { x: 1, y: 2 }, "s:load": { x: 10, y: 20 } });
    clearOverrides(ROOT, PID);
    expect(window.localStorage.getItem(layoutKey(ROOT, PID))).toBeNull();
  });

  it("ignores malformed data and unavailable storage", () => {
    window.localStorage.setItem(layoutKey(ROOT, PID), '{"s:a": {"x": "no"}, "s:b": {"x": 1, "y": 2}}');
    expect(loadOverrides(ROOT, PID)).toEqual({ "s:b": { x: 1, y: 2 } });
    window.localStorage.setItem(layoutKey(ROOT, PID), "not json");
    expect(loadOverrides(ROOT, PID)).toEqual({});
    vi.spyOn(window.localStorage, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(window.localStorage, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    expect(loadOverrides(ROOT, PID)).toEqual({});
    expect(() => saveOverride(ROOT, PID, "s:a", { x: 0, y: 0 })).not.toThrow();
  });
});
