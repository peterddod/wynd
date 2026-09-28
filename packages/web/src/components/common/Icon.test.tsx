import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { ICONS, Icon, type IconName } from "./Icon";
import { Spinner } from "./Spinner";

describe("Icon", () => {
  it("is decorative without a title", () => {
    const { container } = render(<Icon name="copy" />);
    const svg = container.querySelector("svg");
    expect(svg?.getAttribute("aria-hidden")).toBe("true");
    expect(svg?.getAttribute("width")).toBe("16");
    expect(screen.queryByRole("img")).toBeNull();
  });

  it("with a title it is an image with that name", () => {
    render(<Icon name="warning" title="Has issues" size={20} />);
    const img = screen.getByRole("img", { name: "Has issues" });
    expect(img.getAttribute("width")).toBe("20");
  });

  it("every name draws at least one shape", () => {
    for (const name of Object.keys(ICONS) as IconName[]) {
      const { container, unmount } = render(<Icon name={name} />);
      expect(container.querySelectorAll("path, circle, rect").length, name).toBeGreaterThan(0);
      unmount();
    }
  });
});

describe("Spinner", () => {
  it("is a status with an accessible label", () => {
    render(<Spinner />);
    expect(screen.getByRole("status", { name: "Loading" })).toBeTruthy();
    render(<Spinner label="Loading process_supplier_invoice" />);
    expect(screen.getByRole("status", { name: "Loading process_supplier_invoice" })).toBeTruthy();
  });
});
