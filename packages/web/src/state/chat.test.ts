// useChat (snapshot + SSE, turn forwarding to the design session) and sendChatMessage (`$DRAFTS/07 §9.2`, §9.4, §8.6).
import { act, renderHook, waitFor } from "@testing-library/react";
import { createElement, type ReactElement, type ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { ApiContext, DesignContext } from "../api/context";
import type { AssistantItem, ChatSnapshot } from "../api/types";
import { fakeDesign, queries, resetDoubles, type FakeDesign } from "../components/chat/testDoubles";
import { createFakeApi, type FakeApi } from "../test/fakeApi";
import { FakeEventSource } from "../test/fakeEventSource";
import { chatEventFrames, fixture } from "../test/fixtures";
import { SNAPSHOT_RETRY_MS, sendChatMessage, useChat } from "./chat";

vi.mock("../api/sse", () => import("../components/chat/testDoubles").then((m) => m.sseModule));
vi.mock("./query", () => import("../components/chat/testDoubles").then((m) => m.queryModule));
vi.mock("./toasts", () => import("../components/chat/testDoubles").then((m) => m.toastsModule));
vi.mock("./url", () => import("../components/chat/testDoubles").then((m) => m.urlModule));

const CHAT = fixture("chatSnapshot").chat.id;
const PID = "process_supplier_invoice";

function wrapperFor(api: FakeApi, design: FakeDesign | null) {
  return ({ children }: { children: ReactNode }): ReactElement =>
    createElement(ApiContext.Provider, { value: api }, createElement(DesignContext.Provider, { value: design }, children));
}

async function mountChat(api: FakeApi, design: FakeDesign | null) {
  const hook = renderHook(() => useChat(CHAT), { wrapper: wrapperFor(api, design) });
  await waitFor(() => expect(hook.result.current).not.toBeNull());
  return hook;
}

function source(): FakeEventSource {
  const last = FakeEventSource.last();
  if (last === undefined) throw new Error("no event stream was opened");
  return last;
}

beforeEach(() => {
  resetDoubles();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useChat", () => {
  it("loads the snapshot, then streams from its cursor", async () => {
    const api = createFakeApi();
    const { result } = await mountChat(api, fakeDesign(PID));
    expect(api.chats.get).toHaveBeenCalledWith(CHAT);
    expect(source().url).toBe(`/api/chats/${encodeURIComponent(CHAT)}/events?since=14`);
    const state = result.current;
    expect(state?.items.map((i) => i.id)).toEqual(["it_1", "it_2", "it_3", "it_4", "it_5", "it_6", "it_7", "it_8"]);
    expect(state?.cursor).toBe(14);
    expect(state?.runningTurn).toBeNull();
    expect(state?.connection).toBe("connecting");
  });

  it("applies items, deltas and turns; a turn acting on the open process locks, then unlocks and reloads", async () => {
    const api = createFakeApi();
    const design = fakeDesign(PID);
    const { result } = await mountChat(api, design);
    const frames = chatEventFrames();

    act(() => source().play(frames.slice(0, 4)));           // user item, turn running, assistant item, first delta
    expect(result.current?.connection).toBe("open");
    expect(result.current?.runningTurn).toBe("turn_3");
    expect(design.lock).toHaveBeenCalledWith({ chat_id: CHAT, turn_id: "turn_3" });
    const streaming = result.current?.items.find((i) => i.id === "it_10") as AssistantItem;
    expect(streaming.status).toBe("streaming");
    expect(streaming.text).toBe("I'll add a condition to the ");
    expect(design.unlock).not.toHaveBeenCalled();

    act(() => source().play(frames.slice(4)));
    const state = result.current;
    expect(state?.items.map((i) => i.id)).toEqual([
      "it_1", "it_2", "it_3", "it_4", "it_5", "it_6", "it_7", "it_8", "it_9", "it_10", "it_11", "it_12",
    ]);
    expect(state?.items.find((i) => i.id === "it_11")).toMatchObject({ type: "tool", status: "ok" });
    expect(state?.items.find((i) => i.id === "it_10")).toMatchObject({
      text: "I'll add a condition to the `escalate.done` edge.", status: "done",
    });
    expect(state?.cursor).toBe(24);
    expect(state?.runningTurn).toBeNull();
    expect(design.unlock).toHaveBeenCalledWith("turn_3");
    expect(design.reload).toHaveBeenCalledOnce();
    expect(queries.invalidated).toContain("processes");
    expect(queries.invalidated.filter((p) => p === "chats")).toHaveLength(2);   // one per turn event
  });

  it("a turn acting on another process neither locks nor reloads the open design", async () => {
    const design = fakeDesign("finance/monthly_close");
    const { result } = await mountChat(createFakeApi(), design);
    act(() => source().play(chatEventFrames()));
    expect(result.current?.runningTurn).toBeNull();
    expect(design.lock).not.toHaveBeenCalled();
    expect(design.reload).not.toHaveBeenCalled();
    expect(queries.invalidated).toContain("processes");                        // the list's status changed
  });

  it("works without a design session", async () => {
    const { result } = await mountChat(createFakeApi(), null);
    act(() => source().play(chatEventFrames()));
    expect(result.current?.items).toHaveLength(12);
  });

  it("ignores a delta for an unknown item or a non-assistant item", async () => {
    const { result } = await mountChat(createFakeApi(), fakeDesign(PID));
    const before = result.current?.items;
    act(() => {
      source().emit("delta", { item_id: "it_99", text: "lost" }, 15);
      source().emit("delta", { item_id: "it_1", text: "not a reply" }, 16);
    });
    expect(result.current?.items).toEqual(before);
    expect(result.current?.cursor).toBe(16);
  });

  it("an item event replaces the item with the same id", async () => {
    const { result } = await mountChat(createFakeApi(), fakeDesign(PID));
    const failed = fixture("chatSnapshot").items[6] as AssistantItem;
    act(() => source().emit("item", { item: { ...failed, text: "Retried.", status: "done", error: null } }, 15));
    const items = result.current?.items ?? [];
    expect(items.filter((i) => i.id === failed.id)).toEqual([{ ...failed, text: "Retried.", status: "done", error: null }]);
    expect(items).toHaveLength(8);
  });

  it("reset closes the stream, refetches the snapshot and reopens from its cursor", async () => {
    const later: ChatSnapshot = { ...fixture("chatSnapshot"), cursor: 40 };
    const api = createFakeApi();
    vi.mocked(api.chats.get).mockResolvedValueOnce(fixture("chatSnapshot")).mockResolvedValueOnce(later);
    await mountChat(api, fakeDesign(PID));
    const first = source();
    act(() => first.emit("reset", {}, null));
    await waitFor(() => expect(FakeEventSource.instances()).toHaveLength(2));
    expect(first.readyState).toBe(FakeEventSource.CLOSED);
    expect(api.chats.get).toHaveBeenCalledTimes(2);
    expect(source().url).toBe(`/api/chats/${encodeURIComponent(CHAT)}/events?since=40`);
  });

  it("an error shows the stream as connecting until the next event", async () => {
    const { result } = await mountChat(createFakeApi(), fakeDesign(PID));
    act(() => source().open());
    expect(result.current?.connection).toBe("open");
    act(() => source().error());
    expect(result.current?.connection).toBe("connecting");
    act(() => source().emit("item", { item: fixture("sendResult").item }, 15));
    expect(result.current?.connection).toBe("open");
  });

  it("stays empty for a chat that does not exist, and retries other snapshot failures", async () => {
    const missing = createFakeApi();
    vi.mocked(missing.chats.get).mockRejectedValue(new ApiError(404, "not_found", "chat not found"));
    const gone = renderHook(() => useChat(CHAT), { wrapper: wrapperFor(missing, null) });
    await waitFor(() => expect(missing.chats.get).toHaveBeenCalledOnce());
    expect(gone.result.current).toBeNull();
    expect(FakeEventSource.instances()).toEqual([]);
    gone.unmount();

    vi.useFakeTimers();
    const flaky = createFakeApi();
    vi.mocked(flaky.chats.get).mockRejectedValueOnce(new ApiError(0, "network", "offline"));
    const hook = renderHook(() => useChat(CHAT), { wrapper: wrapperFor(flaky, null) });
    await act(async () => vi.advanceTimersByTimeAsync(0));
    expect(hook.result.current).toBeNull();
    await act(async () => vi.advanceTimersByTimeAsync(SNAPSHOT_RETRY_MS));
    expect(flaky.chats.get).toHaveBeenCalledTimes(2);
    expect(hook.result.current?.cursor).toBe(14);
  });

  it("closes the stream on unmount", async () => {
    const { unmount } = await mountChat(createFakeApi(), fakeDesign(PID));
    const stream = source();
    unmount();
    expect(stream.readyState).toBe(FakeEventSource.CLOSED);
  });
});

describe("sendChatMessage", () => {
  beforeEach(() => {
    vi.spyOn(crypto, "randomUUID").mockReturnValue("00000000-0000-4000-8000-000000000001");
  });

  it("commits the open process before sending, then locks the design for the turn", async () => {
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
    await sendChatMessage({ api, design, actingOn: PID }, CHAT, "Only email finance for totals over 10,000");
    expect(log).toEqual(["commit:before_chat", "send"]);
    expect(api.chats.send).toHaveBeenCalledWith(CHAT, {
      text: "Only email finance for totals over 10,000",
      acting_on: PID,
      client_id: "00000000-0000-4000-8000-000000000001",
    });
    expect(design.lock).toHaveBeenCalledWith({ chat_id: CHAT, turn_id: "turn_3" });
  });

  it("with no open process: no commit, no lock, acting_on null", async () => {
    const design = fakeDesign(null);
    const api = createFakeApi();
    await sendChatMessage({ api, design, actingOn: null }, CHAT, "Which processes use fix_fields?");
    expect(design.commit).not.toHaveBeenCalled();
    expect(design.lock).not.toHaveBeenCalled();
    expect(vi.mocked(api.chats.send).mock.calls[0]?.[1]).toMatchObject({ acting_on: null });
  });

  it("does not send when the commit fails", async () => {
    const design = fakeDesign(PID);
    design.commit.mockRejectedValueOnce(new ApiError(409, "revision_conflict", "process.yaml changed"));
    const api = createFakeApi();
    await expect(sendChatMessage({ api, design, actingOn: PID }, CHAT, "hi")).rejects.toMatchObject({
      code: "revision_conflict",
    });
    expect(api.chats.send).not.toHaveBeenCalled();
  });

  it("locks only when the editor has the acting-on process open", async () => {
    const design = fakeDesign("finance/monthly_close");
    await sendChatMessage({ api: createFakeApi(), design, actingOn: PID }, CHAT, "hi");
    expect(design.commit).toHaveBeenCalledWith("before_chat");
    expect(design.lock).not.toHaveBeenCalled();
  });
});
