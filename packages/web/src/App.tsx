// App shell: providers (Api, Meta, Design), layout grid, tabs, ConnectionBanner, JobWatcher, toasts (`$DRAFTS/07 §4`).
// Stub from WEB-SCAFFOLD; WEB-CORE implements it.
import type { ReactElement } from "react";
import type { Api } from "./api/client";

export interface AppProps {
  api?: Api;                         // default createApi(); tests inject a fake
}

export function App(_props: AppProps): ReactElement | null {
  return null;
}
