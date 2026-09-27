// Typed access to the shared golden payloads in src/api/fixtures (P-FIXTURES). `index.json` maps each JSON file to
// its controller DTO name ("Model" or "Model[]" for a JSON array of it); the controller's test_web_contract.py
// validates every file against that pydantic model, so a drift fails on both sides. `chat.events.txt` is a raw SSE
// transcript. Every accessor returns a fresh deep copy: tests may mutate what they get.
import buildsJson from "../api/fixtures/builds.json";
import chatEventsText from "../api/fixtures/chat.events.txt?raw";
import chatSnapshotJson from "../api/fixtures/chat.snapshot.json";
import chatsListJson from "../api/fixtures/chats.list.json";
import designJson from "../api/fixtures/design.process_supplier_invoice.json";
import envCheckJson from "../api/fixtures/envcheck.json";
import exprCheckJson from "../api/fixtures/expr.check.json";
import indexJson from "../api/fixtures/index.json";
import jobBuildSucceededJson from "../api/fixtures/job.build.succeeded.json";
import jobCompileAwaitingJson from "../api/fixtures/job.compile.awaiting.json";
import jobCompileIntegratedJson from "../api/fixtures/job.compile.integrated.json";
import jobCompileStartedJson from "../api/fixtures/job.compile.started.json";
import jobLogsJson from "../api/fixtures/job.logs.json";
import mcpListJson from "../api/fixtures/mcp.list.json";
import metaJson from "../api/fixtures/meta.json";
import oauthStartJson from "../api/fixtures/oauth.start.json";
import processInterfaceJson from "../api/fixtures/process.interface.json";
import processesListJson from "../api/fixtures/processes.list.json";
import processesSearchJson from "../api/fixtures/processes.search.json";
import providersJson from "../api/fixtures/providers.json";
import releasesJson from "../api/fixtures/releases.json";
import runErrorJson from "../api/fixtures/run.error.json";
import runEventsNestedJson from "../api/fixtures/run.events.nested.json";
import runEventsJson from "../api/fixtures/run.events.json";
import runFinishedJson from "../api/fixtures/run.finished.json";
import runsListJson from "../api/fixtures/runs.list.json";
import saveResultJson from "../api/fixtures/save.result.json";
import sendResultJson from "../api/fixtures/send.result.json";
import stepsCatalogJson from "../api/fixtures/steps.catalog.json";
import uploadJson from "../api/fixtures/upload.json";
import type {
  Build, ChatSnapshot, ChatSummary, DesignDoc, EnvCheckDTO, ExprCheck, Job, JobWithChat, LogChunk, McpServerEntry,
  Meta, OAuthStart, ProcessInterface, ProcessSummary, ProviderInfoDTO, Release, Run, SaveResult, SendMessageResult,
  StepCatalogEntry, TraceEvent, UploadResult,
} from "../api/types";
import { parseSse, type SseFrame } from "./fakeEventSource";

// `as T` lets tsc compare each JSON module with its DTO. Three need `unknown` first: tsc normalises arrays of object
// literals by adding `?: undefined` members, which a `Json` index signature cannot hold (free-form docs and events).
const PAYLOADS = {
  meta: metaJson as Meta,
  processesList: processesListJson as ProcessSummary[],       // GET /api/processes (no query; one fails to load)
  processesSearch: processesSearchJson as ProcessSummary[],   // GET /api/processes?q=invoice (with matches)
  design: designJson as unknown as DesignDoc,                 // the dogfood process_supplier_invoice
  saveResult: saveResultJson as SaveResult,                   // after editing validate.done[1] and a blur commit
  exprCheck: exprCheckJson as ExprCheck,                      // the `totl` typo at edges[3].to[0].with.dest
  stepsCatalog: stepsCatalogJson as StepCatalogEntry[],
  providers: providersJson as ProviderInfoDTO[],
  mcpList: mcpListJson as McpServerEntry[],
  oauthStart: oauthStartJson as OAuthStart,
  upload: uploadJson as UploadResult,
  jobCompileStarted: jobCompileStartedJson as JobWithChat,    // the Compile button's {job, chat}
  jobCompileAwaiting: jobCompileAwaitingJson as Job,          // 2 pending questions (clarification + proposal)
  jobCompileIntegrated: jobCompileIntegratedJson as Job,      // succeeded, fast-forwarded
  jobBuildSucceeded: jobBuildSucceededJson as Job,
  jobLogs: jobLogsJson as LogChunk,
  chatsList: chatsListJson as ChatSummary[],
  chatSnapshot: chatSnapshotJson as ChatSnapshot,
  sendResult: sendResultJson as SendMessageResult,
  runsList: runsListJson as Run[],
  runFinished: runFinishedJson as Run,                        // local run through the validate <-> fix loop
  runError: runErrorJson as Run,                              // ended in the default error handler
  runEvents: runEventsJson as unknown as TraceEvent[],        // trace of runFinished
  runEventsNested: runEventsNestedJson as unknown as TraceEvent[],  // finance/monthly_close, child node `sub`
  processInterface: processInterfaceJson as ProcessInterface, // at the released commit
  builds: buildsJson as Build[],
  releases: releasesJson as Release[],
  envCheck: envCheckJson as EnvCheckDTO,
};

export type Fixtures = typeof PAYLOADS;
export type FixtureName = keyof Fixtures;

/** A deep copy of one fixture payload. */
export function fixture<K extends FixtureName>(name: K): Fixtures[K] {
  return structuredClone(PAYLOADS[name]);
}

/** `index.json`: fixture file -> DTO name ("Model" | "Model[]"). */
export const FIXTURE_INDEX: Readonly<Record<string, string>> = indexJson;

/** The chat SSE transcript (`$DRAFTS/07 §12.4` framing) continuing `chatSnapshot` from its cursor. */
export function chatEventFrames(): SseFrame[] {
  return parseSse(chatEventsText);
}

/** A run's trace as the run SSE sends it: one `trace` frame per event (id = seq), then `end`. */
export function runEventFrames(events: TraceEvent[] = fixture("runEvents")): SseFrame[] {
  const frames: SseFrame[] = events.map((ev) => ({ id: String(ev.seq), event: "trace", data: ev }));
  return [...frames, { id: null, event: "end", data: {} }];
}
