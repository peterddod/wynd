// <aside aria-label="Chat">: chat list + the open chat (URL `chat`); a drawer below 1100px (`$DRAFTS/07 §9.1`).
// Collapsing hides the panel but keeps the open chat mounted, so its stream keeps delivering the turn events that
// unlock the editor. The last chat of this browser (localStorage "wynd.chat") reopens when the URL names none.
import { useEffect, type ReactElement } from "react";
import { useApi, useMeta } from "../../api/context";
import { readStoredChat, storeChat } from "../../state/chat";
import { useQuery } from "../../state/query";
import { useUrlState } from "../../state/url";
import { ChatList } from "./ChatList";
import { ChatView } from "./ChatView";

export interface ChatPanelProps {
  open: boolean;
  onClose(): void;
}

export function ChatPanel({ open, onClose }: ChatPanelProps): ReactElement {
  const api = useApi();
  const meta = useMeta();
  const [url, setUrl] = useUrlState();
  const chats = useQuery("chats", () => api.chats.list());
  const selected = url.chat;
  const listed = chats.data?.chats;

  useEffect(() => {
    if (selected !== null || listed === undefined) return;
    const stored = readStoredChat();
    if (stored !== null && listed.some((c) => c.id === stored)) setUrl({ chat: stored }, "replace");
  }, [selected, listed, setUrl]);

  useEffect(() => {
    if (selected !== null) storeChat(selected);
  }, [selected]);

  // a chat created elsewhere (the Compile/Build buttons) is opened while the list refetches: not missing yet
  const missing = selected !== null && listed !== undefined && !chats.loading && !listed.some((c) => c.id === selected);

  return (
    <aside aria-label="Chat" className="wc-panel" hidden={!open}>
      <div className="wc-panel-head">
        <h2 className="wc-panel-title">Chat</h2>
        <button type="button" className="wc-close" aria-label="Close chat" onClick={onClose}>×</button>
      </div>
      {meta?.llm.ready === false && (
        <p className="wc-banner" role="status">
          The assistant is not ready ({meta.llm.provider}): {meta.llm.message}
        </p>
      )}
      <ChatList selected={selected} onSelect={(chatId) => setUrl({ chat: chatId })} />
      {selected !== null && !missing && <ChatView key={selected} chatId={selected} />}
      {missing && <p className="wc-empty">This chat no longer exists.</p>}
      {selected === null && <p className="wc-empty">Pick a chat or start a new one.</p>}
    </aside>
  );
}
