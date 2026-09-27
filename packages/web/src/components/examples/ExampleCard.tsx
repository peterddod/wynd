// One example: sentence title, exit, typed inputs/outputs forms (`$DRAFTS/07 §7.11`). Also used read-only and for
// corrections by the compile job's ProposalCard. Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";
import type { Interface, Json } from "../../api/types";

export interface ExampleCardProps {
  example: Json;
  exits: string[];
  iface: Interface | null;
  onChange?(next: Json): void;       // absent: read-only
  onDuplicate?(): void;
  onMoveUp?(): void;
  onMoveDown?(): void;
  onRemove?(): void;
}

export function ExampleCard(_props: ExampleCardProps): ReactElement | null {
  return null;
}
