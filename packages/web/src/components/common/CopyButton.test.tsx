import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { COPIED_MS, CopyButton } from "./CopyButton";

afterEach(() => {
  Reflect.deleteProperty(navigator, "clipboard");
  vi.useRealTimers();
});

describe("CopyButton", () => {
  it("writes the text, announces Copied, and resets after COPIED_MS", async () => {
    vi.useFakeTimers();
    const writeText = vi.fn(async () => undefined);
    Object.defineProperty(navigator, "clipboard", { value: { writeText }, configurable: true });
    render(<CopyButton text="localhost:5001/wynd/p:9f8e7d6" label="Copy image" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy image" }));
    });
    expect(writeText).toHaveBeenCalledWith("localhost:5001/wynd/p:9f8e7d6");
    expect(screen.getByRole("status").textContent).toBe("Copied");
    act(() => vi.advanceTimersByTime(COPIED_MS));
    expect(screen.getByRole("status").textContent).toBe("");
  });

  it("says so when there is no clipboard", async () => {
    render(<CopyButton text="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy" }));
    expect(await screen.findByText("Copy failed")).toBeTruthy();
  });
});
