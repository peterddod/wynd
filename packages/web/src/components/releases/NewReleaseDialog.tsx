// Build, trigger, env binding -> POST /api/releases (`$DRAFTS/07 §10`). Also opened from a build job's
// [Create release…]. Only built processes can be released: without a build the dialog offers [Build].
import { useState, type ReactElement } from "react";
import { useApi, useDesign } from "../../api/context";
import type { EnvBinding, Trigger } from "../../api/types";
import { invalidate, useQuery } from "../../state/query";
import { useUrlState } from "../../state/url";
import { Dialog } from "../common/Dialog";
import { ErrorBox } from "../common/ErrorBox";
import { startProcessJob } from "../process/startJob";
import { formatTime } from "../runs/format";
import { EnvBindingEditor, defaultBindings } from "./EnvBindingEditor";
import { TriggerEditor, triggerComplete } from "./TriggerEditor";

export interface NewReleaseDialogProps {
  open: boolean;
  processId: string;
  commit?: string | null;            // preselected build; default the HEAD build
  onClose(): void;
}

export function NewReleaseDialog({ open, processId, commit = null, onClose }: NewReleaseDialogProps): ReactElement | null {
  return open ? <NewReleaseForm processId={processId} commit={commit} onClose={onClose} /> : null;
}

function NewReleaseForm({ processId, commit, onClose }: { processId: string; commit: string | null; onClose(): void }): ReactElement {
  const api = useApi();
  const design = useDesign();
  const [, setUrl] = useUrlState();
  const builds = useQuery(`builds:${processId}`, () => api.processes.builds(processId));
  const [picked, setPicked] = useState<string | null>(commit);
  const [trigger, setTrigger] = useState<Trigger>({ kind: "manual" });
  const [envByCommit, setEnvByCommit] = useState<Record<string, Record<string, EnvBinding>>>({});
  const [enabled, setEnabled] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const list = builds.data?.builds ?? [];
  const build = list.find((b) => b.commit === picked) ?? list.find((b) => b.at_head) ?? list[0];
  const iface = useQuery(
    `iface:${processId}:${build?.commit ?? "HEAD"}`,
    () => api.processes.iface(processId, build?.commit),
    { enabled: build !== undefined },
  );
  const env = build === undefined ? {} : envByCommit[build.commit] ?? defaultBindings(build.env);

  async function run(action: () => Promise<void>): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
      setBusy(false);
    }
  }

  const create = (): Promise<void> => run(async () => {
    if (build === undefined) return;
    await api.releases.create({ process_id: processId, commit: build.commit, trigger, env, enabled });
    invalidate(`releases:${processId}`);
    invalidate("processes");
    onClose();
  });

  const startBuild = (): Promise<void> => run(async () => {
    const { chat } = await startProcessJob(api, design, processId, "build");
    setUrl({ chat: chat.id });
    onClose();
  });

  const noBuilds = builds.data !== undefined && list.length === 0;
  return (
    <Dialog
      open
      title="New release"
      className="wo-release-dialog"
      onClose={onClose}
      actions={
        <>
          <button type="button" onClick={onClose}>Cancel</button>
          {!noBuilds && (
            <button type="button" disabled={build === undefined || busy || !triggerComplete(trigger)} onClick={() => void create()}>
              Create
            </button>
          )}
        </>
      }
    >
      <div className="wo-form">
        <ErrorBox error={builds.error} onRetry={() => void builds.reload()} />
        {builds.data === undefined && builds.loading && <p className="wo-muted">Loading builds…</p>}
        {noBuilds && (
          <p>
            Only built processes can be released: Build this process first.{" "}
            <button type="button" disabled={busy} onClick={() => void startBuild()}>Build</button>
          </p>
        )}
        {build !== undefined && (
          <>
            <label>
              Build
              <select value={build.commit} onChange={(e) => setPicked(e.target.value)}>
                {list.map((b) => (
                  <option key={b.commit} value={b.commit}>
                    {`${b.short} · ${formatTime(b.built_at)} · ${b.at_head ? "HEAD" : `${b.behind} behind`}`}
                  </option>
                ))}
              </select>
            </label>
            <p className="wo-muted"><code>{build.image}</code></p>
            <TriggerEditor value={trigger} onChange={setTrigger} inputsSchema={iface.data?.inputs ?? null} />
            <EnvBindingEditor
              vars={build.env}
              value={env}
              onChange={(next) => setEnvByCommit((m) => ({ ...m, [build.commit]: next }))}
            />
            <label className="wo-check">
              <input type="checkbox" checked={enabled} onChange={(e) => setEnabled(e.target.checked)} />
              Enabled (start serving now)
            </label>
          </>
        )}
        <ErrorBox error={error} />
      </div>
    </Dialog>
  );
}
