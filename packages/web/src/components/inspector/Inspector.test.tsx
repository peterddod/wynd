import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Issue } from "../../api/types";
import { getIn, setIn } from "../../model/json";
import { createDesignSession, type DesignSession } from "../../state/design";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { resetDoubles } from "../graph/testDoubles";
import { Inspector } from "./Inspector";

vi.mock("../../state/store", () => import("../graph/testDoubles"));
vi.mock("../../state/query", () => import("../graph/testDoubles"));
vi.mock("../../state/url", () => import("../graph/testDoubles"));
vi.mock("../../state/toasts", () => import("../graph/testDoubles"));
vi.mock("../../state/connection", () => import("../graph/testDoubles"));
vi.mock("../../state/theme", () => import("../graph/testDoubles"));
vi.mock("../common/Dialog", () => import("../graph/testDoubles"));

const PID = "process_supplier_invoice";
const PROCESS = "processes/process_supplier_invoice/process.yaml";

async function show(sel: string | null, edit?: (d: ReturnType<typeof fixture<"design">>) => void): Promise<DesignSession> {
  const api = createFakeApi();
  const doc = fixture("design");
  edit?.(doc);
  vi.mocked(api.processes.design).mockResolvedValue(doc);
  const design = createDesignSession(api);
  await design.open(PID);
  renderApp(<Inspector />, { api, design, url: { process: PID, sel } });
  return design;
}

function sel(): string | null {
  return new URLSearchParams(window.location.search).get("sel");
}

beforeEach(() => {
  resetDoubles();
});

describe("ProcessOverview", () => {
  it("summarises the process and lists issues and cycle notes", async () => {
    await show(null);
    const inspector = screen.getByRole("region", { name: "Inspector" });
    expect(within(inspector).getByRole("heading", { name: PID })).toBeTruthy();
    expect(within(inspector).getByRole("region", { name: "What this process does" }).textContent)
      .toContain("Takes pdf_path (a file path). Finishes with done, returning record (a record (any fields))");
    const cycles = within(inspector).getByRole("region", { name: "Cycles" });
    expect(cycles.textContent).toContain("validate.done[1] → fix: max_traversals 10 (auto)");
    expect(cycles.textContent).toContain("fix.done[0] → validate: max_traversals 10 (auto)");
  });

  it("selects the element an issue points at", async () => {
    const issue: Issue = { severity: "error", code: "E-REF-FIELD", message: "no field totl", file: PROCESS,
                           loc: ["edges", 3, "to", 0, "with", "dest"], span: [3, 37] };
    await show(null, (d) => { d.validation = { ok: false, issues: [issue] }; });
    fireEvent.click(screen.getByRole("button", { name: /E-REF-FIELD no field totl/ }));
    expect(sel()).toBe("b:3:0");
  });

  it("falls back to the overview for a selection that no longer exists", async () => {
    await show("s:gone");
    expect(screen.getByRole("region", { name: "What this process does" })).toBeTruthy();
  });
});

describe("StepInspector", () => {
  it("lists every exit with its route and routes an unrouted one", async () => {
    const design = await show("s:extract");
    const routing = screen.getByRole("region", { name: "Routing" });
    expect(routing.textContent).toContain("done → validate");
    expect(routing.textContent).toContain("not_an_invoice → $exit.not_an_invoice");
    fireEvent.change(within(routing).getByLabelText("Route error to"), { target: { value: "$exit.needs_review" } });
    expect(getIn(design.processDoc(), ["edges", 7])).toEqual({ from: "extract.error", to: "$exit.needs_review" });
    expect(sel()).toBe("b:7:0");
  });

  it("opens the edge of a routed exit and sets the entry", async () => {
    const design = await show("s:validate");
    const routing = screen.getByRole("region", { name: "Routing" });
    fireEvent.click(within(routing).getAllByRole("button", { name: "Edit" })[0]!);
    expect(sel()).toBe("b:3:0");
    expect(getIn(design.processDoc(), ["entry"])).toBe("read");
  });

  it("sets the entry, edits use, and removes the step", async () => {
    const design = await show("s:save");
    fireEvent.click(screen.getByRole("button", { name: "Set as entry" }));
    expect(getIn(design.processDoc(), ["entry"])).toBe("save");
    const use = screen.getByLabelText("use");
    fireEvent.change(use, { target: { value: "shared:records/save" } });
    fireEvent.blur(use);
    expect(getIn(design.processDoc(), ["steps", "save", "use"])).toBe("shared:records/save");
    fireEvent.click(screen.getByRole("button", { name: "Remove step" }));
    expect(screen.getByRole("dialog", { name: "Remove step save" })).toBeTruthy();
  });

  it("shows the agentic lock settings and the compiled hint", async () => {
    await show("s:extract");
    expect(screen.getByText("tier cheap · thinking low")).toBeTruthy();
    expect(screen.getByText("Editing the proto-step makes this step design-phase until recompiled.")).toBeTruthy();
  });

  it("offers to create a proto-step for a local step without one", async () => {
    const design = await show("s:notify", (d) => {
      d.process_file.doc = setIn(d.process_file.doc, ["steps", "notify"], { use: "./steps/notify" });
    });
    fireEvent.click(screen.getByRole("button", { name: "Create proto-step" }));
    expect(design.store.get()!.protos["processes/process_supplier_invoice/proto/notify.yaml"]!.doc)
      .toMatchObject({ kind: "proto_step", name: "notify" });
    expect(screen.getByRole("region", { name: "Proto-step" })).toBeTruthy();
  });
});

describe("TerminalInspector and InputsInspector", () => {
  it("renames a process exit and edits its output fields", async () => {
    const design = await show("x:needs_review");
    fireEvent.change(screen.getByLabelText("Exit name"), { target: { value: "escalated" } });
    fireEvent.click(screen.getByRole("button", { name: "Rename" }));
    expect(getIn(design.processDoc(), ["edges", 6, "to"])).toBe("$exit.escalated");
    expect(sel()).toBe("x:escalated");
    fireEvent.click(screen.getByRole("button", { name: "+ Add field" }));
    expect(getIn(design.processDoc(), ["outputs", "escalated"])).toEqual({ field: "string" });
  });

  it("declares an exit that an edge uses but the process does not", async () => {
    const design = await show("x:rejected", (d) => {
      d.process_file.doc = setIn(d.process_file.doc, ["edges", 7], { from: "extract.error", to: "$exit.rejected" });
    });
    fireEvent.click(screen.getByRole("button", { name: "Declare exit rejected" }));
    expect(getIn(design.processDoc(), ["outputs", "rejected"])).toEqual({});
  });

  it("explains the ignore terminal", async () => {
    await show("x:$ignore");
    expect(screen.getByRole("heading", { name: "Ignored" })).toBeTruthy();
  });

  it("edits process inputs, renaming example keys too, and the entry", async () => {
    const design = await show("in");
    const name = screen.getByLabelText("Field name pdf_path");
    fireEvent.change(name, { target: { value: "file" } });
    fireEvent.blur(name);
    expect(getIn(design.processDoc(), ["inputs"])).toEqual({ file: "path" });
    expect(getIn(design.processDoc(), ["examples", 0, "inputs"])).toEqual({ file: "examples/acme_inv_1042.pdf" });
    fireEvent.change(screen.getByLabelText("Entry step"), { target: { value: "extract" } });
    expect(getIn(design.processDoc(), ["entry"])).toBe("extract");
  });
});
