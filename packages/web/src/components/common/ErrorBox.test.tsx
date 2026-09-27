import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import { ErrorBox } from "./ErrorBox";

describe("ErrorBox", () => {
  it("renders nothing without an error", () => {
    const { container } = render(<ErrorBox error={null} />);
    expect(container.innerHTML).toBe("");
  });

  it("an ApiError shows its code and message as an alert; Retry calls onRetry", () => {
    const onRetry = vi.fn();
    render(<ErrorBox error={new ApiError(409, "not_built", "build the process first")} onRetry={onRetry} />);
    const alert = screen.getByRole("alert");
    expect(alert.textContent).toContain("not_built");
    expect(alert.textContent).toContain("build the process first");
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("a plain Error shows its message, and no Retry without onRetry", () => {
    render(<ErrorBox error={new Error("boom")} />);
    expect(screen.getByRole("alert").textContent).toBe("boom");
    expect(screen.queryByRole("button")).toBeNull();
  });
});
