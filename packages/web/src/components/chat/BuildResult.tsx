// Image ref, commit, tests, build dir, [Create release…] (`$DRAFTS/07 §9.7`). Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { ReactElement } from "react";
import type { BuildResult as BuildResultDTO, Job } from "../../api/types";

export interface BuildResultProps {
  job: Job;
  build: BuildResultDTO;
}

export function BuildResult(_props: BuildResultProps): ReactElement | null {
  return null;
}
