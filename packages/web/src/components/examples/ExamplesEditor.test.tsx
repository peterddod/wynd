import { fireEvent, screen, within } from "@testing-library/react";
import { useState, type ReactElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { Interface, Json } from "../../api/types";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { ExampleCard } from "./ExampleCard";
import { ExamplesEditor } from "./ExamplesEditor";
import { ValueInput } from "./ValueInput";

function extract(): Interface {
  return fixture("design").steps.extract!.interface;
}

function Harness({ initial, iface, onChange }: { initial: Json[]; iface: Interface | null; onChange(next: Json[]): void }): ReactElement {
  const [examples, setExamples] = useState(initial);
  return (
    <ExamplesEditor label="Examples" examples={examples} exits={iface?.exits.map((x) => x.name) ?? ["done"]} iface={iface}
                    onChange={(next) => { setExamples(next); onChange(next); }} />
  );
}

function card(): HTMLElement {
  return screen.getAllByRole("article")[0]!;
}

let changes: Json[][];

beforeEach(() => {
  changes = [];
});

describe("ExamplesEditor", () => {
  it("writes typed values for number and date fields", () => {
    renderApp(<Harness initial={[{ inputs: { invoice_text: "INVOICE" }, exit: "done" }]} iface={extract()} onChange={(n) => changes.push(n)} />);
    fireEvent.change(within(card()).getByLabelText("total"), { target: { value: "1200.5" } });
    fireEvent.change(within(card()).getByLabelText("due_date"), { target: { value: "2026-10-01" } });
    expect(within(card()).getByLabelText("due_date").getAttribute("type")).toBe("date");
    expect(changes.at(-1)).toEqual([{ inputs: { invoice_text: "INVOICE" }, exit: "done", outputs: { total: 1200.5, due_date: "2026-10-01" } }]);
    fireEvent.change(within(card()).getByLabelText("total"), { target: { value: "" } });
    expect(changes.at(-1)).toEqual([{ inputs: { invoice_text: "INVOICE" }, exit: "done", outputs: { due_date: "2026-10-01" } }]);
  });

  it("shows an error for invalid JSON in an object field without propagating it", () => {
    const fix = fixture("design").steps.fix!.interface;
    renderApp(<Harness initial={[{ inputs: {}, exit: "done" }]} iface={fix} onChange={(n) => changes.push(n)} />);
    const fields = within(card()).getByLabelText(/^fields/);
    fireEvent.focus(fields);
    fireEvent.change(fields, { target: { value: "{bad" } });
    fireEvent.blur(fields);
    expect(within(card()).getByRole("alert").textContent).toMatch(/^Invalid JSON/);
    expect(changes).toEqual([]);
    fireEvent.change(fields, { target: { value: '{"supplier": "ACME"}' } });
    fireEvent.blur(fields);
    expect(changes.at(-1)).toEqual([{ inputs: { fields: { supplier: "ACME" } }, exit: "done" }]);
  });

  it("swaps the output fields when the exit changes", () => {
    renderApp(<Harness initial={[{ inputs: { invoice_text: "x" }, exit: "done" }]} iface={extract()} onChange={(n) => changes.push(n)} />);
    expect(within(card()).getByLabelText("total")).toBeTruthy();
    fireEvent.change(within(card()).getByLabelText("Exit"), { target: { value: "not_an_invoice" } });
    expect(within(card()).queryByLabelText("total")).toBeNull();
    expect(within(card()).queryByRole("group", { name: /Outputs/ })).toBeNull();
    expect(changes.at(-1)).toEqual([{ inputs: { invoice_text: "x" }, exit: "not_an_invoice" }]);
  });

  it("flags values that are not in the schema and removes them", () => {
    renderApp(<Harness initial={[{ inputs: { invoice_text: "x", pages: 2 }, exit: "done" }]} iface={extract()} onChange={(n) => changes.push(n)} />);
    const inputs = within(card()).getByRole("group", { name: "Inputs" });
    expect(within(inputs).getByText("not in schema")).toBeTruthy();
    fireEvent.click(within(inputs).getByRole("button", { name: "Remove pages" }));
    expect(changes.at(-1)).toEqual([{ inputs: { invoice_text: "x" }, exit: "done" }]);
  });

  it("flags an unknown exit and titles the card with the example sentence", () => {
    renderApp(<Harness initial={[{ inputs: { invoice_text: "Dear customer" }, exit: "skipped" }]} iface={extract()} onChange={(n) => changes.push(n)} />);
    expect(within(card()).getByText("not an exit of this step")).toBeTruthy();
    expect(within(card()).getByText('Given invoice_text = "Dear customer" → skipped')).toBeTruthy();
  });

  it("adds, duplicates, moves and removes examples", () => {
    renderApp(<Harness initial={[{ inputs: { invoice_text: "a" }, exit: "done" }]} iface={extract()} onChange={(n) => changes.push(n)} />);
    fireEvent.click(screen.getByRole("button", { name: "+ Add example" }));
    expect(changes.at(-1)).toEqual([{ inputs: { invoice_text: "a" }, exit: "done" }, { inputs: {}, exit: "done" }]);
    fireEvent.click(within(card()).getByRole("button", { name: "Duplicate" }));
    expect(changes.at(-1)).toHaveLength(3);
    fireEvent.click(within(screen.getAllByRole("article")[2]!).getByRole("button", { name: "Move example up" }));
    expect((changes.at(-1) as { inputs: object }[]).map((e) => e.inputs)).toEqual([{ invoice_text: "a" }, {}, { invoice_text: "a" }]);
    fireEvent.click(within(card()).getByRole("button", { name: "Remove" }));
    expect(changes.at(-1)).toHaveLength(2);
  });

  it("edits free key/value rows without a schema", () => {
    renderApp(<Harness initial={[{ inputs: { n: 1 }, exit: "done" }]} iface={null} onChange={(n) => changes.push(n)} />);
    const inputs = within(card()).getByRole("group", { name: "Inputs" });
    fireEvent.change(within(inputs).getByLabelText("n"), { target: { value: "[1, 2]" } });
    expect(changes.at(-1)).toEqual([{ inputs: { n: [1, 2] }, exit: "done" }]);
    fireEvent.change(within(inputs).getByLabelText("New input name"), { target: { value: "label" } });
    fireEvent.click(within(inputs).getByRole("button", { name: "Add" }));
    expect(changes.at(-1)).toEqual([{ inputs: { n: [1, 2], label: "" }, exit: "done" }]);
  });

  it("renders read-only without onChange", () => {
    renderApp(<ExampleCard example={{ inputs: { invoice_text: "x" }, exit: "done" }} exits={["done"]} iface={extract()} />);
    expect(screen.queryByRole("button", { name: "Duplicate" })).toBeNull();
    expect(screen.getByLabelText("Exit")).toHaveProperty("disabled", true);
  });
});

describe("ValueInput", () => {
  it("uploads a file for a path field and fills in the returned path", async () => {
    const api = createFakeApi();
    const onChange = vi.fn();
    renderApp(<ValueInput label="pdf_path" schema={{ type: "string", format: "path" }} value={undefined} onChange={onChange} allowUpload required />,
              { api });
    const file = new File(["%PDF"], "inv1.pdf", { type: "application/pdf" });
    fireEvent.change(screen.getByLabelText("File to upload"), { target: { files: [file] } });
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledWith(fixture("upload").path));
    expect(vi.mocked(api.uploads.put)).toHaveBeenCalledWith(file);
  });

  it("uses a checkbox for booleans and the non-null part of a nullable", () => {
    const onChange = vi.fn();
    renderApp(<>
      <ValueInput label="valid" schema={{ type: "boolean" }} value={false} onChange={onChange} />
      <ValueInput label="count" schema={{ anyOf: [{ type: "integer" }, { type: "null" }] }} value={3} onChange={onChange} />
    </>);
    fireEvent.click(screen.getByLabelText("valid"));
    expect(onChange).toHaveBeenLastCalledWith(true);
    expect(screen.getByLabelText("count").getAttribute("step")).toBe("1");
    fireEvent.change(screen.getByLabelText("count"), { target: { value: "4" } });
    expect(onChange).toHaveBeenLastCalledWith(4);
  });
});
