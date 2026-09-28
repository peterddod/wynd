import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { clearToasts, toast } from "../../state/toasts";
import { Toasts } from "./Toasts";

beforeEach(() => {
  clearToasts();
});

describe("Toasts", () => {
  it("renders the store in a polite live region", () => {
    render(<Toasts />);
    const region = screen.getByLabelText("Notifications");
    expect(region.getAttribute("aria-live")).toBe("polite");
    act(() => {
      toast("Process drafts/intake no longer exists", { level: "warning" });
    });
    expect(region.textContent).toContain("Process drafts/intake no longer exists");
  });

  it("an action runs and dismisses its toast; Dismiss removes one toast", () => {
    const run = vi.fn();
    render(<Toasts />);
    act(() => {
      toast("Compile failed", { level: "error", action: { label: "Open chat", run } });
      toast("Saved");
    });
    fireEvent.click(screen.getByRole("button", { name: "Open chat" }));
    expect(run).toHaveBeenCalledOnce();
    expect(screen.queryByText("Compile failed")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(screen.queryByText("Saved")).toBeNull();
  });
});
