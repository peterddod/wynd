// Proto-step editor: instruction, plain-language summary, examples, schema (advanced), env (`$DRAFTS/07 §7.9`).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { ReactElement } from "react";

export interface ProtoStepEditorProps {
  step: string;                      // step key in the open process
  protoPath: string;                 // workspace-relative proto file
}

export function ProtoStepEditor(_props: ProtoStepEditorProps): ReactElement | null {
  return null;
}
