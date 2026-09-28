// Consecutive tool items of a turn: "Used 3 tools ▸" (`$DRAFTS/07 §9.3`). A group holding a write tool starts
// expanded, and so does each write row; a row shows the tool, a one-line args summary, its status and duration,
// and (write tools) the process it acted on; expanding a row shows the args JSON and the result summary.
import { useState, type ReactElement } from "react";
import type { Json, ToolItem } from "../../api/types";
import { Icon } from "../common/Icon";
import { JsonView } from "../common/JsonView";
import { Spinner } from "../common/Spinner";
import { ActingOnChip } from "./ActingOnChip";

export interface ToolActivityGroupProps {
  items: ToolItem[];
}

export const ARGS_SUMMARY_MAX = 80;

/** `k=v, k2=v2` (strings raw, other values as JSON), cut to 80 characters. */
export function argsSummary(args: Json): string {
  const text = args !== null && typeof args === "object" && !Array.isArray(args)
    ? Object.entries(args).map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`).join(", ")
    : JSON.stringify(args);
  return text.length <= ARGS_SUMMARY_MAX ? text : `${text.slice(0, ARGS_SUMMARY_MAX - 1)}…`;
}

function duration(ms: number | null): string {
  if (ms === null) return "";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

function plural(n: number, word: string): string {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

function ToolStatus({ item }: { item: ToolItem }): ReactElement {
  if (item.status === "running") return <Spinner label={`${item.tool} running`} />;
  const ok = item.status === "ok";
  return (
    <span className="wc-tool-status" data-status={item.status}>
      <span aria-label={ok ? "succeeded" : "failed"}>{ok ? "✓" : "✕"}</span> {duration(item.duration_ms)}
    </span>
  );
}

function ToolRow({ item }: { item: ToolItem }): ReactElement {
  const [open, setOpen] = useState(item.write);
  return (
    <li className="wc-tool" data-write={item.write ? "" : undefined}>
      <div className="wc-tool-head">
        <button type="button" aria-expanded={open} onClick={() => setOpen(!open)}>
          <Icon name={item.write ? "write" : "read"} />
          <span className="wc-tool-name">{item.tool}</span>
          <span className="wc-tool-args">{argsSummary(item.args)}</span>
        </button>
        <ToolStatus item={item} />
        {item.write && <ActingOnChip actingOn={item.acting_on} />}
      </div>
      {open && (
        <div className="wc-tool-body">
          <JsonView value={item.args} label="Arguments" collapsed={1} />
          {item.summary !== null && <p className="wc-tool-summary">{item.summary}</p>}
        </div>
      )}
    </li>
  );
}

export function ToolActivityGroup({ items }: ToolActivityGroupProps): ReactElement {
  const writes = items.filter((i) => i.write).length;
  const [chosen, setOpen] = useState<boolean | null>(null);   // null: follow the default (open iff a write)
  const open = chosen ?? writes > 0;
  const title = writes > 0 ? `Used ${plural(items.length, "tool")} · ${plural(writes, "write")}` : `Used ${plural(items.length, "tool")}`;
  return (
    <div className="wc-item wc-tools">
      <button type="button" className="wc-tools-toggle" aria-expanded={open} onClick={() => setOpen(!open)}>
        {title} {open ? "▾" : "▸"}
      </button>
      {open && <ul className="wc-tool-list">{items.map((item) => <ToolRow key={item.id} item={item} />)}</ul>}
    </div>
  );
}
