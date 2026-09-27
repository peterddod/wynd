// Controlled React Flow editor over the design session (`$DRAFTS/07 §7.6`). Selection is URL `sel`.
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { Connection } from "@xyflow/react";
import type { ReactElement } from "react";
import type { DesignSession } from "../../state/design";

export function GraphEditor(): ReactElement | null {
  return null;
}

/** Test hook for drag-connect (pointer drags are unreliable in jsdom): applies `routeExit` for a connection from an
 *  exit handle and returns the new selection ("b:<e>:<b>"), or null when the connection is not valid. */
export function handleConnect(design: DesignSession, connection: Connection): string | null {
  throw new Error("not implemented");
}
