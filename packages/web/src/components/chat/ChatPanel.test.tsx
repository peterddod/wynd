// ChatPanel + ChatList: the chat aside, list order and badges, new chat, remembered chat (`$DRAFTS/07 §9.1`, §9.4).
import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { ChatSummary } from "../../api/types";
import { createFakeApi } from "../../test/fakeApi";
import { fixture } from "../../test/fixtures";
import { renderApp } from "../../test/render";
import { ChatList, jobBadge, sortChats } from "./ChatList";
import { ChatPanel } from "./ChatPanel";
import { fakeDesign, queries, queryModule, resetDoubles, toasts, urls } from "./testDoubles";

vi.mock("../../api/sse", () => import("./testDoubles").then((m) => m.sseModule));
vi.mock("../../state/query", () => import("./testDoubles").then((m) => m.queryModule));
vi.mock("../../state/toasts", () => import("./testDoubles").then((m) => m.toastsModule));
vi.mock("../../state/url", () => import("./testDoubles").then((m) => m.urlModule));

const [COMPILE_CHAT, EDIT_CHAT] = fixture("chatsList") as [ChatSummary, ChatSummary];
const PID = "process_supplier_invoice";

function panel(opts: { chat?: string | null; open?: boolean; api?: ReturnType<typeof createFakeApi>; llmReady?: boolean } = {}) {
  const meta = fixture("meta");
  if (opts.llmReady === false) {
    meta.llm = { provider: "claude-code", ready: false, message: "Claude Code is not logged in. Run `claude`." };
  }
  const onClose = vi.fn();
  const view = renderApp(<ChatPanel open={opts.open ?? true} onClose={onClose} />, {
    api: opts.api ?? createFakeApi(), design: fakeDesign(PID), meta, url: { process: PID, chat: opts.chat ?? null },
  });
  return { ...view, onClose };
}

function listItems(): string[] {
  return within(screen.getByRole("list", { name: "Chats" })).getAllByRole("button").map((b) => b.textContent ?? "");
}

beforeEach(() => {
  resetDoubles();
});

describe("ChatList helpers", () => {
  it("sorts by updated_at, newest first", () => {
    const older = { ...COMPILE_CHAT, id: "a", updated_at: "2026-09-27T08:00:00Z" };
    expect(sortChats([older, EDIT_CHAT, COMPILE_CHAT]).map((c) => c.id)).toEqual([COMPILE_CHAT.id, EDIT_CHAT.id, "a"]);
  });

  it("badges a job-bound chat with its kind and status, or the pending answers", () => {
    const job = COMPILE_CHAT.job;
    if (job === null) throw new Error("fixture: the compile chat is job-bound");
    expect(jobBadge(job)).toBe("Compile · needs answers (2)");
    expect(jobBadge({ ...job, status: "running", pending_questions: 0 })).toBe("Compile · running");
    expect(jobBadge({ ...job, kind: "test_live", status: "succeeded", pending_questions: 0 })).toBe("Live test · succeeded");
  });
});

