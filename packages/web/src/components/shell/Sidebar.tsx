// <nav aria-label="Processes">: search, process list, new process (`$DRAFTS/07 §4.1`, §6.1); a drawer below 1100px.
// Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ReactElement } from "react";

export interface SidebarProps {
  open: boolean;
  onClose(): void;
}

export function Sidebar(_props: SidebarProps): ReactElement | null {
  return null;
}
