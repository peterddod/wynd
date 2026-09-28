// Tests of the shared test harness and fixtures (WEB-SCAFFOLD): every other web unit builds its tests on these.
import { screen } from "@testing-library/react";
import { useContext, type ReactElement } from "react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { DesignContext, useApi, useMeta } from "../api/context";
import { isTrace, type TraceEvent } from "../api/types";
import type { DesignSession } from "../state/design";
import { createFakeApi } from "./fakeApi";
import { FakeEventSource, parseSse } from "./fakeEventSource";
import { FIXTURE_INDEX, chatEventFrames, fixture, runEventFrames } from "./fixtures";
import { renderApp, urlSearch } from "./render";

const JSON_FILES = import.meta.glob("../api/fixtures/*.json", { eager: true, import: "default" });
// Every source module except the entry (main.tsx renders on import), tests and this harness.
const MODULES = import.meta.glob(["../**/*.{ts,tsx}", "!../**/*.test.{ts,tsx}", "!../main.tsx", "!./**"]);

function basename(path: string): string {
  return path.slice(path.lastIndexOf("/") + 1);
}

describe("fixture index", () => {
  it("lists every JSON fixture exactly once, each with a DTO name", () => {
    const files = Object.keys(JSON_FILES).map(basename).filter((f) => f !== "index.json").sort();
    expect(Object.keys(FIXTURE_INDEX).sort()).toEqual(files);
    for (const dto of Object.values(FIXTURE_INDEX)) expect(dto).toMatch(/^[A-Z][A-Za-z]*(\[\])?$/);
  });

  it("marks exactly the JSON arrays with []", () => {
    for (const [path, value] of Object.entries(JSON_FILES)) {
      const dto = FIXTURE_INDEX[basename(path)];
      if (dto === undefined) continue;
      expect(Array.isArray(value), basename(path)).toBe(dto.endsWith("[]"));
    }
  });
});

describe("modules", () => {
  it("every source module imports without throwing", async () => {
    expect(Object.keys(MODULES).length).toBeGreaterThan(100);
    for (const [path, load] of Object.entries(MODULES)) await expect(load(), path).resolves.toBeTypeOf("object");
  });
});

describe("fixture world", () => {
  it("design: step info for every step key, protos for every proto path", () => {
    const design = fixture("design");
    const doc = design.process_file.doc as { steps: Record<string, unknown>; entry: string };
    expect(Object.keys(design.steps)).toEqual(Object.keys(doc.steps));
    expect(doc.entry).toBe("read");
    for (const info of Object.values(design.steps)) {
      expect(info.proto_path === null || info.proto_path in design.protos, info.name).toBe(true);
    }
    expect(design.process_file.parse_error).toBeNull();
  });

  it("status of the open process agrees with the design", () => {
    const design = fixture("design");
    const listed = fixture("processesList").find((p) => p.id === design.process_id);
    expect(listed?.status?.head?.commit).toBe(design.head);
    const designSteps = Object.values(design.steps).filter((s) => s.phase === "design").map((s) => s.name);
    expect(listed?.status?.design_steps).toEqual(designSteps);
    expect(fixture("processesList").some((p) => p.status === null && typeof p.error === "string")).toBe(true);
    expect(fixture("processesSearch").every((p) => p.matches.length > 0)).toBe(true);
  });

  it("run trace: contiguous seqs from run.start to run.end, one run through the validate <-> fix loop", () => {
    const events = fixture("runEvents");
    expect(events.map((e) => e.seq)).toEqual(events.map((_, i) => i + 1));
    expect(events[0]?.type).toBe("run.start");
    expect(events.at(-1)?.type).toBe("run.end");
    expect(new Set(events.map((e) => e.run_id))).toEqual(new Set([fixture("runFinished").id]));
    const started = events.filter((e) => isTrace(e, "step.start")).map((e) => e.step);
    expect(started).toEqual(["read", "extract", "validate", "fix", "validate", "save"]);
    const spans = new Map(events.filter((e) => isTrace(e, "step.start")).map((e) => [e.seq, e.step]));
    for (const e of events) {
      if (isTrace(e, "step.end")) expect(spans.get(e.span)).toBe(e.step);
    }
    const end = events.at(-1) as TraceEvent;
    expect(isTrace(end, "run.end") && end.outputs).toEqual(fixture("runFinished").outputs);
  });

  it("nested trace: child steps point at their ProcessStep span and carry its path prefix", () => {
    const events = fixture("runEventsNested");
    const starts = events.filter((e) => isTrace(e, "step.start"));
    const sub = starts.find((e) => e.kind === "process");
    expect(sub?.step).toBe("sub");
    const children = starts.filter((e) => e.parent !== null);
    expect(children.map((e) => e.step)).toEqual(["sub.read", "sub.extract"]);
    for (const child of children) expect(child.parent).toBe(sub?.span);
    expect(events.some((e) => isTrace(e, "tool.call"))).toBe(true);
  });

  it("chat transcript continues the snapshot from its cursor", () => {
    const frames = chatEventFrames();
    const ids = frames.map((f) => Number(f.id));
    expect(ids[0]).toBe(fixture("chatSnapshot").cursor + 1);
    expect(ids).toEqual([...ids].sort((a, b) => a - b));
    expect(frames[0]).toEqual({ id: ids[0]?.toString(), event: "item", data: { item: fixture("sendResult").item } });
    expect(new Set(frames.map((f) => f.event))).toEqual(new Set(["item", "turn", "delta"]));
  });

  it("jobs: awaiting has two pending questions, integrated fast-forwarded", () => {
    const awaiting = fixture("jobCompileAwaiting");
    expect(awaiting.status).toBe("awaiting_input");
    expect(awaiting.session?.questions.filter((q) => q.status === "pending").map((q) => q.kind))
      .toEqual(["clarification", "example_proposal"]);
    expect(fixture("jobCompileIntegrated").integration?.mode).toBe("fast_forward");
    expect(fixture("jobBuildSucceeded").build?.image).toContain(fixture("builds")[0]?.commit.slice(0, 12));
  });

  it("returns fresh copies", () => {
    fixture("meta").version = "changed";
    expect(fixture("meta").version).toBe("0.1.0");
  });
});

