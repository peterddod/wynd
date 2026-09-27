// Chat view state: snapshot + SSE (`$DRAFTS/07 §9.2`) and sending (§9.4). Turn events are forwarded to the design
// session (§8.6): a running turn acting on the open process locks the editor, its end unlocks it and reloads the
// design when the turn edited (and the controller committed) that process.
import { useContext, useEffect, useReducer } from "react";
import { ApiError, type Api } from "../api/client";
import { DesignContext, useApi, useDesign } from "../api/context";
import { openEventStream } from "../api/sse";
import type {
  ChatDeltaEvent, ChatItem, ChatItemEvent, ChatSnapshot, ChatSummary, ChatTurnEvent,
} from "../api/types";
import type { DesignSession } from "./design";
import { invalidate } from "./query";
import { toast } from "./toasts";
import { useUrlState } from "./url";

export interface ChatViewState {
  chat: ChatSummary;
  items: ChatItem[];
  cursor: number;
  runningTurn: string | null;
  connection: "open" | "connecting" | "closed";
}

export const SNAPSHOT_RETRY_MS = 2000;
export const STORED_CHAT_KEY = "wynd.chat";   // the last chat of this browser

export function readStoredChat(): string | null {
  try {
    return window.localStorage.getItem(STORED_CHAT_KEY);
  } catch {
    return null;
  }
}

export function storeChat(chatId: string): void {
  try {
    window.localStorage.setItem(STORED_CHAT_KEY, chatId);
  } catch {
    // storage unavailable: the chat is just not remembered
  }
}

export function forgetStoredChat(chatId: string): void {
  try {
    if (window.localStorage.getItem(STORED_CHAT_KEY) === chatId) window.localStorage.removeItem(STORED_CHAT_KEY);
  } catch {
    // storage unavailable
  }
}

type Action =
  | { type: "clear" }
  | { type: "snapshot"; snapshot: ChatSnapshot }
  | { type: "item"; item: ChatItem; id: string | null }
  | { type: "delta"; delta: ChatDeltaEvent; id: string | null }
  | { type: "turn"; turn: ChatTurnEvent; id: string | null }
  | { type: "connection"; connection: ChatViewState["connection"] };

function upsert(items: ChatItem[], item: ChatItem): ChatItem[] {
  return [...items.filter((i) => i.id !== item.id), item].sort((a, b) => a.seq - b.seq);
}

function appendDelta(items: ChatItem[], delta: ChatDeltaEvent): ChatItem[] {
  const index = items.findIndex((i) => i.id === delta.item_id);
  const item = items[index];
  if (item === undefined || item.type !== "assistant") return items;   // the next `item` snapshot fixes it
  const next = [...items];
  next[index] = { ...item, text: item.text + delta.text };
  return next;
}

function advance(cursor: number, id: string | null): number {
  const n = id === null ? NaN : Number(id);
  return Number.isFinite(n) && n > cursor ? n : cursor;
}

function reduce(state: ChatViewState | null, action: Action): ChatViewState | null {
  if (action.type === "clear") return null;
  if (action.type === "snapshot") {
    const { chat, items, cursor } = action.snapshot;
    return {
      chat, items: [...items].sort((a, b) => a.seq - b.seq), cursor, runningTurn: chat.running_turn,
      connection: "connecting",
    };
  }
  if (state === null) return null;
  switch (action.type) {
    case "connection":
      return state.connection === action.connection ? state : { ...state, connection: action.connection };
    case "item":
      return { ...state, items: upsert(state.items, action.item), cursor: advance(state.cursor, action.id) };
    case "delta":
      return { ...state, items: appendDelta(state.items, action.delta), cursor: advance(state.cursor, action.id) };
    case "turn":
      return {
        ...state,
        runningTurn: action.turn.status === "running" ? action.turn.turn_id : null,
        cursor: advance(state.cursor, action.id),
      };
  }
}

