// "+ Step" and "Re-layout" (`$DRAFTS/07 §7.6`).
import type { ReactElement } from "react";

export interface GraphToolbarProps {
  readOnly: boolean;
  onAddStep(): void;
  onRelayout(): void;
}

export function GraphToolbar({ readOnly, onAddStep, onRelayout }: GraphToolbarProps): ReactElement | null {
  return (
    <div className="wg-toolbar" role="toolbar" aria-label="Graph">
      <button type="button" onClick={onAddStep} disabled={readOnly}>+ Step</button>
      <button type="button" onClick={onRelayout}>Re-layout</button>
    </div>
  );
}
