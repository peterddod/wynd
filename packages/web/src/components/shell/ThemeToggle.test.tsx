import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { THEME_KEY, setTheme } from "../../state/theme";
import { ThemeToggle } from "./ThemeToggle";

afterEach(() => {
  setTheme("system");
});

describe("ThemeToggle", () => {
  it("cycles system -> light -> dark -> system, applying and storing the choice", () => {
    render(<ThemeToggle />);
    const root = document.documentElement;
    const button = (): HTMLElement => screen.getByRole("button", { name: /^Theme:/ });
    expect(button().getAttribute("aria-label")).toBe("Theme: System (switch to Light)");
    fireEvent.click(button());
    expect(root.dataset.theme).toBe("light");
    expect(localStorage.getItem(THEME_KEY)).toBe("light");
    expect(button().getAttribute("aria-label")).toBe("Theme: Light (switch to Dark)");
    fireEvent.click(button());
    expect(root.dataset.theme).toBe("dark");
    expect(button().textContent).toBe("Dark");
    fireEvent.click(button());
    expect(root.hasAttribute("data-theme")).toBe(false);
    expect(localStorage.getItem(THEME_KEY)).toBeNull();
  });
});
