// JobWatcher: polling, auto-integration, finished-job reactions (`$DRAFTS/07 §9.9`).
import { act, waitFor } from "@testing-library/react";
import { createElement } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import type { Job } from "../api/types";
import { fakeDesign, queries, resetDoubles, toasts, urls } from "../components/chat/testDoubles";
import { createFakeApi } from "../test/fakeApi";
import { fixture } from "../test/fixtures";
import { renderApp } from "../test/render";
import {
  ACTIVE_POLL_MS, AWAITING_POLL_MS, IDLE_POLL_MS, JOB_POLL_MS, JobWatcher, isActive, jobPollMs, needsIntegration,
} from "./jobs";

vi.mock("./query", () => import("../components/chat/testDoubles").then((m) => m.queryModule));
vi.mock("./toasts", () => import("../components/chat/testDoubles").then((m) => m.toastsModule));
vi.mock("./url", () => import("../components/chat/testDoubles").then((m) => m.urlModule));

const PID = "process_supplier_invoice";

/** The compile job as it finished: succeeded with its result branch, not integrated yet. */
function succeededCompile(): Job {
  return { ...fixture("jobCompileIntegrated"), integration: null };
}

function running(job: Job): Job {
  return { ...job, status: "running", finished_at: null, integration: null, build: null, error: null };
}

function dirtyTree(): ApiError {
  return new ApiError(409, "dirty_tree", "the workspace has uncommitted changes", {
    paths: ["processes/process_supplier_invoice/process.yaml"],
  });
}

beforeEach(() => {
  resetDoubles();
});

describe("job predicates", () => {
  it("active = queued or running", () => {
    const job = fixture("jobCompileAwaiting");
    expect(isActive({ ...job, status: "queued" })).toBe(true);
    expect(isActive({ ...job, status: "running" })).toBe(true);
    for (const status of ["awaiting_input", "succeeded", "failed", "cancelled"] as const) {
      expect(isActive({ ...job, status })).toBe(false);
    }
  });

  it("needs integration = a succeeded commit-producing job without an integration", () => {
    expect(needsIntegration(succeededCompile())).toBe(true);
    expect(needsIntegration({ ...succeededCompile(), kind: "test_live" })).toBe(true);
    expect(needsIntegration({ ...succeededCompile(), kind: "optimise" })).toBe(true);
    expect(needsIntegration(fixture("jobCompileIntegrated"))).toBe(false);
    expect(needsIntegration({ ...succeededCompile(), status: "failed" })).toBe(false);
    expect(needsIntegration(fixture("jobBuildSucceeded"))).toBe(false);
    expect(needsIntegration({ ...fixture("jobBuildSucceeded"), kind: "bake" })).toBe(false);
  });

  it("a job card polls 1 s while active, 5 s while awaiting answers, then stops", () => {
    const job = fixture("jobCompileAwaiting");
    expect(jobPollMs({ ...job, status: "queued" })).toBe(JOB_POLL_MS);
    expect(jobPollMs({ ...job, status: "running" })).toBe(1000);
    expect(jobPollMs(job)).toBe(AWAITING_POLL_MS);
    expect(AWAITING_POLL_MS).toBe(5000);
    expect(jobPollMs(fixture("jobCompileIntegrated"))).toBeNull();
    expect(jobPollMs(undefined)).toBeNull();
  });
});

