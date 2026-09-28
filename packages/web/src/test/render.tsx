// Renders a component inside the app's providers (`$DRAFTS/07 §17.1`): Api (a fake by default), Meta (the meta
// fixture by default) and the design session, with the URL query set first.
import { render, type RenderResult } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import type { Api } from "../api/client";
import { ApiContext, DesignContext, MetaContext } from "../api/context";
import type { Meta } from "../api/types";
import type { DesignSession } from "../state/design";
import type { UrlState } from "../state/url";
import { createFakeApi } from "./fakeApi";
import { fixture } from "./fixtures";

export interface RenderAppOptions {
  api?: Api;                         // default createFakeApi()
  url?: Partial<UrlState>;           // written to the query string before rendering (null values omitted)
  design?: DesignSession | null;     // default none: components that need it must be given one
  meta?: Meta | null;                // default the meta fixture; null = not loaded yet
}

export type RenderAppResult<A extends Api> = RenderResult & { api: A };

/** `?process=…&tab=…` for the given URL state, in UrlState field order. */
export function urlSearch(url: Partial<UrlState>): string {
  const params = new URLSearchParams();
  for (const key of ["process", "chat", "tab", "sel", "run"] as const) {
    const value = url[key];
    if (value !== undefined && value !== null) params.set(key, value);
  }
  const query = params.toString();
  return query === "" ? "" : `?${query}`;
}

export function renderApp<A extends Api = ReturnType<typeof createFakeApi>>(
  ui: ReactElement,
  opts: RenderAppOptions & { api?: A } = {},
): RenderAppResult<A> {
  const api = (opts.api ?? createFakeApi()) as A;
  const meta = opts.meta === undefined ? fixture("meta") : opts.meta;
  window.history.replaceState(null, "", `/${urlSearch(opts.url ?? {})}`);
  const wrapper = ({ children }: { children: ReactNode }): ReactElement => (
    <ApiContext.Provider value={api}>
      <MetaContext.Provider value={meta}>
        <DesignContext.Provider value={opts.design ?? null}>{children}</DesignContext.Provider>
      </MetaContext.Provider>
    </ApiContext.Provider>
  );
  return Object.assign(render(ui, { wrapper }), { api });
}
