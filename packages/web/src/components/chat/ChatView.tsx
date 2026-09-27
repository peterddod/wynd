// One chat: header, items, composer (`$DRAFTS/07 §9.1`-§9.3). The title comes from the `chats` list when it has the
// chat (so renames and the controller's retitling show), else from the snapshot. Consecutive tool items of one turn
// render as one group; only the last 300 items render until "show earlier".
import { useEffect, useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import type { ChatItem, ChatSummary, ToolItem } from "../../api/types";
import { forgetStoredChat, useChat, useSendMessage } from "../../state/chat";
import { getQueryData, invalidate, setQueryData, useQuery } from "../../state/query";
import { toast } from "../../state/toasts";
import { useUrlState } from "../../state/url";
import { Spinner } from "../common/Spinner";
import { CommitItem } from "./CommitItem";
import { Composer } from "./Composer";
import { JobCard } from "./JobCard";
import { MessageItem } from "./MessageItem";
import { NoticeItem } from "./NoticeItem";
import { ToolActivityGroup } from "./ToolActivity";

export interface ChatViewProps {
  chatId: string;
}

export const MAX_RENDERED_ITEMS = 300;

type Block = { kind: "item"; item: Exclude<ChatItem, ToolItem> } | { kind: "tools"; items: ToolItem[] };

function groupItems(items: ChatItem[]): Block[] {
  const blocks: Block[] = [];
  for (const item of items) {
    const last = blocks.at(-1);
    if (item.type !== "tool") blocks.push({ kind: "item", item });
    else if (last?.kind === "tools" && last.items[0]?.turn_id === item.turn_id) last.items.push(item);
    else blocks.push({ kind: "tools", items: [item] });
  }
  return blocks;
}

/** The user text a failed assistant message answered: the nearest user item before it. */
function promptOf(items: ChatItem[], index: number): string | null {
  for (let i = index - 1; i >= 0; i--) {
    const item = items[i];
    if (item?.type === "user") return item.text;
  }
  return null;
}

function errorText(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function Title({ chat }: { chat: ChatSummary }): ReactElement {
  const api = useApi();
  const [editing, setEditing] = useState<string | null>(null);

  const save = async (): Promise<void> => {
    const title = (editing ?? "").trim();
    setEditing(null);
    if (title === "" || title === chat.title) return;
    try {
      const renamed = await api.chats.update(chat.id, { title });
      const list = getQueryData<{ chats: ChatSummary[] }>("chats");
      if (list !== undefined) setQueryData("chats", { chats: list.chats.map((c) => (c.id === renamed.id ? renamed : c)) });
      invalidate("chats");
    } catch (err) {
      toast(`Could not rename the chat: ${errorText(err)}`, { level: "error" });
    }
  };

  if (editing === null) {
    return (
      <button type="button" className="wc-title" title="Rename" onClick={() => setEditing(chat.title)}>
        {chat.title}
      </button>
    );
  }
  return (
    <input
      className="wc-title-input"
      aria-label="Chat title"
      value={editing}
      autoFocus
      onChange={(e) => setEditing(e.target.value)}
      onBlur={() => void save()}
      onKeyDown={(e) => {
        if (e.key === "Enter") e.currentTarget.blur();
        if (e.key === "Escape") setEditing(null);
      }}
    />
  );
}

export function ChatView({ chatId }: ChatViewProps): ReactElement {
  const api = useApi();
  const [, setUrl] = useUrlState();
  const state = useChat(chatId);
  const send = useSendMessage(chatId);
  const chats = useQuery("chats", () => api.chats.list());
  const [showAll, setShowAll] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [wasOpen, setWasOpen] = useState(false);
  const connection = state?.connection;

  useEffect(() => {
    if (connection === "open") setWasOpen(true);
  }, [connection]);

  if (state === null) {
    return <section className="wc-view"><Spinner label="Loading chat" /></section>;
  }

  const remove = async (): Promise<void> => {
    try {
      await api.chats.remove(chatId);
    } catch (err) {
      toast(`Could not delete the chat: ${errorText(err)}`, { level: "error" });
      return;
    }
    forgetStoredChat(chatId);
    invalidate("chats");
    setUrl({ chat: null });
  };

  const summary = chats.data?.chats.find((c) => c.id === chatId) ?? state.chat;
  const first = showAll ? 0 : Math.max(0, state.items.length - MAX_RENDERED_ITEMS);
  const visible = state.items.slice(first);

  const renderItem = (item: Exclude<ChatItem, ToolItem>): ReactElement => {
    switch (item.type) {
      case "user":
        return <MessageItem item={item} />;
      case "assistant": {
        const prompt = item.status === "error" ? promptOf(state.items, state.items.indexOf(item)) : null;
        const retry = prompt === null || state.runningTurn !== null ? undefined : () => void send(prompt);
        return <MessageItem item={item} onRetry={retry} />;
      }
      case "commit":
        return <CommitItem item={item} />;
      case "job":
        return <JobCard jobId={item.job_id} />;
      case "notice":
        return <NoticeItem item={item} />;
    }
  };

  return (
    <section className="wc-view" aria-label={`Chat: ${summary.title}`}>
      <header className="wc-view-head">
        <Title chat={summary} />
        {wasOpen && state.connection === "connecting" && <span className="wc-pill" role="status">reconnecting…</span>}
        {confirmDelete
          ? (
            <span className="wc-confirm">
              Delete this chat?{" "}
              <button type="button" className="wc-danger" onClick={() => void remove()}>Delete</button>{" "}
              <button type="button" onClick={() => setConfirmDelete(false)}>Keep</button>
            </span>
          )
          : <button type="button" onClick={() => setConfirmDelete(true)}>Delete</button>}
      </header>
      {first > 0 && (
        <button type="button" className="wc-earlier" onClick={() => setShowAll(true)}>
          Show {first} earlier
        </button>
      )}
      <ol className="wc-items">
        {groupItems(visible).map((block) => (
          block.kind === "tools"
            ? <li key={block.items[0]?.id}><ToolActivityGroup items={block.items} /></li>
            : <li key={block.item.id}>{renderItem(block.item)}</li>
        ))}
      </ol>
      <Composer chatId={chatId} running={state.runningTurn !== null} />
    </section>
  );
}