function forwardTurn(design: DesignSession | null, chatId: string, turn: ChatTurnEvent): void {
  invalidate("chats");
  if (turn.status !== "running" && turn.edited.length > 0) invalidate("processes");
  if (design === null) return;
  const open = design.store.get()?.processId ?? null;
  if (turn.status === "running") {
    if (open !== null && turn.acting_on === open) design.lock({ chat_id: chatId, turn_id: turn.turn_id });
    return;
  }
  design.unlock(turn.turn_id);
  if (open !== null && turn.edited.includes(open)) void design.reload();
}

/** Snapshot + SSE; one stream per mounted ChatView. Null until the snapshot has loaded. */
export function useChat(chatId: string): ChatViewState | null {
  const api = useApi();
  const design = useContext(DesignContext);
  const [state, dispatch] = useReducer(reduce, null);

  useEffect(() => {
    let stopped = false;
    let close = (): void => undefined;
    let retry: ReturnType<typeof setTimeout> | undefined;

    const load = async (): Promise<void> => {
      let snapshot: ChatSnapshot;
      try {
        snapshot = await api.chats.get(chatId);
      } catch (err) {
        if (stopped || (err instanceof ApiError && err.status === 404)) return;
        retry = setTimeout(() => void load(), SNAPSHOT_RETRY_MS);
        return;
      }
      if (stopped) return;
      dispatch({ type: "snapshot", snapshot });
      close = openEventStream(api.chats.eventsUrl(chatId, snapshot.cursor), {
        events: {
          item: (data, id) => dispatch({ type: "item", item: (data as ChatItemEvent).item, id }),
          delta: (data, id) => dispatch({ type: "delta", delta: data as ChatDeltaEvent, id }),
          turn: (data, id) => {
            const turn = data as ChatTurnEvent;
            dispatch({ type: "turn", turn, id });
            forwardTurn(design, chatId, turn);
          },
          reset: () => {
            close();
            dispatch({ type: "connection", connection: "connecting" });
            void load();
          },
        },
        onOpen: () => dispatch({ type: "connection", connection: "open" }),
        onError: () => dispatch({ type: "connection", connection: "connecting" }),
      });
    };

    dispatch({ type: "clear" });
    void load();
    return () => {
      stopped = true;
      clearTimeout(retry);
      close();
    };
  }, [api, chatId, design]);

  return state;
}

export interface SendDeps {
  api: Api;
  design: DesignSession;
  actingOn: string | null;           // the open process: the pointer sent with every message
}

/** Commits the open process ("before_chat"), sends, and locks the design for the turn when acting on it. */
export async function sendChatMessage(deps: SendDeps, chatId: string, text: string): Promise<void> {
  const { api, design, actingOn } = deps;
  if (actingOn !== null) await design.commit("before_chat");
  const sent = await api.chats.send(chatId, { text, acting_on: actingOn, client_id: crypto.randomUUID() });
  if (actingOn !== null && design.store.get()?.processId === actingOn) {
    design.lock({ chat_id: chatId, turn_id: sent.turn_id });
  }
}

function sendFailure(err: unknown): string {
  if (err instanceof ApiError && err.code === "turn_in_progress") {
    return "The assistant is still answering in this chat: wait for it or press Stop.";
  }
  return `Message not sent: ${err instanceof Error ? err.message : String(err)}`;
}

/** Sends from the chat UI with the open process (URL `process`) as `acting_on`; a failure becomes a toast.
 *  Resolves true when the message was sent. */
export function useSendMessage(chatId: string): (text: string) => Promise<boolean> {
  const api = useApi();
  const design = useDesign();
  const [url] = useUrlState();
  return async (text) => {
    try {
      await sendChatMessage({ api, design, actingOn: url.process }, chatId, text);
      return true;
    } catch (err) {
      toast(sendFailure(err), { level: "error" });
      return false;
    }
  };
}
