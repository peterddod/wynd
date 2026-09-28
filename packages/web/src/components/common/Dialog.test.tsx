import { fireEvent, render, screen } from "@testing-library/react";
import { useState, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { Dialog } from "./Dialog";

function Harness({ onClose }: { onClose?(): void }): ReactElement {
  const [open, setOpen] = useState(false);
  const close = (): void => {
    onClose?.();
    setOpen(false);
  };
  return (
    <div data-testid="surface">
      <button type="button" onClick={() => setOpen(true)}>
        Open
      </button>
      <Dialog open={open} title="Remove step" onClose={close} actions={<button type="button">Remove</button>}>
        <p>Remove step fix and its edges?</p>
      </Dialog>
    </div>
  );
}

describe("Dialog", () => {
  it("renders nothing while closed", () => {
    render(<Harness />);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("opens as a dialog labelled by its title, in place (inside its parent), with body and actions", () => {
    render(<Harness />);
    fireEvent.click(screen.getByText("Open"));
    const dialog = screen.getByRole("dialog", { name: "Remove step" });
    expect(dialog.hasAttribute("open")).toBe(true);
    expect(screen.getByTestId("surface").contains(dialog)).toBe(true);
    expect(screen.getByText("Remove step fix and its edges?")).toBeTruthy();
    expect(screen.getByRole("button", { name: "Remove" })).toBeTruthy();
  });

  it("the close button and Escape call onClose", () => {
    const onClose = vi.fn();
    render(<Harness onClose={onClose} />);
    fireEvent.click(screen.getByText("Open"));
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(onClose).toHaveBeenCalledOnce();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByText("Open"));
    fireEvent.keyDown(screen.getByText("Remove step fix and its edges?"), { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("an Escape already handled inside (default prevented) does not close it", () => {
    const onClose = vi.fn();
    render(
      <Dialog open title="Edit branch" onClose={onClose}>
        <input aria-label="when" onKeyDown={(e) => e.key === "Escape" && e.preventDefault()} />
      </Dialog>,
    );
    fireEvent.keyDown(screen.getByRole("textbox", { name: "when" }), { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });

  it("uses showModal when the browser has it, and cancels the native close request", () => {
    const showModal = vi.fn(function (this: HTMLDialogElement) {
      this.setAttribute("open", "");
    });
    Object.defineProperty(HTMLDialogElement.prototype, "showModal", { value: showModal, configurable: true });
    try {
      render(<Dialog open title="T" onClose={() => undefined} className="wide" />);
      const dialog = screen.getByRole("dialog");
      expect(showModal).toHaveBeenCalledOnce();
      expect(dialog.className).toBe("wy-dialog wide");
      const cancel = new Event("cancel", { cancelable: true });
      dialog.dispatchEvent(cancel);
      expect(cancel.defaultPrevented).toBe(true);
    } finally {
      delete (HTMLDialogElement.prototype as Partial<HTMLDialogElement>).showModal;
    }
  });
});
