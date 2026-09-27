import { fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { getIn } from "../../model/json";
import { createDesignSession, type DesignSession } from "../../state/design";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { resetDoubles } from "../graph/testDoubles";
import { ProcessSettings } from "./ProcessSettings";

vi.mock("../../state/store", () => import("../graph/testDoubles"));
vi.mock("../../state/query", () => import("../graph/testDoubles"));
vi.mock("../../state/url", () => import("../graph/testDoubles"));
vi.mock("../../state/connection", () => import("../graph/testDoubles"));

const PID = "process_supplier_invoice";

async function show(): Promise<DesignSession> {
  const api = createFakeApi();
  const design = createDesignSession(api);
  await design.open(PID);
  renderApp(<ProcessSettings />, { api, design, url: { process: PID, tab: "process" } });
  return design;
}

beforeEach(() => {
  resetDoubles();
});

describe("ProcessSettings", () => {
  it("edits top-level fields through processDoc ops", async () => {
    const design = await show();
    fireEvent.change(screen.getByLabelText("Goal"), { target: { value: "Pay suppliers on time." } });
    fireEvent.change(screen.getByLabelText("Latency"), { target: { value: "normal" } });
    fireEvent.change(screen.getByLabelText("Base image"), { target: { value: "alpine-python" } });
    fireEvent.change(screen.getByLabelText("On error"), { target: { value: "escalate" } });
    const doc = design.processDoc();
    expect(getIn(doc, ["goal"])).toBe("Pay suppliers on time.");
    expect(getIn(doc, ["latency"])).toBe("normal");
    expect(getIn(doc, ["env"])).toEqual({ ...(fixture("design").process_file.doc as { env: object }).env, base: "alpine-python" });
    expect(getIn(doc, ["on_error"])).toBe("escalate");
    expect(design.store.get()!.opLabels).toEqual(["edit process settings"]);
    fireEvent.change(screen.getByLabelText("Latency"), { target: { value: "" } });
    expect(getIn(design.processDoc(), ["latency"])).toBeUndefined();
  });

  it("lists providers from the registry after the workspace default", async () => {
    const design = await show();
    const provider = screen.getByLabelText("Provider") as HTMLSelectElement;
    await vi.waitFor(() => expect([...provider.options].map((o) => o.value)).toEqual(
      ["", ...new Set([...fixture("providers").map((p) => p.name), "claude-code"])]));
    expect(provider.options[0]!.textContent).toBe("(workspace default: claude-code)");
    fireEvent.change(provider, { target: { value: "" } });
    expect(getIn(design.processDoc(), ["provider"])).toBeUndefined();
  });

  it("toggles finally steps in step order", async () => {
    const design = await show();
    const fin = screen.getByRole("group", { name: "Finally" });
    fireEvent.click(within(fin).getByLabelText("save"));
    fireEvent.click(within(fin).getByLabelText("read"));
    expect(getIn(design.processDoc(), ["finally"])).toEqual(["read", "save"]);
    fireEvent.click(within(fin).getByLabelText("read"));
    fireEvent.click(within(fin).getByLabelText("save"));
    expect(getIn(design.processDoc(), ["finally"])).toBeUndefined();
  });

  it("renames a process exit everywhere and edits process examples", async () => {
    const design = await show();
    const name = screen.getByLabelText("Exit needs_review name");
    fireEvent.change(name, { target: { value: "escalated" } });
    fireEvent.blur(name);
    expect(getIn(design.processDoc(), ["edges", 6, "to"])).toBe("$exit.escalated");
    expect(getIn(design.processDoc(), ["examples", 4, "exit"])).toBe("escalated");
    const examples = screen.getByRole("region", { name: "Process examples" });
    expect(within(examples).getAllByRole("article")).toHaveLength(5);
    fireEvent.click(within(examples).getAllByRole("button", { name: "Remove" })[3]!);
    expect((getIn(design.processDoc(), ["examples"]) as unknown[])).toHaveLength(4);
    expect(getIn(design.processDoc(), ["examples", 0, "env"])).toEqual(
      getIn(fixture("design").process_file.doc, ["examples", 0, "env"]));
  });
});
