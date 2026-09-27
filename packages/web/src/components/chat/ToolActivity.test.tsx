// Tool activity groups (`$DRAFTS/07 §9.3`).
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { ToolItem } from "../../api/types";
import { fixture } from "../../test/fixtures";
import { ARGS_SUMMARY_MAX, ToolActivityGroup, argsSummary } from "./ToolActivity";

const [readTool, writeTool] = fixture("chatSnapshot").items.filter((i): i is ToolItem => i.type === "tool") as [
  ToolItem, ToolItem,
];

describe("argsSummary", () => {
  it("is k=v pairs, strings raw and other values as JSON", () => {
    expect(argsSummary({ op: "set_when", branch: 0, when: "steps.escalate.outputs.total > 10000" }))
      .toBe("op=set_when, branch=0, when=steps.escalate.outputs.total > 10000");
    expect(argsSummary({ fields: { a: 1 }, flags: [true] })).toBe('fields={"a":1}, flags=[true]');
    expect(argsSummary(["x"])).toBe('["x"]');
    expect(argsSummary(null)).toBe("null");
  });

  it("is cut to 80 characters", () => {
    const summary = argsSummary({ instruction: "x".repeat(200) });
    expect(summary).toHaveLength(ARGS_SUMMARY_MAX);
    expect(summary.endsWith("…")).toBe(true);
  });
});

describe("ToolActivityGroup", () => {
  it("shows a running tool without a result, and a failed one", () => {
    const running: ToolItem = { ...writeTool, id: "t1", status: "running", summary: null, duration_ms: null };
    const failed: ToolItem = { ...readTool, id: "t2", status: "error", summary: "unknown process 'x'", duration_ms: 1250 };
    render(<ToolActivityGroup items={[running, failed]} />);
    expect(screen.getByRole("button", { name: "Used 2 tools · 1 write ▾" })).toBeTruthy();
    expect(screen.getByLabelText("failed").parentElement?.textContent).toBe("✕ 1.3 s");
    expect(screen.queryByLabelText("succeeded")).toBeNull();
  });

  it("opens by itself once a write joins a read-only group, unless the user chose", () => {
    const { rerender } = render(<ToolActivityGroup items={[readTool]} />);
    expect(screen.getByRole("button", { name: "Used 1 tool ▸" })).toBeTruthy();
    rerender(<ToolActivityGroup items={[readTool, writeTool]} />);
    expect(screen.getByText("acting on: process_supplier_invoice")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Used 2 tools · 1 write ▾" }));
    rerender(<ToolActivityGroup items={[readTool, writeTool, { ...writeTool, id: "it_99" }]} />);
    expect(screen.getByRole("button", { name: "Used 3 tools · 2 writes ▸" })).toBeTruthy();
    expect(screen.queryByText("acting on: process_supplier_invoice")).toBeNull();
  });

  it("a row expands to its summary and collapses again", () => {
    render(<ToolActivityGroup items={[readTool, writeTool]} />);
    const row = screen.getByRole("button", { name: /^read_design/ });
    expect(row.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(row);
    expect(screen.getByText("process_supplier_invoice: 6 steps, 7 edges")).toBeTruthy();
    fireEvent.click(row);
    expect(screen.queryByText("process_supplier_invoice: 6 steps, 7 edges")).toBeNull();
    expect(screen.getByText("38 ms")).toBeTruthy();
  });
});
