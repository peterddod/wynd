// The design session: autosave to the working tree and commit at boundaries (`$DRAFTS/07 §8`). Edits only ever
// `apply()`; a debounced save writes the dirty files (DEBOUNCE_MS trailing, MAX_WAIT_MS from the first unsaved change)
// and never commits; `commit(reason)` saves whatever is dirty and commits this process's design paths in one request.
import { ApiError, type Api } from "../api/client";
import type {
  AvailableLocalStep, CommitReason, DesignConventions, DesignDoc, FileDoc, Json, LockOwner, ParseError,
  ProcessInterface, SaveResult, SaveWrite, StepInfo, ValidationReportDTO,
} from "../api/types";
import { onReconnect } from "./connection";
import { invalidate } from "./query";
import { createStore, type Store } from "./store";

export type { CommitReason };

export const DEBOUNCE_MS = 800;     // trailing autosave debounce
export const MAX_WAIT_MS = 5000;    // from the first unsaved change
export const RETRY_MS: readonly number[] = [2000, 5000, 10000];   // save retries after an error (docs are kept)

export interface FileState {
  path: string;
  revision: string | null;
  doc: Json;
  savedDoc: Json;                    // dirty iff doc !== savedDoc (by reference)
  yaml: string | null;
  deleted?: boolean;
}

export interface DesignState {
  processId: string;
  processPath: string;
  process: FileState;
  protos: Record<string, FileState>;           // by path
  steps: Record<string, StepInfo>;
  iface: ProcessInterface | null;              // process-level interface for examples and the run form
  report: ValidationReportDTO | null;
  issuesStale: boolean;
  conventions: DesignConventions;
  availableLocal: AvailableLocalStep[];
  parseError: ParseError | null;
  saveState: "clean" | "pending" | "saving" | "error" | "conflict";
  uncommitted: boolean;                        // saved to the working tree since the last commit
  opLabels: string[];                          // labels since the last commit, de-duplicated, in order
  lockedBy: LockOwner | null;
  lastCommit: { sha: string; message: string } | null;
  error: ApiError | null;
}

/** In-memory docs an op transforms; a proto set to null is deleted. */
export interface Drafts {
  process: Json;
  protos: Record<string, Json | null>;
}

export interface Clock {
  setTimeout: typeof setTimeout;
  clearTimeout: typeof clearTimeout;
  now(): number;
}

export interface DesignSession {
  store: Store<DesignState | null>;
  open(pid: string): Promise<void>;                                  // GET design; replaces state
  apply(label: string, change: (d: Drafts) => Drafts, opts?: { structural?: boolean }): void;
  flush(): Promise<void>;                                            // cancel the debounce, save dirty files now
  commit(reason: CommitReason): Promise<{ sha: string } | null>;     // save dirty + commit in ONE request
  reload(): Promise<void>;                                           // GET design; only when not dirty
  resolveConflict(choice: "theirs" | "mine"): Promise<void>;
  lock(turn: LockOwner): void;
  unlock(turnId: string): void;
  processDoc(): Json;                                                // current in-memory process doc
  dirtyProtoDocs(): Record<string, Json>;                            // in-memory protos differing from savedDoc
  close(): void;
}

// Globals are looked up at call time so fake timers installed after import still apply.
const systemClock: Clock = {
  setTimeout: ((handler: () => void, ms?: number) => setTimeout(handler, ms)) as typeof setTimeout,
  clearTimeout: ((id?: number) => clearTimeout(id)) as typeof clearTimeout,
  now: () => Date.now(),
};

export function isDirty(f: FileState): boolean {
  return f.deleted === true || f.doc !== f.savedDoc;
}

export function anyDirty(s: DesignState): boolean {
  return isDirty(s.process) || Object.values(s.protos).some(isDirty);
}

/** The commit summary: the first five op labels, then "+N more". */
export function commitSummary(labels: string[]): string {
  if (labels.length === 0) return "edit design";
  const head = labels.slice(0, 5).join("; ");
  return labels.length > 5 ? `${head}; +${labels.length - 5} more` : head;
}

function fileState(f: FileDoc): FileState {
  return { path: f.path, revision: f.revision, doc: f.doc, savedDoc: f.doc, yaml: f.yaml };
}

function stateFrom(d: DesignDoc): DesignState {
  return {
    processId: d.process_id,
    processPath: d.process_file.path,
    process: fileState(d.process_file),
    protos: Object.fromEntries(Object.entries(d.protos).map(([path, f]) => [path, fileState(f)])),
    steps: d.steps,
    iface: d.interface,
    report: d.validation,
    issuesStale: false,
    conventions: d.conventions,
    availableLocal: d.available_local,
    parseError: d.process_file.parse_error,
    saveState: "clean",
    uncommitted: false,
    opLabels: [],
    lockedBy: d.locked_by,
    lastCommit: null,
    error: null,
  };
}

function writesOf(s: DesignState): SaveWrite[] {
  return [s.process, ...Object.values(s.protos)].filter(isDirty).map((f) => (f.deleted === true
    ? { path: f.path, base_revision: f.revision, delete: true }
    : { path: f.path, base_revision: f.revision, doc: f.doc }));
}

