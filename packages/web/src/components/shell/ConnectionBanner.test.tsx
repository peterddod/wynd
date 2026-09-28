import { act, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { reportNetworkError, resetConnection, setHealthProbe } from "../../state/connection";
import { ConnectionBanner } from "./ConnectionBanner";

afterEach(() => {
  resetConnection();
  vi.useRealTimers();
});

describe("ConnectionBanner", () => {
  it("is absent online, alerts while offline, and goes away on recovery", async () => {
    vi.useFakeTimers();
    let up = false;
    setHealthProbe(async () => {
      if (!up) throw new Error("down");
    });
    render(<ConnectionBanner />);
    expect(screen.queryByRole("alert")).toBeNull();
    act(() => reportNetworkError());
    expect(screen.getByRole("alert").textContent).toBe("Can't reach wynd serve-api. Retrying…");
    up = true;
    await act(() => vi.advanceTimersByTimeAsync(1000));
    expect(screen.queryByRole("alert")).toBeNull();
  });
});
