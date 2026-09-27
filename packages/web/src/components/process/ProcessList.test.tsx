import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { createFakeApi } from "../../test/fakeApi";
import { renderApp } from "../../test/render";
import { invalidated, openProcessSpy, resetDoubles } from "../runs/testDoubles";
import { ProcessList } from "./ProcessList";
import { PROCESS_SEARCH_ID } from "./ProcessSearch";

vi.mock("../../state/query", () => import("../runs/testDoubles").then((m) => m.queryModule));
vi.mock("../../state/url", () => import("../runs/testDoubles").then((m) => m.urlModule));
vi.mock("../../state/openProcess", () => import("../runs/testDoubles").then((m) => m.openProcessModule));
vi.mock("../common/Dialog", () => import("../runs/testDoubles").then((m) => m.dialogModule));
vi.mock("../common/ErrorBox", () => import("../runs/testDoubles").then((m) => m.errorBoxModule));

beforeEach(() => resetDoubles());
afterEach(() => {
  vi.useRealTimers();
});

async function flush(): Promise<void> {
  await act(async () => {});
}

function row(id: string): HTMLElement {
  const button = screen.getAllByRole("button").find((b) => b.querySelector(".wo-row-id")?.textContent === id);
  if (button === undefined) throw new Error(`no row ${id}`);
  return button;
}

describe("ProcessList", () => {
  it("rows show independent badges; a process that fails to load shows its error", async () => {
    renderApp(<ProcessList />);
    await screen.findByText("process_supplier_invoice");
    const invoice = row("process_supplier_invoice");
    expect([...invoice.querySelectorAll(".wo-badge")].map((b) => b.textContent)).toEqual(["design", "released 9f8e7d6 · 3 behind"]);
    expect(within(invoice).getByText("design").getAttribute("title")).toContain("fix");
    expect([...row("finance/monthly_close").querySelectorAll(".wo-badge")].map((b) => b.textContent)).toEqual(["compiled", "built"]);
    expect(within(row("drafts/intake")).getByText(/mapping values are not allowed/)).toBeTruthy();
  });

  it("search is debounced 250 ms; chips filter at once; terms are marked", async () => {
    vi.useFakeTimers();
    const { api } = renderApp(<ProcessList />);
    await flush();
    expect(api.processes.list).toHaveBeenCalledTimes(1);
    expect(api.processes.list).toHaveBeenLastCalledWith("", []);

    const input = document.getElementById(PROCESS_SEARCH_ID) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "inv" } });
    fireEvent.change(input, { target: { value: "invoice" } });
    await act(async () => { vi.advanceTimersByTime(249); });
    expect(api.processes.list).toHaveBeenCalledTimes(1);
    await act(async () => { vi.advanceTimersByTime(1); });
    expect(api.processes.list).toHaveBeenCalledTimes(2);
    expect(api.processes.list).toHaveBeenLastCalledWith("invoice", []);

    const released = screen.getByRole("button", { name: "released" });
    fireEvent.click(released);
    await flush();
    expect(released.getAttribute("aria-pressed")).toBe("true");
    expect(api.processes.list).toHaveBeenLastCalledWith("invoice", ["released"]);
    fireEvent.click(screen.getByRole("button", { name: "design" }));
    await flush();
    expect(api.processes.list).toHaveBeenLastCalledWith("invoice", ["design", "released"]);

    const marks = [...document.querySelectorAll("mark")].map((m) => m.textContent?.toLowerCase());
    expect(marks.length).toBeGreaterThanOrEqual(4);          // id, goal, instruction snippet, monthly_close goal
    expect(new Set(marks)).toEqual(new Set(["invoice"]));
    expect(within(row("process_supplier_invoice")).getByText(/extract:/)).toBeTruthy();
  });

  it("clicking a row opens that process; the open process is marked current", async () => {
    renderApp(<ProcessList />, { url: { process: "finance/monthly_close" } });
    await screen.findByText("process_supplier_invoice");
    expect(row("finance/monthly_close").getAttribute("aria-current")).toBe("true");
    expect(row("process_supplier_invoice").getAttribute("aria-current")).toBeNull();
    fireEvent.click(row("process_supplier_invoice"));
    expect(openProcessSpy).toHaveBeenCalledWith("process_supplier_invoice");
  });

  it("New process: checks the id, creates, refreshes the list and opens it", async () => {
    const { api } = renderApp(<ProcessList />);
    await screen.findByText("process_supplier_invoice");
    fireEvent.click(screen.getByRole("button", { name: "+ New process" }));
    const dialog = screen.getByRole("dialog", { name: "New process" });
    const create = within(dialog).getByRole("button", { name: "Create" }) as HTMLButtonElement;
    const id = within(dialog).getByLabelText("Process id");
    expect(create.disabled).toBe(true);
    fireEvent.change(id, { target: { value: "finance//x" } });
    expect(id.getAttribute("aria-invalid")).toBe("true");
    expect(create.disabled).toBe(true);
    fireEvent.change(id, { target: { value: "finance/invoices" } });
    fireEvent.change(within(dialog).getByLabelText("Goal"), { target: { value: " Pay suppliers " } });
    expect(within(dialog).queryByLabelText("Process root")).toBeNull();   // one root in the meta fixture
    fireEvent.click(create);
    await flush();
    expect(api.processes.create).toHaveBeenCalledWith({ id: "finance/invoices", goal: "Pay suppliers", root: null });
    expect(invalidated).toContain("processes");
    expect(openProcessSpy).toHaveBeenCalledWith("finance/invoices");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("New process: a server error stays in the dialog", async () => {
    const api = createFakeApi({
      processes: { create: async () => { throw new ApiError(422, "invalid", "'status' is a reserved process name"); } },
    });
    renderApp(<ProcessList />, { api });
    fireEvent.click(screen.getByRole("button", { name: "+ New process" }));
    const dialog = screen.getByRole("dialog", { name: "New process" });
    fireEvent.change(within(dialog).getByLabelText("Process id"), { target: { value: "status" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    expect((await within(dialog).findByRole("alert")).textContent).toContain("reserved process name");
    expect(openProcessSpy).not.toHaveBeenCalled();
  });

  it("New process: several process roots offer a root select", async () => {
    const meta = (await createFakeApi().meta());
    meta.workspace.process_roots = ["processes", "legacy"];
    const { api } = renderApp(<ProcessList />, { meta });
    fireEvent.click(screen.getByRole("button", { name: "+ New process" }));
    const dialog = screen.getByRole("dialog", { name: "New process" });
    fireEvent.change(within(dialog).getByLabelText("Process id"), { target: { value: "old" } });
    fireEvent.change(within(dialog).getByLabelText("Process root"), { target: { value: "legacy" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await flush();
    expect(api.processes.create).toHaveBeenCalledWith({ id: "old", goal: null, root: "legacy" });
  });
});
