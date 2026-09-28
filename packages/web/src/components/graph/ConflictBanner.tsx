// "<path> changed outside the editor." [Use the version on disk] [Keep my version] (`$DRAFTS/07 §8.4`).
import { type ReactElement } from "react";
import { useDesign } from "../../api/context";
import { useDesignState } from "./designHooks";

export function ConflictBanner(): ReactElement | null {
  const design = useDesign();
  const s = useDesignState();
  if (s === null || s.saveState !== "conflict") return null;
  const details = s.error?.details as { path?: string } | null | undefined;
  const path = details?.path ?? s.processPath;
  return (
    <div className="wg-banner wg-banner-conflict" role="alert">
      <span><span className="wg-mono">{path}</span> changed outside the editor.</span>
      <button type="button" onClick={() => void design.resolveConflict("theirs")}>Use the version on disk</button>
      <button type="button" onClick={() => void design.resolveConflict("mine")}>Keep my version</button>
    </div>
  );
}
