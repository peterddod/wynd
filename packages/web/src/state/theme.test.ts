import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { THEME_KEY, applyTheme, nextTheme, readTheme, setTheme, useTheme } from "./theme";

afterEach(() => {
  setTheme("system");
  vi.restoreAllMocks();
});

describe("theme", () => {
  it("reads the stored theme; anything else is system", () => {
    expect(readTheme()).toBe("system");
    localStorage.setItem(THEME_KEY, "dark");
    expect(readTheme()).toBe("dark");
    localStorage.setItem(THEME_KEY, "light");
    expect(readTheme()).toBe("light");
    localStorage.setItem(THEME_KEY, "purple");
    expect(readTheme()).toBe("system");
  });

  it("storage that throws means system, and setting still applies the theme", () => {
    const setItem = vi.spyOn(localStorage, "setItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    vi.spyOn(localStorage, "getItem").mockImplementation(() => {
      throw new DOMException("denied", "SecurityError");
    });
    expect(readTheme()).toBe("system");
    expect(() => setTheme("dark")).not.toThrow();
    expect(setItem).toHaveBeenCalled();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("applyTheme sets data-theme, and removes it for system", () => {
    applyTheme("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    applyTheme("system");
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("cycles system -> light -> dark -> system", () => {
    expect(nextTheme("system")).toBe("light");
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("system");
  });

  it("useTheme: the setter stores, applies and re-renders every user", () => {
    const a = renderHook(() => useTheme());
    const b = renderHook(() => useTheme());
    expect(a.result.current[0]).toBe("system");
    act(() => a.result.current[1]("dark"));
    expect(b.result.current[0]).toBe("dark");
    expect(localStorage.getItem(THEME_KEY)).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    act(() => b.result.current[1]("system"));
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });
});
