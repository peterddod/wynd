// Focus boundary of the design editors: blur out of it (or the tab going hidden) commits (`$DRAFTS/07 §8.3`).
// App wraps the graph (GraphEditor + Inspector) and process-settings tabs in it. Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement, ReactNode } from "react";

export interface DesignSurfaceProps {
  children?: ReactNode;
  className?: string;
}

export function DesignSurface(_props: DesignSurfaceProps): ReactElement | null {
  return null;
}
