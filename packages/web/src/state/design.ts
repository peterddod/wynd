// The design session: autosave to the working tree and commit at boundaries (`$DRAFTS/07 §8`).
// Stub from WEB-SCAFFOLD; WEB-GRAPH implements it.
import type { Api, ApiError } from "../api/client";
import type {
  AvailableLocalStep, CommitReason, DesignConventions, Json, LockOwner, ParseError, ProcessInterface, StepInfo,
  ValidationReportDTO,
} from "../api/types";
import type { Store } from "./store";

export type { CommitReason };

export const DEBOUNCE_MS = 800;     // trailing autosave debounce
export const MAX_WAIT_MS = 5000;    // from the first unsaved change

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

export function createDesignSession(api: Api, clock?: Clock): DesignSession {
  throw new Error("not implemented");
}
