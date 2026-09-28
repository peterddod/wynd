// Query-string URL state, no router (`$DRAFTS/07 §4.2`): `?process=&chat=&tab=&sel=&run=`, read from
// `window.location` on every change (setUrl or popstate), so every `useUrlState` caller sees the same state.
import { useSyncExternalStore } from "react";

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

const TAB_IDS: readonly Tab[] = ["graph", "process", "yaml", "runs", "releases"];
const DEFAULT_TAB: Tab = "graph";

const listeners = new Set<() => void>();
let lastSearch: string | null = null;
let lastState: UrlState | null = null;

function isTab(value: string | null): value is Tab {
  return value !== null && (TAB_IDS as readonly string[]).includes(value);
}

function param(params: URLSearchParams, name: string): string | null {
  const value = params.get(name);
  return value === null || value === "" ? null : value;
}

export function readUrl(search?: string): UrlState {
  const params = new URLSearchParams(search ?? window.location.search);
  const tab = params.get("tab");
  return {
    process: param(params, "process"),
    chat: param(params, "chat"),
    tab: isTab(tab) ? tab : DEFAULT_TAB,
    sel: param(params, "sel"),
    run: param(params, "run"),
  };
}

/** `?process=…&tab=…` in UrlState field order; null fields and the default tab are omitted. */
export function formatUrl(state: UrlState): string {
  const params = new URLSearchParams();
  if (state.process !== null) params.set("process", state.process);
  if (state.chat !== null) params.set("chat", state.chat);
  if (state.tab !== DEFAULT_TAB) params.set("tab", state.tab);
  if (state.sel !== null) params.set("sel", state.sel);
  if (state.run !== null) params.set("run", state.run);
  const query = params.toString();
  return query === "" ? "" : `?${query}`;
}

function notify(): void {
  for (const fn of [...listeners]) fn();
}

export const setUrl: SetUrl = (patch, mode) => {
  const current = readUrl();
  const defined = Object.fromEntries(Object.entries(patch).filter(([, value]) => value !== undefined));
  const next: UrlState = { ...current, ...defined };
  const search = formatUrl(next);
  const push = mode === undefined ? next.process !== current.process || next.chat !== current.chat : mode === "push";
  const url = `${window.location.pathname}${search}${window.location.hash}`;
  if (push && search !== window.location.search) window.history.pushState(null, "", url);
  else window.history.replaceState(null, "", url);
  notify();
};

function subscribe(fn: () => void): () => void {
  listeners.add(fn);
  if (listeners.size === 1) window.addEventListener("popstate", notify);
  return () => {
    listeners.delete(fn);
    if (listeners.size === 0) window.removeEventListener("popstate", notify);
  };
}

function snapshot(): UrlState {
  const search = window.location.search;
  if (search !== lastSearch || lastState === null) {
    lastSearch = search;
    lastState = readUrl(search);
  }
  return lastState;
}

export function useUrlState(): [UrlState, SetUrl] {
  return [useSyncExternalStore(subscribe, snapshot), setUrl];
}
