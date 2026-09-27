// Expression textarea validated by the controller (300 ms debounce) with scope completion (`$DRAFTS/07 §7.7`).
// The request carries the in-flight process doc, so errors reflect unsaved edits; a request counter drops
// out-of-order responses. Scope refs from the first response are cached until the input loses focus.
import { useEffect, useId, useRef, useState, type KeyboardEvent, type ReactElement } from "react";
import { useApi, useDesign, useMeta } from "../../api/context";
import type { ExprCheck, Loc } from "../../api/types";
import { completionPrefix } from "../../model/expr";
import { classes } from "../graph/designHooks";

export interface ExpressionInputProps {
  id: string;
  label: string;
  value: string;
  loc: Loc;
  onChange(v: string): void;         // applies the op immediately (autosave debounces)
  readOnly?: boolean;
  placeholder?: string;
  autoFocus?: boolean;               // a new pending condition (`when: ""`) takes focus
}

export const VALIDATE_MS = 300;
const MAX_SUGGESTIONS = 8;

export function ExpressionInput(props: ExpressionInputProps): ReactElement | null {
  const { id, label, value, loc, onChange, readOnly = false, placeholder, autoFocus = false } = props;
  const api = useApi();
  const design = useDesign();
  const meta = useMeta();
  const [check, setCheck] = useState<ExprCheck | null>(null);
  const [scope, setScope] = useState<string[] | null>(null);
  const [caret, setCaret] = useState<number | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const counter = useRef(0);
  const scopeRef = useRef<string[] | null>(null);
  const area = useRef<HTMLTextAreaElement>(null);
  const pendingCaret = useRef<number | null>(null);
  const listId = useId();
  const locKey = JSON.stringify(loc);
  scopeRef.current = scope;

  useEffect(() => {
    const n = ++counter.current;
    if (value.trim() === "") {
      setCheck(null);
      return;
    }
    const timer = setTimeout(() => {
      const pid = design.store.get()?.processId;
      if (pid === undefined) return;
      api.expressions.validate({
        process_id: pid, process: design.processDoc(), protos: design.dirtyProtoDocs(), loc: JSON.parse(locKey) as Loc,
        expr: value, scope: scopeRef.current === null,
      }).then((r) => {
        if (n !== counter.current) return;
        setCheck(r);
        if (r.scope !== null && scopeRef.current === null) setScope(r.scope);
      }, () => undefined);
    }, VALIDATE_MS);
    return () => clearTimeout(timer);
  }, [value, locKey, api, design]);

  useEffect(() => {
    const el = area.current;
    if (el === null) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
    if (pendingCaret.current !== null) {
      el.setSelectionRange(pendingCaret.current, pendingCaret.current);
      pendingCaret.current = null;
    }
  }, [value]);

  const prefix = caret === null ? null : completionPrefix(value, caret);
  const candidates = prefix === null || scope === null ? [] : [...scope, ...(meta?.expr_functions ?? [])]
    .filter((ref) => ref.startsWith(prefix.prefix) && ref !== prefix.prefix)
    .slice(0, MAX_SUGGESTIONS);
  const showList = open && !readOnly && candidates.length > 0;
  const current = Math.min(active, Math.max(0, candidates.length - 1));

  const pendingWhen = loc.at(-1) === "when" && value === "";
  const error = pendingWhen ? null : check?.errors[0] ?? null;
  const warnings = pendingWhen || check === null ? [] : check.warnings;

  function accept(ref: string): void {
    if (prefix === null || caret === null) return;
    pendingCaret.current = prefix.start + ref.length;
    setCaret(pendingCaret.current);
    setOpen(false);
    onChange(value.slice(0, prefix.start) + ref + value.slice(caret));
  }

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>): void {
    if (!showList) return;
    switch (e.key) {
      case "ArrowDown":
        e.preventDefault();
        setActive((current + 1) % candidates.length);
        return;
      case "ArrowUp":
        e.preventDefault();
        setActive((current - 1 + candidates.length) % candidates.length);
        return;
      case "Enter":
      case "Tab":
        e.preventDefault();
        accept(candidates[current] as string);
        return;
      case "Escape":
        e.preventDefault();
        e.stopPropagation();
        setOpen(false);
    }
  }

  const trackCaret = (): void => setCaret(area.current?.selectionStart ?? null);
  const messageId = `${id}-message`;
  const invalid = pendingWhen || error !== null;

  return (
    <div className="wg-expr">
      <label htmlFor={id}>{label}</label>
      <textarea
        ref={area}
        id={id}
        rows={1}
        className="wg-mono"
        value={value}
        placeholder={placeholder}
        readOnly={readOnly}
        autoFocus={autoFocus}
        spellCheck={false}
        aria-invalid={invalid}
        aria-describedby={messageId}
        aria-autocomplete="list"
        aria-controls={showList ? listId : undefined}
        aria-activedescendant={showList ? `${listId}-${current}` : undefined}
        onChange={(e) => {
          onChange(e.target.value);
          setCaret(e.target.selectionStart);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={onKeyDown}
        onKeyUp={trackCaret}
        onClick={trackCaret}
        onSelect={trackCaret}
        onBlur={() => {
          setOpen(false);
          setScope(null);
        }}
      />
      {showList && (
        <ul id={listId} role="listbox" className="wg-completions" aria-label={`Suggestions for ${label}`}>
          {candidates.map((ref, i) => (
            <li key={ref} id={`${listId}-${i}`} role="option" aria-selected={i === current}
                className={classes(i === current && "wg-active")}
                onMouseDown={(e) => {
                  e.preventDefault();
                  accept(ref);
                }}>
              {ref}
            </li>
          ))}
        </ul>
      )}
      <div id={messageId} aria-live="polite" className="wg-expr-message">
        {pendingWhen && <span className="wg-error-text">Condition required, or tick 'Otherwise (else)'</span>}
        {error !== null && <span className="wg-error-text">col {error.start + 1}: {error.message}</span>}
        {warnings.map((w) => <span key={`${w.start}:${w.message}`} className="wg-warning-text">col {w.start + 1}: {w.message}</span>)}
      </div>
    </div>
  );
}
