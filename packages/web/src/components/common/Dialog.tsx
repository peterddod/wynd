// Native <dialog> with showModal() (focus trap, Esc closes), rendered in place, never portalled, so a dialog opened
// inside the design surface keeps focus inside it (`$DRAFTS/07 §4.8`). Mounted only while open; the parent owns
// `open`, so Esc and the close button call `onClose` instead of closing the element themselves.
import { useEffect, useId, useRef, type KeyboardEvent, type ReactElement, type ReactNode, type SyntheticEvent } from "react";
import { Icon } from "./Icon";

export interface DialogProps {
  open: boolean;
  title: string;
  onClose(): void;
  children?: ReactNode;
  actions?: ReactNode;               // footer buttons
  className?: string;
}

export function Dialog({ open, title, onClose, children, actions, className }: DialogProps): ReactElement | null {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const el = ref.current;
    if (el === null || el.open) return;
    // jsdom has no showModal(): the open attribute renders it the same way (without the top layer).
    if (typeof el.showModal === "function") el.showModal();
    else el.setAttribute("open", "");
  }, [open]);

  if (!open) return null;

  // A control inside that handles Escape itself (a suggestions popover) prevents its default.
  const onKeyDown = (e: KeyboardEvent<HTMLDialogElement>): void => {
    if (e.key !== "Escape" || e.defaultPrevented) return;
    e.preventDefault();
    e.stopPropagation();
    onClose();
  };
  // The native close request (Esc) would close the element behind React's back.
  const onCancel = (e: SyntheticEvent<HTMLDialogElement>): void => e.preventDefault();

  return (
    <dialog
      ref={ref}
      className={className === undefined ? "wy-dialog" : `wy-dialog ${className}`}
      aria-labelledby={titleId}
      onKeyDown={onKeyDown}
      onCancel={onCancel}
      onClose={onClose}
    >
      <header className="wy-dialog-head">
        <h2 id={titleId}>{title}</h2>
        <button type="button" className="wy-icon-btn" aria-label="Close" onClick={onClose}>
          <Icon name="close" />
        </button>
      </header>
      <div className="wy-dialog-body">{children}</div>
      {actions !== undefined && <footer className="wy-dialog-actions">{actions}</footer>}
    </dialog>
  );
}
