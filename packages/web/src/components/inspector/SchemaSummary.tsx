// Plain-language interface with its source label (`$DRAFTS/07 §7.10`).
import type { ReactElement } from "react";
import type { Interface, InterfaceSource } from "../../api/types";
import { stepSentence } from "../../model/schemaText";

export interface SchemaSummaryProps {
  iface: Interface | null;
  updating?: boolean;                // "(updating…)" while the design is dirty
}

export const SOURCE_LABEL: Record<InterfaceSource, string> = {
  declared: "as declared",
  inferred: "inferred from examples",
  compiled: "from the compiled step",
  process: "child process contract",
};

export function SchemaSummary({ iface, updating = false }: SchemaSummaryProps): ReactElement | null {
  if (iface === null || iface.source === null) {
    return <p className="wg-schema-summary wg-muted">Not declared. The compiler will infer it from the examples.</p>;
  }
  return (
    <div className="wg-schema-summary">
      <p>{stepSentence(iface)}</p>
      <small className="wg-muted">{SOURCE_LABEL[iface.source]}{updating ? " (updating…)" : ""}</small>
    </div>
  );
}
