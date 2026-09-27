import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getIn, setIn } from "../../model/json";
import { createDesignSession, type DesignSession } from "../../state/design";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { resetDoubles } from "../graph/testDoubles";
import { WithMappingEditor, withValue } from "./WithMappingEditor";
import { targetFields } from "../graph/designHooks";

vi.mock("../../state/store", () => import("../graph/testDoubles"));
vi.mock("../../state/query", () => import("../graph/testDoubles"));
vi.mock("../../state/url", () => import("../graph/testDoubles"));
vi.mock("../../state/connection", () => import("../graph/testDoubles"));

const PID = "process_supplier_invoice";

async function open(edit?: (doc: ReturnType<typeof fixture<"design">>) => void): Promise<DesignSession> {
  const api = createFakeApi();
  const doc = fixture("design");
  edit?.(doc);
  vi.mocked(api.processes.design).mockResolvedValue(doc);
  const design = createDesignSession(api);
  await design.open(PID);
  return design;
}

function renderRows(design: DesignSession, e: number, b: number, fields: "target" | "unknown" = "target"): void {
  const s = design.store.get()!;
  const target = e === 0 ? { type: "step" as const, step: "extract" } : { type: "step" as const, step: "save" };
  renderApp(<WithMappingEditor e={e} b={b} targetFields={fields === "target" ? targetFields(s, target) : null} />,
            { design, url: { process: PID } });
}

beforeEach(() => {
  resetDoubles();
});

describe("WithMappingEditor", () => {
  it("shows one row per target input with its type in plain language", async () => {
    const design = await open();
    renderRows(design, 3, 0);
    const dest = screen.getByLabelText("dest — a file path (required)") as HTMLTextAreaElement;
    expect(dest.value).toBe("if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR else env.RECORDS_DIR");
    expect(screen.getByLabelText(/^record — a record with key \(text\), supplier \(text\)/)).toBeTruthy();
    expect(screen.queryByText(/not an input of/)).toBeNull();
  });

  it("flags keys that are not inputs of the target and removes them", async () => {
    const design = await open((d) => {
      d.process_file.doc = setIn(d.process_file.doc, ["edges", 3, "to", 0, "with", "bogus"], "run.id");
    });
    renderRows(design, 3, 0);
    const row = screen.getByLabelText("bogus").closest("li")!;
    expect(within(row).getByText("not an input of save")).toBeTruthy();
    fireEvent.click(within(row).getByRole("button", { name: "Remove mapping bogus" }));
    expect(getIn(design.processDoc(), ["edges", 3, "to", 0, "with", "bogus"])).toBeUndefined();
  });

  it("deletes the key when an input is cleared", async () => {
    const design = await open();
    renderRows(design, 3, 0);
    fireEvent.change(screen.getByLabelText("dest — a file path (required)"), { target: { value: "" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 0, "with"])).toEqual({ record: "steps.validate.outputs.record" });
    expect(design.store.get()!.opLabels).toEqual(["edit branch validate.done[0]"]);
  });

  it("writes a shorthand edge's mapping on the edge", async () => {
    const design = await open();
    renderRows(design, 0, 0);
    fireEvent.change(screen.getByLabelText("invoice_text — text (required)"), { target: { value: "lower(steps.read.outputs.text)" } });
    expect(getIn(design.processDoc(), ["edges", 0])).toEqual({
      from: "read.done", to: "extract", with: { invoice_text: "lower(steps.read.outputs.text)" },
    });
  });

  it("keeps literals literal and falls back to free rows for an unknown interface", async () => {
    expect(withValue("6", 5)).toBe(6);
    expect(withValue('{"a": 1}', { a: 0 })).toEqual({ a: 1 });
    expect(withValue("steps.x.outputs.n", 5)).toBe("steps.x.outputs.n");
    expect(withValue("6", "5")).toBe("6");
    expect(withValue("", "x")).toBeUndefined();
    const design = await open();
    renderRows(design, 3, 0, "unknown");
    fireEvent.change(screen.getByLabelText("New mapping field"), { target: { value: "note" } });
    fireEvent.click(screen.getByRole("button", { name: "+ Add mapping" }));
    expect(getIn(design.processDoc(), ["edges", 3, "to", 0, "with", "note"])).toBe("null");
    expect(screen.getByLabelText("note")).toBeTruthy();
  });
});