function asApiError(e: unknown): ApiError {
  return e instanceof ApiError ? e : new ApiError(0, "error", e instanceof Error ? e.message : String(e));
}

export function createDesignSession(api: Api, clock: Clock = systemClock): DesignSession {
  const store = createStore<DesignState | null>(null);
  let timer: ReturnType<typeof setTimeout> | null = null;
  let firstPendingAt: number | null = null;
  let inFlight: Promise<void> | null = null;
  let retries = 0;
  let generation = 0;                // bumped by open/close: responses for an older state are dropped
  let structuralSinceSnapshot = false;

  function cancelTimer(): void {
    if (timer === null) return;
    clock.clearTimeout(timer);
    timer = null;
  }

  function schedule(delay?: number): void {
    const now = clock.now();
    if (firstPendingAt === null) firstPendingAt = now;
    const due = delay === undefined ? Math.min(now + DEBOUNCE_MS, firstPendingAt + MAX_WAIT_MS) : now + delay;
    cancelTimer();
    timer = clock.setTimeout(() => {
      timer = null;
      save(null).catch(() => undefined);
    }, Math.max(0, due - now));
  }

  function patch(fn: (s: DesignState) => DesignState): void {
    store.set((s) => (s === null ? s : fn(s)));
  }

  async function save(reason: CommitReason | null, keepalive = false): Promise<SaveResult | null> {
    while (inFlight !== null) await inFlight;
    const s = store.get();
    if (s === null) return null;
    const writes = writesOf(s);
    if (writes.length === 0 && reason === null) {
      if (s.saveState === "pending") patch((cur) => ({ ...cur, saveState: "clean" }));
      return null;
    }
    cancelTimer();
    firstPendingAt = null;
    structuralSinceSnapshot = false;
    const gen = generation;
    const snapshot = new Map(writes.filter((w) => w.delete !== true).map((w) => [w.path, w.doc as Json]));
    const labels = s.opLabels;
    const body = { writes, commit: reason === null ? null : { reason, summary: commitSummary(labels) } };
    patch((cur) => ({ ...cur, saveState: "saving" }));
    const request = keepalive
      ? api.processes.save(s.processId, body, { keepalive: true })
      : api.processes.save(s.processId, body);
    const settled = request.then(() => undefined, () => undefined);
    inFlight = settled;
    try {
      const r = await request;
      if (gen === generation) settle(r, snapshot, labels, writes.length > 0);
      return r;
    } catch (e) {
      if (gen === generation) fail(asApiError(e));
      throw e;
    } finally {
      if (inFlight === settled) inFlight = null;
    }
  }

  function settle(r: SaveResult, snapshot: Map<string, Json>, labels: string[], wrote: boolean): void {
    patch((cur) => {
      const saved = new Map(r.files.map((f) => [f.path, f]));
      const update = (f: FileState): FileState | null => {
        const out = saved.get(f.path);
        if (out === undefined) return f;
        if (f.deleted === true && out.revision === null) return null;
        const doc = snapshot.get(f.path);
        return { ...f, revision: out.revision, yaml: out.yaml ?? f.yaml, savedDoc: doc === undefined ? f.savedDoc : doc };
      };
      const protos: Record<string, FileState> = {};
      for (const [path, f] of Object.entries(cur.protos)) {
        const next = update(f);
        if (next !== null) protos[path] = next;
      }
      const next: DesignState = {
        ...cur,
        process: update(cur.process) ?? cur.process,
        protos,
        report: r.validation,
        issuesStale: structuralSinceSnapshot,
        steps: r.steps,
        iface: r.interface,
        uncommitted: r.commit !== null ? false : cur.uncommitted || wrote,
        lastCommit: r.commit ?? cur.lastCommit,
        opLabels: r.commit !== null ? cur.opLabels.filter((l) => !labels.includes(l)) : cur.opLabels,
        error: null,
      };
      return { ...next, saveState: anyDirty(next) ? "pending" : "clean" };
    });
    retries = 0;
    if (r.commit !== null) invalidate("processes");
    const s = store.get();
    if (s !== null && anyDirty(s) && s.lockedBy === null && timer === null) schedule();
  }

  function fail(e: ApiError): void {
    switch (e.code) {
      case "revision_conflict":
        cancelTimer();
        patch((cur) => ({ ...cur, saveState: "conflict", error: e }));
        return;
      case "design_locked":
        cancelTimer();
        patch((cur) => ({ ...cur, saveState: "pending", lockedBy: e.details as LockOwner, error: null }));
        return;
    }
    patch((cur) => ({ ...cur, saveState: "error", error: e }));
    schedule(RETRY_MS[Math.min(retries, RETRY_MS.length - 1)]);
    retries += 1;
  }

  onReconnect(() => {
    if (store.get()?.saveState === "error") save(null).catch(() => undefined);
  });

  async function load(pid: string): Promise<DesignState | null> {
    const gen = generation;
    const d = await api.processes.design(pid);
    return gen === generation ? stateFrom(d) : null;
  }

  return {
    store,

    async open(pid) {
      generation += 1;
      cancelTimer();
      firstPendingAt = null;
      retries = 0;
      store.set(null);
      const next = await load(pid);
      if (next !== null) store.set(next);
    },

    apply(label, change, opts) {
      const s = store.get();
      if (s === null || s.lockedBy !== null || s.parseError !== null) return;
      const drafts: Drafts = {
        process: s.process.doc,
        protos: Object.fromEntries(Object.entries(s.protos).map(([p, f]) => [p, f.deleted === true ? null : f.doc])),
      };
      const next = change(drafts);
      const process = next.process === drafts.process ? s.process : { ...s.process, doc: next.process };
      let protos = s.protos;
      for (const [path, doc] of Object.entries(next.protos)) {
        const cur = s.protos[path];
        if (cur === undefined) {
          if (doc !== null) protos = { ...protos, [path]: { path, revision: null, doc, savedDoc: null, yaml: null } };
          continue;
        }
        if (doc === null) {
          if (cur.deleted === true) continue;
          if (cur.revision === null) {
            protos = Object.fromEntries(Object.entries(protos).filter(([p]) => p !== path));
          } else {
            protos = { ...protos, [path]: { ...cur, deleted: true } };
          }
          continue;
        }
        if (doc !== cur.doc || cur.deleted === true) protos = { ...protos, [path]: { ...cur, doc, deleted: false } };
      }
      if (process === s.process && protos === s.protos) return;
      const structural = opts?.structural === true;
      if (structural) structuralSinceSnapshot = true;
      const conflict = s.saveState === "conflict";
      store.set({
        ...s,
        process,
        protos,
        opLabels: s.opLabels.includes(label) ? s.opLabels : [...s.opLabels, label],
        issuesStale: s.issuesStale || structural,
        saveState: conflict ? "conflict" : "pending",
      });
      if (!conflict) schedule();
    },

    async flush() {
      await save(null).catch(() => undefined);
    },

    async commit(reason) {
      const s = store.get();
      if (s === null || s.lockedBy !== null) return null;
      if (!anyDirty(s) && !s.uncommitted) return null;
      const r = await save(reason, reason === "unload");
      return r?.commit ? { sha: r.commit.sha } : null;
    },

    async reload() {
      const s = store.get();
      if (s === null || anyDirty(s) || inFlight !== null) return;
      const fresh = await load(s.processId);
      const cur = store.get();
      if (fresh === null || cur === null || anyDirty(cur) || cur.processId !== fresh.processId) return;
      const same = (f: FileState, old: FileState | undefined): FileState =>
        (old !== undefined && old.revision === f.revision ? old : f);
      store.set({
        ...fresh,
        process: same(fresh.process, cur.process),
        protos: Object.fromEntries(Object.entries(fresh.protos).map(([p, f]) => [p, same(f, cur.protos[p])])),
        uncommitted: cur.uncommitted,
        lastCommit: cur.lastCommit,
        opLabels: cur.opLabels,
      });
    },

    async resolveConflict(choice) {
      const s = store.get();
      if (s === null) return;
      const fresh = await load(s.processId);
      const cur = store.get();
      if (fresh === null || cur === null) return;
      if (choice === "theirs") {
        store.set({ ...fresh, lastCommit: cur.lastCommit });
        return;
      }
      // Keep every locally edited doc on top of the server's current revision; take the server's version of the rest.
      const mine = (local: FileState, server: FileState | undefined): FileState | null => {
        if (!isDirty(local)) return server ?? null;
        if (server === undefined) return local.deleted === true ? null : { ...local, revision: null, savedDoc: null };
        return { ...local, revision: server.revision, savedDoc: server.doc, yaml: server.yaml };
      };
      const protos: Record<string, FileState> = { ...fresh.protos };
      for (const [path, f] of Object.entries(cur.protos)) {
        const kept = mine(f, fresh.protos[path]);
        if (kept === null) delete protos[path];
        else protos[path] = kept;
      }
      store.set({
        ...fresh,
        process: mine(cur.process, fresh.process) ?? fresh.process,
        protos,
        uncommitted: cur.uncommitted,
        lastCommit: cur.lastCommit,
        opLabels: cur.opLabels,
        saveState: "pending",
      });
      await save(null).catch(() => undefined);
    },

    lock(turn) {
      cancelTimer();
      patch((cur) => ({ ...cur, lockedBy: turn }));
    },

    unlock(turnId) {
      const s = store.get();
      if (s === null || s.lockedBy?.turn_id !== turnId) return;
      store.set({ ...s, lockedBy: null });
      if (anyDirty(s)) schedule();
    },

    processDoc() {
      return store.get()?.process.doc ?? null;
    },

    dirtyProtoDocs() {
      const s = store.get();
      if (s === null) return {};
      return Object.fromEntries(Object.entries(s.protos)
        .filter(([, f]) => f.deleted !== true && f.doc !== f.savedDoc)
        .map(([p, f]) => [p, f.doc]));
    },

    close() {
      generation += 1;
      cancelTimer();
      firstPendingAt = null;
      store.set(null);
    },
  };
}
