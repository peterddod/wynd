// role="tablist" with arrow-key navigation (`$DRAFTS/07 §4.1`). Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ReactElement } from "react";

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

export function Tabs<T extends string>(_props: TabsProps<T>): ReactElement | null {
  return null;
}
