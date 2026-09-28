// Chats by updated_at desc, job badges, [+ New] (`$DRAFTS/07 §9.1`). The list folds away once a chat is picked;
// "Chats ▾" unfolds it. A new chat is created empty (the controller titles it from the first message) and opened.
import { useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import type { ChatJob, ChatSummary } from "../../api/types";
import { JOB_KIND_LABEL, JOB_STATUS_LABEL } from "../../state/jobs";
import { getQueryData, invalidate, setQueryData, useQuery } from "../../state/query";
import { toast } from "../../state/toasts";
import { ErrorBox } from "../common/ErrorBox";

export interface ChatListProps {
  selected: string | null;
  onSelect(chatId: string): void;
}

export function sortChats(chats: ChatSummary[]): ChatSummary[] {
  return [...chats].sort((a, b) => (a.updated_at < b.updated_at ? 1 : a.updated_at > b.updated_at ? -1 : 0));
}

export function jobBadge(job: ChatJob): string {
  const status = job.pending_questions > 0 ? `needs answers (${job.pending_questions})` : JOB_STATUS_LABEL[job.status];
  return `${JOB_KIND_LABEL[job.kind]} · ${status}`;
}

export function ChatList({ selected, onSelect }: ChatListProps): ReactElement {
  const api = useApi();
  const chats = useQuery("chats", () => api.chats.list());
  const [expanded, setExpanded] = useState<boolean | null>(null);   // null: open iff no chat is selected
  const [creating, setCreating] = useState(false);
  const open = expanded ?? selected === null;

  const pick = (chatId: string): void => {
    setExpanded(false);
    onSelect(chatId);
  };

  const create = async (): Promise<void> => {
    setCreating(true);
    try {
      const chat = await api.chats.create({});
      const list = getQueryData<{ chats: ChatSummary[] }>("chats")?.chats ?? [];
      setQueryData("chats", { chats: [chat, ...list.filter((c) => c.id !== chat.id)] });
      invalidate("chats");
      pick(chat.id);
    } catch (err) {
      toast(`Could not start a chat: ${err instanceof Error ? err.message : String(err)}`, { level: "error" });
    } finally {
      setCreating(false);
    }
  };

  return (
    <div className="wc-list">
      <div className="wc-list-head">
        <button type="button" className="wc-list-toggle" aria-expanded={open} onClick={() => setExpanded(!open)}>
          Chats {open ? "▾" : "▸"}
        </button>
        <button type="button" disabled={creating} onClick={() => void create()}>+ New</button>
      </div>
      {open && chats.error !== null && <ErrorBox error={chats.error} onRetry={() => void chats.reload()} />}
      {open && (
        <ul className="wc-list-items" aria-label="Chats">
          {sortChats(chats.data?.chats ?? []).map((chat) => (
            <li key={chat.id}>
              <button
                type="button"
                className="wc-list-item"
                aria-current={chat.id === selected ? "true" : undefined}
                onClick={() => pick(chat.id)}
              >
                <span className="wc-list-title">{chat.title}</span>
                {chat.job !== null && (
                  <span className="wc-pill" data-status={chat.job.status}>{jobBadge(chat.job)}</span>
                )}
                {chat.running_turn !== null && <span className="wc-pill" data-status="running">working…</span>}
              </button>
            </li>
          ))}
          {chats.data?.chats.length === 0 && <li className="wc-muted">No chats yet.</li>}
        </ul>
      )}
    </div>
  );
}