describe("JobWatcher", () => {
  it("polls active jobs every 2 s while any is active, else every 15 s, and fills the job cache", async () => {
    const api = createFakeApi();
    renderApp(createElement(JobWatcher), { api, design: fakeDesign(PID) });
    await waitFor(() => expect(queries.data("jobs:active")).toBeDefined());
    expect(api.jobs.list).toHaveBeenCalledWith({ active: true });
    const pollMs = queries.opts<{ jobs: Job[] }>("jobs:active")?.pollMs as (d: { jobs: Job[] } | undefined) => number;
    expect(pollMs({ jobs: [fixture("jobCompileAwaiting")] })).toBe(ACTIVE_POLL_MS);
    expect(pollMs({ jobs: [] })).toBe(IDLE_POLL_MS);
    expect(pollMs(undefined)).toBe(IDLE_POLL_MS);
    expect(queries.data(`job:${fixture("jobCompileAwaiting").id}`)).toEqual(fixture("jobCompileAwaiting"));
  });

  it("integrates a succeeded compile once, committing and reloading the open process around it", async () => {
    const log: string[] = [];
    const design = fakeDesign(PID, { log });
    const done = succeededCompile();
    const api = createFakeApi({
      jobs: {
        list: async () => ({ jobs: [done] }),                  // a stale list keeps showing it unintegrated
        integrate: async () => {
          log.push("integrate");
          return fixture("jobCompileIntegrated");
        },
      },
    });
    renderApp(createElement(JobWatcher), { api, design });
    await waitFor(() => expect(design.reload).toHaveBeenCalledOnce());
    expect(log).toEqual(["commit:before_integrate", "integrate"]);
    expect(api.jobs.integrate).toHaveBeenCalledWith(done.id);
    expect(queries.data(`job:${done.id}`)).toEqual(fixture("jobCompileIntegrated"));
    expect(queries.invalidated).toContain("processes");

    await act(async () => {
      await queries.refetch("jobs:active");
      await queries.refetch("jobs:active");
    });
    expect(api.jobs.list).toHaveBeenCalledTimes(3);
    expect(api.jobs.integrate).toHaveBeenCalledOnce();
    expect(toasts).toEqual([]);
  });

  it("does not commit or reload when another process is open", async () => {
    const design = fakeDesign("finance/monthly_close");
    const api = createFakeApi({ jobs: { list: async () => ({ jobs: [succeededCompile()] }) } });
    renderApp(createElement(JobWatcher), { api, design });
    await waitFor(() => expect(api.jobs.integrate).toHaveBeenCalledOnce());
    await waitFor(() => expect(queries.invalidated).toContain("processes"));
    expect(design.commit).not.toHaveBeenCalled();
    expect(design.reload).not.toHaveBeenCalled();
  });

  it("on dirty_tree commits once more and retries once", async () => {
    const design = fakeDesign(PID);
    const api = createFakeApi({ jobs: { list: async () => ({ jobs: [succeededCompile()] }) } });
    vi.mocked(api.jobs.integrate).mockRejectedValueOnce(dirtyTree());
    renderApp(createElement(JobWatcher), { api, design });
    await waitFor(() => expect(design.reload).toHaveBeenCalledOnce());
    expect(api.jobs.integrate).toHaveBeenCalledTimes(2);
    expect(design.commit.mock.calls).toEqual([["before_integrate"], ["before_integrate"]]);
    expect(toasts).toEqual([]);
  });

  it("a second dirty_tree gives up with a toast listing the paths", async () => {
    const api = createFakeApi({
      jobs: {
        list: async () => ({ jobs: [succeededCompile()] }),
        integrate: async () => {
          throw dirtyTree();
        },
      },
    });
    renderApp(createElement(JobWatcher), { api, design: fakeDesign(PID) });
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(api.jobs.integrate).toHaveBeenCalledTimes(2);
    expect(toasts[0]?.level).toBe("error");
    expect(toasts[0]?.text).toContain("processes/process_supplier_invoice/process.yaml");
    await act(async () => queries.refetch("jobs:active"));
    expect(api.jobs.integrate).toHaveBeenCalledTimes(2);            // not retried on every poll
  });

  it("a job that fails shows a toast that opens its chat", async () => {
    const failed: Job = {
      ...fixture("jobCompileAwaiting"), status: "failed", finished_at: "2026-09-27T10:05:00Z",
      error: { message: "fix: 1 of 3 examples still failing after 4 revisions", detail: null },
    };
    const api = createFakeApi({ jobs: { get: async () => failed } });
    vi.mocked(api.jobs.list)
      .mockResolvedValueOnce({ jobs: [running(failed)] })
      .mockResolvedValueOnce({ jobs: [] });
    renderApp(createElement(JobWatcher), { api, design: fakeDesign(PID) });
    await waitFor(() => expect(queries.data("jobs:active")).toBeDefined());
    expect(toasts).toEqual([]);

    await act(async () => queries.refetch("jobs:active"));
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(api.jobs.get).toHaveBeenCalledWith(failed.id);
    expect(queries.data(`job:${failed.id}`)).toEqual(failed);
    expect(toasts[0]).toMatchObject({ level: "error" });
    expect(toasts[0]?.text).toBe(`Compile of ${PID} failed: fix: 1 of 3 examples still failing after 4 revisions`);
    act(() => toasts[0]?.action?.run());
    expect(urls.current().chat).toBe(failed.chat_id);
  });

  it("a build that succeeds refreshes that process's builds and the process list", async () => {
    const build = fixture("jobBuildSucceeded");
    const api = createFakeApi();
    vi.mocked(api.jobs.list)
      .mockResolvedValueOnce({ jobs: [running(build)] })
      .mockResolvedValueOnce({ jobs: [] });
    renderApp(createElement(JobWatcher), { api, design: fakeDesign(PID) });
    await waitFor(() => expect(queries.data("jobs:active")).toBeDefined());
    await act(async () => queries.refetch("jobs:active"));
    await waitFor(() => expect(queries.invalidated).toEqual([`builds:${PID}`, "processes"]));
    expect(queries.data(`job:${build.id}`)).toEqual(build);
    expect(toasts).toEqual([]);
  });

  it("a cancelled job leaves quietly", async () => {
    const cancelled: Job = { ...fixture("jobCompileAwaiting"), status: "cancelled" };
    const api = createFakeApi({ jobs: { get: async () => cancelled } });
    vi.mocked(api.jobs.list)
      .mockResolvedValueOnce({ jobs: [fixture("jobCompileAwaiting")] })
      .mockResolvedValueOnce({ jobs: [] });
    renderApp(createElement(JobWatcher), { api, design: fakeDesign(PID) });
    await waitFor(() => expect(queries.data("jobs:active")).toBeDefined());
    await act(async () => queries.refetch("jobs:active"));
    await waitFor(() => expect(queries.data(`job:${cancelled.id}`)).toEqual(cancelled));
    expect(toasts).toEqual([]);
    expect(queries.invalidated).toEqual([]);
  });
});
