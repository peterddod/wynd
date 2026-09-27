// Example cards for a proto-step or the process (`$DRAFTS/07 §7.11`).
import type { ReactElement } from "react";
import type { Interface, Json } from "../../api/types";
import { ExampleCard } from "./ExampleCard";

export interface ExamplesEditorProps {
  label: string;
  examples: Json[];
  exits: string[];
  iface: Interface | null;
  onChange(next: Json[]): void;
  readOnly?: boolean;
}

function moved(list: Json[], from: number, to: number): Json[] {
  const next = [...list];
  const [item] = next.splice(from, 1);
  next.splice(to, 0, item as Json);
  return next;
}

export function ExamplesEditor({ label, examples, exits, iface, onChange, readOnly = false }: ExamplesEditorProps): ReactElement | null {
  return (
    <section className="wg-examples" aria-label={label}>
      <h4>{label}</h4>
      {examples.length === 0 && <p className="wg-muted">No examples yet.</p>}
      <ol>
        {examples.map((ex, i) => (
          <li key={i}>
            <ExampleCard
              example={ex}
              exits={exits}
              iface={iface}
              onChange={readOnly ? undefined : (next) => onChange(examples.map((e, j) => (j === i ? next : e)))}
              onDuplicate={readOnly ? undefined : () => onChange([...examples.slice(0, i + 1), structuredClone(ex), ...examples.slice(i + 1)])}
              onMoveUp={readOnly || i === 0 ? undefined : () => onChange(moved(examples, i, i - 1))}
              onMoveDown={readOnly || i === examples.length - 1 ? undefined : () => onChange(moved(examples, i, i + 1))}
              onRemove={readOnly ? undefined : () => onChange(examples.filter((_, j) => j !== i))}
            />
          </li>
        ))}
      </ol>
      {!readOnly && (
        <button type="button" onClick={() => onChange([...examples, { inputs: {}, exit: exits[0] ?? "done" }])}>+ Add example</button>
      )}
    </section>
  );
}
