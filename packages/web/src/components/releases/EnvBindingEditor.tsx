// One row per build env var: not bound, from environment or value; secrets are from_env only (`$DRAFTS/07 §10`).
// A `from_env` source that is unset stops the release from starting, so only required vars (outside a one-of
// group, without a default) are bound by default.
import type { ReactElement } from "react";
import type { EnvBinding, EnvVar } from "../../api/types";

export interface EnvBindingEditorProps {
  vars: EnvVar[];
  value: Record<string, EnvBinding>;
  onChange(next: Record<string, EnvBinding>): void;
}

type Source = "none" | "from_env" | "value";

export function defaultBindings(vars: EnvVar[]): Record<string, EnvBinding> {
  const out: Record<string, EnvBinding> = {};
  for (const v of vars) {
    if (v.required && v.default === null && v.one_of === null) out[v.name] = { from_env: v.name };
  }
  return out;
}

function sourceOf(b: EnvBinding | undefined): Source {
  if (b === undefined) return "none";
  return "value" in b ? "value" : "from_env";
}

export function EnvBindingEditor({ vars, value, onChange }: EnvBindingEditorProps): ReactElement {
  if (vars.length === 0) return <p className="wo-muted">This build declares no environment variables.</p>;

  function set(name: string, b: EnvBinding | undefined): void {
    const next = { ...value };
    if (b === undefined) delete next[name];
    else next[name] = b;
    onChange(next);
  }

  function pick(v: EnvVar, source: Source): void {
    switch (source) {
      case "none":
        return set(v.name, undefined);
      case "from_env":
        return set(v.name, { from_env: v.name });
      case "value":
        return set(v.name, { value: v.default ?? "" });
    }
  }

  return (
    <table className="wo-env">
      <caption>Environment</caption>
      <thead>
        <tr><th scope="col">Variable</th><th scope="col">Source</th><th scope="col">Binding</th></tr>
      </thead>
      <tbody>
        {vars.map((v) => {
          const binding = value[v.name];
          return (
            <tr key={v.name}>
              <th scope="row">
                <code>{v.name}</code>
                {v.required && <span className="wo-tag">required</span>}
                {v.secret && <span className="wo-tag">secret</span>}
                {v.one_of !== null && <span className="wo-tag">one of {v.one_of}</span>}
                {v.description !== "" && <div className="wo-muted">{v.description}</div>}
                {v.used_by.length > 0 && <div className="wo-muted">used by {v.used_by.join(", ")}</div>}
              </th>
              <td>
                <select aria-label={`${v.name} source`} value={sourceOf(binding)} onChange={(e) => pick(v, e.target.value as Source)}>
                  <option value="none">Not bound</option>
                  <option value="from_env">From environment</option>
                  <option value="value" disabled={v.secret}>Value</option>
                </select>
              </td>
              <td>
                {binding !== undefined && "from_env" in binding && (
                  <input
                    aria-label={`${v.name} environment variable`}
                    value={binding.from_env}
                    onChange={(e) => set(v.name, { from_env: e.target.value.trim() })}
                  />
                )}
                {binding !== undefined && "value" in binding && (
                  <input aria-label={`${v.name} value`} value={binding.value} onChange={(e) => set(v.name, { value: e.target.value })} />
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
