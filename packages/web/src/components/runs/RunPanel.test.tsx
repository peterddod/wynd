import { act, fireEvent, screen, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Run } from "../../api/types";
import type { DesignSession } from "../../state/design";
import { createFakeApi } from "../../test/fakeApi";
import { FakeEventSource } from "../../test/fakeEventSource";
import { fixture, runEventFrames } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { RunPanel } from "./RunPanel";
import { invalidated, resetDoubles, valueInputs } from "./testDoubles";

vi.mock("../../state/query", () => import("./testDoubles").then((m) => m.queryModule));
vi.mock("../../state/url", () => import("./testDoubles").then((m) => m.urlModule));
vi.mock("../../api/sse", () => import("./testDoubles").then((m) => m.sseModule));
vi.mock("../common/ErrorBox", () => import("./testDoubles").then((m) => m.errorBoxModule));
vi.mock("../common/JsonView", () => import("./testDoubles").then((m) => m.jsonViewModule));
vi.mock("../examples/ValueInput", () => import("./testDoubles").then((m) => m.valueInputModule));

beforeEach(() => resetDoubles());
afterEach(() => {
  vi.useRealTimers();
});

const PID = "process_supplier_invoice";
const EXAMPLE_PDF = "/home/dev/invoices/processes/process_supplier_invoice/examples/acme_inv_1042.pdf";

function fakeDesign(log: string[]): DesignSession {
  return { commit: vi.fn(async (reason: string) => { log.push(`commit:${reason}`); return null; }) } as unknown as DesignSession;
}

async function flush(): Promise<void> {
  await act(async () => {});
}

function url(): URLSearchParams {
  return new URLSearchParams(window.location.search);
}

function startButton(): HTMLButtonElement {
  return screen.getByRole("button", { name: "Start" }) as HTMLButtonElement;
}

/** The step rows' paths, in document order. */
function stepPaths(): string[] {
  return [...document.querySelectorAll(".wo-step-path")].map((el) => el.textContent ?? "");
}

describe("RunForm", () => {
  it("is generated from the interface: required path field with uploads, Start waits for it", async () => {
    renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs" }, design: fakeDesign([]) });
    await screen.findByLabelText("pdf_path");
    const props = valueInputs.filter((p) => p.label === "pdf_path").at(-1);
    expect(props).toMatchObject({ required: true, allowUpload: true, schema: { type: "string", format: "path" } });
    expect(startButton().disabled).toBe(true);
    expect(screen.getByText("Required: pdf_path")).toBeTruthy();
  });

  it("fill from example makes relative path inputs absolute against the process directory", async () => {
    renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs" }, design: fakeDesign([]) });
    const examples = await screen.findByLabelText("Fill from example");
    await flush();
    fireEvent.change(examples, { target: { value: "0" } });
    expect((screen.getByLabelText("pdf_path") as HTMLInputElement).value).toBe(EXAMPLE_PDF);
    expect(startButton().disabled).toBe(false);
  });

  it("Start posts a local run after committing the design, then shows the new run", async () => {
    const log: string[] = [];
    const created: Run = { ...fixture("runFinished"), id: "run_new", status: "running", exit: null, outputs: null };
    const api = createFakeApi({
      runs: {
        create: async () => { log.push("create"); return created; },
        get: async () => created,
      },
    });
    renderApp(<RunPanel processId={PID} />, { api, url: { process: PID, tab: "runs" }, design: fakeDesign(log) });
    fireEvent.change(await screen.findByLabelText("pdf_path"), { target: { value: "/tmp/inv.pdf" } });
    fireEvent.click(startButton());
    await flush();
    expect(log).toEqual(["commit:before_job", "create"]);
    expect(api.runs.create).toHaveBeenCalledWith({ process_id: PID, target: { kind: "local" }, inputs: { pdf_path: "/tmp/inv.pdf" } });
    expect(invalidated).toContain(`runs:${PID}`);
    expect(url().get("run")).toBe("run_new");
    expect(await screen.findByRole("article", { name: "Run run_new" })).toBeTruthy();
    expect(FakeEventSource.last()?.url).toBe("/api/runs/run_new/events?since=0");
  });

  it("an image target uses that build's interface; a release target fires the release, without a commit", async () => {
    const log: string[] = [];
    const { api } = renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs" }, design: fakeDesign(log) });
    const target = await screen.findByLabelText("Target");
    await flush();
    const labels = within(target).getAllByRole("option").map((o) => o.textContent);
    expect(labels).toEqual([
      "Local (source at HEAD)", "Image 9f8e7d6 (3 behind)", "Image 5e4d3c2 (9 behind)",
      "Release 9f8e7d6 (schedule)", "Release 5e4d3c2 (webhook)",
    ]);
    const build = fixture("builds")[0]!;
    fireEvent.change(target, { target: { value: `image:${build.commit}` } });
    await flush();
    expect(api.processes.iface).toHaveBeenLastCalledWith(PID, build.commit);
    fireEvent.change(screen.getByLabelText("pdf_path"), { target: { value: "/tmp/a.pdf" } });
    fireEvent.click(startButton());
    await flush();
    expect(api.runs.create).toHaveBeenLastCalledWith({ process_id: PID, target: { kind: "image", commit: build.commit }, inputs: { pdf_path: "/tmp/a.pdf" } });

    fireEvent.click(screen.getByRole("button", { name: "New run" }));
    const release = fixture("releases")[1]!;
    fireEvent.change(await screen.findByLabelText("Target"), { target: { value: `release:${release.id}` } });
    await flush();
    fireEvent.change(screen.getByLabelText("pdf_path"), { target: { value: "/tmp/b.pdf" } });
    fireEvent.click(startButton());
    await flush();
    expect(api.releases.trigger).toHaveBeenCalledWith(release.id, { pdf_path: "/tmp/b.pdf" });
    expect(log).toEqual([]);
  });
});

