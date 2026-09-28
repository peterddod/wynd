// Collapsible JSON tree over native <details>; strings over 200 characters truncated with [show all]; copy-JSON
// button (`$DRAFTS/07 §11.5`).
import { useState, type ReactElement } from "react";
import type { Json } from "../../api/types";
import { CopyButton } from "./CopyButton";

export interface JsonViewProps {
  value: Json | undefined;
  label?: string;
  collapsed?: boolean | number;      // true: all collapsed; n: expanded to depth n
}

export const TRUNCATE_AT = 200;
export const DEFAULT_DEPTH = 2;       // levels expanded when `collapsed` is not given

function openDepth(collapsed: boolean | number | undefined): number {
  if (collapsed === true) return 0;
  if (collapsed === false) return Number.POSITIVE_INFINITY;
  return collapsed ?? DEFAULT_DEPTH;
}

export function JsonView({ value, label, collapsed }: JsonViewProps): ReactElement {
  return (
    <div className="wy-json">
      <div className="wy-json-bar">
        {label !== undefined && <span className="wy-json-label">{label}</span>}
        <CopyButton text={JSON.stringify(value ?? null, null, 2)} label={label === undefined ? "Copy JSON" : `Copy ${label}`} />
      </div>
      {value === undefined ? <span className="wy-json-none">—</span> : <JsonNode value={value} depth={0} open={openDepth(collapsed)} />}
    </div>
  );
}

function JsonNode({ value, depth, open }: { value: Json; depth: number; open: number }): ReactElement {
  if (value === null) return <span className="wy-json-null">null</span>;
  if (typeof value === "string") return <JsonString value={value} />;
  if (typeof value !== "object") return <span className={`wy-json-${typeof value}`}>{String(value)}</span>;
  const entries: [string, Json][] = Array.isArray(value) ? value.map((v, i) => [String(i), v]) : Object.entries(value);
  const [start, end] = Array.isArray(value) ? ["[", "]"] : ["{", "}"];
  if (entries.length === 0) return <span className="wy-json-empty">{start + end}</span>;
  const size = Array.isArray(value) ? `${entries.length} items` : `${entries.length} keys`;
  return (
    <details className="wy-json-node" open={depth < open}>
      <summary>
        {start} <span className="wy-json-size">{size}</span> {end}
      </summary>
      <ul>
        {entries.map(([key, child]) => (
          <li key={key}>
            <span className="wy-json-key">{key}:</span> <JsonNode value={child} depth={depth + 1} open={open} />
          </li>
        ))}
      </ul>
    </details>
  );
}

function JsonString({ value }: { value: string }): ReactElement {
  const [full, setFull] = useState(false);
  if (full || value.length <= TRUNCATE_AT) return <span className="wy-json-string">{JSON.stringify(value)}</span>;
  return (
    <span className="wy-json-string">
      {`${JSON.stringify(value.slice(0, TRUNCATE_AT)).slice(0, -1)}…"`}{" "}
      <button type="button" className="wy-link-btn" onClick={() => setFull(true)}>
        show all ({value.length} characters)
      </button>
    </span>
  );
}
