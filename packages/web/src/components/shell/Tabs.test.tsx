import { fireEvent, render, screen } from "@testing-library/react";
import { useState, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { Tabs, tabId, tabPanelId, type TabItem } from "./Tabs";

type T = "graph" | "runs" | "releases";
const TABS: TabItem<T>[] = [
  { id: "graph", label: "Graph" },
  { id: "runs", label: "Runs" },
  { id: "releases", label: "Releases" },
];

function Harness({ onChange }: { onChange(id: T): void }): ReactElement {
  const [value, setValue] = useState<T>("graph");
  return (
    <Tabs
      label="Process views"
      tabs={TABS}
      value={value}
      onChange={(id) => {
        onChange(id);
        setValue(id);
      }}
    />
  );
}

function selected(): string | undefined {
  return screen.getAllByRole("tab").find((t) => t.getAttribute("aria-selected") === "true")?.textContent ?? undefined;
}

describe("Tabs", () => {
  it("is a labelled tablist whose tabs point at their panels, with a roving tabindex", () => {
    render(<Harness onChange={vi.fn()} />);
    expect(screen.getByRole("tablist", { name: "Process views" })).toBeTruthy();
    const runs = screen.getByRole("tab", { name: "Runs" });
    expect(runs.id).toBe(tabId("runs"));
    expect(runs.getAttribute("aria-controls")).toBe(tabPanelId("runs"));
    expect(screen.getAllByRole("tab").map((t) => t.tabIndex)).toEqual([0, -1, -1]);
  });

  it("a click selects", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: "Releases" }));
    expect(onChange).toHaveBeenCalledWith("releases");
    expect(selected()).toBe("Releases");
    expect(screen.getAllByRole("tab").map((t) => t.tabIndex)).toEqual([-1, -1, 0]);
  });

  it("arrow keys move and activate (wrapping); Home and End jump; focus follows", () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const list = screen.getByRole("tablist");
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(selected()).toBe("Runs");
    expect(document.activeElement).toBe(screen.getByRole("tab", { name: "Runs" }));
    fireEvent.keyDown(list, { key: "ArrowRight" });
    fireEvent.keyDown(list, { key: "ArrowRight" });
    expect(selected()).toBe("Graph");
    fireEvent.keyDown(list, { key: "ArrowLeft" });
    expect(selected()).toBe("Releases");
    fireEvent.keyDown(list, { key: "Home" });
    expect(selected()).toBe("Graph");
    fireEvent.keyDown(list, { key: "End" });
    expect(selected()).toBe("Releases");
    expect(onChange.mock.calls.map(([id]) => id)).toEqual(["runs", "releases", "graph", "releases", "graph", "releases"]);
    fireEvent.keyDown(list, { key: "a" });
    expect(onChange).toHaveBeenCalledTimes(6);
  });
});
