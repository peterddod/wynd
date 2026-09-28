import { act, fireEvent, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { SaveRequest } from "../../api/types";
import { updateBranch } from "../../model/processDoc";
import { createDesignSession, type DesignSession } from "../../state/design";
import { createFakeApi, type FakeApi } from "../../test/fakeApi";
import { renderApp } from "../../test/render";
import { Dialog } from "../common/Dialog";
import { ConflictBanner } from "./ConflictBanner";
import { DesignSurface } from "./DesignSurface";
import { resetDoubles } from "./testDoubles";

vi.mock("../../state/store", () => import("./testDoubles"));
vi.mock("../../state/query", () => import("./testDoubles"));
vi.mock("../../state/url", () => import("./testDoubles"));
vi.mock("../../state/toasts", () => import("./testDoubles"));
vi.mock("../../state/connection", () => import("./testDoubles"));
vi.mock("../../state/theme", () => import("./testDoubles"));
vi.mock("../common/Dialog", () => import("./testDoubles"));

const PID = "process_supplier_invoice";

async function open(api: FakeApi = createFakeApi()): Promise<{ api: FakeApi; design: DesignSession }> {
  const design = createDesignSession(api);
  await design.open(PID);
  return { api, design };
}

function edit(design: DesignSession): void {
  act(() => design.apply("edit branch validate.done[1]", (d) => ({ ...d, process: updateBranch(d.process, 3, 1, { when: "x" }) })));
}

function commits(api: FakeApi): string[] {
  return api.calls.filter((c) => c.method === "processes.save")
    .map((c) => (c.args[1] as SaveRequest).commit?.reason ?? "none");
}

function renderSurface(api: FakeApi, design: DesignSession): void {
  renderApp(
    <>
      <DesignSurface>
        <ConflictBanner />
        <input aria-label="first" />
        <input aria-label="second" />
        <Dialog open title="Inner dialog" onClose={() => undefined}><input aria-label="in dialog" /></Dialog>
        <span>plain text</span>
      </DesignSurface>
      <button type="button">outside</button>
    </>,
    { api, design },
  );
}

beforeEach(() => {
  resetDoubles();
});

describe("DesignSurface", () => {
  it("does not commit when focus moves between inputs inside", async () => {
    const { api, design } = await open();
    renderSurface(api, design);
    edit(design);
    fireEvent.focusOut(screen.getByLabelText("first"), { relatedTarget: screen.getByLabelText("second") });
    fireEvent.focusOut(screen.getByLabelText("second"), { relatedTarget: screen.getByLabelText("in dialog") });
    await act(async () => undefined);
    expect(commits(api)).toEqual([]);
  });

  it("commits once with reason blur when focus leaves the surface", async () => {
    const { api, design } = await open();
    renderSurface(api, design);
    edit(design);
    fireEvent.focusOut(screen.getByLabelText("first"), { relatedTarget: screen.getByRole("button", { name: "outside" }) });
    await act(async () => undefined);
    expect(commits(api)).toEqual(["blur"]);
    fireEvent.focusOut(screen.getByLabelText("first"), { relatedTarget: null });
    await act(async () => undefined);
    expect(commits(api)).toEqual(["blur"]);
  });

  it("commits with reason hidden when the tab goes hidden", async () => {
    const { api, design } = await open();
    renderSurface(api, design);
    edit(design);
    Object.defineProperty(document, "visibilityState", { value: "hidden", configurable: true });
    try {
      document.dispatchEvent(new Event("visibilitychange"));
      await act(async () => undefined);
    } finally {
      Object.defineProperty(document, "visibilityState", { value: "visible", configurable: true });
    }
    expect(commits(api)).toEqual(["hidden"]);
  });

  it("sends a keepalive unload commit on pagehide", async () => {
    const { api, design } = await open();
    renderSurface(api, design);
    edit(design);
    window.dispatchEvent(new Event("pagehide"));
    await act(async () => undefined);
    const call = api.calls.find((c) => c.method === "processes.save")!;
    expect((call.args[1] as SaveRequest).commit?.reason).toBe("unload");
    expect(call.args[2]).toEqual({ keepalive: true });
  });

  it("focuses itself on a click on non-focusable content", async () => {
    const { api, design } = await open();
    renderSurface(api, design);
    fireEvent.pointerDown(screen.getByText("plain text"));
    expect(document.activeElement?.classList.contains("wg-design-surface")).toBe(true);
  });

  it("shows the lock notice and the conflict banner", async () => {
    const api = createFakeApi();
    vi.mocked(api.processes.save).mockRejectedValueOnce(
      new ApiError(409, "revision_conflict", "changed", { path: "processes/process_supplier_invoice/process.yaml" }));
    const { design } = await open(api);
    renderSurface(api, design);
    act(() => design.lock({ chat_id: "c", turn_id: "t" }));
    expect(screen.getByRole("status").textContent).toContain("Assistant is working on this process");
    act(() => design.unlock("t"));
    edit(design);
    await act(() => design.flush());
    expect(screen.getByRole("alert").textContent).toContain("processes/process_supplier_invoice/process.yaml changed outside the editor.");
    fireEvent.click(screen.getByRole("button", { name: "Use the version on disk" }));
    await act(async () => undefined);
    expect(screen.queryByRole("alert")).toBeNull();
    expect(design.store.get()!.saveState).toBe("clean");
  });
});
