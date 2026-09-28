// JobCard and its parts: compile session, questions, build result, integration, logs (`$DRAFTS/07 §9.7`, §17.3).
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Integration, Interface, Job, StepInfo } from "../../api/types";
import { createFakeApi, type FakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { IntegrationOutcome } from "./IntegrationOutcome";
import { JobCard, elapsed } from "./JobCard";
import { JobLogs, LOG_POLL_MS, MAX_LOG_LINES, logTail } from "./JobLogs";
import { fakeDesign, queries, queryModule, resetDoubles, toasts, type FakeDesign } from "./testDoubles";

vi.mock("../../state/query", () => import("./testDoubles").then((m) => m.queryModule));
vi.mock("../../state/toasts", () => import("./testDoubles").then((m) => m.toastsModule));
vi.mock("../../state/url", () => import("./testDoubles").then((m) => m.urlModule));
vi.mock("../../model/values", () => import("./testDoubles").then((m) => m.valuesModule));
vi.mock("../../model/schemaText", () => import("./testDoubles").then((m) => m.schemaTextModule));
vi.mock("../examples/ExampleCard", () => import("./testDoubles").then((m) => m.exampleCardModule));
vi.mock("../releases/NewReleaseDialog", () => import("./testDoubles").then((m) => m.newReleaseDialogModule));

const PID = "process_supplier_invoice";
const AWAITING = fixture("jobCompileAwaiting");

/** Renders the card of `job`, served by the fake api's `jobs.get`. */
async function renderCard(job: Job, opts: { api?: FakeApi; design?: FakeDesign } = {}) {
  const api = opts.api ?? createFakeApi({ jobs: { get: async () => job } });
  const view = renderApp(<JobCard jobId={job.id} />, { api, design: opts.design ?? fakeDesign(null) });
  await screen.findByRole("region", { name: new RegExp(`job for ${job.process_id}`) });
  return { ...view, api };
}

function row(step: string): HTMLElement {
  return screen.getByRole("rowheader", { name: new RegExp(`^${step} `) }).closest("tr") as HTMLElement;
}

function proposal(): HTMLElement {
  return screen.getByRole("article", { name: /^Proposal:/ });
}

beforeEach(() => {
  resetDoubles();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("JobCard header", () => {
  it("shows kind, process, ref, status, cost and actions of a compile awaiting answers", async () => {
    await renderCard(AWAITING);
    const head = screen.getByRole("region", { name: `Compile job for ${PID}` }).querySelector("header") as HTMLElement;
    expect(head.querySelector(".wc-job-title")?.textContent).toBe(`Compile ${PID} at a1b2c3d`);
    expect(within(head).getByText("awaiting input")).toBeTruthy();
    expect(within(head).getByText("$0.41")).toBeTruthy();
    expect(within(head).getByRole("button", { name: "Cancel" })).toBeTruthy();
    expect(within(head).getByRole("button", { name: "Logs ▸" })).toBeTruthy();
  });

  it("polls the job 5 s while it awaits answers", async () => {
    await renderCard(AWAITING);
    const pollMs = queries.opts<Job>(`job:${AWAITING.id}`)?.pollMs as (j: Job | undefined) => number | null;
    expect(pollMs(AWAITING)).toBe(5000);
    expect(pollMs({ ...AWAITING, status: "running" })).toBe(1000);
    expect(pollMs(fixture("jobCompileIntegrated"))).toBeNull();
  });

  it("cancels an active job and shows the returned record", async () => {
    const running: Job = { ...AWAITING, status: "running" };
    const api = createFakeApi({
      jobs: { get: async () => running, cancel: async () => ({ ...running, status: "cancelled" }) },
    });
    await renderCard(running, { api });
    fireEvent.click(screen.getByRole("button", { name: "Cancel" }));
    await screen.findByText("cancelled");
    expect(api.jobs.cancel).toHaveBeenCalledWith(running.id);
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
  });

  it("a finished job offers no cancel, and a failed one shows its error and detail", async () => {
    const failed: Job = {
      ...AWAITING, status: "failed", finished_at: "2026-09-27T10:05:00Z", session: null,
      error: { message: "fix: 1 of 3 examples still failing", detail: "Traceback (most recent call last): …" },
    };
    await renderCard(failed);
    expect(screen.queryByRole("button", { name: "Cancel" })).toBeNull();
    const alert = screen.getByRole("alert");
    expect(within(alert).getByText("fix: 1 of 3 examples still failing")).toBeTruthy();
    fireEvent.click(within(alert).getByText("Details"));
    expect(within(alert).getByText("Traceback (most recent call last): …")).toBeTruthy();
  });

  it("elapsed runs from start to finish, or to now while unfinished", () => {
    const job = fixture("jobCompileIntegrated");                    // 10:00:01 -> 10:09:44
    expect(elapsed(job, 0)).toBe("9m 43s");
    const now = Date.parse("2026-09-27T10:00:43Z");
    expect(elapsed({ ...job, finished_at: null }, now)).toBe("42s");
    expect(elapsed({ ...job, started_at: null, finished_at: "2026-09-27T11:05:00Z" }, 0)).toBe("1h 05m");
  });
});

describe("CompileSessionView", () => {
  it("lists the per-step decisions; skipped steps give their reason", async () => {
    await renderCard(AWAITING);
    const rows = screen.getAllByRole("row").slice(1);
    expect(rows.map((r) => r.querySelector("th")?.firstChild?.textContent?.trim()))
      .toEqual(["read", "extract", "validate", "fix", "save", "escalate"]);
    expect(within(row("read")).getByText("proto-step unchanged")).toBeTruthy();
    const fix = row("fix");
    expect(within(fix).getByText("agentic · cheap · low")).toBeTruthy();
    expect(within(fix).getByText("2/3")).toBeTruthy();
    expect(within(fix).getAllByRole("cell").at(-1)?.textContent).toBe("2");
    fireEvent.click(within(fix).getByText("Why"));
    expect(within(fix).getByText(/no pure function of the inputs reproduces examples 2 and 3/)).toBeTruthy();
  });

  it("explains the two-step split", async () => {
    const job = structuredClone(AWAITING);
    const fix = job.session?.steps.find((s) => s.step === "fix");
    if (fix?.decision == null) throw new Error("fixture: fix has a decision");
    fix.decision.split = { deterministic: "fix_fields_rules", agentic: "fix_fields_judgement" };
    await renderCard(job);
    expect(within(row("fix")).getByText("split → fix_fields_rules + fix_fields_judgement")).toBeTruthy();
    expect(within(row("fix")).getByText(/Deterministic code passed 2 of 3 examples; the rest go\s+to an agentic step via the error exit\./))
      .toBeTruthy();
  });

  it("lists pending questions first and folds answered ones away", async () => {
    await renderCard(AWAITING);
    const pending = screen.getByRole("region", { name: "Pending questions" });
    expect(within(pending).getByText("Questions for you (2)")).toBeTruthy();
    expect(within(pending).getAllByRole("article").map((a) => a.getAttribute("aria-label"))).toEqual([
      "Question: When the text names no currency at all, should fix_fields assume GBP or leave the field for escalation?",
      "Proposal: What should happen if due_date is outside the allowed range because of a two-digit year?",
    ]);

    act(() => queryModule.setQueryData(`job:${AWAITING.id}`, fixture("jobCompileIntegrated")));
    expect(screen.queryByRole("region", { name: "Pending questions" })).toBeNull();
    fireEvent.click(screen.getByText("Answered questions (2)"));
    expect(screen.getByText("Answer: Leave it for escalation.")).toBeTruthy();
    expect(screen.getByText("Answer: Confirmed")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "Confirm" })).toBeNull();
  });

  it("shows inferred schemas and the events timeline", async () => {
    const job = structuredClone(AWAITING);
    const iface: Interface = {
      inputs: { type: "object", properties: { fields: { type: "object" }, errors: { type: "array" } } },
      exits: [{ name: "done", schema: null }],
      source: "inferred",
    };
    if (job.session === null) throw new Error("fixture: session");
    job.session.inferred_schemas = { fix: iface };
    await renderCard(job);
    const inferred = screen.getByRole("region", { name: "Inferred schemas" });
    expect(inferred.textContent).toBe("fix: takes fields, errors Inferred from examples");
    fireEvent.click(screen.getByText("Events (3)"));
    expect(screen.getByText("2/3 examples pass after revision 2")).toBeTruthy();
  });
});

describe("answering", () => {
  it("a clarification posts {question_id, text} and caches the returned job", async () => {
    const answered: Job = { ...AWAITING, status: "queued" };
    const api = createFakeApi({ jobs: { get: async () => AWAITING, answer: async () => answered } });
    await renderCard(AWAITING, { api });
    const card = screen.getByRole("article", { name: /^Question:/ });
    const answer = within(card).getByRole("button", { name: "Answer" }) as HTMLButtonElement;
    expect(answer.disabled).toBe(true);
    fireEvent.change(within(card).getByLabelText("Your answer"), { target: { value: "  Leave it for escalation. " } });
    fireEvent.click(answer);
    await waitFor(() => expect(queries.data(`job:${AWAITING.id}`)).toEqual(answered));
    expect(api.jobs.answer).toHaveBeenCalledWith(AWAITING.id, {
      question_id: "fix.clarify.1", text: "Leave it for escalation.",
    });
    expect(queries.invalidated).toContain("chats");
  });

  it("a proposal is shown as a sentence and a card; Confirm posts decision confirm", async () => {
    const { api } = await renderCard(AWAITING);
    const card = proposal();
    expect(within(card).getByText(/^sentence \{"inputs":\{"invoice_text":"Umbrella Corp/)).toBeTruthy();
    expect(within(card).getByTestId("example-card").textContent).toMatch(/\| exits: done,error$/);
    fireEvent.click(within(card).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(api.jobs.answer).toHaveBeenCalledOnce());
    expect(api.jobs.answer).toHaveBeenCalledWith(AWAITING.id, { question_id: "fix.example.1", decision: "confirm" });
  });

  it("Correct… edits a copy of the proposal and posts it as the example", async () => {
    const { api } = await renderCard(AWAITING);
    fireEvent.click(within(proposal()).getByRole("button", { name: "Correct…" }));
    const editor = within(proposal()).getByLabelText("Example JSON") as HTMLTextAreaElement;
    const example = JSON.parse(editor.value) as { exit: string; outputs: Record<string, unknown> };
    expect(example.exit).toBe("done");
    const corrected = { ...example, exit: "error", outputs: {} };
    fireEvent.change(editor, { target: { value: JSON.stringify(corrected) } });
    fireEvent.click(within(proposal()).getByRole("button", { name: "Save correction" }));
    await waitFor(() => expect(api.jobs.answer).toHaveBeenCalledOnce());
    expect(api.jobs.answer).toHaveBeenCalledWith(AWAITING.id, {
      question_id: "fix.example.1", decision: "correct", example: corrected,
    });
    await waitFor(() => expect(within(proposal()).queryByLabelText("Example JSON")).toBeNull());
  });

  it("Cancel leaves the correction without posting; Not a real case posts decision reject", async () => {
    const { api } = await renderCard(AWAITING);
    fireEvent.click(within(proposal()).getByRole("button", { name: "Correct…" }));
    fireEvent.click(within(proposal()).getByRole("button", { name: "Cancel" }));
    fireEvent.click(within(proposal()).getByRole("button", { name: "Not a real case" }));
    await waitFor(() => expect(api.jobs.answer).toHaveBeenCalledOnce());
    expect(api.jobs.answer).toHaveBeenCalledWith(AWAITING.id, { question_id: "fix.example.1", decision: "reject" });
  });

  it("the correction card uses the open process's step interface", async () => {
    const fix = fixture("design").steps.fix as StepInfo;
    const steps = {
      fix: { ...fix, interface: { ...fix.interface, exits: [...fix.interface.exits, { name: "cannot_fix", schema: null }] } },
    };
    await renderCard(AWAITING, { design: fakeDesign(PID, { steps }) });
    expect(within(proposal()).getByTestId("example-card").textContent).toMatch(/\| exits: done,cannot_fix,error$/);
  });

  it("a rejected answer shows the error and keeps the question open", async () => {
    const api = createFakeApi({
      jobs: {
        get: async () => AWAITING,
        answer: async () => {
          throw new Error("unknown question 'fix.example.1'");
        },
      },
    });
    await renderCard(AWAITING, { api });
    fireEvent.click(within(proposal()).getByRole("button", { name: "Confirm" }));
    await waitFor(() => expect(api.jobs.answer).toHaveBeenCalledOnce());
    expect(within(proposal()).getByRole("button", { name: "Confirm" })).toBeTruthy();
    expect(queries.data(`job:${AWAITING.id}`)).toEqual(AWAITING);
  });
});

describe("build result", () => {
  it("shows the image, commit, reused tests and build dir, and opens a release for this build", async () => {
    const build = fixture("jobBuildSucceeded");
    await renderCard(build);
    const facts = within(screen.getByText("Image").closest("dl") as HTMLElement);
    expect(facts.getByText("localhost:5001/wynd/process_supplier_invoice:9f8e7d6c5b4a")).toBeTruthy();
    expect(facts.getByText("9f8e7d6").getAttribute("title")).toBe("9f8e7d6c5b4a39281706f5e4d3c2b1a098765432");
    expect(facts.getByText("12/12 passed").textContent).toBe("12/12 passed (reused result recorded for this commit)");
    expect(facts.getByText(".wynd/build/process_supplier_invoice/9f8e7d6c5b4a39281706f5e4d3c2b1a098765432")).toBeTruthy();
    expect(screen.queryByRole("dialog")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Create release…" }));
    expect(screen.getByRole("dialog", { name: "New release" }).textContent)
      .toContain(`release ${PID} at 9f8e7d6c5b4a39281706f5e4d3c2b1a098765432`);
    fireEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("IntegrationOutcome", () => {
  const base = fixture("jobCompileIntegrated").integration as Integration;

  it("fast_forward", () => {
    const { container } = render(<IntegrationOutcome integration={base} />);
    expect(container.textContent).toBe(
      "Fast-forwarded wynd/compile/process_supplier_invoice/job_20260927T100000123_4f1c2e onto main, now at d4e5f60.",
    );
  });

  it("rebased", () => {
    const { container } = render(<IntegrationOutcome integration={{ ...base, mode: "rebased", skipped_commits: 3 }} />);
    expect(container.textContent).toBe("Rebased onto main past 3 unrelated commits, now at d4e5f60.");
  });

  it("pr_branch", () => {
    const integration: Integration = {
      ...base, mode: "pr_branch", head: null, conflicts: ["processes/process_supplier_invoice/process.yaml"],
      reason: "gh pr create --head wynd/compile/process_supplier_invoice/job_20260927T100000123_4f1c2e --base main",
    };
    render(<IntegrationOutcome integration={integration} />);
    expect(screen.getByText(/for review: commits inside this process's closure moved since the job started/)).toBeTruthy();
    expect(within(screen.getByRole("list", { name: "Conflicting paths" })).getByText(integration.conflicts[0] as string))
      .toBeTruthy();
    expect(screen.getByText(/^gh pr create --head/)).toBeTruthy();
    expect(screen.getByText(`git switch ${base.branch}`)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("pr_branch with a pull request link", () => {
    render(<IntegrationOutcome integration={{ ...base, mode: "pr_branch", pr_url: "https://github.com/acme/ops/pull/7" }} />);
    const link = screen.getByRole("link", { name: "Open the pull request" });
    expect(link.getAttribute("href")).toBe("https://github.com/acme/ops/pull/7");
    expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  });

  it("noop", () => {
    const { container } = render(<IntegrationOutcome integration={{ ...base, mode: "noop", head: null }} />);
    expect(container.textContent).toBe("Nothing to integrate.");
  });

  it("is shown on the job card", async () => {
    await renderCard(fixture("jobCompileIntegrated"));
    expect(screen.getByText(/^Fast-forwarded/)).toBeTruthy();
  });
});

describe("JobLogs", () => {
  it("keeps the last 500 lines", () => {
    const lines = Array.from({ length: MAX_LOG_LINES + 20 }, (_, i) => `line ${i}`);
    const tail = logTail(`${lines.join("\n")}\n`);
    expect(tail.split("\n")).toHaveLength(MAX_LOG_LINES + 1);
    expect(tail.startsWith("line 20\n")).toBe(true);
    expect(tail.endsWith("line 519\n")).toBe(true);
    expect(logTail("a\nb")).toBe("a\nb");
  });

  it("appends from the returned offset every second while active and stops when done", async () => {
    vi.useFakeTimers();
    const api = createFakeApi();
    vi.mocked(api.jobs.logs)
      .mockResolvedValueOnce({ text: "10:00:01 checkout\n", offset: 18, done: false })
      .mockResolvedValueOnce({ text: "", offset: 18, done: false })
      .mockResolvedValueOnce({ text: "10:00:02 compile\n", offset: 35, done: true });
    renderApp(<JobLogs jobId={AWAITING.id} active />, { api });
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByLabelText("Job log").textContent).toBe("10:00:01 checkout\n");
    await act(async () => vi.advanceTimersByTimeAsync(LOG_POLL_MS));
    await act(async () => vi.advanceTimersByTimeAsync(LOG_POLL_MS));
    expect(vi.mocked(api.jobs.logs).mock.calls).toEqual([[AWAITING.id, 0], [AWAITING.id, 18], [AWAITING.id, 18]]);
    expect(screen.getByLabelText("Job log").textContent).toBe("10:00:01 checkout\n10:00:02 compile\n");
    await act(async () => vi.advanceTimersByTimeAsync(5 * LOG_POLL_MS));
    expect(api.jobs.logs).toHaveBeenCalledTimes(3);
  });

  it("reads once when the job is not active", async () => {
    vi.useFakeTimers();
    const api = createFakeApi();
    renderApp(<JobLogs jobId={AWAITING.id} active={false} />, { api });
    await act(async () => vi.advanceTimersByTimeAsync(5 * LOG_POLL_MS));
    expect(api.jobs.logs).toHaveBeenCalledOnce();
    expect(screen.getByLabelText("Job log").textContent).toContain("fix: 2/3 examples pass after revision 2");
  });

  it("opens from the card's Logs button", async () => {
    const { api } = await renderCard(AWAITING);
    fireEvent.click(screen.getByRole("button", { name: "Logs ▸" }));
    await waitFor(() => expect(api.jobs.logs).toHaveBeenCalledWith(AWAITING.id, 0));
    expect(screen.getByRole("button", { name: "Logs ▾" }).getAttribute("aria-expanded")).toBe("true");
    expect(toasts).toEqual([]);
  });
});
