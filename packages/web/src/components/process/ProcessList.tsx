// Sidebar list = selector + search: ProcessSearch, result rows with badges and <mark>ed snippets, [+ New process]
// (`$DRAFTS/07 §6.1`). Search is server-side; the marks are client-side, case-insensitive term matches.
import { useState, type ReactElement, type ReactNode } from "react";
import { useApi } from "../../api/context";
import type { ProcessSummary, StatusFlag } from "../../api/types";
import { useOpenProcess } from "../../state/openProcess";
import { useQuery } from "../../state/query";
import { useUrlState } from "../../state/url";
import { ErrorBox } from "../common/ErrorBox";
import { NewProcessDialog } from "./NewProcessDialog";
import { ProcessSearch } from "./ProcessSearch";
import { StatusBadges } from "./StatusBadges";

export function ProcessList(): ReactElement {
  const api = useApi();
  const openProcess = useOpenProcess();
  const [url] = useUrlState();
  const [q, setQ] = useState("");
  const [flags, setFlags] = useState<StatusFlag[]>([]);
  const [creating, setCreating] = useState(false);
  const list = useQuery(`processes?${q}&${flags.join(",")}`, () => api.processes.list(q, flags));
  const terms = searchTerms(q);
  const processes = list.data?.processes;

  return (
    <div className="wo-process-list">
      <ProcessSearch q={q} flags={flags} onChange={(nextQ, nextFlags) => { setQ(nextQ); setFlags(nextFlags); }} />
      <ErrorBox error={list.error} onRetry={() => void list.reload()} />
      {processes === undefined && list.loading && <p className="wo-muted">Loading…</p>}
      {processes !== undefined && processes.length === 0 && (
        <p className="wo-muted">{terms.length > 0 || flags.length > 0 ? "No matching processes." : "No processes yet."}</p>
      )}
      {processes !== undefined && processes.length > 0 && (
        <ul className="wo-rows" aria-label="Processes">
          {processes.map((p) => (
            <ProcessRow key={p.id} process={p} terms={terms} current={url.process === p.id} onOpen={() => void openProcess(p.id)} />
          ))}
        </ul>
      )}
      <button type="button" className="wo-new" onClick={() => setCreating(true)}>+ New process</button>
      <NewProcessDialog open={creating} onClose={() => setCreating(false)} />
    </div>
  );
}

interface ProcessRowProps {
  process: ProcessSummary;
  terms: string[];
  current: boolean;
  onOpen(): void;
}

function ProcessRow({ process: p, terms, current, onOpen }: ProcessRowProps): ReactElement {
  const lastSegment = p.id.slice(p.id.lastIndexOf("/") + 1);
  const instructions = terms.length === 0 ? [] : p.matches.filter((m) => m.field === "instruction");
  return (
    <li>
      <button type="button" className="wo-row" aria-current={current ? "true" : undefined} onClick={onOpen}>
        <span className="wo-row-id">{markTerms(p.id, terms)}</span>
        {p.name !== lastSegment && <span className="wo-row-name">{p.name}</span>}
        {p.goal !== null && <span className="wo-row-goal">{markTerms(p.goal, terms)}</span>}
        <StatusBadges status={p.status} />
        {p.error != null && <span className="wo-row-error">{p.error}</span>}
        {instructions.map((m, i) => (
          <span key={i} className="wo-row-match">
            {m.step !== null && <span className="wo-row-step">{m.step}: </span>}
            {markTerms(m.snippet, terms)}
          </span>
        ))}
      </button>
    </li>
  );
}

/** The query split on whitespace and casefolded, as the server matches it. */
export function searchTerms(q: string): string[] {
  return q.toLowerCase().split(/\s+/).filter((t) => t !== "");
}

/** `text` with every case-insensitive occurrence of a term wrapped in <mark> (longest term first). */
export function markTerms(text: string, terms: string[]): ReactNode[] {
  if (terms.length === 0) return [text];
  const sorted = [...terms].sort((a, b) => b.length - a.length);
  const lower = text.toLowerCase();
  const out: ReactNode[] = [];
  let plain = 0;
  let i = 0;
  while (i < text.length) {
    const term = sorted.find((t) => lower.startsWith(t, i));
    if (term === undefined) {
      i += 1;
      continue;
    }
    if (plain < i) out.push(text.slice(plain, i));
    out.push(<mark key={i}>{text.slice(i, i + term.length)}</mark>);
    i += term.length;
    plain = i;
  }
  if (plain < text.length) out.push(text.slice(plain));
  return out;
}
