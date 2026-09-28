// Spinner; a static "…" under prefers-reduced-motion (`$DRAFTS/07 §4.7`, app.css).
import type { ReactElement } from "react";

export interface SpinnerProps {
  label?: string;                    // accessible label, default "Loading"
}

export function Spinner({ label = "Loading" }: SpinnerProps): ReactElement {
  return (
    <span className="wy-spinner" role="status" aria-label={label}>
      <span className="wy-spinner-ring" aria-hidden="true" />
    </span>
  );
}
