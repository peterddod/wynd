// Image ref, commit, tests, build dir, [Create release…] (`$DRAFTS/07 §9.7`).
import { useState, type ReactElement } from "react";
import type { BuildResult as BuildResultDTO, Job } from "../../api/types";
import { CopyButton } from "../common/CopyButton";
import { NewReleaseDialog } from "../releases/NewReleaseDialog";

export interface BuildResultProps {
  job: Job;
  build: BuildResultDTO;
}

export function BuildResult({ job, build }: BuildResultProps): ReactElement {
  const [releasing, setReleasing] = useState(false);
  const { passed, total, source } = build.tests;
  return (
    <div className="wc-build">
      <dl className="wc-facts">
        <dt>Image</dt>
        <dd><code>{build.image}</code> <CopyButton text={build.image} label="Copy image" /></dd>
        <dt>Commit</dt>
        <dd><code title={build.commit}>{build.commit.slice(0, 7)}</code></dd>
        <dt>Tests</dt>
        <dd>
          {passed}/{total} passed
          {source === "registry" && <span className="wc-muted"> (reused result recorded for this commit)</span>}
        </dd>
        <dt>Build dir</dt>
        <dd><code>{build.build_dir}</code></dd>
      </dl>
      <button type="button" onClick={() => setReleasing(true)}>Create release…</button>
      {releasing && (
        <NewReleaseDialog open processId={job.process_id} commit={build.commit} onClose={() => setReleasing(false)} />
      )}
    </div>
  );
}
