// role="tablist" with a roving tabindex: arrow keys, Home and End move and activate (`$DRAFTS/07 §4.1`, §4.8).
// The panel for tab `id` uses `id={tabPanelId(id)}` and `aria-labelledby={tabId(id)}`.
import { useRef, type KeyboardEvent, type ReactElement } from "react";

export interface TabItem<T extends string> {
  id: T;
  label: string;
}

export interface TabsProps<T extends string> {
  label: string;                     // aria-label of the tablist
  tabs: TabItem<T>[];
  value: T;
  onChange(id: T): void;
}

export function tabId(id: string): string {
  return `wy-tab-${id}`;
}

export function tabPanelId(id: string): string {
  return `wy-tabpanel-${id}`;
}

export function Tabs<T extends string>({ label, tabs, value, onChange }: TabsProps<T>): ReactElement {
  const buttons = useRef(new Map<string, HTMLButtonElement>());

  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>): void => {
    const current = tabs.findIndex((t) => t.id === value);
    const last = tabs.length - 1;
    let next: number;
    switch (e.key) {
      case "ArrowRight":
        next = current >= last ? 0 : current + 1;
        break;
      case "ArrowLeft":
        next = current <= 0 ? last : current - 1;
        break;
      case "Home":
        next = 0;
        break;
      case "End":
        next = last;
        break;
      default:
        return;
    }
    const tab = tabs[next];
    if (tab === undefined) return;
    e.preventDefault();
    onChange(tab.id);
    buttons.current.get(tab.id)?.focus();
  };

  return (
    <div role="tablist" aria-label={label} className="wy-tabs" onKeyDown={onKeyDown}>
      {tabs.map((t) => (
        <button
          key={t.id}
          ref={(el) => {
            if (el === null) buttons.current.delete(t.id);
            else buttons.current.set(t.id, el);
          }}
          type="button"
          role="tab"
          id={tabId(t.id)}
          aria-controls={tabPanelId(t.id)}
          aria-selected={t.id === value}
          tabIndex={t.id === value ? 0 : -1}
          className="wy-tab"
          onClick={() => onChange(t.id)}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
