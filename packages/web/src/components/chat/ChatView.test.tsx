// ChatView: snapshot, streaming, tool groups, composer and header (`$DRAFTS/07 §9.1`-§9.4, §17.3).
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import type { ChatItem, ChatSnapshot, NoticeItem } from "../../api/types";
import { createFakeApi, type FakeApi } from "../../test/fakeApi";
import { FakeEventSource } from "../../test/fakeEventSource";
import { chatEventFrames, fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { ChatView, MAX_RENDERED_ITEMS } from "./ChatView";
import { fakeDesign, queries, resetDoubles, toasts, urls, type FakeDesign } from "./testDoubles";

vi.mock("../../api/sse", () => import("./testDoubles").then((m) => m.sseModule));
vi.mock("../../state/query", () => import("./testDoubles").then((m) => m.queryModule));
vi.mock("../../state/toasts", () => import("./testDoubles").then((m) => m.toastsModule));
vi.mock("../../state/url", () => import("./testDoubles").then((m) => m.urlModule));

const CHAT = fixture("chatSnapshot").chat.id;
const PID = "process_supplier_invoice";

async function renderChat(opts: { api?: FakeApi; design?: FakeDesign; process?: string | null } = {}) {
  const api = opts.api ?? createFakeApi();
  const design = opts.design ?? fakeDesign(PID);
  const process = opts.process === undefined ? PID : opts.process;
  const view = renderApp(<ChatView chatId={CHAT} />, { api, design, url: { process, chat: CHAT } });
  await screen.findByText("Add a step that emails the finance team when an invoice is escalated");
  return { ...view, api, design };
}

function stream(): FakeEventSource {
  const last = FakeEventSource.last();
  if (last === undefined) throw new Error("no event stream was opened");
  return last;
}

function message(): HTMLTextAreaElement {
  return screen.getByLabelText("Message") as HTMLTextAreaElement;
}

beforeEach(() => {
  resetDoubles();
  vi.spyOn(crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-000000000001");
});

describe("ChatView snapshot", () => {
  it("renders every item kind", async () => {
    await renderChat();
    expect(screen.getByRole("button", { name: "Add a step that emails finance when escalating" })).toBeTruthy();
    // user items carry the pointer they were sent with
    const first = screen.getByText("Add a step that emails the finance team when an invoice is escalated").parentElement;
    expect(within(first as HTMLElement).getByText(`acting on: ${PID}`)).toBeTruthy();
    const second = screen.getByText("Which processes use the fix_fields step?").parentElement;
    expect(within(second as HTMLElement).getByText("no process")).toBeTruthy();
    // assistant Markdown
    expect(screen.getByText("notify_finance").tagName).toBe("CODE");
    // tools of turn_1 grouped; the write tool is expanded with its acting-on chip
    const group = screen.getByRole("button", { name: "Used 2 tools · 1 write ▾" });
    const tools = group.parentElement as HTMLElement;
    expect(within(tools).getByText("edit_design")).toBeTruthy();
    expect(within(tools).getByText("op=add_step, name=notify_finance, use=./steps/notify_finance")).toBeTruthy();
    expect(within(tools).getByText(`acting on: ${PID}`)).toBeTruthy();
    expect(within(tools).getByText("Added step notify_finance (./steps/notify_finance) and routed escalate.done → notify_finance")).toBeTruthy();
    expect(within(tools).queryByText("process_supplier_invoice: 6 steps, 7 edges")).toBeNull();   // read row collapsed
    // commit, failed assistant reply, notice
    expect(screen.getByText("b4d2e19").closest("p")?.textContent)
      .toBe("Committed b4d2e19 design(process_supplier_invoice): add step notify_finance");
    expect(screen.getByRole("alert").textContent).toContain("Claude Code is not logged in.");
    expect(screen.getByText("The assistant could not answer: the claude-code provider is not ready.").parentElement?.textContent)
      .toBe("Warning: The assistant could not answer: the claude-code provider is not ready.");
  });

  it("a group of read tools starts collapsed and expands on click", async () => {
    const snapshot = fixture("chatSnapshot");
    const reads = snapshot.items.filter((i) => i.type !== "tool" || !i.write);
    const api = createFakeApi({ chats: { get: async () => ({ ...snapshot, items: reads }) } });
    await renderChat({ api });
    const toggle = screen.getByRole("button", { name: "Used 1 tool ▸" });
    expect(screen.queryByText("read_design")).toBeNull();
    fireEvent.click(toggle);
    fireEvent.click(screen.getByRole("button", { name: /read_design/ }));
    expect(screen.getByText("process_supplier_invoice: 6 steps, 7 edges")).toBeTruthy();
  });

  it("a job item renders the job's card", async () => {
    const build = fixture("jobBuildSucceeded");
    const snapshot: ChatSnapshot = {
      chat: { ...fixture("chatSnapshot").chat, title: `Build ${PID}` },
      items: [{ id: "it_1", seq: 1, type: "job", created_at: build.created_at, job_id: build.id, kind: "build", process_id: PID }],
      cursor: 1,
    };
    const api = createFakeApi({ chats: { get: async () => snapshot } });
    renderApp(<ChatView chatId={CHAT} />, { api, design: fakeDesign(PID), url: { chat: CHAT } });
    const card = await screen.findByRole("region", { name: `Build job for ${PID}` });
    expect(api.jobs.get).toHaveBeenCalledWith(build.id);
    expect(within(card).getByText("succeeded")).toBeTruthy();
    expect(within(card).getByRole("button", { name: "Create release…" })).toBeTruthy();
  });

  it("renders only the last 300 items until asked for the earlier ones", async () => {
    const snapshot = fixture("chatSnapshot");
    const notices: NoticeItem[] = Array.from({ length: MAX_RENDERED_ITEMS + 5 }, (_, i) => ({
      id: `n_${i}`, seq: 100 + i, type: "notice", created_at: "2026-09-27T09:30:00Z", level: "info", text: `note ${i}`,
    }));
    const items: ChatItem[] = [...snapshot.items, ...notices];
    const api = createFakeApi({ chats: { get: async (): Promise<ChatSnapshot> => ({ ...snapshot, items }) } });
    renderApp(<ChatView chatId={CHAT} />, { api, design: fakeDesign(PID), url: { chat: CHAT } });
    await screen.findByText("note 5");                              // 8 snapshot items + notes 0-4 are hidden
    expect(screen.queryByText("note 4")).toBeNull();
    expect(screen.queryByText("Which processes use the fix_fields step?")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Show 13 earlier" }));
    expect(screen.getByText("note 4")).toBeTruthy();
    expect(screen.getByText("Which processes use the fix_fields step?")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /earlier/ })).toBeNull();
  });
});

describe("ChatView streaming", () => {
  it("streams deltas into the assistant item and locks the composer while the turn runs", async () => {
    const { api } = await renderChat();
    const frames = chatEventFrames();
    act(() => stream().play(frames.slice(0, 5)));
    const reply = screen.getByText(/I'll add a condition to the/).closest("[aria-busy]");
    expect(reply?.getAttribute("aria-busy")).toBe("true");
    expect(reply?.textContent).toBe("I'll add a condition to the escalate.done edge.");
    expect(message().disabled).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await waitFor(() => expect(api.chats.cancel).toHaveBeenCalledWith(CHAT));

    act(() => stream().play(frames.slice(5)));
    expect(reply?.getAttribute("aria-busy")).toBe("false");
    expect(screen.getByRole("button", { name: "Used 1 tool · 1 write ▾" })).toBeTruthy();
    expect(screen.getByText("e5f6071").closest("p")?.textContent)
      .toBe("Committed e5f6071 design(process_supplier_invoice): only notify finance over 10,000");
    expect(message().disabled).toBe(false);
    expect(screen.queryByRole("button", { name: "Stop" })).toBeNull();
  });

  it("reset refetches the snapshot and reopens the stream", async () => {
    const { api } = await renderChat();
    act(() => stream().emit("reset", {}, null));
    await waitFor(() => expect(FakeEventSource.instances()).toHaveLength(2));
    expect(api.chats.get).toHaveBeenCalledTimes(2);
  });

  it("shows a reconnecting pill after the stream drops", async () => {
    await renderChat();
    expect(screen.queryByText("reconnecting…")).toBeNull();          // first connect: nothing to report
    act(() => stream().open());
    act(() => stream().error());
    expect(screen.getByText("reconnecting…").getAttribute("role")).toBe("status");
    act(() => stream().open());
    expect(screen.queryByText("reconnecting…")).toBeNull();
  });
});

describe("Composer", () => {
  it("Enter commits the open process first, then sends with it as acting_on and clears the text", async () => {
    const log: string[] = [];
    const design = fakeDesign(PID, { log });
    const api = createFakeApi({
      chats: {
        send: async () => {
          log.push("send");
          return fixture("sendResult");
        },
      },
    });
    await renderChat({ api, design });
    expect(screen.getByRole("button", { name: `acting on: ${PID}` })).toBeTruthy();
    fireEvent.change(message(), { target: { value: "Only email finance for totals over 10,000" } });
    fireEvent.keyDown(message(), { key: "Enter" });
    await waitFor(() => expect(message().value).toBe(""));
    expect(log).toEqual(["commit:before_chat", "send"]);
    expect(api.chats.send).toHaveBeenCalledWith(CHAT, {
      text: "Only email finance for totals over 10,000", acting_on: PID, client_id: "00000000-0000-4000-8000-000000000001",
    });
    expect(design.lock).toHaveBeenCalledWith({ chat_id: CHAT, turn_id: "turn_3" });
  });

  it("the acting-on chip fires the process-search shortcut", async () => {
    await renderChat();
    const keys: [string, boolean][] = [];
    const onKey = (e: KeyboardEvent): void => void keys.push([e.key, e.ctrlKey]);
    window.addEventListener("keydown", onKey);
    fireEvent.click(screen.getByRole("button", { name: `acting on: ${PID}` }));
    window.removeEventListener("keydown", onKey);
    expect(keys).toEqual([["k", true]]);
  });

  it("with no open process sends acting_on null without committing", async () => {
    const design = fakeDesign(null);
    const { api } = await renderChat({ design, process: null });
    expect(screen.getByRole("button", { name: "acting on: nothing (read-only)" })).toBeTruthy();
    fireEvent.change(message(), { target: { value: "Which processes use fix_fields?" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(api.chats.send).toHaveBeenCalledOnce());
    expect(vi.mocked(api.chats.send).mock.calls[0]?.[1]).toMatchObject({ acting_on: null });
    expect(design.commit).not.toHaveBeenCalled();
  });

  it("Shift+Enter is a newline, and empty text is not sent", async () => {
    const { api } = await renderChat();
    fireEvent.keyDown(message(), { key: "Enter" });
    fireEvent.change(message(), { target: { value: "first line" } });
    fireEvent.keyDown(message(), { key: "Enter", shiftKey: true });
    expect(api.chats.send).not.toHaveBeenCalled();
    expect((screen.getByRole("button", { name: "Send" }) as HTMLButtonElement).disabled).toBe(false);
  });

  it("a refused send keeps the text and shows a toast", async () => {
    const api = createFakeApi();
    vi.mocked(api.chats.send).mockRejectedValueOnce(new ApiError(409, "turn_in_progress", "chat is running a turn"));
    await renderChat({ api });
    fireEvent.change(message(), { target: { value: "again" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(toasts[0]?.level).toBe("error");
    expect(toasts[0]?.text).toContain("still answering");
    expect(message().value).toBe("again");
  });

  it("a failed commit blocks the send", async () => {
    const design = fakeDesign(PID);
    design.commit.mockRejectedValueOnce(new ApiError(409, "revision_conflict", "process.yaml changed outside the editor"));
    const { api } = await renderChat({ design });
    fireEvent.change(message(), { target: { value: "hello" } });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(toasts[0]?.text).toBe("Message not sent: process.yaml changed outside the editor");
    expect(api.chats.send).not.toHaveBeenCalled();
    expect(message().value).toBe("hello");
  });

  it("Retry on a failed reply resends the user message it answered", async () => {
    const { api } = await renderChat();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(api.chats.send).toHaveBeenCalledOnce());
    expect(vi.mocked(api.chats.send).mock.calls[0]?.[1]).toMatchObject({
      text: "Which processes use the fix_fields step?", acting_on: PID,
    });
  });
});

describe("ChatView header", () => {
  it("renames the chat on Enter", async () => {
    let title = fixture("chatSnapshot").chat.title;
    const api = createFakeApi({
      chats: {
        list: async () => ({ chats: fixture("chatsList").map((c) => (c.id === CHAT ? { ...c, title } : c)) }),
        update: async (id, body) => {
          title = body.title;
          return { ...fixture("chatSnapshot").chat, id, title };
        },
      },
    });
    await renderChat({ api });
    fireEvent.click(screen.getByRole("button", { name: "Add a step that emails finance when escalating" }));
    const input = screen.getByLabelText("Chat title");
    fireEvent.change(input, { target: { value: "Finance notifications" } });
    fireEvent.keyDown(input, { key: "Enter" });
    await waitFor(() => expect(api.chats.update).toHaveBeenCalledWith(CHAT, { title: "Finance notifications" }));
    await screen.findByRole("button", { name: "Finance notifications" });
    expect(queries.invalidated).toContain("chats");
  });

  it("Escape cancels a rename", async () => {
    const { api } = await renderChat();
    fireEvent.click(screen.getByRole("button", { name: "Add a step that emails finance when escalating" }));
    fireEvent.keyDown(screen.getByLabelText("Chat title"), { key: "Escape" });
    expect(screen.getByRole("button", { name: "Add a step that emails finance when escalating" })).toBeTruthy();
    expect(api.chats.update).not.toHaveBeenCalled();
  });

  it("deletes after confirmation and closes the chat", async () => {
    localStorage.setItem("wynd.chat", CHAT);
    const { api } = await renderChat();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Keep" }));
    expect(api.chats.remove).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    await waitFor(() => expect(urls.current().chat).toBeNull());
    expect(api.chats.remove).toHaveBeenCalledWith(CHAT);
    expect(localStorage.getItem("wynd.chat")).toBeNull();
    expect(queries.invalidated).toContain("chats");
  });
});