describe("parseSse", () => {
  it("parses frames, skips retry and comments, keeps non-JSON data raw", () => {
    const frames = parseSse('retry: 2000\n\n: ping\n\nid: 7\nevent: trace\ndata: {"seq":7}\n\nevent: end\ndata: oops\n\n');
    expect(frames).toEqual([
      { id: "7", event: "trace", data: { seq: 7 } },
      { id: null, event: "end", data: "oops" },
    ]);
  });

  it("run frames: one trace frame per event with id = seq, then end", () => {
    const frames = runEventFrames();
    expect(frames.at(-1)).toEqual({ id: null, event: "end", data: {} });
    expect(frames[1]).toEqual({ id: "2", event: "trace", data: fixture("runEvents")[1] });
  });
});

describe("FakeEventSource", () => {
  it("is the global EventSource and tracks instances", () => {
    const source = new EventSource("/api/runs/r1/events?since=0");
    expect(source).toBeInstanceOf(FakeEventSource);
    expect(FakeEventSource.last()).toBe(source);
    expect(FakeEventSource.instances().map((s) => s.url)).toEqual(["/api/runs/r1/events?since=0"]);
  });

  it("delivers named events with data and lastEventId, opening first", () => {
    const source = new FakeEventSource("/x");
    const onOpen = vi.fn();
    const got: [string, string][] = [];
    source.onopen = onOpen;
    source.addEventListener("trace", (ev) => {
      const msg = ev as MessageEvent<string>;
      got.push([msg.data, msg.lastEventId]);
    });
    source.emit("trace", { seq: 3 }, 3);
    expect(onOpen).toHaveBeenCalledOnce();
    expect(source.readyState).toBe(FakeEventSource.OPEN);
    expect(got).toEqual([['{"seq":3}', "3"]]);
  });

  it("error() goes back to CONNECTING; close() stops delivery", () => {
    const source = new FakeEventSource("/x");
    const onError = vi.fn();
    const onItem = vi.fn();
    source.onerror = onError;
    source.addEventListener("item", onItem);
    source.open();
    source.error();
    expect(onError).toHaveBeenCalledOnce();
    expect(source.readyState).toBe(FakeEventSource.CONNECTING);
    source.close();
    source.play(chatEventFrames());
    expect(onItem).not.toHaveBeenCalled();
  });

  it("instances are reset between tests", () => {
    expect(FakeEventSource.instances()).toEqual([]);
  });
});