describe("RunList", () => {
  it("lists runs by outcome, polls every 3 s while one is live, and selects a run", async () => {
    vi.useFakeTimers();
    const { api } = renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs" }, design: fakeDesign([]) });
    await flush();
    const runs = fixture("runsList");
    expect(screen.getAllByRole("button", { name: /^Run run_/ }).map((b) => b.getAttribute("aria-label")))
      .toEqual([`Run ${runs[0]!.id}: running`, `Run ${runs[1]!.id}: error`, `Run ${runs[2]!.id}: done`]);
    expect(api.runs.list).toHaveBeenCalledTimes(1);
    expect(api.runs.list).toHaveBeenCalledWith({ process_id: PID });
    await act(async () => { vi.advanceTimersByTime(3000); });
    expect(api.runs.list).toHaveBeenCalledTimes(2);

    fireEvent.click(screen.getByRole("button", { name: `Run ${runs[2]!.id}: done` }));
    expect(url().get("run")).toBe(runs[2]!.id);
  });

  it("stops polling once no run is queued or running", async () => {
    vi.useFakeTimers();
    const finished = fixture("runsList").slice(1);
    const api = createFakeApi({ runs: { list: async () => ({ runs: finished }) } });
    renderApp(<RunPanel processId={PID} />, { api, url: { process: PID, tab: "runs" }, design: fakeDesign([]) });
    await flush();
    await act(async () => { vi.advanceTimersByTime(10_000); });
    expect(api.runs.list).toHaveBeenCalledTimes(1);
  });
});

