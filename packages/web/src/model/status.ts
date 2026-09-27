// Status badges: pure display of controller-derived flags (`$DRAFTS/07 §6.2`). Stub from WEB-SCAFFOLD; WEB-OPS implements it.
import type { ProcessStatus } from "../api/types";

export interface Badge {
  kind: "design" | "compiled" | "built" | "released" | "tests_failed";
  label: string;
  title: string;
}

export function statusBadges(s: ProcessStatus): Badge[] {
  throw new Error("not implemented");
}
