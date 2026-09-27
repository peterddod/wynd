// Polls GET /api/jobs/{id}/logs?offset= every 1 s while active; monospace tail (`$DRAFTS/07 §9.7`).
// Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";

export interface JobLogsProps {
  jobId: string;
  active: boolean;
}

export function JobLogs(_props: JobLogsProps): ReactElement | null {
  return null;
}