describe("createFakeApi", () => {
  it("answers from the fixtures and records calls in order", async () => {
    const api = createFakeApi();
    await expect(api.meta()).resolves.toEqual(fixture("meta"));
    await api.processes.list("", []);
    const { processes } = await api.processes.list("invoice", ["design"]);
    expect(processes).toEqual(fixture("processesSearch"));
    expect(api.calls).toEqual([
      { method: "meta", args: [] },
      { method: "processes.list", args: ["", []] },
      { method: "processes.list", args: ["invoice", ["design"]] },
    ]);
    expect(api.processes.list).toHaveBeenCalledTimes(2);
  });

  it("overrides replace implementations by path; mockResolvedValueOnce still records", async () => {
    const api = createFakeApi({ jobs: { get: async (id) => ({ ...fixture("jobBuildSucceeded"), id }) } });
    await expect(api.jobs.get("job_x")).resolves.toMatchObject({ id: "job_x", kind: "build" });
    vi.mocked(api.health).mockResolvedValueOnce({ ok: true });
    await api.health();
    expect(api.calls.map((c) => c.method)).toEqual(["jobs.get", "health"]);
  });

  it("rejects unknown ids with a not_found ApiError", async () => {
    const api = createFakeApi();
    const err = await api.runs.get("run_missing").catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect(err).toMatchObject({ status: 404, code: "not_found" });
  });

  it("save echoes the writes and commits only when asked", async () => {
    const api = createFakeApi();
    const path = "processes/process_supplier_invoice/process.yaml";
    const saved = await api.processes.save("process_supplier_invoice", { writes: [{ path, base_revision: null, doc: {} }], commit: null });
    expect(saved.files.map((f) => f.path)).toEqual([path]);
    expect(saved.commit).toBeNull();
    const committed = await api.processes.save("process_supplier_invoice", { writes: [], commit: { reason: "blur", summary: "edit branch validate.done[1]" } });
    expect(committed.commit?.message).toBe("design(process_supplier_invoice): edit branch validate.done[1]");
  });

  it("active jobs are the unfinished ones and unintegrated compiles", async () => {
    const api = createFakeApi();
    const { jobs } = await api.jobs.list({ active: true });
    expect(jobs.map((j) => j.status)).toEqual(["awaiting_input"]);
  });
});

function Probe(): ReactElement {
  const api = useApi();
  const meta = useMeta();
  const design = useContext(DesignContext);
  return (
    <p>
      {typeof api.meta}|{meta?.default_provider ?? "no meta"}|{design === null ? "no design" : "design"}|{window.location.search}
    </p>
  );
}

describe("renderApp", () => {
  it("provides the api, meta and design contexts and sets the URL", () => {
    const design = { close: vi.fn() } as unknown as DesignSession;
    const { api } = renderApp(<Probe />, { url: { process: "finance/invoices", tab: "runs", run: null }, design });
    expect(screen.getByText("function|claude-code|design|?process=finance%2Finvoices&tab=runs")).toBeTruthy();
    expect(api.calls).toEqual([]);
  });

  it("defaults: a fake api, the meta fixture, no design, no query", () => {
    renderApp(<Probe />, { meta: null });
    expect(screen.getByText("function|no meta|no design|")).toBeTruthy();
    expect(urlSearch({})).toBe("");
  });
});

describe("setup", () => {
  it("storage is in memory (1/2)", () => {
    localStorage.setItem("wynd.theme", "dark");
    expect(window.localStorage.getItem("wynd.theme")).toBe("dark");
  });

  it("storage is emptied before each test (2/2)", () => {
    expect(localStorage.getItem("wynd.theme")).toBeNull();
    expect(localStorage.length).toBe(0);
  });

  it("installs the React Flow shims", () => {
    expect(() => new ResizeObserver(() => {}).observe(document.body)).not.toThrow();
    expect(new DOMMatrixReadOnly("translate(10px, 5px) scale(0.5)").m22).toBe(0.5);
    const el = document.createElement("div");
    el.style.width = "220px";
    expect(el.offsetWidth).toBe(220);
    expect(el.offsetHeight).toBe(1);
  });
});
