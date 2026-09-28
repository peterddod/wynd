// Search input (250 ms debounce) + the four ANDed flag chips (`$DRAFTS/07 §6.1`). Text changes reach `onChange`
// after the debounce; a chip toggle reaches it at once (with the current text).
import { useEffect, useRef, useState, type ReactElement } from "react";
import type { StatusFlag } from "../../api/types";

/** id of the search input: Ctrl/Cmd+K and the chat's acting-on chip focus it. */
export const PROCESS_SEARCH_ID = "wynd-process-search";

export const SEARCH_DEBOUNCE_MS = 250;

const FLAGS: StatusFlag[] = ["design", "compiled", "built", "released"];

export interface ProcessSearchProps {
  q: string;
  flags: StatusFlag[];
  onChange(q: string, flags: StatusFlag[]): void;
}

export function ProcessSearch({ q, flags, onChange }: ProcessSearchProps): ReactElement {
  const [text, setText] = useState(q);
  const latest = useRef({ onChange, flags });
  useEffect(() => {
    latest.current = { onChange, flags };
  });
  useEffect(() => setText(q), [q]);
  useEffect(() => {
    if (text === q) return;
    const timer = setTimeout(() => latest.current.onChange(text, latest.current.flags), SEARCH_DEBOUNCE_MS);
    return () => clearTimeout(timer);
  }, [text, q]);

  function toggle(flag: StatusFlag): void {
    const on = flags.includes(flag);
    onChange(text, FLAGS.filter((f) => (f === flag ? !on : flags.includes(f))));
  }

  return (
    <div className="wo-search" role="search">
      <label className="wo-label" htmlFor={PROCESS_SEARCH_ID}>Search processes</label>
      <input
        id={PROCESS_SEARCH_ID}
        type="search"
        value={text}
        placeholder="name, goal or step instruction"
        autoComplete="off"
        onChange={(e) => setText(e.target.value)}
      />
      <div className="wo-chips" role="group" aria-label="Filter by status">
        {FLAGS.map((flag) => (
          <button key={flag} type="button" className="wo-chip" aria-pressed={flags.includes(flag)} onClick={() => toggle(flag)}>
            {flag}
          </button>
        ))}
      </div>
    </div>
  );
}
