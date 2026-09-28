// Target, inputs from the interface, fill from example, [Start] (`$DRAFTS/07 §11.2`). A local run commits the open
// design first, so it runs what the user sees; a release target fires the release's trigger. No <form> element: the
// form is also rendered inside dialogs.
import { useState, type ReactElement } from "react";
import { useApi, useDesign, useMeta } from "../../api/context";
import type { Build, Json, JsonSchema, Release, Run, RunTarget } from "../../api/types";
import { invalidate, useQuery } from "../../state/query";
import { ErrorBox } from "../common/ErrorBox";
import { isPathSchema } from "./format";
import { SchemaForm, missingRequired } from "./SchemaForm";

export interface RunFormProps {
  processId: string;
  releaseId?: string | null;         // preselects that release as the target
  onStarted(run: Run): void;
}

interface TargetChoice {
  key: string;
  label: string;
  target: RunTarget;
  commit: string | null;             // the interface to use; null: the working tree
}

export function targetChoices(builds: Build[], releases: Release[]): TargetChoice[] {
  return [
    { key: "local", label: "Local (source at HEAD)", target: { kind: "local" }, commit: null },
    ...builds.map((b) => ({
      key: `image:${b.commit}`,
      label: `Image ${b.short}${b.at_head ? " (HEAD)" : ` (${b.behind} behind)`}`,
      target: { kind: "image" as const, commit: b.commit },
      commit: b.commit,
    })),
    ...releases.map((r) => ({
      key: `release:${r.id}`,
      label: `Release ${r.short} (${r.trigger.kind})`,
      target: { kind: "release" as const, release_id: r.id },
      commit: r.commit,
    })),
  ];
}

/** Example inputs with relative `path` values made absolute against the process directory (PLAN §3.3). */
export function exampleInputs(
  inputs: Record<string, Json>, schema: JsonSchema | null, workspaceRoot: string | null, processDir: string | null,
): Record<string, Json> {
  const out: Record<string, Json> = {};
  for (const [name, v] of Object.entries(inputs)) {
    const relative = typeof v === "string" && v !== "" && !v.startsWith("/") && !v.startsWith("{tmp}");
    out[name] = relative && workspaceRoot !== null && processDir !== null && isPathSchema(schema?.properties?.[name])
      ? `${workspaceRoot}/${processDir}/${v}`
      : v;
  }
  return out;
}

export function RunForm({ processId, releaseId = null, onStarted }: RunFormProps): ReactElement {
  const api = useApi();
  const design = useDesign();
  const meta = useMeta();
  const builds = useQuery(`builds:${processId}`, () => api.processes.builds(processId));
  const releases = useQuery(`releases:${processId}`, () => api.releases.list(processId));
  const summary = useQuery(`processes:${processId}`, () => api.processes.get(processId));
  const [choiceKey, setChoiceKey] = useState(releaseId === null ? "local" : `release:${releaseId}`);
  const choices = targetChoices(builds.data?.builds ?? [], releases.data?.releases ?? []);
  const choice = choices.find((c) => c.key === choiceKey) ?? (choices[0] as TargetChoice);
  const iface = useQuery(`iface:${processId}:${choice.commit ?? "HEAD"}`, () => api.processes.iface(processId, choice.commit ?? undefined));
  const [inputs, setInputs] = useState<Record<string, Json>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<Error | null>(null);
  const schema = iface.data?.inputs ?? null;
  const examples = iface.data?.examples ?? [];
  const missing = missingRequired(schema, inputs);

  function fillFromExample(index: string): void {
    const example = examples[Number(index)];
    if (example === undefined) return;
    setInputs(exampleInputs(example.inputs, schema, meta?.workspace.root ?? null, summary.data?.path ?? null));
  }

  async function start(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      let run: Run;
      switch (choice.target.kind) {
        case "release":
          run = await api.releases.trigger(choice.target.release_id, inputs);
          break;
        case "local":
          await design.commit("before_job");
          run = await api.runs.create({ process_id: processId, target: choice.target, inputs });
          break;
        case "image":
          run = await api.runs.create({ process_id: processId, target: choice.target, inputs });
          break;
      }
      invalidate(`runs:${processId}`);
      onStarted(run);
    } catch (err) {
      setError(err instanceof Error ? err : new Error(String(err)));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="wo-run-form wo-form">
      <label>
        Target
        <select value={choice.key} onChange={(e) => setChoiceKey(e.target.value)}>
          {choices.map((c) => <option key={c.key} value={c.key}>{c.label}</option>)}
        </select>
      </label>
      {examples.length > 0 && (
        <label>
          Fill from example
          <select value="" onChange={(e) => fillFromExample(e.target.value)}>
            <option value="">Choose an example…</option>
            {examples.map((ex, i) => (
              <option key={i} value={i}>{`Example ${i + 1} → ${ex.exit}: ${JSON.stringify(ex.inputs)}`}</option>
            ))}
          </select>
        </label>
      )}
      <ErrorBox error={iface.error} onRetry={() => void iface.reload()} />
      {iface.data === undefined && iface.loading ? (
        <p className="wo-muted">Loading the process interface…</p>
      ) : (
        <SchemaForm schema={schema} value={inputs} onChange={setInputs} allowUpload />
      )}
      <ErrorBox error={error} />
      <div className="wo-form-actions">
        {missing.length > 0 && <span className="wo-muted">Required: {missing.join(", ")}</span>}
        <button type="button" disabled={busy || missing.length > 0 || iface.data === undefined} onClick={() => void start()}>
          Start
        </button>
      </div>
    </div>
  );
}
