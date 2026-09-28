// Header [Compile] [Build] [Release…] for the open process (`$DRAFTS/07 §6.3`). Compile and build commit the open
// design first ("before_job"), submit the job and open its chat; while a job of that kind runs, the button opens it.
import { useState, type ReactElement } from "react";
import { useApi, useDesign } from "../../api/context";
import type { Job } from "../../api/types";
import { useQuery } from "../../state/query";
import { toast } from "../../state/toasts";
import { useUrlState } from "../../state/url";
import { Dialog } from "../common/Dialog";
import { NewReleaseDialog } from "../releases/NewReleaseDialog";
import { errorMessage } from "../runs/format";
import { dirtyPaths, startProcessJob, type JobButton } from "./startJob";

export interface ProcessActionsProps {
  processId: string;
}

export function ProcessActions({ processId }: ProcessActionsProps): ReactElement {
  const api = useApi();
  const design = useDesign();
  const [, setUrl] = useUrlState();
  const summary = useQuery(`processes:${processId}`, () => api.processes.get(processId));
  const active = useQuery("jobs:active", () => api.jobs.list({ active: true }));
  const builds = useQuery(`builds:${processId}`, () => api.processes.builds(processId));
  const [busy, setBusy] = useState<JobButton | null>(null);
  const [dirty, setDirty] = useState<{ kind: JobButton; paths: string[] } | null>(null);
  const [releasing, setReleasing] = useState(false);

  const status = summary.data?.status ?? null;
  const mine = (active.data?.jobs ?? []).filter((j) => j.process_id === processId);
  const compileJob = mine.find((j) => j.kind === "compile" && ["queued", "running", "awaiting_input"].includes(j.status));
  const buildJob = mine.find((j) => j.kind === "build" && (j.status === "queued" || j.status === "running"));
  const designOnly = status?.design_steps ?? [];
  const canBuild = status !== null && !status.design;
  const canRelease = status?.built === true || (builds.data?.builds.length ?? 0) > 0;

  function openChat(job: Job): void {
    if (job.chat_id !== null) setUrl({ chat: job.chat_id });
  }

  async function start(kind: JobButton): Promise<void> {
    setBusy(kind);
    setDirty(null);
    try {
      const { chat } = await startProcessJob(api, design, processId, kind);
      setUrl({ chat: chat.id });
    } catch (err) {
      const paths = dirtyPaths(err);
      if (paths !== null) setDirty({ kind, paths });
      else toast(`${kind === "compile" ? "Compile" : "Build"} did not start: ${errorMessage(err)}`, { level: "error" });
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="wo-actions" role="group" aria-label="Process actions">
      {compileJob !== undefined ? (
        <button type="button" onClick={() => openChat(compileJob)}>{compileLabel(compileJob)}</button>
      ) : (
        <button type="button" disabled={busy !== null} onClick={() => void start("compile")}>Compile</button>
      )}
      {buildJob !== undefined ? (
        <button type="button" onClick={() => openChat(buildJob)}>Building…</button>
      ) : (
        <button
          type="button"
          disabled={!canBuild || busy !== null}
          title={status?.design === true ? `Compile first: steps ${designOnly.join(", ")} are design-only` : undefined}
          onClick={() => void start("build")}
        >
          Build
        </button>
      )}
      <button
        type="button"
        disabled={!canRelease}
        title={canRelease ? undefined : "Only built processes can be released"}
        onClick={() => { setUrl({ tab: "releases" }); setReleasing(true); }}
      >
        Release…
      </button>
      {dirty !== null && (
        <Dialog
          open
          title="Uncommitted changes"
          onClose={() => setDirty(null)}
          actions={
            <>
              <button type="button" onClick={() => setDirty(null)}>Close</button>
              <button type="button" onClick={() => void start(dirty.kind)}>Retry</button>
            </>
          }
        >
          <p>Commit or discard these changes outside Wynd, then retry.</p>
          <ul className="wo-paths">{dirty.paths.map((p) => <li key={p}><code>{p}</code></li>)}</ul>
        </Dialog>
      )}
      <NewReleaseDialog open={releasing} processId={processId} onClose={() => setReleasing(false)} />
    </div>
  );
}

function compileLabel(job: Job): string {
  if (job.status !== "awaiting_input") return "Compiling…";
  const pending = job.session?.questions.filter((q) => q.status === "pending").length ?? 0;
  return `Needs answers (${pending})`;
}
