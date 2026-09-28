import { act, renderHook } from "@testing-library/react";
import type { ReactNode } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { DesignContext } from "../api/context";
import { resetDoubles, toasts } from "../components/graph/testDoubles";
import { updateBranch } from "../model/processDoc";
import { createFakeApi } from "../test/fakeApi";
import { createDesignSession } from "./design";
import { openProcess, showSwitchBlocked, SWITCH_BLOCKED_CLASS, useOpenProcess } from "./openProcess";

vi.mock("./store", () => import("../components/graph/testDoubles"));
vi.mock("./query", () => import("../components/graph/testDoubles"));
vi.mock("./connection", () => import("../components/graph/testDoubles"));
vi.mock("./toasts", () => import("../components/graph/testDoubles"));
vi.mock("./url", () => import("../components/graph/testDoubles"));

const PID = "process_supplier_invoice";

beforeEach(() => {
  resetDoubles();
  window.history.replaceState(null, "", "/?process=other&sel=s:x&run=run_1&tab=runs");
  document.body.innerHTML = "";
});

describe("openProcess", () => {
  it("commits the current process before switching, then opens the next", async () => {
    const api = createFakeApi();
    const design = createDesignSession(api);
    await design.open(PID);
    design.apply("edit", (d) => ({ ...d, process: updateBranch(d.process, 3, 1, { when: "x" }) }));
    const setUrl = vi.fn();
    await openProcess({ design, setUrl }, "other");
    const methods = api.calls.map((c) => c.method);
    expect(methods.slice(1)).toEqual(["processes.save", "processes.design"]);
    expect((api.calls[1]!.args[1] as { commit: { reason: string } }).commit.reason).toBe("process_switch");
    expect(setUrl).toHaveBeenCalledWith({ process: "other", sel: null, run: null }, "push");
  });

  it("does not commit when re-opening the same process and closes on null", async () => {
    const api = createFakeApi();
    const design = createDesignSession(api);
    await design.open(PID);
    const setUrl = vi.fn();
    await openProcess({ design, setUrl }, PID);
    expect(api.calls.filter((c) => c.method === "processes.save")).toHaveLength(0);
    await openProcess({ design, setUrl }, null);
    expect(design.store.get()).toBeNull();
    expect(setUrl).toHaveBeenLastCalledWith({ process: null, sel: null, run: null }, "push");
  });

  it("toasts and clears the URL when the process no longer exists", async () => {
    const design = createDesignSession(createFakeApi());
    const setUrl = vi.fn();
    await openProcess({ design, setUrl }, "gone");
    expect(toasts.map((t) => t.text)).toEqual(["Process gone no longer exists"]);
    expect(setUrl).toHaveBeenLastCalledWith({ process: null, sel: null, run: null }, "replace");
  });

  it("blocks the switch when the commit fails, offering retry and discard", async () => {
    const api = createFakeApi();
    vi.mocked(api.processes.save).mockRejectedValueOnce(new ApiError(500, "internal", "disk full"));
    const design = createDesignSession(api);
    await design.open(PID);
    design.apply("edit", (d) => ({ ...d, process: updateBranch(d.process, 3, 1, { when: "x" }) }));
    const setUrl = vi.fn();
    await openProcess({ design, setUrl }, "other");
    expect(setUrl).not.toHaveBeenCalled();
    const dialog = document.querySelector(`dialog.${SWITCH_BLOCKED_CLASS}`)!;
    expect(dialog.textContent).toContain("disk full");
    const discard = [...dialog.querySelectorAll("button")].find((b) => b.textContent === "Discard my changes and switch")!;
    discard.click();
    await vi.waitFor(() => expect(setUrl).toHaveBeenCalledWith({ process: "other", sel: null, run: null }, "push"));
    expect(document.querySelector(`dialog.${SWITCH_BLOCKED_CLASS}`)).toBeNull();
    expect(api.calls.filter((c) => c.method === "processes.save")).toHaveLength(1);
  });

  it("retries through the dialog", () => {
    const retry = vi.fn();
    showSwitchBlocked(new Error("x"), retry, vi.fn());
    const button = [...document.querySelectorAll(`dialog.${SWITCH_BLOCKED_CLASS} button`)].find((b) => b.textContent === "Retry")!;
    (button as HTMLButtonElement).click();
    expect(retry).toHaveBeenCalledOnce();
  });
});

describe("useOpenProcess", () => {
  it("binds the session and the URL state", async () => {
    const design = createDesignSession(createFakeApi());
    const wrapper = ({ children }: { children: ReactNode }) => <DesignContext.Provider value={design}>{children}</DesignContext.Provider>;
    const { result } = renderHook(() => useOpenProcess(), { wrapper });
    await act(() => result.current(PID));
    expect(new URLSearchParams(window.location.search).get("process")).toBe(PID);
    expect(new URLSearchParams(window.location.search).get("sel")).toBeNull();
    expect(design.store.get()!.processId).toBe(PID);
  });
});
