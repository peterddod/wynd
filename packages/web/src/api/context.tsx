// React contexts for the Api object, meta and the single design session (`$DRAFTS/07 §4.3`). Transferred to
// WEB-CORE. The contexts are complete (the test render helpers provide them); `DesignContext` lives here rather than
// in App.tsx so tests can provide it without importing the app.
import { createContext, useContext } from "react";
import type { DesignSession } from "../state/design";
import type { Api } from "./client";
import type { Meta } from "./types";

export const ApiContext = createContext<Api | null>(null);
export const MetaContext = createContext<Meta | null>(null);
export const DesignContext = createContext<DesignSession | null>(null);

export function useApi(): Api {
  const api = useContext(ApiContext);
  if (api === null) throw new Error("useApi() outside <ApiContext.Provider>");
  return api;
}

/** Null until `GET /api/meta` has loaded. */
export function useMeta(): Meta | null {
  return useContext(MetaContext);
}

/** The app's single design session; its store holds null while no process is open. */
export function useDesign(): DesignSession {
  const design = useContext(DesignContext);
  if (design === null) throw new Error("useDesign() outside <DesignContext.Provider>");
  return design;
}
