// Chat view state: snapshot + SSE (`$DRAFTS/07 §9.2`) and sending (§9.4).
// Stub from WEB-SCAFFOLD; WEB-CHAT implements it.
import type { Api } from "../api/client";
import type { ChatItem, ChatSummary } from "../api/types";
import type { DesignSession } from "./design";

export interface ChatViewState {
  chat: ChatSummary;
  items: ChatItem[];
  cursor: number;
  runningTurn: string | null;
  connection: "open" | "connecting" | "closed";
}

/** Snapshot + SSE; one stream per mounted ChatView. Null until the snapshot has loaded. */
export function useChat(chatId: string): ChatViewState | null {
  throw new Error("not implemented");
}

export interface SendDeps {
  api: Api;
  design: DesignSession;
  actingOn: string | null;           // the open process: the pointer sent with every message
}

/** Commits the open process ("before_chat"), sends, and locks the design for the turn when acting on it. */
export async function sendChatMessage(deps: SendDeps, chatId: string, text: string): Promise<void> {
  throw new Error("not implemented");
}
