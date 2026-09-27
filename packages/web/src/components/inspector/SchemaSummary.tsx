// Plain-language interface with its source label (`$DRAFTS/07 §7.10`). Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { Interface } from "../../api/types";

export interface SchemaSummaryProps {
  iface: Interface | null;
  updating?: boolean;                // "(updating…)" while the design is dirty
}

export function SchemaSummary(_props: SchemaSummaryProps): ReactElement | null {
  return null;
}
