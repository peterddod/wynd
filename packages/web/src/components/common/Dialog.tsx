// Native <dialog> with showModal() (focus trap, Esc closes), rendered in place, never portalled (`$DRAFTS/07 §4.8`).
// Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ReactElement, ReactNode } from "react";

export interface DialogProps {
  open: boolean;
  title: string;
  onClose(): void;
  children?: ReactNode;
  actions?: ReactNode;               // footer buttons
  className?: string;
}

export function Dialog(_props: DialogProps): ReactElement | null {
  return null;
}
