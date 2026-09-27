// Polls GET /api/jobs/{id}/logs?offset= every 1 s while active; monospace tail (`$DRAFTS/07 §9.7`). Text is appended
// from the returned next offset; polling stops at `done`; only the last 500 lines are kept. When the job stops
// being active one last read picks up the rest.
import { useEffect, useRef, useState, type ReactElement } from "react";
import { useApi } from "../../api/context";
import { ErrorBox } from "../common/ErrorBox";

export interface JobLogsProps {
  jobId: string;
  active: boolean;
}

export const LOG_POLL_MS = 1000;
export const MAX_LOG_LINES = 500;

/** The last `MAX_LOG_LINES` lines of `text` (a trailing newline does not count as a line). */
export function logTail(text: string): string {
  const lines = text.split("\n");
  const keep = MAX_LOG_LINES + (text.endsWith("\n") ? 1 : 0);
  return lines.length <= keep ? text : lines.slice(lines.length - keep).join("\n");
}

export function JobLogs({ jobId, active }: JobLogsProps): ReactElement {
  const api = useApi();
  const [text, setText] = useState("");
  const [error, setError] = useState<Error | null>(null);
  const cursor = useRef({ jobId, offset: 0, done: false });
  const pre = useRef<HTMLPreElement>(null);

  useEffect(() => {
    if (cursor.current.jobId !== jobId) {
      cursor.current = { jobId, offset: 0, done: false };
      setText("");
    }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const poll = async (): Promise<void> => {
      if (cursor.current.done) return;
      try {
        const chunk = await api.jobs.logs(jobId, cursor.current.offset);
        if (stopped) return;
        cursor.current = { jobId, offset: chunk.offset, done: chunk.done };
        if (chunk.text !== "") setText((prev) => logTail(prev + chunk.text));
        setError(null);
      } catch (err) {
        if (stopped) return;
        setError(err instanceof Error ? err : new Error(String(err)));
      }
      if (active && !cursor.current.done) timer = setTimeout(() => void poll(), LOG_POLL_MS);
    };
    void poll();
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [api, jobId, active]);

  useEffect(() => {
    const el = pre.current;
    if (el !== null) el.scrollTop = el.scrollHeight;
  }, [text]);

  return (
    <div className="wc-logs">
      {error !== null && <ErrorBox error={error} />}
      <pre ref={pre} className="wc-log" tabIndex={0} aria-label="Job log">{text === "" ? "(no output yet)" : text}</pre>
    </div>
  );
}
