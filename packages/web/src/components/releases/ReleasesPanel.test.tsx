import { act, fireEvent, screen, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { DesignSession } from "../../state/design";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { invalidated, resetDoubles } from "../runs/testDoubles";
import { ReleasesPanel } from "./ReleasesPanel";

vi.mock("../../state/query", () => import("../runs/testDoubles").then((m) => m.queryModule));
vi.mock("../../state/url", () => import("../runs/testDoubles").then((m) => m.urlModule));
vi.mock("../common/Dialog", () => import("../runs/testDoubles").then((m) => m.dialogModule));
vi.mock("../common/ErrorBox", () => import("../runs/testDoubles").then((m) => m.errorBoxModule));
vi.mock("../common/CopyButton", () => import("../runs/testDoubles").then((m) => m.copyButtonModule));
vi.mock("../examples/ValueInput", () => import("../runs/testDoubles").then((m) => m.valueInputModule));

beforeEach(() => resetDoubles());

const PID = "process_supplier_invoice";
const [SCHEDULED, WEBHOOK] = fixture("releases");

function fakeDesign(log: string[]): DesignSession {
  return { commit: vi.fn(async (reason: string) => { log.push(`commit:${reason}`); return null; }) } as unknown as DesignSession;
}

async function flush(): Promise<void> {
  await act(async () => {});
}

function card(short: string): HTMLElement {
  return screen.getByRole("article", { name: `Release ${short}` });
}

async function openNewRelease(): Promise<HTMLElement> {
  fireEvent.click(screen.getByRole("button", { name: "New release" }));
  const dialog = screen.getByRole("dialog", { name: "New release" });
  await flush();
  return dialog;
}

describe("ReleasesPanel", () => {
  it("cards show commit and HEAD distance, state, trigger summary, image and env check", async () => {
    renderApp(<ReleasesPanel processId={PID} />, { design: fakeDesign([]) });
    await screen.findByRole("article", { name: "Release 9f8e7d6" });
    await flush();
    const scheduled = card("9f8e7d6");
    expect(within(scheduled).getByText("3 behind")).toBeTruthy();
    expect(within(scheduled).getByText("serving")).toBeTruthy();
    expect(within(scheduled).getByText(/^schedule · 0 7 \* \* 1-5 \(Europe\/London\) · next /)).toBeTruthy();
    expect(within(scheduled).getByRole("button", { name: "Copy image" }).getAttribute("data-copy")).toBe(SCHEDULED!.image);
    expect(within(scheduled).getByText("✕ missing: ESCALATIONS_DIR, REVIEW_DIR")).toBeTruthy();
    const webhook = card("5e4d3c2");
    expect(within(webhook).getByText(`webhook · POST ${WEBHOOK!.webhook_url}`)).toBeTruthy();
    expect(within(webhook).getByRole("button", { name: "Copy webhook URL" })).toBeTruthy();
    expect(within(webhook).getByText("stopped · disabled")).toBeTruthy();
    expect(within(webhook).getByRole("button", { name: "Enable" })).toBeTruthy();
  });

  it("a complete environment reads as such", async () => {
    const api = createFakeApi({ releases: { envCheck: async () => ({ ok: true, missing: [], unbound: [], issues: [] }) } });
    renderApp(<ReleasesPanel processId={PID} />, { api, design: fakeDesign([]) });
    await screen.findByRole("article", { name: "Release 9f8e7d6" });
    await flush();
    expect(within(card("9f8e7d6")).getByText("✓ environment complete")).toBeTruthy();
  });

  it("with no builds, New release says only built processes can be released and offers Build", async () => {
    const log: string[] = [];
    const started = { ...fixture("jobCompileStarted"), job: fixture("jobBuildSucceeded") };
    const api = createFakeApi({
      processes: { builds: async () => ({ builds: [] }), build: async () => { log.push("build"); return started; } },
    });
    renderApp(<ReleasesPanel processId={PID} />, { api, design: fakeDesign(log) });
    const dialog = await openNewRelease();
    expect(within(dialog).getByText(/Only built processes can be released: Build this process first\./)).toBeTruthy();
    expect(within(dialog).queryByRole("button", { name: "Create" })).toBeNull();
    fireEvent.click(within(dialog).getByRole("button", { name: "Build" }));
    await flush();
    expect(log).toEqual(["commit:before_job", "build"]);
    expect(new URLSearchParams(window.location.search).get("chat")).toBe(started.chat.id);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Create posts the build, trigger and env; required vars default to their environment", async () => {
    const { api } = renderApp(<ReleasesPanel processId={PID} />, { design: fakeDesign([]) });
    const dialog = await openNewRelease();
    const build = fixture("builds")[0]!;
    expect((within(dialog).getByLabelText("Build") as HTMLSelectElement).value).toBe(build.commit);
    fireEvent.click(within(dialog).getByLabelText("Schedule"));
    const cron = within(dialog).getByLabelText("Cron");
    const create = within(dialog).getByRole("button", { name: "Create" }) as HTMLButtonElement;
    fireEvent.change(cron, { target: { value: "0 6 *" } });
    expect(cron.getAttribute("aria-invalid")).toBe("true");
    expect(create.disabled).toBe(true);
    fireEvent.change(cron, { target: { value: "0 6 * * *" } });
    fireEvent.change(within(dialog).getByLabelText("pdf_path"), { target: { value: "/inbox/latest.pdf" } });
    fireEvent.click(create);
    await flush();
    expect(api.releases.create).toHaveBeenCalledWith({
      process_id: PID,
      commit: build.commit,
      trigger: { kind: "schedule", cron: "0 6 * * *", timezone: null, inputs: { pdf_path: "/inbox/latest.pdf" } },
      env: {
        ESCALATIONS_DIR: { from_env: "ESCALATIONS_DIR" },
        RECORDS_DIR: { from_env: "RECORDS_DIR" },
        REVIEW_DIR: { from_env: "REVIEW_DIR" },
      },
      enabled: true,
    });
    expect(invalidated).toEqual(expect.arrayContaining([`releases:${PID}`, "processes"]));
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("env binding: secrets cannot take a value; values and renamed sources are sent", async () => {
    const { api } = renderApp(<ReleasesPanel processId={PID} />, { design: fakeDesign([]) });
    const dialog = await openNewRelease();
    const token = within(dialog).getByLabelText("CLAUDE_CODE_OAUTH_TOKEN source") as HTMLSelectElement;
    expect(token.value).toBe("none");
    expect((within(token).getByRole("option", { name: "Value" }) as HTMLOptionElement).disabled).toBe(true);
    fireEvent.change(token, { target: { value: "from_env" } });
    fireEvent.change(within(dialog).getByLabelText("CLAUDE_CODE_OAUTH_TOKEN environment variable"), { target: { value: "DEV_KEY" } });
    fireEvent.change(within(dialog).getByLabelText("RECORDS_DIR source"), { target: { value: "value" } });
    fireEvent.change(within(dialog).getByLabelText("RECORDS_DIR value"), { target: { value: "/data/records" } });
    fireEvent.change(within(dialog).getByLabelText("REVIEW_DIR source"), { target: { value: "none" } });
    fireEvent.click(within(dialog).getByLabelText("Enabled (start serving now)"));
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    await flush();
    expect(api.releases.create).toHaveBeenCalledWith(expect.objectContaining({
      trigger: { kind: "manual" },
      env: {
        ESCALATIONS_DIR: { from_env: "ESCALATIONS_DIR" },
        RECORDS_DIR: { value: "/data/records" },
        CLAUDE_CODE_OAUTH_TOKEN: { from_env: "DEV_KEY" },
      },
      enabled: false,
    }));
  });

  it("a create error (409 not_built) stays in the dialog", async () => {
    const { ApiError } = await import("../../api/client");
    const api = createFakeApi({ releases: { create: async () => { throw new ApiError(409, "not_built", "no build of process_supplier_invoice at 9f8e7d6"); } } });
    renderApp(<ReleasesPanel processId={PID} />, { api, design: fakeDesign([]) });
    const dialog = await openNewRelease();
    fireEvent.click(within(dialog).getByRole("button", { name: "Create" }));
    expect((await within(dialog).findByRole("alert")).textContent).toContain("no build of process_supplier_invoice");
  });

  it("Run now fires the release with the entered inputs and opens the run in the Runs tab", async () => {
    const log: string[] = [];
    const { api } = renderApp(<ReleasesPanel processId={PID} />, { design: fakeDesign(log) });
    await screen.findByRole("article", { name: "Release 9f8e7d6" });
    fireEvent.click(within(card("9f8e7d6")).getByRole("button", { name: "Run now" }));
    const dialog = screen.getByRole("dialog", { name: "Run release 9f8e7d6" });
    await flush();
    expect((within(dialog).getByLabelText("Target") as HTMLSelectElement).value).toBe(`release:${SCHEDULED!.id}`);
    fireEvent.change(within(dialog).getByLabelText("pdf_path"), { target: { value: "/inbox/today.pdf" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Start" }));
    await flush();
    expect(api.releases.trigger).toHaveBeenCalledWith(SCHEDULED!.id, { pdf_path: "/inbox/today.pdf" });
    expect(log).toEqual([]);
    const url = new URLSearchParams(window.location.search);
    expect(url.get("tab")).toBe("runs");
    expect(url.get("run")).toBe("run_20260927T120000000_0a0b0c");
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Disable patches enabled; Delete asks first", async () => {
    const { api } = renderApp(<ReleasesPanel processId={PID} />, { design: fakeDesign([]) });
    await screen.findByRole("article", { name: "Release 9f8e7d6" });
    fireEvent.click(within(card("9f8e7d6")).getByRole("button", { name: "Disable" }));
    await flush();
    expect(api.releases.update).toHaveBeenCalledWith(SCHEDULED!.id, { enabled: false });
    expect(invalidated).toEqual(expect.arrayContaining([`releases:${PID}`, `envcheck:${SCHEDULED!.id}`, "processes"]));

    fireEvent.click(within(card("5e4d3c2")).getByRole("button", { name: "Delete" }));
    const confirm = screen.getByRole("dialog", { name: "Delete release 5e4d3c2?" });
    expect(api.releases.remove).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole("button", { name: "Delete" }));
    await flush();
    expect(api.releases.remove).toHaveBeenCalledWith(WEBHOOK!.id);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Edit trigger and Edit env save patches", async () => {
    const { api } = renderApp(<ReleasesPanel processId={PID} />, { design: fakeDesign([]) });
    await screen.findByRole("article", { name: "Release 9f8e7d6" });
    fireEvent.click(within(card("9f8e7d6")).getByRole("button", { name: "Edit trigger" }));
    let dialog = screen.getByRole("dialog", { name: "Trigger of release 9f8e7d6" });
    expect((within(dialog).getByLabelText("Cron") as HTMLInputElement).value).toBe("0 7 * * 1-5");
    fireEvent.click(within(dialog).getByLabelText("Webhook"));
    fireEvent.change(within(dialog).getByLabelText("Secret env var"), { target: { value: "HOOK_SECRET" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await flush();
    expect(api.releases.update).toHaveBeenCalledWith(SCHEDULED!.id, { trigger: { kind: "webhook", secret_env: "HOOK_SECRET" } });

    fireEvent.click(within(card("9f8e7d6")).getByRole("button", { name: "Edit env" }));
    dialog = screen.getByRole("dialog", { name: "Environment of release 9f8e7d6" });
    await flush();
    expect((within(dialog).getByLabelText("RECORDS_DIR value") as HTMLInputElement).value).toBe("/data/records");
    fireEvent.change(within(dialog).getByLabelText("RECORDS_DIR value"), { target: { value: "/srv/records" } });
    fireEvent.click(within(dialog).getByRole("button", { name: "Save" }));
    await flush();
    expect(api.releases.update).toHaveBeenLastCalledWith(SCHEDULED!.id, {
      env: { ...SCHEDULED!.env, RECORDS_DIR: { value: "/srv/records" } },
    });
  });
});
