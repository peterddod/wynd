import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { fixture } from "../../test/fixtures";
import { JsonView, TRUNCATE_AT } from "./JsonView";

function details(container: HTMLElement): HTMLDetailsElement[] {
  return [...container.querySelectorAll("details")];
}

describe("JsonView", () => {
  it("renders scalars, keys and nested values", () => {
    render(<JsonView value={{ invoice_number: "INV-1042", total: 1200.5, paid: false, note: null, lines: [1, 2] }} />);
    expect(screen.getByText('"INV-1042"')).toBeTruthy();
    expect(screen.getByText("1200.5")).toBeTruthy();
    expect(screen.getByText("false")).toBeTruthy();
    expect(screen.getByText("null")).toBeTruthy();
    expect(screen.getByText("total:")).toBeTruthy();
    expect(screen.getByText("2 items")).toBeTruthy();
  });

  it("expands two levels by default; true collapses everything; false expands everything; n expands n levels", () => {
    const value = fixture("runFinished").outputs;             // {record: {...}} -> 2 levels of objects
    const deep = { a: { b: { c: { d: 1 } } } };                // root, a, b and c are objects
    const byDefault = render(<JsonView value={deep} />);
    expect(details(byDefault.container).map((d) => d.open)).toEqual([true, true, false, false]);
    byDefault.unmount();
    const collapsed = render(<JsonView value={value} collapsed />);
    expect(details(collapsed.container).map((d) => d.open)).toEqual([false, false]);
    collapsed.unmount();
    const expanded = render(<JsonView value={deep} collapsed={false} />);
    expect(details(expanded.container).map((d) => d.open)).toEqual([true, true, true, true]);
    expanded.unmount();
    const one = render(<JsonView value={deep} collapsed={1} />);
    expect(details(one.container).map((d) => d.open)).toEqual([true, false, false, false]);
  });

  it("empty containers and a missing value render inline", () => {
    const { container } = render(<JsonView value={{ a: {}, b: [] }} />);
    expect(screen.getByText("{}")).toBeTruthy();
    expect(screen.getByText("[]")).toBeTruthy();
    expect(details(container)).toHaveLength(1);
    render(<JsonView value={undefined} label="Outputs" />);
    expect(screen.getByText("—")).toBeTruthy();
  });

  it(`truncates strings over ${TRUNCATE_AT} characters until [show all]`, () => {
    const long = "x".repeat(TRUNCATE_AT + 50);
    render(<JsonView value={{ text: long }} />);
    expect(screen.queryByText(JSON.stringify(long))).toBeNull();
    expect(screen.getByText(`"${"x".repeat(TRUNCATE_AT)}…"`, { exact: false })).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: `show all (${long.length} characters)` }));
    expect(screen.getByText(JSON.stringify(long))).toBeTruthy();
  });

  it("copies the whole value as indented JSON", async () => {
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    try {
      render(<JsonView value={{ a: [1] }} label="Inputs" />);
      fireEvent.click(screen.getByRole("button", { name: "Copy Inputs" }));
      expect(writeText).toHaveBeenCalledWith('{\n  "a": [\n    1\n  ]\n}');
      expect(await screen.findByText("Copied")).toBeTruthy();
    } finally {
      Reflect.deleteProperty(navigator, "clipboard");
    }
  });
});
