// One row per build env var: from environment or value; secrets are from_env only (`$DRAFTS/07 §10`).
// Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ReactElement } from "react";
import type { EnvBinding, EnvVar } from "../../api/types";

export interface EnvBindingEditorProps {
  vars: EnvVar[];
  value: Record<string, EnvBinding>;
  onChange(next: Record<string, EnvBinding>): void;
}

export function EnvBindingEditor(_props: EnvBindingEditorProps): ReactElement | null {
  return null;
}
