// Target, inputs from the interface, fill from example, [Start] (`$DRAFTS/07 §11.2`). Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";
import type { Run } from "../../api/types";

export interface RunFormProps {
  processId: string;
  releaseId?: string | null;         // preselects that release as the target
  onStarted(run: Run): void;
}

export function RunForm(_props: RunFormProps): ReactElement | null {
  return null;
}
