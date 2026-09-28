import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getIn } from "../../model/json";
import { createDesignSession, isDirty, type DesignSession } from "../../state/design";
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
const PROTOS = "processes/process_supplier_invoice/proto";

async function openStep(step: string, usedBy?: string[]): Promise<DesignSession> {
  const api = createFakeApi();
  const doc = fixture("design");
  if (usedBy !== undefined) doc.steps[step]!.used_by = usedBy;
  vi.mocked(api.processes.design).mockResolvedValue(doc);
  const design = createDesignSession(api);
  await design.open(PID);
  renderApp(<Inspector />, { api, design, url: { process: PID, sel: `s:${step}` } });
  return design;
}

function editor(): HTMLElement {
  return screen.getByRole("region", { name: "Proto-step" });
}

beforeEach(() => {
  resetDoubles();
});

describe("ProtoStepEditor", () => {
  it("applies an instruction edit to the proto file", async () => {
    const design = await openStep("extract");
    fireEvent.change(within(editor()).getByLabelText("Instruction"), { target: { value: "Extract the fields." } });
    const s = design.store.get()!;
    const file = s.protos[`${PROTOS}/extract_invoice_fields.yaml`]!;
    expect(getIn(file.doc, ["instruction"])).toBe("Extract the fields.");
    expect(isDirty(file)).toBe(true);
    expect(s.process.doc).toBe(s.process.savedDoc);
    expect(s.opLabels).toEqual(["edit proto extract_invoice_fields"]);
  });

  it("renders the plain-language summary from the step interface", async () => {
    await openStep("extract");
    const summary = within(editor()).getByRole("region", { name: "What this step does" });
    expect(summary.textContent).toContain("Takes invoice_text (text). Finishes with done, returning supplier (text)");
    expect(summary.textContent).toContain("from the compiled step");
  });

  it("converts flat outputs to nested when an exit is added", async () => {
    const design = await openStep("read");
    fireEvent.change(within(editor()).getByLabelText("New exit name"), { target: { value: "encrypted" } });
    fireEvent.click(within(editor()).getByRole("button", { name: "Add exit" }));
    const doc = design.store.get()!.protos[`${PROTOS}/read_pdf.yaml`]!.doc;
    expect(getIn(doc, ["outputs"])).toEqual({ done: { text: "string", pages: "integer" }, encrypted: {} });
    expect(getIn(doc, ["exits"])).toEqual(["done", "encrypted"]);
  });

  it("renames an exit in the proto and the process edge from it, in one apply", async () => {
    const design = await openStep("extract");
    const name = within(editor()).getByLabelText("Exit not_an_invoice name") as HTMLInputElement;
    fireEvent.change(name, { target: { value: "other_document" } });
    fireEvent.blur(name);
    const s = design.store.get()!;
    const proto = s.protos[`${PROTOS}/extract_invoice_fields.yaml`]!;
    expect(getIn(proto.doc, ["exits"])).toEqual(["done", "other_document"]);
    expect(getIn(s.process.doc, ["edges", 2, "from"])).toBe("extract.other_document");
    expect(isDirty(proto) && isDirty(s.process)).toBe(true);
    expect(s.opLabels).toEqual(["rename exit extract_invoice_fields.not_an_invoice → other_document"]);
    expect(s.issuesStale).toBe(true);
  });

  it("shows the shared-step banner with the other processes", async () => {
    await openStep("extract", [PID, "finance/credit_notes"]);
    expect(within(editor()).getByRole("note").textContent).toBe(
      "Shared step ./steps/extract_invoice_fields, also used by finance/credit_notes. Edits affect those processes.");
  });

  it("edits examples and the env deps", async () => {
    const design = await openStep("read");
    const examples = within(editor()).getByRole("region", { name: "Examples" });
    fireEvent.click(within(examples).getByRole("button", { name: "+ Add example" }));
    const path = `${PROTOS}/read_pdf.yaml`;
    const added = (getIn(design.store.get()!.protos[path]!.doc, ["examples"]) as unknown[]).at(-1);
    expect(added).toEqual({ inputs: {}, exit: "done" });
    const deps = within(editor()).getByLabelText("Dependencies (one requirement per line)");
    fireEvent.change(deps, { target: { value: "pypdf>=6,<7\n\n" } });
    fireEvent.blur(deps);
    expect(getIn(design.store.get()!.protos[path]!.doc, ["env", "deps"])).toEqual(["pypdf>=6,<7"]);
  });

  it("maps shell exit codes", async () => {
    const design = await openStep("read");
    fireEvent.click(within(editor()).getByRole("button", { name: "Map shell exit codes" }));
    fireEvent.change(within(editor()).getByLabelText("Exit code"), { target: { value: "0" } });
    fireEvent.click(within(editor()).getByRole("button", { name: "Add code" }));
    fireEvent.change(within(editor()).getByLabelText("Exit code"), { target: { value: "*" } });
    fireEvent.click(within(editor()).getByRole("button", { name: "Add code" }));
    expect(getIn(design.store.get()!.protos[`${PROTOS}/read_pdf.yaml`]!.doc, ["exit_codes"])).toEqual({ "0": "done", "*": "error" });
  });
});
