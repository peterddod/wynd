// Commit/behind, image, state, trigger summary, env check, actions (`$DRAFTS/07 §10`). [Run now] opens a RunForm
// targeted at this release and switches to the Runs tab on the new run.
import { useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import type { EnvBinding, EnvVar, Release, ReleasePatch, Trigger } from "../../api/types";
import { invalidate, useQuery } from "../../state/query";
import { useUrlState } from "../../state/url";
import { CopyButton } from "../common/CopyButton";
import { Dialog } from "../common/Dialog";
import { ErrorBox } from "../common/ErrorBox";
import { formatTime } from "../runs/format";
import { RunForm } from "../runs/RunForm";
import { EnvBindingEditor } from "./EnvBindingEditor";
import { TriggerEditor, triggerComplete } from "./TriggerEditor";

export interface ReleaseCardProps {
  release: Release;
}

type Open = "run" | "trigger" | "env" | "delete" | null;

export function triggerSummary(r: Release): string {
  const t = r.trigger;
  switch (t.kind) {
    case "manual":
      return "manual";
    case "schedule":
      return `schedule · ${t.cron} (${t.timezone ?? "UTC"})${r.next_fire_at === null ? "" : ` · next ${formatTime(r.next_fire_at)}`}`;
    case "webhook":
      return `webhook · POST ${r.webhook_url ?? "(no URL yet)"}`;
  }
}

export function ReleaseCard({ release: r }: ReleaseCardProps): ReactElement {
  const api = useApi();
  const [, setUrl] = useUrlState();
  const check = useQuery(`envcheck:${r.id}`, () => api.releases.envCheck(r.id));
  const [open, setOpen] = useState<Open>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);

  async function act(action: () => Promise<unknown>, deleting = false): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      await action();
      setOpen(null);
      invalidate(`releases:${r.process_id}`);
      invalidate("processes");
      if (!deleting) invalidate(`envcheck:${r.id}`);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setBusy(false);
    }
  }

  const patch = (body: ReleasePatch): Promise<void> => act(() => api.releases.update(r.id, body));

  return (
    <article className="wo-card" aria-label={`Release ${r.short}`}>
      <header className="wo-card-head">
        <h3><code>{r.short}</code> <span className="wo-muted">{r.behind === 0 ? "HEAD" : `${r.behind} behind`}</span></h3>
        <span className={`wo-pill wo-state-${r.state}`} title={r.state_detail ?? undefined}>
          {r.state}{r.state_detail !== null && ` · ${r.state_detail}`}
        </span>
        {!r.enabled && <span className="wo-tag">disabled</span>}
      </header>
      <p className="wo-card-line"><code>{r.image}</code> <CopyButton text={r.image} label="Copy image" /></p>
      <p className="wo-card-line">
        {triggerSummary(r)}
        {r.trigger.kind === "webhook" && r.webhook_url !== null && <> <CopyButton text={r.webhook_url} label="Copy webhook URL" /></>}
      </p>
      <p className="wo-card-line" aria-live="polite">{envCheckText(check.data, check.error)}</p>
      <div className="wo-card-actions">
        <button type="button" onClick={() => setOpen("run")}>Run now</button>
        <button type="button" onClick={() => setOpen("trigger")}>Edit trigger</button>
        <button type="button" onClick={() => setOpen("env")}>Edit env</button>
        <button type="button" disabled={busy} onClick={() => void patch({ enabled: !r.enabled })}>{r.enabled ? "Disable" : "Enable"}</button>
        <button type="button" onClick={() => setOpen("delete")}>Delete</button>
      </div>
      {open === null && <ErrorBox error={error} />}
      {open === "run" && (
        <Dialog open title={`Run release ${r.short}`} onClose={() => setOpen(null)} actions={<button type="button" onClick={() => setOpen(null)}>Cancel</button>}>
          <RunForm
            processId={r.process_id}
            releaseId={r.id}
            onStarted={(run) => {
              setOpen(null);
              setUrl({ tab: "runs", run: run.id });
            }}
          />
        </Dialog>
      )}
      {open === "trigger" && (
        <EditTrigger release={r} busy={busy} error={error} onSave={(trigger) => void patch({ trigger })} onClose={() => setOpen(null)} />
      )}
      {open === "env" && (
        <EditEnv release={r} busy={busy} error={error} onSave={(env) => void patch({ env })} onClose={() => setOpen(null)} />
      )}
      {open === "delete" && (
        <Dialog
          open
          title={`Delete release ${r.short}?`}
          onClose={() => setOpen(null)}
          actions={
            <>
              <button type="button" onClick={() => setOpen(null)}>Cancel</button>
              <button type="button" disabled={busy} onClick={() => void act(() => api.releases.remove(r.id), true)}>Delete</button>
            </>
          }
        >
          <p>The release stops serving and its trigger is removed. The image and its build stay.</p>
          <ErrorBox error={error} />
        </Dialog>
      )}
    </article>
  );
}

function envCheckText(check: { ok: boolean; missing: string[]; unbound: string[] } | undefined, error: Error | null): string {
  if (error !== null) return `environment check failed: ${error.message}`;
  if (check === undefined) return "checking environment…";
  if (check.ok) return "✓ environment complete";
  const parts = [];
  if (check.missing.length > 0) parts.push(`missing: ${check.missing.join(", ")}`);
  if (check.unbound.length > 0) parts.push(`not bound: ${check.unbound.join(", ")}`);
  return `✕ ${parts.join("; ")}`;
}

interface EditProps<T> {
  release: Release;
  busy: boolean;
  error: Error | null;
  onSave(value: T): void;
  onClose(): void;
}

function EditTrigger({ release: r, busy, error, onSave, onClose }: EditProps<Trigger>): ReactElement {
  const api = useApi();
  const iface = useQuery(`iface:${r.process_id}:${r.commit}`, () => api.processes.iface(r.process_id, r.commit));
  const [trigger, setTrigger] = useState<Trigger>(r.trigger);
  return (
    <Dialog
      open
      title={`Trigger of release ${r.short}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button" disabled={busy || !triggerComplete(trigger)} onClick={() => onSave(trigger)}>Save</button>
        </>
      }
    >
      <TriggerEditor value={trigger} onChange={setTrigger} inputsSchema={iface.data?.inputs ?? null} />
      <ErrorBox error={error} />
    </Dialog>
  );
}

function EditEnv({ release: r, busy, error, onSave, onClose }: EditProps<Record<string, EnvBinding>>): ReactElement {
  const api = useApi();
  const builds = useQuery(`builds:${r.process_id}`, () => api.processes.builds(r.process_id));
  const [env, setEnv] = useState<Record<string, EnvBinding>>(r.env);
  const build = builds.data?.builds.find((b) => b.commit === r.commit);
  const vars = build?.env ?? Object.keys(r.env).map(boundOnly);
  return (
    <Dialog
      open
      title={`Environment of release ${r.short}`}
      onClose={onClose}
      actions={
        <>
          <button type="button" onClick={onClose}>Cancel</button>
          <button type="button" disabled={busy || builds.data === undefined} onClick={() => onSave(env)}>Save</button>
        </>
      }
    >
      {builds.data === undefined ? <p className="wo-muted">Loading the build's variables…</p> : <EnvBindingEditor vars={vars} value={env} onChange={setEnv} />}
      <ErrorBox error={error} />
    </Dialog>
  );
}

/** A bound var whose build is gone: all that is known is its name. */
function boundOnly(name: string): EnvVar {
  return { name, description: "", secret: false, required: false, default: null, one_of: null, used_by: [] };
}