describe("RunDetail", () => {
  const run = fixture("runFinished");

  it("streams the trace into the step tree, loop runs included, and reloads the run at the end", async () => {
    const { api } = renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs", run: run.id }, design: fakeDesign([]) });
    await screen.findByRole("article", { name: `Run ${run.id}` });
    const source = FakeEventSource.last() as FakeEventSource;
    expect(source.url).toBe(`/api/runs/${run.id}/events?since=0`);
    act(() => source.play(runEventFrames()));
    expect(stepPaths()).toEqual(["read", "extract", "validate", "fix", "validate", "save"]);
    expect(screen.getByText("run 2")).toBeTruthy();
    expect(screen.getByText("→ fix via validate.done[1]")).toBeTruthy();
    expect(source.readyState).toBe(FakeEventSource.CLOSED);
    await flush();
    expect(api.runs.get).toHaveBeenCalledTimes(2);
    expect(invalidated).toContain("runs:");
    expect(screen.getByLabelText("Outputs").textContent).toContain("IC-5521");
  });

  it("expanding a step shows its inputs, outputs, summary, usage and activity", async () => {
    renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs", run: run.id }, design: fakeDesign([]) });
    await screen.findByRole("article", { name: `Run ${run.id}` });
    act(() => (FakeEventSource.last() as FakeEventSource).play(runEventFrames()));
    const extract = screen.getAllByRole("button", { expanded: false }).find((b) => b.textContent?.startsWith("extract"));
    expect(extract).toBeDefined();
    fireEvent.click(extract!);
    const details = document.getElementById(extract!.getAttribute("aria-controls") ?? "") as HTMLElement;
    expect(within(details).getByLabelText("Inputs").textContent).toContain("Initech Canada Inc.");
    expect(within(details).getByLabelText("Outputs").textContent).toContain("IC-5521");
    expect(within(details).getByLabelText("Key outputs")).toBeTruthy();
    expect(within(details).getByText(/2104 in \/ 571 out tokens/)).toBeTruthy();
    expect(within(details).getByText("claude-code/claude-haiku-4-5-20251001 · cheap · thinking low")).toBeTruthy();
    expect(within(details).getByText(/\(1 model call\(s\), 0 tool call\(s\)\)/)).toBeTruthy();
  });

  it("a running step is expanded until it ends", async () => {
    renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs", run: run.id }, design: fakeDesign([]) });
    await screen.findByRole("article", { name: `Run ${run.id}` });
    const source = FakeEventSource.last() as FakeEventSource;
    const frames = runEventFrames();
    act(() => source.play(frames.slice(0, 6)));               // extract has started
    const extract = screen.getAllByRole("button").find((b) => b.textContent?.startsWith("extract"));
    expect(extract?.getAttribute("aria-expanded")).toBe("true");
    expect(extract?.textContent).toContain("running…");
    act(() => source.play(frames.slice(6, 8)));               // model.call, step.end
    expect(extract?.getAttribute("aria-expanded")).toBe("false");
  });

  it("a ProcessStep's children nest under it with a process tag", async () => {
    const nested = fixture("runEventsNested");
    const nestedRun: Run = { ...run, id: nested[0]!.run_id, process_id: "finance/monthly_close", outputs: {}, exit: "nothing_to_do" };
    const api = createFakeApi({ runs: { get: async () => nestedRun } });
    renderApp(<RunPanel processId="finance/monthly_close" />, { api, url: { process: "finance/monthly_close", tab: "runs", run: nestedRun.id }, design: fakeDesign([]) });
    await screen.findByRole("article", { name: `Run ${nestedRun.id}` });
    act(() => (FakeEventSource.last() as FakeEventSource).play(runEventFrames(nested)));
    expect(screen.getByText("process: process_supplier_invoice")).toBeTruthy();
    const sub = [...document.querySelectorAll<HTMLElement>(".wo-step")].find((li) => li.querySelector(".wo-step-path")?.textContent === "sub");
    expect([...(sub?.querySelectorAll(":scope > .wo-steps .wo-step-path") ?? [])].map((e) => e.textContent))
      .toEqual(["sub.read", "sub.extract"]);
  });

  it("a run that ended in its error handler shows the ProcessError", async () => {
    const failed = fixture("runError");
    renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs", run: failed.id }, design: fakeDesign([]) });
    const view = await screen.findByRole("region", { name: `Process error in ${PID}` });
    expect(within(view).getByText("Process error: step_error")).toBeTruthy();
    expect(within(view).getByText("save")).toBeTruthy();
    expect(within(view).getByText(/Step error: exception \(PermissionError\)/)).toBeTruthy();
    expect(within(view).getByText(/Workspace kept for inspection/)).toBeTruthy();
    expect(within(view).getByLabelText("Error inputs").textContent).toContain("/data/records");
    expect(screen.queryByLabelText("Outputs")).toBeNull();
  });

  it("shows 'reconnecting…' while the stream is down and ignores replayed events", async () => {
    renderApp(<RunPanel processId={PID} />, { url: { process: PID, tab: "runs", run: run.id }, design: fakeDesign([]) });
    await screen.findByRole("article", { name: `Run ${run.id}` });
    const source = FakeEventSource.last() as FakeEventSource;
    const frames = runEventFrames();
    act(() => source.play(frames.slice(0, 4)));
    act(() => source.error());
    expect(screen.getByText("reconnecting…").getAttribute("role")).toBe("status");
    act(() => source.play(frames.slice(0, 5)));               // the browser resends; seq 1-4 are duplicates
    expect(screen.queryByText("reconnecting…")).toBeNull();
    expect(stepPaths()).toEqual(["read"]);
  });
});
