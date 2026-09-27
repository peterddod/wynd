// <nav aria-label="Processes">: search, process list, new process (ProcessList, `$DRAFTS/07 §4.1`, §6.1). Hidden
// (still mounted, so the search keeps its state) when closed; an overlay drawer below 1100px (app.css).
import type { ReactElement } from "react";
import { Icon } from "../common/Icon";
import { ProcessList } from "../process/ProcessList";

export interface SidebarProps {
  open: boolean;
  onClose(): void;
}

export const SIDEBAR_ID = "wy-sidebar";

export function Sidebar({ open, onClose }: SidebarProps): ReactElement {
  return (
    <nav id={SIDEBAR_ID} className="wy-sidebar" aria-label="Processes" hidden={!open}>
      <div className="wy-drawer-head">
        <span>Processes</span>
        <button type="button" className="wy-icon-btn" aria-label="Close processes" onClick={onClose}>
          <Icon name="close" />
        </button>
      </div>
      <ProcessList />
    </nav>
  );
}
