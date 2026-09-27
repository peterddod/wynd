import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { SIDEBAR_ID, Sidebar } from "./Sidebar";

// ProcessList belongs to WEB-OPS; the sidebar only hosts it.
vi.mock("../process/ProcessList", () => ({ ProcessList: () => <input aria-label="Search processes" defaultValue="" /> }));

describe("Sidebar", () => {
  it("is the Processes navigation landmark hosting the process list", () => {
    render(<Sidebar open onClose={vi.fn()} />);
    const nav = screen.getByRole("navigation", { name: "Processes" });
    expect(nav.id).toBe(SIDEBAR_ID);
    expect(nav.hidden).toBe(false);
    expect(screen.getByRole("textbox", { name: "Search processes" })).toBeTruthy();
  });

  it("closed, it stays mounted (the search keeps its text) but hidden", () => {
    const view = render(<Sidebar open onClose={vi.fn()} />);
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "invoice" } });
    view.rerender(<Sidebar open={false} onClose={vi.fn()} />);
    expect(screen.queryByRole("navigation")).toBeNull();
    view.rerender(<Sidebar open onClose={vi.fn()} />);
    expect((screen.getByRole("textbox") as HTMLInputElement).value).toBe("invoice");
  });

  it("the drawer's close button calls onClose", () => {
    const onClose = vi.fn();
    render(<Sidebar open onClose={onClose} />);
    fireEvent.click(screen.getByRole("button", { name: "Close processes" }));
    expect(onClose).toHaveBeenCalledOnce();
  });
});
