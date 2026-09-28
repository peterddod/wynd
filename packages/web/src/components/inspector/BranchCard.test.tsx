import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getIn } from "../../model/json";
import { createDesignSession, type DesignSession } from "../../state/design";
import { createFakeApi } from "../../test/fakeApi";
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

async function renderBranch(sel: string): Promise<DesignSession> {
  const api = createFakeApi();
  const design = createDesignSession(api);
  await design.open(PID);
  renderApp(<Inspector />, { api, design, url: { process: PID, sel } });
  return design;
}

function card(n: number): HTMLElement {
  return screen.getByRole("listitem", { name: `Branch ${n}` });
}

function sel(): string | null {
  return new URLSearchParams(window.location.search).get("sel");
}

beforeEach(() => {
  resetDoubles();
});

describe("BranchCard", () => {
  it("deletes and restores `when` with the Otherwise checkbox", async () => {
    const design = await renderBranch("b:3:1");
    const otherwise = within(card(2)).getByRole("checkbox", { name: "Otherwise (else)" }) as HTMLInputElement;
    expect(otherwise.checked).toBe(false);
    fireEvent.click(otherwise);
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "when"])).toBeUndefined();
    expect(within(card(2)).queryByLabelText("Condition")).toBeNull();
    expect(within(card(2)).getByText("else")).toBeTruthy();
    fireEvent.click(within(card(2)).getByRole("checkbox", { name: "Otherwise (else)" }));
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "when"])).toBe("");
    const condition = within(card(2)).getByLabelText("Condition");
    expect(document.activeElement).toBe(condition);
    expect(within(card(2)).getByText("Condition required, or tick 'Otherwise (else)'")).toBeTruthy();
    expect(design.store.get()!.opLabels).toEqual(["edit branch validate.done[1]"]);
  });

  it("reorders branches with the up and down buttons", async () => {
    const design = await renderBranch("b:3:1");
    fireEvent.click(within(card(2)).getByRole("button", { name: "Move branch up" }));
    const steps = (getIn(design.processDoc(), ["edges", 3, "to"]) as { step: string }[]).map((b) => b.step);
    expect(steps).toEqual(["fix", "save", "escalate"]);
    expect(sel()).toBe("b:3:0");
    expect(within(card(1)).getByRole("button", { name: "Move branch up" })).toHaveProperty("disabled", true);
    fireEvent.click(within(card(1)).getByRole("button", { name: "Move branch down" }));
    expect((getIn(design.processDoc(), ["edges", 3, "to"]) as { step: string }[]).map((b) => b.step)).toEqual(["save", "fix", "escalate"]);
    expect(design.store.get()!.issuesStale).toBe(true);
  });

  it("retargets through the target select", async () => {
    const design = await renderBranch("b:3:2");
    const target = within(card(3)).getByLabelText("Target") as HTMLSelectElement;
    expect(target.value).toBe("escalate");
    fireEvent.change(target, { target: { value: "$exit.needs_review" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 2, "step"])).toBe("$exit.needs_review");
    const options = [...target.options].map((o) => o.value);
    expect(options).toContain("$ignore");
    expect(options).toContain("$exit.done");
  });

  it("writes and clears a branch name", async () => {
    const design = await renderBranch("b:3:1");
    fireEvent.change(within(card(2)).getByLabelText("Name"), { target: { value: "retry" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "name"])).toBe("retry");
    expect(Object.keys(getIn(design.processDoc(), ["edges", 3, "to", 1]) as object)).toEqual(["step", "name", "when", "with"]);
    fireEvent.change(within(card(2)).getByLabelText("Name"), { target: { value: "" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "name"])).toBeUndefined();
  });

  it("expands a collapsed branch by selecting it", async () => {
    await renderBranch("b:3:0");
    const collapsed = screen.getByRole("button", { name: /^2\. if steps\.validate\.outputs\.fixable/ });
    fireEvent.click(collapsed);
    expect(sel()).toBe("b:3:1");
  });

  it("shows the agentic check and context only for an agentic edge", async () => {
    const design = await renderBranch("b:3:0");
    expect(within(card(1)).queryByLabelText("Check (agentic)")).toBeNull();
    fireEvent.change(screen.getByLabelText("Kind"), { target: { value: "agentic" } });
    expect(getIn(design.processDoc(), ["edges", 3, "kind"])).toBe("agentic");
    fireEvent.change(within(card(1)).getByLabelText("Check (agentic)"), { target: { value: "the record looks complete" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 0, "check"])).toBe("the record looks complete");
    fireEvent.change(within(card(1)).getByLabelText("New context entry"), { target: { value: "steps.extract.outputs" } });
    fireEvent.click(within(card(1)).getByRole("button", { name: "Add context" }));
    expect(getIn(design.processDoc(), ["edges", 3, "to", 0, "context"])).toEqual(["steps.extract.outputs"]);
    fireEvent.click(within(card(1)).getByRole("button", { name: "Remove context steps.extract.outputs" }));
    expect(getIn(design.processDoc(), ["edges", 3, "to", 0, "context"])).toBeUndefined();
  });

  it("removes a branch and a whole edge", async () => {
    const design = await renderBranch("b:3:1");
    fireEvent.click(within(card(2)).getByRole("button", { name: "Remove branch" }));
    expect((getIn(design.processDoc(), ["edges", 3, "to"]) as unknown[])).toHaveLength(2);
    expect(sel()).toBe("b:3:0");
    fireEvent.click(screen.getByRole("button", { name: "Remove edge" }));
    expect((getIn(design.processDoc(), ["edges"]) as unknown[])).toHaveLength(6);
    expect(sel()).toBeNull();
  });

  it("adds a branch before the else from the edge inspector", async () => {
    const design = await renderBranch("b:3:0");
    fireEvent.change(screen.getByLabelText("+ Add branch"), { target: { value: "$exit.needs_review" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 2])).toEqual({ step: "$exit.needs_review", when: "" });
    expect(sel()).toBe("b:3:2");
  });

  it("edits limits as numbers or expressions and keeps unknown branch keys", async () => {
    const design = await renderBranch("b:3:1");
    const max = within(card(2)).getByLabelText("max_traversals") as HTMLInputElement;
    expect(max.placeholder).toBe("10 (auto — on a cycle)");
    fireEvent.change(max, { target: { value: "5" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "limits"])).toEqual({ max_traversals: 5 });
    fireEvent.change(within(card(2)).getByLabelText("timeout"), { target: { value: "env.TIMEOUT" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "limits", "timeout"])).toBe("env.TIMEOUT");
    fireEvent.change(max, { target: { value: "" } });
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "limits"])).toEqual({ timeout: "env.TIMEOUT" });
    const extra = within(card(2)).getByLabelText("Other fields (JSON)");
    fireEvent.change(extra, { target: { value: '{"x_note": "keep"}' } });
    fireEvent.blur(extra);
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "x_note"])).toBe("keep");
    expect(getIn(design.processDoc(), ["edges", 3, "to", 1, "step"])).toBe("fix");
  });
});
