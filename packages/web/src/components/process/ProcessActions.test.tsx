import { act, fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { DesignSession } from "../../state/design";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { invalidated, resetDoubles, toastSpy } from "../runs/testDoubles";
import { ProcessActions } from "./ProcessActions";

vi.mock("../../state/query", () => import("../runs/testDoubles").then((m) => m.queryModule));
vi.mock("../../state/url", () => import("../runs/testDoubles").then((m) => m.urlModule));
vi.mock("../../state/toasts", () => import("../runs/testDoubles").then((m) => m.toastsModule));
vi.mock("../common/Dialog", () => import("../runs/testDoubles").then((m) => m.dialogModule));
vi.mock("../common/ErrorBox", () => import("../runs/testDoubles").then((m) => m.errorBoxModule));
vi.mock("../examples/ValueInput", () => import("../runs/testDoubles").then((m) => m.valueInputModule));

beforeEach(() => resetDoubles());

const PID = "process_supplier_invoice";

function fakeDesign(log: string[]): DesignSession {
  return { commit: vi.fn(async (reason: string) => { log.push(`commit:${reason}`); return null; }) } as unknown as DesignSession;
}

async function flush(): Promise<void> {
  await act(async () => {});
}

function button(name: string | RegExp): HTMLButtonElement {
  return screen.getByRole("button", { name }) as HTMLButtonElement;
}

describe("ProcessActions", () => {
  it("Build is disabled while steps are design-only, with a tooltip naming them", async () => {
    renderApp(<ProcessActions processId={PID} />, { design: fakeDesign([]) });
    await flush();
    expect(button("Build").disabled).toBe(true);
    expect(button("Build").title).toBe("Compile first: steps fix are design-only");
  });

  it("Compile commits the design first, submits, seeds the job and opens the returned chat", async () => {
    const log: string[] = [];
    const started = fixture("jobCompileStarted");
    const api = createFakeApi({
      jobs: { list: async () => ({ jobs: [] }) },
      processes: { compile: async () => { log.push("compile"); return started; } },
    });
    renderApp(<ProcessActions processId={PID} />, { api, design: fakeDesign(log) });
    await flush();
    fireEvent.click(button("Compile"));
    await flush();
    expect(log).toEqual(["commit:before_job", "compile"]);
    expect(api.processes.compile).toHaveBeenCalledWith(PID);
    expect(new URLSearchParams(window.location.search).get("chat")).toBe(started.chat.id);
    expect(invalidated).toEqual(expect.arrayContaining(["jobs", "chats"]));
  });

  it("an active compile job turns Compile into 'Needs answers (n)', which opens its chat", async () => {
    const { api } = renderApp(<ProcessActions processId={PID} />, { design: fakeDesign([]) });
    const needs = await screen.findByRole("button", { name: "Needs answers (2)" });
    fireEvent.click(needs);
    expect(new URLSearchParams(window.location.search).get("chat")).toBe(fixture("jobCompileAwaiting").chat_id);
    expect(api.processes.compile).not.toHaveBeenCalled();
  });

  it("a running build turns Build into 'Building…'", async () => {
    const running = { ...fixture("jobBuildSucceeded"), status: "running" as const };
    const api = createFakeApi({ jobs: { list: async () => ({ jobs: [running] }) } });
    renderApp(<ProcessActions processId={PID} />, { api, design: fakeDesign([]) });
    fireEvent.click(await screen.findByRole("button", { name: "Building…" }));
    expect(new URLSearchParams(window.location.search).get("chat")).toBe(running.chat_id);
  });

  it("dirty_tree shows the paths; Retry submits again", async () => {
    const log: string[] = [];
    let calls = 0;
    const api = createFakeApi({
      processes: {
        build: async () => {
          calls += 1;
          if (calls === 1) throw new ApiError(409, "dirty_tree", "the workspace has uncommitted changes", { paths: ["notes.txt", "processes/x/process.yaml"] });
          return { ...fixture("jobCompileStarted"), job: fixture("jobBuildSucceeded") };
        },
      },
    });
    renderApp(<ProcessActions processId="finance/monthly_close" />, { api, design: fakeDesign(log) });
    await flush();
    expect(button("Build").disabled).toBe(false);
    fireEvent.click(button("Build"));
    const dialog = await screen.findByRole("dialog", { name: "Uncommitted changes" });
    expect(within(dialog).getAllByRole("listitem").map((li) => li.textContent)).toEqual(["notes.txt", "processes/x/process.yaml"]);
    expect(toastSpy).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Retry" }));
    await flush();
    expect(api.processes.build).toHaveBeenCalledTimes(2);
    expect(log).toEqual(["commit:before_job", "commit:before_job"]);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("any other failure is a toast", async () => {
    const api = createFakeApi({
      jobs: { list: async () => ({ jobs: [] }) },
      processes: { compile: async () => { throw new ApiError(503, "unavailable", "job runner is down"); } },
    });
    renderApp(<ProcessActions processId={PID} />, { api, design: fakeDesign([]) });
    await flush();
    fireEvent.click(button("Compile"));
    await flush();
    expect(toastSpy).toHaveBeenCalledWith("Compile did not start: job runner is down", { level: "error" });
  });

  it("Release… switches to the Releases tab and opens the new-release dialog", async () => {
    renderApp(<ProcessActions processId={PID} />, { design: fakeDesign([]) });
    await flush();
    fireEvent.click(button("Release…"));
    expect(new URLSearchParams(window.location.search).get("tab")).toBe("releases");
    expect(await screen.findByRole("dialog", { name: "New release" })).toBeTruthy();
  });

  it("Release… is disabled with no build and no built flag", async () => {
    const api = createFakeApi({ processes: { builds: async () => ({ builds: [] }) } });
    renderApp(<ProcessActions processId={PID} />, { api, design: fakeDesign([]) });
    await flush();
    expect(button("Release…").disabled).toBe(true);
  });
});
