// Query-string URL state, no router (`$DRAFTS/07 §4.2`). Stub from WEB-SCAFFOLD; WEB-CORE implements it.

export type Tab = "graph" | "process" | "yaml" | "runs" | "releases";

export interface UrlState {
  process: string | null;   // open process id, e.g. "finance/invoices"
  chat: string | null;      // open chat id
  tab: Tab;
  sel: string | null;       // graph selection: "s:<step>" | "b:<edgeIdx>:<branchIdx>" | "x:<exit>" | "in"
  run: string | null;       // run shown in the Runs tab
}

/** `process`/`chat` changes push, `tab`/`sel`/`run` replace; `mode` overrides. */
export type SetUrl = (patch: Partial<UrlState>, mode?: "push" | "replace") => void;

export function readUrl(search?: string): UrlState {
  throw new Error("not implemented");
}

export function useUrlState(): [UrlState, SetUrl] {
  throw new Error("not implemented");
}
