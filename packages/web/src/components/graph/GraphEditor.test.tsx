import { act, fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { JsonObject } from "../../api/types";
import { getIn } from "../../model/json";
import { edgesOf, stepNames } from "../../model/processDoc";
import { createDesignSession, type DesignSession } from "../../state/design";
import { createFakeApi, type FakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { Inspector } from "../inspector/Inspector";
import { GraphEditor, handleConnect } from "./GraphEditor";
import { resetDoubles } from "./testDoubles";

vi.mock("../../state/store", () => import("./testDoubles"));
vi.mock("../../state/query", () => import("./testDoubles"));
vi.mock("../../state/url", () => import("./testDoubles"));
vi.mock("../../state/toasts", () => import("./testDoubles"));
vi.mock("../../state/connection", () => import("./testDoubles"));
vi.mock("../../state/theme", () => import("./testDoubles"));
vi.mock("../common/Dialog", () => import("./testDoubles"));

const PID = "process_supplier_invoice";
const FIX_PROTO = "processes/process_supplier_invoice/proto/fix_fields.yaml";

async function open(api: FakeApi = createFakeApi()): Promise<{ api: FakeApi; design: DesignSession }> {
  const design = createDesignSession(api);
  await design.open(PID);
  return { api, design };
}

function sel(): string | null {
  return new URLSearchParams(window.location.search).get("sel");
}

beforeEach(() => {
  resetDoubles();
});

describe("GraphEditor", () => {
  it("renders the dogfood process", async () => {
    const { api, design } = await open();
    renderApp(<GraphEditor />, { api, design, url: { process: PID } });
    for (const name of ["read", "extract", "validate", "fix", "save", "escalate"]) expect(screen.getByText(name)).toBeTruthy();
    expect(screen.getByText("$exit.needs_review")).toBeTruthy();
    expect(screen.getByLabelText("Step extract, agentic, compiled, exits: done, not_an_invoice, error")).toBeTruthy();
    expect(screen.getByLabelText("Process inputs: pdf_path")).toBeTruthy();
  });

  it("opens the edge inspector with three branch cards for a selected validate.done branch", async () => {
    const { api, design } = await open();
    renderApp(<><GraphEditor /><Inspector /></>, { api, design, url: { process: PID, sel: "b:3:0" } });
    const inspector = screen.getByRole("region", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { name: "validate · done" })).toBeTruthy();
    const branches = within(within(inspector).getByRole("list", { name: "Branches" })).getAllByRole("listitem")
      .filter((li) => li.classList.contains("wg-branch-card"));
    expect(branches).toHaveLength(3);
    expect(within(inspector).getByRole("listitem", { name: "Branch 1" })).toBeTruthy();
  });

  it("hides connect handles while an assistant turn holds the lock", async () => {
    const { api, design } = await open();
    const { container } = renderApp(<GraphEditor />, { api, design, url: { process: PID } });
    expect(container.querySelectorAll(".react-flow__handle.connectable").length).toBeGreaterThan(0);
    act(() => design.lock({ chat_id: "chat_1", turn_id: "turn_1" }));
    expect(container.querySelectorAll(".react-flow__handle.connectable")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "+ Step" })).toHaveProperty("disabled", true);
  });

  it("removes a selected step through the confirm dialog, offering to delete its unshared proto", async () => {
    const { api, design } = await open();
    renderApp(<GraphEditor />, { api, design, url: { process: PID, sel: "s:fix" } });
    fireEvent.keyDown(document.body, { key: "Delete" });
    const dialog = await screen.findByRole("dialog", { name: "Remove step fix" });
    expect(within(dialog).getByText("Remove step fix and its edges?")).toBeTruthy();
    const box = within(dialog).getByRole("checkbox") as HTMLInputElement;
    expect(box.checked).toBe(true);
    expect(dialog.textContent).toContain(FIX_PROTO);
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    const s = design.store.get()!;
    expect(stepNames(s.process.doc)).not.toContain("fix");
    expect(edgesOf(s.process.doc).map((e) => e.from)).not.toContain("fix.done");
    expect(s.protos[FIX_PROTO]!.deleted).toBe(true);
    expect(s.opLabels).toEqual(["remove step fix"]);
    expect(sel()).toBeNull();
  });

  it("does not offer to delete a proto another process shares", async () => {
    const api = createFakeApi();
    const design = fixture("design");
    design.steps.fix!.used_by = [PID, "finance/other"];
    vi.mocked(api.processes.design).mockResolvedValue(design);
    const opened = await open(api);
    renderApp(<GraphEditor />, { api, design: opened.design, url: { process: PID, sel: "s:fix" } });
    fireEvent.keyDown(document.body, { key: "Delete" });
    const dialog = await screen.findByRole("dialog", { name: "Remove step fix" });
    expect(within(dialog).queryByRole("checkbox")).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Remove" }));
    expect(opened.design.store.get()!.protos[FIX_PROTO]!.deleted).toBeUndefined();
  });

  it("adds a new proto-step from the toolbar", async () => {
    const { api, design } = await open();
    renderApp(<GraphEditor />, { api, design, url: { process: PID } });
    fireEvent.click(screen.getByRole("button", { name: "+ Step" }));
    const dialog = screen.getByRole("dialog", { name: "Add a step" });
    fireEvent.change(within(dialog).getByLabelText("Step name"), { target: { value: "notify" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add step" }));
    const s = design.store.get()!;
    expect(getIn(s.process.doc, ["steps", "notify"])).toEqual({ use: "./steps/notify" });
    expect(s.protos["processes/process_supplier_invoice/proto/notify.yaml"]!.doc).toEqual({
      kind: "proto_step", name: "notify", instruction: "", exits: ["done"], examples: [],
    });
    expect(sel()).toBe("s:notify");
    expect(screen.getByText("notify")).toBeTruthy();
  });

  it("rejects an invalid or duplicate step name", async () => {
    const { api, design } = await open();
    renderApp(<GraphEditor />, { api, design, url: { process: PID } });
    fireEvent.click(screen.getByRole("button", { name: "+ Step" }));
    const dialog = screen.getByRole("dialog", { name: "Add a step" });
    fireEvent.change(within(dialog).getByLabelText("Step name"), { target: { value: "fix" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Add step" }));
    expect(dialog.textContent).toContain('A step named "fix" already exists.');
    expect(stepNames(design.processDoc())).toHaveLength(6);
  });

  it("adds an existing step-root step from the catalog", async () => {
    const { api, design } = await open();
    renderApp(<GraphEditor />, { api, design, url: { process: PID } });
    fireEvent.click(screen.getByRole("button", { name: "+ Step" }));
    const dialog = screen.getByRole("dialog", { name: "Add a step" });
    fireEvent.change(within(dialog).getByLabelText("Step name"), { target: { value: "shared_step" } });
    fireEvent.click(within(dialog).getByLabelText("Existing step"));
    const first = fixture("stepsCatalog")[0]!;
    const option = await within(dialog).findByText(first.use);
    fireEvent.click(option.closest("label")!.querySelector("input")!);
    fireEvent.click(within(dialog).getByRole("button", { name: "Add step" }));
    expect(getIn(design.processDoc(), ["steps", "shared_step"])).toEqual({ use: first.use });
    expect(Object.keys(design.store.get()!.protos)).toHaveLength(6);
  });

  it("renames a step on double-click and reports the rewritten references", async () => {
    const { api, design } = await open();
    renderApp(<GraphEditor />, { api, design, url: { process: PID, sel: "s:validate" } });
    fireEvent.doubleClick(screen.getByLabelText(/^Step validate,/));
    const dialog = screen.getByRole("dialog", { name: "Rename step validate" });
    fireEvent.change(within(dialog).getByLabelText("New name"), { target: { value: "check" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Rename" }));
    expect(within(dialog).getByRole("status").textContent).toContain("Updated 8 references.");
    expect(stepNames(design.processDoc())).toContain("check");
    expect(getIn(design.processDoc(), ["edges", 3, "from"])).toBe("check.done");
    expect(sel()).toBe("s:check");
  });

  it("routes an exit through the connect hook", async () => {
    const { design } = await open();
    expect(handleConnect(design, { source: "s:extract", sourceHandle: "o:error", target: "x:needs_review", targetHandle: "t" }))
      .toBe("b:7:0");
    expect(getIn(design.processDoc(), ["edges", 7])).toEqual({ from: "extract.error", to: "$exit.needs_review" });
    expect(handleConnect(design, { source: "s:validate", sourceHandle: "o:done", target: "s:fix", targetHandle: "t" })).toBe("b:3:2");
    expect(getIn(design.processDoc(), ["edges", 3, "to", 2])).toEqual({ step: "fix", when: "" });
    expect(handleConnect(design, { source: "in", sourceHandle: "o", target: "s:fix", targetHandle: "t" })).toBeNull();
    expect(handleConnect(design, { source: "s:save", sourceHandle: "o:error", target: "in", targetHandle: null })).toBeNull();
    expect(handleConnect(design, { source: "s:save", sourceHandle: "o:error", target: "x:$ignore", targetHandle: "t" })).toBe("b:8:0");
    expect(getIn(design.processDoc(), ["edges", 8, "to"])).toBe("$ignore");
    expect(design.store.get()!.issuesStale).toBe(true);
    design.lock({ chat_id: "c", turn_id: "t" });
    expect(handleConnect(design, { source: "s:save", sourceHandle: "o:done", target: "s:fix", targetHandle: "t" })).toBeNull();
  });

  it("shows the parse error instead of the canvas", async () => {
    const api = createFakeApi();
    const broken = fixture("design");
    broken.process_file.doc = null;
    broken.process_file.parse_error = { message: "mapping values are not allowed here", line: 3, column: 7 };
    vi.mocked(api.processes.design).mockResolvedValue(broken);
    const { design } = await open(api);
    renderApp(<GraphEditor />, { api, design, url: { process: PID } });
    expect(screen.getByRole("alert").textContent).toBe(
      "process.yaml can't be parsed: mapping values are not allowed here (line 3, col 7). Fix it in your editor or ask the chat.");
  });

  it("keeps a dragged position per browser and clears it on re-layout", async () => {
    const { api, design } = await open();
    renderApp(<GraphEditor />, { api, design, url: { process: PID } });
    const key = `wynd.layout.${fixture("meta").workspace.root}.${PID}`;
    window.localStorage.setItem(key, JSON.stringify({ "s:read": { x: 5, y: 6 } }));
    fireEvent.click(screen.getByRole("button", { name: "Re-layout" }));
    expect(window.localStorage.getItem(key)).toBeNull();
    expect((design.store.get()!.process.doc as JsonObject).ui).toBeUndefined();
  });
});
