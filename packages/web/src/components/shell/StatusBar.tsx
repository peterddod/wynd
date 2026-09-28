// Design save state from the design store ("Saved · committed a1b2c3d", "Not saved: … [Retry]"), aria-live polite
// (`$DRAFTS/07 §4.1`, §4.6). The conflict itself is resolved in the design surface's ConflictBanner.
import type { ReactElement } from "react";
import { useDesign } from "../../api/context";
import type { DesignState } from "../../state/design";
import { useStore } from "../../state/store";

export type SaveTone = "ok" | "busy" | "warn" | "err";

export function saveStatus(s: DesignState): { text: string; tone: SaveTone } {
  if (s.lockedBy !== null) return { text: "Assistant is working on this process…", tone: "busy" };
  if (s.parseError !== null) return { text: "Read-only: process.yaml can't be parsed", tone: "warn" };
  switch (s.saveState) {
    case "pending":
      return { text: "Unsaved changes", tone: "busy" };
    case "saving":
      return { text: "Saving…", tone: "busy" };
    case "error":
      return { text: `Not saved: ${s.error?.message ?? "unknown error"}`, tone: "err" };
    case "conflict":
      return { text: "Not saved: changed outside the editor", tone: "warn" };
    case "clean":
      if (s.uncommitted || s.lastCommit === null) return { text: "Saved", tone: "ok" };
      return { text: `Saved · committed ${s.lastCommit.sha.slice(0, 7)}`, tone: "ok" };
  }
}

export function StatusBar(): ReactElement | null {
  const design = useDesign();
  const state = useStore(design.store, (s) => s);
  if (state === null) return null;
  const { text, tone } = saveStatus(state);
  return (
    <div className={`wy-statusbar wy-save-${tone}`} role="status">
      <span className="wy-save-dot" aria-hidden="true">●</span>
      <span>{text}</span>
      {state.saveState === "error" && (
        <button type="button" onClick={() => void design.flush().catch(() => undefined)}>
          Retry
        </button>
      )}
    </div>
  );
}