describe("ChatPanel", () => {
  it("is the Chat aside; with no chat open the list shows, newest first, with job badges", async () => {
    const { onClose } = panel();
    expect(screen.getByRole("complementary", { name: "Chat" })).toBeTruthy();
    await waitFor(() => expect(listItems()).toHaveLength(2));
    expect(listItems()).toEqual([
      "Compile process_supplier_invoiceCompile · needs answers (2)",
      "Add a step that emails finance when escalating",
    ]);
    expect(screen.getByText("Pick a chat or start a new one.")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Close chat" }));
    expect(onClose).toHaveBeenCalledOnce();
  });

  it("picking a chat opens it, remembers it and folds the list", async () => {
    const { api } = panel();
    await waitFor(() => expect(listItems()).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: EDIT_CHAT.title }));
    expect(urls.current().chat).toBe(EDIT_CHAT.id);
    expect(localStorage.getItem("wynd.chat")).toBe(EDIT_CHAT.id);
    await screen.findByText("Add a step that emails the finance team when an invoice is escalated");
    expect(api.chats.get).toHaveBeenCalledWith(EDIT_CHAT.id);
    expect(screen.queryByRole("list", { name: "Chats" })).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Chats ▸" }));
    expect(screen.getByRole("list", { name: "Chats" })).toBeTruthy();
  });

  it("+ New creates an empty chat and opens it", async () => {
    const created: ChatSummary[] = [];
    const api = createFakeApi({
      chats: {
        list: async () => ({ chats: [...created, ...fixture("chatsList")] }),
        create: async () => {
          const chat = { ...EDIT_CHAT, id: "chat_20260927T120000000_0d0e0f", title: "New chat", usage_totals: null };
          created.push(chat);
          return chat;
        },
      },
    });
    panel({ api });
    await waitFor(() => expect(listItems()).toHaveLength(2));
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    await waitFor(() => expect(urls.current().chat).toBe("chat_20260927T120000000_0d0e0f"));
    expect(api.chats.create).toHaveBeenCalledWith({});
    expect(queries.invalidated).toContain("chats");
    expect(screen.queryByText("This chat no longer exists.")).toBeNull();
  });

  it("a failed create shows a toast", async () => {
    const api = createFakeApi({
      chats: {
        create: async () => {
          throw new Error("controller unreachable");
        },
      },
    });
    panel({ api });
    fireEvent.click(screen.getByRole("button", { name: "+ New" }));
    await waitFor(() => expect(toasts).toHaveLength(1));
    expect(toasts[0]?.text).toBe("Could not start a chat: controller unreachable");
    expect(urls.current().chat).toBeNull();
  });

  it("reopens this browser's last chat when the URL names none", async () => {
    localStorage.setItem("wynd.chat", EDIT_CHAT.id);
    panel();
    await waitFor(() => expect(urls.current().chat).toBe(EDIT_CHAT.id));
    expect(urls.calls).toContainEqual({ patch: { chat: EDIT_CHAT.id }, mode: "replace" });
  });

  it("ignores a remembered chat that no longer exists", async () => {
    localStorage.setItem("wynd.chat", "chat_gone");
    panel();
    await waitFor(() => expect(listItems()).toHaveLength(2));
    expect(urls.current().chat).toBeNull();
  });

  it("says so when the URL names a chat that does not exist", async () => {
    panel({ chat: "chat_gone" });
    await screen.findByText("This chat no longer exists.");
  });

  it("opens a chat created elsewhere while the list is still refetching", async () => {
    const jobChat = fixture("jobCompileStarted").chat;
    let release: () => void = () => undefined;
    const api = createFakeApi({
      chats: {
        list: async () => ({ chats: fixture("chatsList") }),
        get: async (id) => ({ chat: { ...jobChat, id }, items: [], cursor: 0 }),
      },
    });
    panel({ api });
    await waitFor(() => expect(listItems()).toHaveLength(2));
    const refetched = new Promise<void>((resolve) => { release = resolve; });
    vi.mocked(api.chats.list).mockImplementationOnce(async () => {
      await refetched;
      return { chats: [{ ...jobChat, id: "chat_new" }, ...fixture("chatsList")] };
    });
    act(() => {
      queryModule.invalidate("chats");
      urls.set({ chat: "chat_new" });
    });
    expect(screen.queryByText("This chat no longer exists.")).toBeNull();
    await waitFor(() => expect(api.chats.get).toHaveBeenCalledWith("chat_new"));
    await act(async () => release());
    expect(screen.queryByText("This chat no longer exists.")).toBeNull();
  });

  it("warns when the assistant's provider is not ready", () => {
    panel({ llmReady: false });
    expect(screen.getByText(/The assistant is not ready \(claude-code\): Claude Code is not logged in/)).toBeTruthy();
  });

  it("collapsed, it stays mounted but hidden", async () => {
    const { container } = panel({ open: false, chat: EDIT_CHAT.id });
    expect(screen.queryByRole("complementary")).toBeNull();
    const aside = container.querySelector<HTMLElement>('aside[aria-label="Chat"]');
    expect(aside?.hidden).toBe(true);
    await waitFor(() => expect(aside?.textContent).toContain("Which processes use the fix_fields step?"));
  });
});

describe("ChatList", () => {
  it("marks the selected chat", async () => {
    renderApp(<ChatList selected={COMPILE_CHAT.id} onSelect={() => undefined} />, { design: fakeDesign(PID) });
    fireEvent.click(screen.getByRole("button", { name: "Chats ▸" }));
    await waitFor(() => expect(listItems()).toHaveLength(2));
    const current = screen.getAllByRole("button").find((b) => b.getAttribute("aria-current") === "true");
    expect(current?.textContent).toContain(COMPILE_CHAT.title);
  });
});
