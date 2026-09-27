// Per-step decisions, inferred schemas, questions, events (`$DRAFTS/07 §9.7`). Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";
import type { CompileSession, Job } from "../../api/types";

export interface CompileSessionViewProps {
  job: Job;
  session: CompileSession;
}

export function CompileSessionView(_props: CompileSessionViewProps): ReactElement | null {
  return null;
}
