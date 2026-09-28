// ActingOnChip + textarea + [Send]/[Stop]; Enter sends, Shift+Enter is a newline (`$DRAFTS/07 §9.4`). Sending commits
// the open process first; a failed send keeps the text. Disabled while a turn runs; [Stop] cancels it.
import { useId, useState, type KeyboardEvent, type ReactElement } from "react";
import { useApi } from "../../api/context";
import { useSendMessage } from "../../state/chat";
import { toast } from "../../state/toasts";
import { useUrlState } from "../../state/url";
import { ActingOnChip } from "./ActingOnChip";

export interface ComposerProps {
  chatId: string;
  running: boolean;                  // a turn is running: disabled, [Stop] shown
}

/** The app's Ctrl/Cmd+K shortcut (`$DRAFTS/07 §4.8`): opens the sidebar if it is a closed drawer and focuses the
 *  process search. */
function focusProcessSearch(): void {
  window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true }));
}

export function Composer({ chatId, running }: ComposerProps): ReactElement {
  const api = useApi();
  const [url] = useUrlState();
  const send = useSendMessage(chatId);
  const [text, setText] = useState("");
  const [sending, setSending] = useState(false);
  const id = useId();

  const submit = async (): Promise<void> => {
    const body = text.trim();
    if (body === "" || running || sending) return;
    setSending(true);
    if (await send(body)) setText("");
    setSending(false);
  };

  const stop = async (): Promise<void> => {
    try {
      await api.chats.cancel(chatId);
    } catch (err) {
      toast(`Could not stop: ${err instanceof Error ? err.message : String(err)}`, { level: "error" });
    }
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>): void => {
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    void submit();
  };

  return (
    <form className="wc-composer" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
      <ActingOnChip actingOn={url.process} onClick={focusProcessSearch} />
      <label htmlFor={id} className="wc-sr-only">Message</label>
      <textarea
        id={id}
        className="wc-input"
        rows={3}
        value={text}
        disabled={running || sending}
        placeholder={running ? "The assistant is working…" : "Ask about any process, or change the open one"}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={onKeyDown}
      />
      {running
        ? <button type="button" className="wc-stop" onClick={() => void stop()}>Stop</button>
        : <button type="submit" disabled={sending || text.trim() === ""}>Send</button>}
    </form>
  );
}
