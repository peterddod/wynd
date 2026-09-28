# 07 — `web` package design (M4): graph editor, chats, run panel

Status: draft for synthesis. Owner area: `packages/web` (AGPL-3.0-or-later) plus the web-facing HTTP contract that the controller implements.

The web package is a **pure view over the controller HTTP API** served by `wynd serve-api`. It holds no Docker, git, YAML, executor or LLM code. Everything it does is an HTTP call to `/api/...`. The browser never parses YAML: the controller serves every design file as a JSON document and writes the YAML back. That keeps the UI dependency-free apart from the approved stack, and it means YAML handling has one owner (`wynd.spec` via the controller).

---

## 0. Spec clauses this area is responsible for (coverage audit)

| Clause | What web does |
|---|---|
| §5 (layout: `web/ # Graph editor + compile chat. Pure view over the controller API served by wynd serve-api. LAST.`) | Package at `packages/web`. Only talks HTTP to `/api`. It imports nothing from Python. |
| §5 dependency direction `controller ← {cli, web}` | The web depends only on the controller HTTP contract (§12 of this doc). |
| §11 "A pure view over the controller API. No Docker or executor imports." | Covered by construction (see §3). |
| §11 Process selector: open process, the pointer is sent with each chat message | §6, §9.4 (`acting_on` on every `POST /messages`). |
| §11 Chats decoupled from processes, read any process, writes apply only to the open process, "acting on" chip | §9. |
| §11 Graph editor (nodes = step refs, edges with branch/transform editors, examples editor with a plain-language schema view), design-phase only | §7. |
| §11 No save button, design auto-commits, autosave continuously, commit on meaningful boundaries, never per keystroke, never spanning two processes | §8. |
| §11 Compile button, chat bound to the serialised session (decisions, clarification questions, edge-case proposals), ff/rebase/PR | §9.6–9.8. |
| §11 Build button, a separate action against the process HEAD | §9.6, §6.4. |
| §11 Status derived per commit, released at X and design at HEAD shown together, closure HEAD, four flags | §6.2 (display). Computation is consumed from the controller (C-CTL-STATUS). |
| §11 Search over name, goal and step instructions, with the four flags as filters | §6.3. |
| §11 Release = image + trigger + env binding, only built processes, triggers in the controller | §10. |
| §11 Run panel: enter inputs, watch the trace stream, inspect each step's inputs, outputs and summary | §11. |
| §12 M4 list: process selector, decoupled chats with the "acting on" chip, graph editor, auto-commit, build chat over job sessions, derived status and search, Release records and triggers, run panel | All of the above. Release records and triggers in the controller are the controller's job. Web is the UI for them. |
| §3.2 ProcessStep children nest in traces (`parent.child` step path) | §11.5 trace tree. |
| §3.3 `entry:` binding from `process.inputs` by name | Entry selector and inputs editor (§7.8). The mismatch error comes from the validator. |
| §3.4 edges: one per exit, `to:` branch list, `when`/`with`/`limits`/`name`, shorthand, else, ignored-after-else, `$exit` | §7.2, §7.6. |
| §3.4 `max_traversals` auto-filled on every branch that lies on a cycle | §7.4 (client-side cycle display). The validator is authoritative. |
| §3.4.1 expressions in `when`/`with`/`limits`; counters; `edges["x.y"]` references | §7.3 (lexer, rename), §7.7 (ExpressionInput validated by the controller). |
| §3.5 implicit `error` exit, `on_error`, `finally`, `ProcessError` | Implicit error handle (§7.5), settings (§7.8), ProcessError view (§11.6). |
| §3.6 step 5 structured summary `{step, exit, key_outputs, note}` | Displayed per step run (§11.5). |
| §3.9 usage (tokens, cost, latency) per step | Displayed per step run and per job (§11.5, §9.7). |
| §5.1 `use:` forms, ids are relative paths, roots | Add-step dialog (§7.6). Ids contain `/`, so the HTTP path encoding is in §12.0. |
| §6.1 proto-step schema (instruction, inputs, outputs by exit, exits, exit_codes, examples, env) | Proto-step editor (§7.9). |
| §6.2 process schema | Process settings and graph (§7.8). |
| §6.5 compile output is a branch that the UI integrates; design always at a clean tree | §8, §9.8. |
| §6.6 CLI and web submit jobs and poll; the compile session is serialisable; the web chat is a view over the job record | §9.6–9.9 (poll), job card. |
| §9 plain-language schema inference, example proposals, per-step decisions, two-step fallback reported clearly | §7.10, §9.7. |
| §15 AGPL licence for web; usage fully observable | `LICENSE`, usage display. |
| §13 "Design auto-commits; there is no save", "Process status is derived per commit, never stored", "Only built processes can be released" | §8, §6.2, §10. |

---

## 1. Fixed constraints I design within

- Stack: React 19 + Vite 8 + TypeScript + `@xyflow/react` 12. Dev-only: `@vitejs/plugin-react`, `vitest`, `jsdom`, `@testing-library/react` (+ its required peer `@testing-library/dom`), `@types/react`, `@types/react-dom`. **No other runtime deps.** There is no router, no state library, no YAML library, no markdown library, no dagre or elk, and no CSS framework. §1.1 justifies each one I avoid.
- The browser does not parse or emit YAML. The controller serves design files as JSON and writes YAML.
- Default LLM provider for chat is `claude-code`, driven by the controller. The web only shows provider readiness (`meta.llm`).
- Tests are vitest and fully offline. No Playwright (justified in §17.4).
- No `if <environment>:` branches. The dev and prod differences are Vite proxy config versus same-origin serving. The UI code is identical.

### 1.1 Dependency justification (what is deliberately NOT used)

| Need | Choice | Why no dep |
|---|---|---|
| Routing | Query-string URL state (`?process=&chat=&tab=&sel=&run=`), ~40 lines | One page. Deep links work. serve-api needs no SPA fallback. |
| State | `useSyncExternalStore` stores + tiny query cache, ~150 lines | Few global stores. Everything else is server state. |
| Graph layout | Own layered (Sugiyama-lite) layout, ~150 lines, §7.4 | Graphs are small (≤50 nodes), strictly sequential, and one edge per exit. dagre is unmaintained and elkjs is 1.3 MB. |
| YAML | Server-side | One owner of YAML semantics. The browser edits JSON. |
| Markdown in chat | Own safe subset renderer to React elements, ~120 lines | Avoids `dangerouslySetInnerHTML` and a dependency tree. |
| Forms, JSON viewer, dialogs | Native `<dialog>`, small components | Tiny surface. |

---

## 2. Big picture

```
 Browser (packages/web, static bundle)                         wynd serve-api (controller as a service)
 ┌──────────────────────────────────────────────┐   HTTP/JSON   ┌──────────────────────────────────────────┐
 │ App shell ─ URL state ─ stores ─ query cache  │ ────────────▶ │ /api/*  (FastAPI; routes in §12)          │
 │ Design session (autosave/auto-commit)         │               │   process loader/validator (wynd.process) │
 │ Graph editor (React Flow) + inspector         │ ◀──── SSE ─── │   jobs (JobRunner), chats (claude-code)   │
 │ Chats + job cards (poll jobs)                 │               │   runs → executor / supervisor run API    │
 │ Run panel (SSE trace) + Releases              │               │   releases + trigger scheduler            │
 └──────────────────────────────────────────────┘               │ /      static bundle (web_dist/)          │
                                                                └──────────────────────────────────────────┘
```

- **Polling vs streaming.** Jobs are **polled** (§6.6 "submit jobs and poll"). Chat turns and run traces are **streamed over SSE**. Design and status are **refetched** after actions and on window focus. There is no global event bus.
- **One open process at a time**, so there is exactly one live design session.

---

## 3. Package layout and build tooling

### 3.1 Files at the package root

```
packages/web/
  LICENSE                 # AGPL-3.0-or-later full text
  README.md               # dev / build / test commands (≤40 lines)
  package.json
  package-lock.json       # committed; npm (no pnpm)
  tsconfig.json
  vite.config.ts          # also holds the vitest config
  index.html
  src/...                 # §16
```

### 3.2 `package.json`

```json
{
  "name": "@wynd/web",
  "private": true,
  "version": "0.1.0",
  "license": "AGPL-3.0-or-later",
  "type": "module",
  "engines": { "node": ">=20" },
  "scripts": {
    "dev": "vite",
    "build": "vite build",
    "typecheck": "tsc --noEmit -p tsconfig.json",
    "test": "vitest run",
    "test:watch": "vitest",
    "check": "npm run typecheck && npm run test && npm run build"
  },
  "dependencies": {
    "@xyflow/react": "^12.11.6",
    "react": "^19.3.0",
    "react-dom": "^19.3.0"
  },
  "devDependencies": {
    "@testing-library/dom": "^10.4.2",
    "@testing-library/react": "^16.3.3",
    "@types/react": "^19.3.0",
    "@types/react-dom": "^19.3.0",
    "@vitejs/plugin-react": "^6.1.1",
    "jsdom": "^30.1.1",
    "typescript": "^7.0.2",
    "vite": "^8.3.0",
    "vitest": "^5.0.1"
  }
}
```

I checked these versions against the npm registry on 2026-09-22. `@vitejs/plugin-react@6` requires `vite ^8`, and `vitest@5` accepts `vite ^8`. Vite and Vitest transpile with Oxc and do not type-check, so TypeScript 7 (the native `tsc`) is only used by `npm run typecheck`.

### 3.3 `vite.config.ts`

```ts
/// <reference types="vitest/config" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { fileURLToPath } from "node:url";

const apiTarget = process.env.WYND_API_URL ?? "http://127.0.0.1:8780"; // `wynd serve-api` default (C-CLI-1)
const outDir = fileURLToPath(new URL("../controller/src/wynd/controller/web_dist", import.meta.url));

export default defineConfig({
  plugins: [react()],
  server: {
    host: "127.0.0.1",
    port: 5173,
    proxy: { "/api": { target: apiTarget, changeOrigin: false } }, // SSE streams through http-proxy unbuffered
  },
  build: { outDir, emptyOutDir: true, sourcemap: true, target: "es2022" },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    css: false,
    restoreMocks: true,
  },
});
```

### 3.4 `tsconfig.json`

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "jsx": "react-jsx",
    "strict": true,
    "noUncheckedIndexedAccess": true,
    "noImplicitOverride": true,
    "verbatimModuleSyntax": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "types": ["vite/client"],
    "noEmit": true
  },
  "include": ["src", "vite.config.ts"]
}
```

### 3.5 Where the production build goes and how Python finds it

- `npm --prefix packages/web run build` writes `index.html` and `assets/*` into **`packages/controller/src/wynd/controller/web_dist/`**. That directory is git-ignored and is a build output.
- The controller wheel ships it as package data. Its `pyproject.toml` needs `[tool.hatch.build.targets.wheel] artifacts = ["src/wynd/controller/web_dist/**"]`. The uv workspace installs members editable, so a repo checkout finds it in place.
- The controller resolves the directory with this function (controller-owned, spec in C-CTL-STATIC):

```python
# packages/controller/src/wynd/controller/api/static.py   (owner: controller)
from pathlib import Path
from fastapi import FastAPI

def web_dist_dir() -> Path | None:
    """$WYND_WEB_DIST if set (must contain index.html), else the packaged
    wynd/controller/web_dist/ if it contains index.html, else None."""

def mount_web(app: FastAPI, dist: Path | None) -> None:
    """Register AFTER all /api routes. dist present: StaticFiles(directory=dist, html=True)
    at "/". dist None: GET "/" returns a text/html page saying
    'Web UI not built: run `npm --prefix packages/web ci && npm --prefix packages/web run build`'."""
```

- The UI uses query-string state only, so no SPA fallback route is needed.
- Root `.gitignore` (repo scaffolding owner) adds `packages/web/node_modules/` and `packages/controller/src/wynd/controller/web_dist/`.

### 3.6 Dev loop

```
# terminal 1 (in a workspace, e.g. examples/invoices)
uv run wynd serve-api                 # 127.0.0.1:8780
# terminal 2
npm --prefix packages/web ci
npm --prefix packages/web run dev     # http://127.0.0.1:5173, /api proxied to 8780 (override: WYND_API_URL)
```

The controller must send SSE responses with `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`, and no gzip. With those headers they pass through the Vite proxy unbuffered.

---

## 4. App architecture

### 4.1 Layout (wireframe)

```
┌ header ──────────────────────────────────────────────────────────────────────────────────────┐
│ Wynd ▸ process_supplier_invoice  [design] [released 9f8e7d6 · 3 behind]   HEAD a1b2c3d        │
│                                    [Compile] [Build] [Release…]   ● Saved · committed a1b2c3d │ [◐ theme]
├ nav (sidebar, 280px) ─┬ main ────────────────────────────────────────────┬ aside (chat, 380px) ┤
│ [search…         ]    │ tabs: Graph | Process | YAML | Runs | Releases    │ Chats ▾  [+ New]    │
│ ☐design ☐compiled     │ ┌ canvas (React Flow) ────────┬ inspector 360px ┐│ ┌ chat view ───────┐│
│ ☐built  ☐released     │ │ ▶inputs → read → extract →  │ selected node / ││ │ items…           ││
│ ─────────────────     │ │   validate ⇄ fix            │ edge / overview ││ │ job card         ││
│ process_supplier_inv  │ │   → save → $exit.done       │                 ││ └──────────────────┘│
│  [design][rel 9f8e7d6]│ │ toolbar: +Step  Re-layout   │                 ││ acting on: <proc>   │
│ finance/invoices …    │ └─────────────────────────────┴─────────────────┘│ [composer……] [Send]│
│ [+ New process]       │                                                   │                     │
└───────────────────────┴───────────────────────────────────────────────────┴─────────────────────┘
```

- `<header>`, `<nav aria-label="Processes">`, `<main>`, and `<aside aria-label="Chat">`. The sidebar and chat collapse with header toggle buttons.
- Below 1100px the sidebar and chat become overlay drawers, opened by header buttons. Below 720px the inspector stacks under the canvas. There is no horizontal page scroll.
- Main tabs are `graph | process | yaml | runs | releases`. They use `role="tablist"`, and the arrow keys move between tabs.

### 4.2 URL state (no router)

`src/state/url.ts` (owner web-core):

```ts
export interface UrlState {
  process: string | null;   // open process id, e.g. "finance/invoices"
  chat: string | null;      // open chat id
  tab: "graph" | "process" | "yaml" | "runs" | "releases";
  sel: string | null;       // graph selection: "s:<step>" | "b:<edgeIdx>:<branchIdx>" | "x:<exit>" | "in"
  run: string | null;       // run shown in the Runs tab
}
export function readUrl(search?: string): UrlState;
export function useUrlState(): [UrlState, (patch: Partial<UrlState>, mode?: "push" | "replace") => void];
```

Changing `process` or `chat` uses `pushState`. Changing `tab`, `sel` or `run` uses `replaceState`. A `popstate` listener re-reads the URL. Every navigation that changes `process` goes through `openProcess()` (§8.5), which commits the current process first.

### 4.3 State management

`src/state/store.ts` (owner web-core):

```ts
export interface Store<T> { get(): T; set(next: T | ((prev: T) => T)): void; subscribe(fn: () => void): () => void; }
export function createStore<T>(initial: T): Store<T>;
/** Selector must return a stable reference (a slice), never a freshly built object. */
export function useStore<T, S>(store: Store<T>, select: (s: T) => S): S; // useSyncExternalStore
```

`src/state/query.ts` is the server-state cache (owner web-core):

```ts
export interface QueryResult<T> { data: T | undefined; error: ApiError | null; loading: boolean; reload(): Promise<void>; }
export interface QueryOpts<T> {
  enabled?: boolean;                                    // default true
  pollMs?: number | ((data: T | undefined) => number | null); // null = stop polling
  refetchOnFocus?: boolean;                             // default true
}
export function useQuery<T>(key: string, fetcher: () => Promise<T>, opts?: QueryOpts<T>): QueryResult<T>;
export function setQueryData<T>(key: string, data: T): void;
export function getQueryData<T>(key: string): T | undefined;
export function invalidate(prefix: string): void; // refetch mounted queries whose key starts with prefix; drop others
```

Cache implementation: a `Map<key, {data, error, promise, subscribers:Set, updatedAt}>`. Requests to the same key are de-duplicated. A `window` `focus` listener refetches mounted `refetchOnFocus` queries. Polling uses one `setTimeout` per key and pauses while `document.hidden`.

Canonical query keys:

| Key | Fetcher |
|---|---|
| `meta` | `GET /api/meta` |
| `processes?<q>&<flags>` | `GET /api/processes?q=&flags=` |
| `steps` | `GET /api/steps` |
| `providers` | `GET /api/providers` |
| `chats` | `GET /api/chats` |
| `jobs:active` | `GET /api/jobs?active=true` (polled by JobWatcher) |
| `job:<id>` | `GET /api/jobs/<id>` |
| `builds:<pid>` | `GET /api/processes/<pid>/builds` |
| `iface:<pid>:<commit or HEAD>` | `GET /api/processes/<pid>/interface?commit=` |
| `runs:<pid>` | `GET /api/runs?process_id=` |
| `releases:<pid>` | `GET /api/releases?process_id=` |
| `envcheck:<rel>` | `GET /api/releases/<rel>/env-check` |

The design session (§8) and chat views (§9) are separate stores, not queries.

**Context providers** (`src/App.tsx`): `ApiContext` holds the `Api` object, so tests inject fakes. `DesignContext` holds the single `DesignSession`. `MetaContext` holds meta.

### 4.4 Typed API client

`src/api/client.ts` (owner web-core):

```ts
export class ApiError extends Error {
  constructor(public status: number, public code: string, message: string, public details: unknown = null) { super(message); }
}
type Method = "GET" | "POST" | "PATCH" | "DELETE";
export async function request<T>(method: Method, path: string, body?: unknown,
                                 init?: { signal?: AbortSignal; keepalive?: boolean }): Promise<T>;
export function pidPath(pid: string): string; // "finance/invoices" -> "finance/invoices" with each segment encodeURIComponent'd
export function createApi(): Api; // the object below, bound to request()
```

Error mapping:

- A non-2xx response with body `{"error": {code, message, details}}` becomes `ApiError(status, code, message, details)`.
- FastAPI's 422 `{"detail": [...]}` becomes `ApiError(422, "invalid", first msg, detail)`.
- A fetch `TypeError` (network failure) becomes `ApiError(0, "network", ...)` and reports to `connection` (§4.6).

The `Api` interface is the full client surface. Each method corresponds 1:1 to a route in §12.

```ts
export interface Api {
  health(): Promise<{ ok: true }>;
  meta(): Promise<Meta>;
  processes: {
    list(q: string, flags: StatusFlag[]): Promise<{ processes: ProcessSummary[] }>;
    get(pid: string): Promise<ProcessSummary>;
    create(body: { id: string; goal?: string; root?: string }): Promise<ProcessSummary>;
    design(pid: string): Promise<DesignDoc>;
    save(pid: string, body: SaveRequest, init?: { keepalive?: boolean }): Promise<SaveResult>;
    compile(pid: string): Promise<{ job: Job; chat: ChatSummary }>;
    build(pid: string): Promise<{ job: Job; chat: ChatSummary }>;
    builds(pid: string): Promise<{ builds: Build[] }>;
    iface(pid: string, commit?: string): Promise<ProcessInterface>;
  };
  expressions: { validate(body: ExprCheckRequest): Promise<ExprCheck> };
  steps: { list(): Promise<{ steps: StepCatalogEntry[] }> };
  providers: { list(): Promise<{ providers: ProviderEntry[] }> };
  jobs: {
    list(p: { process_id?: string; active?: boolean }): Promise<{ jobs: Job[] }>;
    get(id: string): Promise<Job>;
    logs(id: string, offset: number): Promise<JobLogs>;
    answer(id: string, body: AnswerRequest): Promise<Job>;
    integrate(id: string): Promise<Job>;
    cancel(id: string): Promise<Job>;
  };
  chats: {
    list(): Promise<{ chats: ChatSummary[] }>;
    create(body: { title?: string }): Promise<ChatSummary>;
    get(id: string): Promise<ChatSnapshot>;
    update(id: string, body: { title: string }): Promise<ChatSummary>;
    remove(id: string): Promise<void>;
    send(id: string, body: SendMessageRequest): Promise<{ turn_id: string; item: ChatItem }>;
    cancel(id: string): Promise<{ ok: true }>;
    eventsUrl(id: string, since: number): string;
  };
  runs: {
    create(body: CreateRunRequest): Promise<Run>;
    list(p: { process_id?: string; release_id?: string; limit?: number }): Promise<{ runs: Run[] }>;
    get(id: string): Promise<Run>;
    eventsUrl(id: string, since?: number): string;
  };
  uploads: { put(file: File): Promise<{ path: string }> };
  releases: {
    list(pid: string): Promise<{ releases: Release[] }>;
    create(body: CreateReleaseRequest): Promise<Release>;
    update(id: string, body: Partial<Pick<Release, "trigger" | "env" | "enabled">>): Promise<Release>;
    remove(id: string): Promise<void>;
    trigger(id: string, inputs: Record<string, Json>): Promise<Run>;
    envCheck(id: string): Promise<EnvCheck>;
  };
}
```

All DTO types live in `src/api/types.ts` (§12.2). They are hand-written to mirror the controller's pydantic response models. Drift is caught by the shared fixtures contract (P-FIXTURES, §14).

### 4.5 SSE handling

`src/api/sse.ts` (owner web-core):

```ts
export type SseHandler = (data: unknown, lastEventId: string | null) => void;
export interface SseOptions { events: Record<string, SseHandler>; onOpen?(): void; onError?(): void; }
export function openEventStream(url: string, opts: SseOptions): () => void; // returns close()
export function setEventSourceImpl(impl: typeof EventSource): void;          // test seam
```

- Uses native `EventSource`, which reconnects automatically and resends `Last-Event-ID`. The server sends `retry: 2000`.
- Each named event (`item`, `delta`, `turn`, `reset`, `trace`, `end`) is registered via `addEventListener`. `data` is `JSON.parse`d. Parse failures are logged to the console and dropped.
- `end` makes the client call `close()`. `reset` is delivered to the handler, which refetches the snapshot and reopens with a new `since`.
- While an `EventSource` has `readyState === CONNECTING` after an error, a "reconnecting…" pill shows in the view that owns it.

### 4.6 Error states

| Situation | UI |
|---|---|
| Controller unreachable (`ApiError.status === 0`) | `ConnectionBanner` at the top reads "Can't reach wynd serve-api. Retrying…". `connection` store backs off on `GET /api/health` (1s, 2s, 5s, 10s cap). On recovery it runs `invalidate("")` and the design session retries its save. |
| Query error | Inline `ErrorBox` in the panel with code, message and a [Retry] button that calls `reload()`. |
| Save failure (non-409) | Status bar reads "Not saved: <message> [Retry]". **The in-memory doc is kept**. The session retries with backoff. `beforeunload` warns while `dirty`. |
| 409 `revision_conflict` | Conflict banner in the design surface (§8.4). |
| 423 `design_locked` | Editor is read-only with the notice "Assistant is editing this process" (§8.6). |
| `process.yaml` unparseable (`DesignDoc.process_file.parse_error`) | Graph tab shows "process.yaml can't be parsed: <message> (line L, col C). Fix it in your editor or ask the chat." The YAML tab shows the raw text. The editor is read-only. |
| 404 on the open process | Toast "Process <id> no longer exists". `process` is set to null. |
| Job `failed` | Job card shows `error.message`, an expandable `error.detail`, and the log tail. |
| `meta.llm.ready === false` | Chat panel banner shows `meta.llm.detail`, for example "Claude Code is not logged in. Run `claude` or set CLAUDE_CODE_OAUTH_TOKEN (from `claude setup-token`) for serve-api." Send stays enabled, and the server's error arrives as a `notice` item. |

Toasts are a tiny `toasts` store rendered in an `aria-live="polite"` region.

### 4.7 Theming (light and dark)

- `src/styles/tokens.css` defines colour tokens on `:root`: `--bg --surface --surface-2 --border --text --text-muted --accent --accent-contrast --ok --warn --err --info --focus --code-bg --badge-design --badge-compiled --badge-built --badge-released`. It sets `color-scheme: light`.
- Dark values are defined twice: under `@media (prefers-color-scheme: dark) { :root:not([data-theme="light"]) {...} }` and under `:root[data-theme="dark"] {...}`. `body` has an explicit `background: var(--bg)`.
- The theme toggle cycles `system → light → dark`. It is stored in `localStorage["wynd.theme"]` (wrapped in try/catch) and applied as `document.documentElement.dataset.theme` (removed for system). React Flow receives `colorMode={theme}` (`"system" | "light" | "dark"`, supported natively in v12).
- Plain CSS only: `tokens.css` plus `app.css` with component-prefixed class names (`.wg-node`, `.wc-item`, …). `@xyflow/react/dist/style.css` is imported once in `main.tsx`.
- Every state badge carries text, so colour is never the only signal. Token pairs meet 4.5:1 contrast.
- `@media (prefers-reduced-motion: reduce)` disables animated edges and spinners, which fall back to a static "…".

### 4.8 Accessibility basics

- Landmarks and headings per §4.1. All actions are `<button>`. All inputs have `<label>`. Errors use `aria-invalid` and `aria-describedby`.
- **Every graph operation has a non-drag path in the inspector**: add step, route an exit ("Route exit → target" select), add/reorder/remove branches (up and down buttons, no drag-and-drop), retarget, remove edge, set entry, rename and remove step. React Flow's `nodesFocusable`/`edgesFocusable` stay on. Each node has an `aria-label`, for example "Step extract, agentic, compiled, exits: done, not_an_invoice, error". Pressing Enter on a focused node selects it into the inspector.
- Live regions: autosave status (`polite`), the streaming assistant message (`aria-live="polite"` with `aria-busy` while streaming), and run status.
- Dialogs are native `<dialog>` with `showModal()`, which gives a focus trap and Esc to close. Dialogs used inside the design surface are rendered **inside** its DOM subtree, not portalled, so they do not trigger a blur commit (§8.3).
- Keyboard: `Ctrl/Cmd+K` focuses process search. In the canvas, `Delete`/`Backspace` removes the selection after confirmation and `Esc` clears the selection.

---

## 5. Domain vocabulary (UI-side)

- **Raw doc**: the JSON form of a YAML file as the controller serves it (`DesignDoc.process_file.doc`, `DesignDoc.protos[path].doc`). The UI edits raw docs **in place with path-preserving immutable updates**. Keys the UI does not understand are never touched, and key order is preserved. This is the "must not lose data" guarantee. YAML comments are lost when the controller rewrites the file, which the brief allows.
- **Normalized view**: read-only projections, where every edge is a branch list. Validator `loc`s refer to the normalized form (C-PROC-2).
- **Step info**: per step key, the controller's resolution of `use:` (kind, phase, interface schemas) (§12.2 `StepInfo`).

---

## 6. Process selector, status badges, search

### 6.1 Sidebar list = selector + search

- The `ProcessSearch` input is debounced 250ms. Four toggle chips (`design`, `compiled`, `built`, `released`) are **ANDed** filters. The request is `GET /api/processes?q=<q>&flags=design,released`.
- Each result row shows the id, the display name if it differs, the goal (1 line, ellipsized), status badges (§6.2), and, when `q` is non-empty, the match snippet with query terms wrapped in `<mark>`. Marking is done client-side by case-insensitive term matching on the snippet text.
- Clicking or pressing Enter on a row calls `openProcess(id)`.
- The list is refetched on window focus, after every commit, after job integration, and after release changes (`invalidate("processes")`).
- `[+ New process]` opens `NewProcessDialog`. Fields: id (segments `[a-z0-9_]+` joined by `/`), process root (a select from `meta.workspace.process_roots`, shown only when there is more than one), and goal. The dialog calls `POST /api/processes`, and the controller scaffolds and commits (`wynd new`). It then opens the new process.

Server-side search semantics (provided to the controller, C-CTL-SEARCH):

1. `q` is split on whitespace into terms and casefolded. The empty `q` matches everything.
2. A process matches when every term occurs as a substring in at least one of: `id`, `name`, `goal`, or the `instruction` of any step in its closure (proto `instruction`, or the compiled step docstring when no proto exists).
3. `flags` filters with AND over `status.<flag> == true`.
4. `matches[]` holds up to 3 entries `{field, step, snippet}`. `snippet` is a ±40-character window around the first term hit in that field, with whitespace collapsed.
5. Ordering: any term hit in `id`/`name` first, then `goal`, then `instruction`. Ties sort by `id` ascending.

### 6.2 Status badges (derived per commit, never stored)

`src/model/status.ts` (owner web-ops):

```ts
export interface Badge { kind: "design" | "compiled" | "built" | "released" | "tests_failed"; label: string; title: string; }
export function statusBadges(s: ProcessStatus): Badge[];
```

Rules (pure display of controller-derived flags):

- `design`: shown if `s.design`. Label "design". Title "N step(s) have proto-steps with no matching compiled source at HEAD: a, b".
- `compiled`: shown if `s.compiled`. Title "Compiled source matches proto hashes and tests pass at HEAD <short>".
- `tests_failed`: shown if `!s.compiled && !s.design && s.tests === "failed"`. Label "tests failing".
- `built`: shown if `s.built`. Title "Build artefact exists for HEAD <short>".
- One `released` badge per release in `s.releases`. The label is `released <short>`, followed by `· HEAD` when `behind === 0` or `· <n> behind` otherwise. The title gives the image and the trigger kind.

The header shows `HEAD <s.head.short>` with a tooltip of the commit subject and time. This is how "released at X and in design at HEAD" appear side by side, for example `[design] [released 9f8e7d6 · 3 behind]`.

### 6.3 Header actions for the open process

| Button | Enabled when | Action |
|---|---|---|
| Compile | always, unless a compile job for this pid is active. In that case the label is "Compiling…" or "Needs answers (n)" and it opens that job's chat | `await design.commit("before_job")`, then `api.processes.compile(pid)`. `setQueryData("job:"+job.id)`, and open `chat.id` in the chat panel. |
| Build | `!status.design` (tooltip otherwise: "Compile first: steps a, b are design-only") and no active build for this pid | `await design.commit("before_job")`, then `api.processes.build(pid)`, then open the chat. On 409 `dirty_tree`, show a dialog listing `details.paths` with the text "Commit or discard these changes outside Wynd, then retry". |
| Release… | `status.built` or any build in `builds:<pid>` | Switch to the Releases tab and open `NewReleaseDialog` (§10). |

The active-job lookup uses `jobs:active` filtered by `process_id` and `kind`.

---

## 7. Design editing

### 7.1 Model modules (pure TypeScript, no React)

| Module | Purpose |
|---|---|
| `src/model/json.ts` | `Json` type, `getIn(doc, path)`, `setIn(doc, path, value \| undefined)` (immutable; `undefined` deletes; an existing key keeps its position; a new key is appended), `renameKey(obj, old, new)` (in place, position preserved), `deepEqual`. |
| `src/model/processDoc.ts` | Read views and ops over the raw process doc (§7.2). |
| `src/model/protoDoc.ts` | Read views and ops over a raw proto-step doc (§7.9). |
| `src/model/expr.ts` | Expression lexer, `renameStepInExpr`, completion prefix (§7.3). |
| `src/model/cycles.ts` | Tarjan SCC giving the set of branches on cycles (§7.4). |
| `src/model/graph.ts` | `toFlow()`: raw docs + step info → React Flow nodes and edges (§7.5). |
| `src/model/layout.ts` | Layered layout (§7.4). |
| `src/model/schemaText.ts` | JSON Schema → plain language (§7.10). |
| `src/model/values.ts` | Example and run value parsing (§7.11). |
| `src/model/issues.ts` | Map validator `loc` to a UI selection (§7.12). |

### 7.2 `processDoc.ts`: read view and ops

```ts
export type Loc = (string | number)[];
export type Target = { type: "step"; step: string } | { type: "exit"; exit: string };
export interface BranchView {
  index: number; target: Target; when: string | null; // null = no `when` key (else / plain)
  name: string | null; with: Record<string, Json>; limits: Record<string, Json>;
  isElse: boolean;      // when === null
  ignored: boolean;     // comes after the first else branch
  extra: Record<string, Json>; // unknown keys on the branch (shorthand: unknown edge keys other than kind)
}
export interface EdgeView {
  index: number; from: string; fromStep: string; fromExit: string;
  shorthand: boolean;   // `to:` is a string
  kind: string;         // edge.kind ?? "deterministic"
  branches: BranchView[];
}
export function parseFrom(from: string): { step: string; exit: string }; // split at the FIRST "."
export function parseTarget(t: string): Target;                         // "$exit.<name>" -> exit, else step
export function targetString(t: Target): string;
export function stepNames(doc: Json): string[];                         // keys of doc.steps in order
export function edgesOf(doc: Json): EdgeView[];
export function edgeIndexFor(doc: Json, step: string, exit: string): number; // -1 if none
export function branchLoc(e: EdgeView, b: number, ...rest: Loc): Loc;  // raw path (handles shorthand)
export function normalizedLoc(edgeIdx: number, b: number, ...rest: Loc): Loc; // ["edges",i,"to",b,...rest]
```

**Shorthand rule.** An edge `{from, to: "<t>", with?, limits?, ...}` is read as one branch `{step: t, with, limits}`. Its raw paths for `with`/`limits` are `["edges", i, "with" | "limits", ...]`. For list edges the raw path is `["edges", i, "to", b, ...]`. `branchLoc` hides this difference.

**Ops.** Every op returns a new doc and never mutates. Each op leaves all other keys and their order untouched.

```ts
export function addStep(doc: Json, name: string, use: string): Json;
  // steps[name] = { use }; if !doc.entry -> doc.entry = name. Creates `steps` (appended) if absent.
export function removeStep(doc: Json, name: string): Json;
  // delete steps[name]; drop edges whose fromStep === name; drop branches targeting name; drop edges left
  // with 0 branches; delete entry/on_error if === name; filter name out of `finally`.
export function renameStep(doc: Json, from: string, to: string): { doc: Json; rewritten: number; unparsed: Loc[] };
  // steps key renamed in place; edges[].from "<from>.<exit>" -> "<to>.<exit>"; branch/shorthand targets;
  // entry, on_error, finally; every string value under edges[*].to[*].when|with|limits and
  // edges[*].with|limits (recursively through nested objects/lists) via renameStepInExpr.
  // `rewritten` = number of strings changed; `unparsed` = locs whose expression failed to lex (left as is).
export function routeExit(doc: Json, step: string, exit: string, target: Target): { doc: Json; edgeIndex: number; branchIndex: number };
  // no edge for step.exit: push { from: "step.exit", to: targetString(target) } (shorthand)
  // edge exists: addBranch(...)
export function addBranch(doc: Json, edgeIndex: number, target: Target): { doc: Json; branchIndex: number };
  // expand shorthand first; if an else branch exists at k: insert { step, when: "" } at k (new branch needs a
  // condition; UI focuses its `when`); else append { step } (it becomes the else).
export function expandShorthand(doc: Json, edgeIndex: number): Json;
  // { from, to: "t", with: W, limits: L, ...rest } -> { from, to: [{ step: "t", with: W, limits: L }], ...rest }
  // (keys order: `to` stays where it was; with/limits keys removed from the edge; branch keys in order step, with, limits)
export function updateBranch(doc: Json, e: number, b: number,
  patch: { when?: string | null; name?: string | null; target?: Target }): Json;
  // `when` or `name` on a shorthand edge -> expandShorthand first; null deletes the key; "" is kept (pending condition)
export function setBranchMapValue(doc: Json, e: number, b: number, map: "with" | "limits", key: string, value: Json | undefined): Json;
  // writes via branchLoc; deleting the last key deletes the map
export function renameBranchMapKey(doc: Json, e: number, b: number, map: "with" | "limits", from: string, to: string): Json;
export function moveBranch(doc: Json, e: number, from: number, to: number): Json; // expands shorthand if needed
export function removeBranch(doc: Json, e: number, b: number): Json;  // last branch -> removeEdge
export function removeEdge(doc: Json, e: number): Json;
export function setEdgeKind(doc: Json, e: number, kind: string): Json; // "deterministic" deletes the key
export function setEntry(doc: Json, name: string): Json;
export function setTop(doc: Json, key: "name" | "goal" | "latency" | "provider" | "on_error", value: Json | undefined): Json;
export function setBase(doc: Json, base: string | undefined): Json; // env.base; deleting last env key deletes env
export function setFinally(doc: Json, steps: string[]): Json;        // [] deletes the key
export function setFieldMap(doc: Json, path: ["inputs"] | ["outputs", string], fields: [string, Json][]): Json;
export function renameProcessExit(doc: Json, from: string, to: string): Json;
  // outputs key rename; every target "$exit.<from>" -> "$exit.<to>"; examples[].exit
export function setExamples(doc: Json, examples: Json[]): Json;
```

The UI never collapses a list back to shorthand. A list edge with one plain branch stays a list, which keeps diffs minimal.

**Step name rule (client hint only; server authoritative):** `^[A-Za-z_][A-Za-z0-9_]*$`, unique within `steps`, and not a reserved word (`steps process env run previous edges and or not in if then elif else true false null`).

### 7.3 `expr.ts`: lexer and step rename inside expressions

```ts
export type Tok = { t: "ws" | "str" | "num" | "id" | "punct"; s: string; start: number; end: number; quote?: '"' | "'" };
export function lex(expr: string): Tok[] | null; // null on an unterminated string
export function renameStepInExpr(expr: string, from: string, to: string): string | null; // null if lex fails
export function completionPrefix(expr: string, caret: number): { start: number; prefix: string } | null;
```

Lexer rules:

- `ws` = `/\s+/`
- `str` = `"` or `'` quoted, with `\\` escapes
- `num` = `/\d+(\.\d+)?/`
- `id` = `/[A-Za-z_][A-Za-z0-9_]*/`
- `punct` = any single other character. Multi-character operators do not matter for renaming.

Rename algorithm (tokens with `ws` skipped when looking backwards):

1. An `id` token equal to `from` is replaced when the previous two non-ws tokens are `id:"steps"` then `punct:"."`, **and** the token before `steps` is not `punct:"."`. This matches `steps.from.*` and not `x.steps.from`.
2. A `str` token is rewritten when the previous two non-ws tokens are `id:"edges"` then `punct:"["`, and its content starts with `from + "."`. Only that prefix is replaced, and the quote style is preserved.
3. The result is the original text re-assembled from tokens, with only the replaced token texts changed. Byte-identical output is guaranteed when nothing matches.

Completion prefix: if the caret is inside a string literal (checked with `lex` on `expr.slice(0, caret)` returning null), there is no completion. Otherwise the regex `/[A-Za-z_][\w]*(?:\.[A-Za-z_][\w]*|\["[^"]*"\]|\[\d+\])*\.?$/` is applied to the text before the caret.

### 7.4 Cycles and layout

`src/model/cycles.ts`:

```ts
export function branchesOnCycles(doc: Json): Set<string>; // "e:b" keys
```

This builds a step graph with one edge per branch, for step targets only. It runs Tarjan's SCC. A branch u→v is on a cycle iff `scc(u) === scc(v)` and (`u !== v` or it is a self-loop). This matches §3.4, which says "every branch that lies on a cycle". It drives the "auto max_traversals" placeholder and the ↻ label. The validator stays authoritative for filling the value (C-PROC-4).

`src/model/layout.ts`:

```ts
export interface LayoutNode { id: string; w: number; h: number; order: number; rank?: "first" | "last"; }
export interface LayoutEdge { source: string; target: string; order: number; }
export function layout(nodes: LayoutNode[], edges: LayoutEdge[]): Record<string, { x: number; y: number }>;
export const GAP_X = 100, GAP_Y = 36;
```

Algorithm (deterministic, O(k·(V+E))):

1. **Nodes.** `in` (rank first, 180×44). Steps in `doc.steps` order (220 × (60 + 22·exitCount)). Terminals `x:<exit>` in `outputs` order (rank last, 160×44). `order` is the document order index.
2. **Back edges.** DFS starting at `in`. Adjacency lists are ordered by `LayoutEdge.order` (edge index, then branch index). When DFS is done, run it again from each unvisited node in `order`. Any edge to a node currently on the DFS stack is a back edge. Self-loops are ignored. The DAG is all remaining edges.
3. **Layering (longest path).** Kahn topological order, with ties broken by `order`. `layer(v) = max(layer(u)+1)` over DAG predecessors, or 0 if none. Rank-first nodes get 0. **All terminals get `maxStepLayer + 1`.**
4. **Ordering (barycenter).** The initial order within a layer is by `order`. Four sweeps run in the sequence down, up, down, up. In a down sweep, key(v) is the mean current index of v's DAG predecessors, or v's own index if it has none. An up sweep uses successors. Sorting is stable.
5. **Coordinates.** `x = layer · (220 + GAP_X)`. In each layer, nodes are stacked with `y += h + GAP_Y`, then the layer is centred with `y -= layerHeight/2`.
6. Back edges render as React Flow `smoothstep` edges with `pathOptions.offset = 24`, which routes them around the nodes.

Positions are recomputed only when the **structure key** changes: sorted node ids plus `source>target` pairs. Text edits do not re-layout. Manual drags are stored as overrides in `localStorage["wynd.layout.<workspace root>.<pid>"] = {"<step>": {x, y}}` (try/catch; per-viewer convenience only, never in `process.yaml`). The toolbar's "Re-layout" clears them, and `renameStep` migrates the key.

Expected layout for the dogfood process, asserted in tests:

- Layers: `in`=0, `read`=1, `extract`=2, `validate`=3, and `save`, `fix`, `escalate`=4.
- Terminals `x:done`, `x:not_an_invoice`, `x:needs_review`=5.
- Back edge: `fix→validate`.

### 7.5 `graph.ts`: projection to React Flow

```ts
export interface StepNodeData extends Record<string, unknown> {
  kind: "step"; name: string; use: string;
  stepKind: "deterministic" | "agentic" | "shell" | "process" | null; // null = not compiled
  phase: "design" | "compiled" | "handwritten" | "missing";
  tier: string | null;
  exits: { name: string; routed: boolean; implicit: boolean; declared: boolean }[];
  isEntry: boolean; isErrorHandler: boolean; isFinally: boolean;
  issueCount: number; readOnly: boolean;
}
export interface TerminalNodeData extends Record<string, unknown> { kind: "exit"; exit: string; declared: boolean; }
export interface InputsNodeData extends Record<string, unknown> { kind: "inputs"; fields: string[]; }
export interface BranchEdgeData extends Record<string, unknown> {
  edgeIndex: number; branchIndex: number; label: string;
  isElse: boolean; ignored: boolean; onCycle: boolean; maxTraversals: Json | null; hasIssues: boolean;
}
export interface FlowInput {
  process: Json;
  protos: Record<string, Json>;            // in-memory proto docs by path
  steps: Record<string, StepInfo>;         // controller step info by step key
  issues: Issue[] | null;                  // null when stale (§7.12)
  readOnly: boolean;
}
export function toFlow(input: FlowInput): { nodes: Node[]; edges: Edge[] };
```

Mapping:

- **Nodes.**
  - `in` (type `inputs`) is a decoration with fields `keys(process.inputs)`. There is one non-deletable edge `entry` from `in` to `s:<entry>`, which is not selectable.
  - `s:<name>` (type `step`) for each `steps` key.
  - `x:<exit>` (type `terminal`) for each `outputs` key, plus one for every `$exit.<n>` targeted but not declared (`declared:false`, rendered as an error).
- **Step exits** (source handles `o:<exit>` on the right side, in this order):
  1. Declared exits. If the step's `proto_path` is loaded in the session, they come from `protoDoc.exits(protos[proto_path])`, so they update instantly while editing. Otherwise they come from `steps[name].interface.exits[].name`.
  2. `error` (`implicit: true`, muted, bottom).
  3. Exits referenced by an edge `from` but not declared (`declared:false`, red).
  - `routed` means there is an edge with `from === "<name>.<exit>"`.
- **Edges.** One React Flow edge per branch. id is `b:<e>:<b>`, type `branch`, source `s:<fromStep>`, sourceHandle `o:<fromExit>`, target `s:<step>` or `x:<exit>`, targetHandle `t`.
- **Labels:**
  - A single branch with no `when` has an empty label.
  - With more than one branch, the label starts with `${b+1}. `.
  - Then comes `else` for an else branch, or `if <when truncated at 32 chars with …>`, followed by ` [name]` when named.
  - A ↻ suffix is added for cycle branches, with `max_traversals` or "10 (auto)" from `meta.default_max_traversals`.
- Ignored branches are dashed and muted, with the tooltip "Ignored: comes after the else branch". Branches with issues get an error colour.
- React Flow props: `nodesConnectable={!readOnly}`, `edgesReconnectable={!readOnly}`, `deleteKeyCode={readOnly ? null : ["Delete","Backspace"]}`.

### 7.6 Graph editor interactions (`components/graph/GraphEditor.tsx`)

The editor is controlled. Nodes and edges are derived with `toFlow()` on each design-state change and memoized on `(process, protos, steps, issues, readOnly)`. Positions come from `layout()` plus overrides. `onNodesChange` only applies `position` (drag), `dimensions` and `select` changes. Removal is routed through `onBeforeDelete`.

| Gesture | Effect (op label for the commit summary) |
|---|---|
| Drag from exit handle `o:<exit>` of `s:A` to `s:B` / `x:E` | `routeExit(doc, A, exit, target)`, then select `b:<e>:<b>`. If the new branch was inserted before an else with `when: ""`, the inspector focuses its `when`. Label: `route A.exit → B`. |
| `isValidConnection` | The source must be a step exit handle. The target must be a step or terminal node, never `in`. |
| `onReconnect` (drag an edge end to a new node) | `updateBranch(..., {target})`. Label: `retarget A.exit[b]`. |
| Delete with an edge selected (`onBeforeDelete` returns `false`, then we apply) | `removeBranch`. Label: `remove branch A.exit[b]`. |
| Delete with a node selected | Confirm dialog: "Remove step X and its edges?" plus a checkbox "Also delete proto-step file <path>". The checkbox is shown only for `ref_kind === "local"` when no other step key uses the same `proto_path`, and defaults to on. It calls `removeStep`, plus a proto delete write if checked. **Compiled `steps/<name>/` source is never deleted by the UI.** Label: `remove step X`. |
| Double-click a node's title, or Rename in the inspector | `RenameStepDialog` validates the name rule. It applies `renameStep` and reports "Updated N references". If `unparsed.length`, it adds "M expressions could not be updated automatically (listed)". Label: `rename step A → B`. |
| Toolbar "+ Step" | `AddStepDialog` (below). |
| Toolbar "Re-layout" | Clear position overrides. |
| `onNodeDragStop` | Save the override position. This does not affect the doc. |
| Node button "Open process" (on `process:` refs) | `openProcess(childId)`. |

`AddStepDialog` fields:

- **Step name**: the key.
- **Source**: a radio with three options.
  1. **New proto-step.** Proto name defaults to the step name. It adds `use = conventions.local_use.replace("{name}", protoName)`, and creates a proto file at `conventions.local_proto_path.replace("{name}", protoName)` with the skeleton `{kind: "proto_step", name, instruction: "", exits: ["done"], examples: []}`.
  2. **Existing step.** A searchable list of `DesignDoc.available_local` plus `GET /api/steps` (step-root steps, labelled `alias:path`, with instruction snippet and phase).
  3. **Another process.** A list from the processes query excluding the current one, which writes `use: process:<id>`.
- The dialog calls `addStep`, plus the proto create write when applicable. Label: `add step X`. Reference cycles are a load-time validator error surfaced by the save report. The UI does not pre-check.

### 7.7 Inspector (`components/inspector/*`)

The selection comes from URL `sel`. The panel content by selection:

- **None → `ProcessOverview`.** A plain-language summary of the process inputs and outputs (§7.10), a validation issue list (click → select via §7.12), and cycle notes.
- **`s:<name>` → `StepInspector`:**
  - Header: name with a [Rename] button, and `use` (text input; on blur it runs `setIn(["steps",name,"use"])`; the hint lists the three forms). Kind and phase badges. For agentic steps, `lock.tier`, `lock.provider` and `lock.thinking`. For shared steps, a "used by: p2, p3" chip.
  - Actions: [Set as entry] and [Remove step].
  - **Routing** section: one row per exit (declared exits and `error`). A routed exit shows its target summary and an [Edit] button that selects the edge. An unrouted exit shows a "Route to…" `<select>` of steps and `$exit.*` that calls `routeExit`.
  - **Interface** section: `SchemaSummary` from `interface` (read-only), with a source label (§7.10).
  - **Proto-step** section: `ProtoStepEditor` (§7.9) if `proto_path`. For a local step without a proto, a [Create proto-step] button writes the skeleton to `conventions.local_proto_path`. `process:` refs show the child's interface and an [Open process] button.
  - If `phase === "compiled"` and a proto exists, the hint reads "Editing the proto-step makes this step design-phase until recompiled".
- **`b:<e>:<b>` → `EdgeInspector`** for the whole edge `e`, with branch `b` expanded:
  - Header `<fromStep> · <fromExit>` and `kind` `<select>`. Options come from `meta.edge_kinds`; v1 has `["deterministic"]` and M5 adds `agentic`. [Remove edge].
  - An ordered list of `BranchCard`s, then an [+ Add branch] button (target select, then `addBranch`).
  - Each `BranchCard` contains:
    - index, and up/down buttons (`moveBranch`)
    - optional `name` (identifier; placeholder "optional, for edges[\"A.exit\"].<name>.taken")
    - **target** `<select>` of steps, `$exit.*`, and "New step…" (which opens AddStepDialog and then retargets)
    - **condition**: an "Otherwise (else)" checkbox. Checked means `when` is deleted. Unchecking sets `when: ""` and focuses it. Otherwise an `ExpressionInput` bound to `when` (loc `["edges",e,"to",b,"when"]`).
    - status chips "else", "ignored (after else)", "on a cycle"
    - `WithMappingEditor` and `LimitsEditor`
    - an `ExtraFieldsEditor` for unknown branch keys (a JSON textarea parsed on blur; lossless escape hatch)
    - [Remove branch]
- **`x:<exit>` → `TerminalInspector`.** A `FieldTableEditor` for `outputs.<exit>`, and a rename that uses `renameProcessExit`.
- **`in` → `InputsInspector`.** A `FieldTableEditor` for `process.inputs`, an entry `<select>`, and a note that the entry step's inputs must match by name (validator error `entry_input_mismatch` shown inline).

`WithMappingEditor` (props `{e, b, targetFields: FieldRow[] | null}`):

- `targetFields` for a step target come from the target step's `interface.inputs` properties. When the target has a loaded proto, they come from `protoDoc.inputNames(doc)` plus server types. For `$exit.<n>`, they come from `process.outputs[n]`.
- There is one row per target field, with the label `field — <plain type>` and a required marker. The `ExpressionInput` is bound to `with[field]`, with loc `["edges",e,"to",b,"with",field]`. An empty input deletes the key.
- Keys in `with` that are not target fields are shown flagged "not an input of <target>", with a remove button.
- If `targetFields === null` (unknown interface), the editor falls back to free key rows plus [+ Add mapping].
- A non-string value (a YAML literal number or mapping) is shown as JSON text. On edit the value is written as a string when it does not `JSON.parse`. For example, a mapping value `{a: steps.x.outputs.a}` is edited as JSON text and stays a mapping.

`LimitsEditor`:

- Known keys `max_traversals`, `timeout` and `retries` (names consumed from C-SPEC-5) are text inputs.
- A value that matches `/^-?\d+(\.\d+)?$/` is written as a number. Any other non-empty value is written as a string (an expression or a duration literal). Empty deletes the key.
- The `max_traversals` placeholder is `"<default> (auto — on a cycle)"` when the branch is in `branchesOnCycles`, or "not needed (not on a cycle)" otherwise.
- Unknown limit keys are rendered as generic rows.

`ExpressionInput` (`components/inspector/ExpressionInput.tsx`, owner web-graph):

```ts
interface ExpressionInputProps {
  id: string; label: string; value: string; loc: Loc;
  onChange(v: string): void; // applies the op immediately (autosave debounces)
  readOnly?: boolean; placeholder?: string;
}
```

- A `<textarea rows=1>` that auto-grows. Enter inserts a newline (expressions may span lines). The font is monospace.
- **Validation.** Validation runs 300ms after the last change, and only for a non-empty value. The call is `api.expressions.validate({process_id, process: session.processDoc, protos: session.dirtyProtoDocs(), loc, expr: value, scope: !scopeLoaded})`.
  - A request counter drops out-of-order responses.
  - The response's `errors[0]` is rendered as `col <start+1>: <message>` in an `aria-live="polite"` element linked via `aria-describedby`, with `aria-invalid`. Warnings show in a muted colour.
  - An empty `when` shows "Condition required, or tick 'Otherwise (else)'" without a request.
- **Completion.** `scope` refs from the first response are cached until the input loses focus. When `completionPrefix` returns a prefix, a listbox popover shows up to 8 refs that start with it (`role="listbox"`, `aria-activedescendant`). Up and Down move, Enter or Tab accepts (replacing `prefix` at `start`), and Esc closes. Built-in function names from `meta.expr_functions` are appended to the candidates.

### 7.8 Process settings tab (`components/process-settings/ProcessSettings.tsx`)

Every field edits `process.yaml` through `processDoc` ops. The fields are:

- `name` (text) and `goal` (textarea).
- `latency`: `<select>` with "(none)" plus `meta.latency`.
- `provider`: `<select>` with "(workspace default: `meta.default_provider`)" plus `GET /api/providers` names.
- `env.base`: `<select>` from `meta.bases`.
- `entry`: `<select>` of steps.
- `inputs`: `FieldTableEditor`.
- `outputs` by exit: an `ExitsEditor` of process exits, where each exit has a `FieldTableEditor` and rename uses `renameProcessExit`.
- `on_error`: `<select>` of "(default handler)" plus steps.
- `finally`: a multi-select checkbox list of steps.
- **Process examples**: `ExamplesEditor` (§7.11) with `exits = keys(outputs)` and schemas from `DesignDoc.interface`.
- `ExtraFieldsEditor` for unknown top-level keys.

The **YAML tab** (`YamlView.tsx`) shows read-only `<pre>` blocks of `process_file.yaml` and each `protos[path].yaml`, taken from the last save or load response. While the session is dirty, a note reads "Showing last saved version".

### 7.9 Proto-step editor (`components/inspector/ProtoStepEditor.tsx`) and `protoDoc.ts`

```ts
export function outputsForm(doc: Json): "flat" | "nested";
  // nested iff `exits` present, OR outputs is a non-empty mapping whose values are all mappings; else flat
export function exits(doc: Json): string[];
  // `exits` if present; else nested -> keys(outputs); else ["done"]
export function inputNames(doc: Json): string[];
export function outputFields(doc: Json, exit: string): [string, Json][];
export function setInstruction(doc: Json, text: string): Json;
export function setFieldType(doc: Json, where: ["inputs"] | ["outputs", string], name: string, type: Json): Json;
  // flat form + where ["outputs","done"] writes outputs[name]
export function renameField(doc: Json, where: ["inputs"] | ["outputs", string], from: string, to: string): Json;
  // also renames the key in examples[].inputs (inputs) or examples[exit==X].outputs (outputs X)
export function removeField(doc: Json, where: ["inputs"] | ["outputs", string], name: string): Json;
  // schema only; example values are kept and shown as "not in schema" (lossless)
export function addExit(doc: Json, name: string): Json;
  // flat -> nested: outputs = { done: <flat>, [name]: {} }, exits = ["done", name]; nested -> outputs[name] = {}, exits push
export function renameExit(doc: Json, from: string, to: string): Json;   // outputs key, exits entry, examples[].exit
export function removeExit(doc: Json, name: string): Json;                // outputs key, exits entry; examples kept+flagged
export function setExitCode(doc: Json, code: string, exit: string | undefined): Json; // exit_codes; code "0" | "1" | "*"
export function setExamples(doc: Json, examples: Json[]): Json;
export function setEnv(doc: Json, patch: { deps?: string[]; requires?: string | null }): Json;
```

Editor sections, in order:

1. Shared-step banner, when the step is shared: "Shared step `shared:extract`, also used by p2, p3. Edits affect those processes."
2. **Instruction**: textarea.
3. **What this step does (plain language)**: `SchemaSummary` of `steps[name].interface` with its source label. While dirty it shows "(updating…)".
4. **Examples**: `ExamplesEditor` with `exits = protoDoc.exits(doc)` and schemas from `interface`. This comes before the schema tables because §6.1 and §9 say the user edits examples, not schemas.
5. **Schema (advanced)**, collapsed by default:
   - an inputs `FieldTableEditor`
   - an `ExitsEditor` with per-exit `FieldTableEditor` (add, rename and remove exit)
   - an **exit codes** table (shown only if `exit_codes` is present, or after clicking "Map shell exit codes")
   - **env**: deps as a textarea with one requirement per line, and a `requires` select of "(none)" or "glibc"
6. `ExtraFieldsEditor` for unknown keys.

`renameExit` on a proto also renames `from: "<k>.<old>"` in the **open process** for every step key `k` whose `proto_path` is that file. This is one `apply()` touching two files, saved in one request (§8). Other processes that share the proto are not modified. The banner warns about this, and their validators will flag it.

`FieldTableEditor` (props `{rows: [string, Json][]; onChange(rows): void; onRename(from,to): void; typeOptions: string[]}`):

- Each row has a name input (rename applied on blur within the surface; this is not a commit boundary) and a type input with a `<datalist>` from `meta.proto_types`. Free text is allowed, for example `list[string]`.
- A non-string type (for example an inline mapping) is edited as JSON text.
- Rows have [×] and [+ Add field] buttons.

### 7.10 Plain-language schema view (`src/model/schemaText.ts`)

```ts
export function describe(s: JsonSchema | null | undefined): string;          // noun phrase
export function describeFields(s: JsonSchema | null | undefined): string;    // "a (text), b (a number, optional)"
export function stepSentence(iface: Interface): string;
// "Takes invoice_text (text). Finishes with done, returning invoice_number (text), total (a number),
//  currency (text) and due_date (a date); or not_an_invoice, returning nothing."
```

`describe` rules, checked in this order:

| Condition | Output |
|---|---|
| `enum` | `one of "a", "b"` |
| `anyOf` / `oneOf` | non-null parts described and joined with " or ", then ", or empty" if null is included |
| `x-wynd-type === "path"` | `a file path` |
| `format === "date"` | `a date` |
| `format === "date-time"` | `a date and time` |
| `type` string | `text` |
| `type` number | `a number` |
| `type` integer | `a whole number` |
| `type` boolean | `yes or no` |
| `type` null | `nothing` |
| `type` array | `a list of <plural(items)>` using the plural table `text→text values`, `a number→numbers`, `a whole number→whole numbers`, `a date→dates`, `a file path→file paths`, `yes or no→yes/no values`, record→`records, each with <fields>`. `items` absent gives `a list` |
| `type` object | with `properties`: `a record with <describeFields>`. Without: `a record (any fields)` |
| otherwise | `any value` |

When `type` is an array like `["string","null"]`, it is treated as `anyOf`.

`SchemaSummary` shows the source label from `interface.source`:

| `interface.source` | Label |
|---|---|
| `"declared"` | "as declared" |
| `"inferred"` | "inferred from examples" |
| `"compiled"` | "from the compiled step" |
| `"process"` | "child process contract" |
| `null` | "Not declared. The compiler will infer it from the examples." |

### 7.11 Examples editor (`components/examples/*`, `src/model/values.ts`)

```ts
export function parseLoose(text: string): Json;   // valid JSON (number/bool/null/"str"/object/array) -> parsed; else raw string
export function formatLoose(v: Json): string;     // strings raw; others JSON.stringify(v)
export function parseTyped(text: string, s: JsonSchema | null): { ok: true; value: Json } | { ok: false; error: string };
export function exampleSentence(ex: Json, iface: Interface | null): string;
// "Given invoice_text = "Dear customer…" → done with invoice_number = "INV-1042", total = 1200.5"
```

`ExamplesEditor` props: `{examples: Json[]; exits: string[]; iface: Interface | null; onChange(next: Json[]): void; label: string}`.

- Each example card has:
  - a title from `exampleSentence`, truncated at 120 characters
  - an `exit` `<select>` of exits (an unknown value is shown flagged)
  - an **inputs** form: one `ValueInput` per input field in the schema, plus flagged extra keys. Without a schema, free key/value rows use `parseLoose`.
  - an **outputs** form for the chosen exit's fields. It is hidden when the exit declares no fields and the example has no outputs.
  - [Duplicate], [↑] [↓] and [Remove]
- [+ Add example] appends `{inputs: {}, exit: exits[0] ?? "done"}`.

`ValueInput` (props `{schema: JsonSchema | null; value: Json | undefined; onChange(v: Json | undefined): void; label: string; allowUpload?: boolean}`):

| Schema | Control | Written value |
|---|---|---|
| string + `format: date` | `<input type=date>` | `"YYYY-MM-DD"` string |
| string + `x-wynd-type: path` | text (+ Upload button when `allowUpload`, §11.3) | string |
| string | `<input>`, or `<textarea>` when the value contains `\n` or is longer than 80 characters (toggle available) | string |
| number / integer | `<input type=number step=any / 1>` | `Number(v)`. Empty means `undefined` |
| boolean | checkbox | boolean |
| object / array / anyOf / unknown | JSON `<textarea>` with local text state, parsed on blur. When invalid, it shows an error and does **not** propagate | parsed JSON |
| `null` (no schema) | text with `parseLoose` | parsed |

### 7.12 Mapping validator issues to UI (`src/model/issues.ts`)

```ts
export type Selection = { kind: "step"; step: string } | { kind: "branch"; e: number; b: number }
  | { kind: "edge"; e: number } | { kind: "exit"; exit: string } | { kind: "inputs" } | { kind: "process" };
export function selectionForIssue(issue: Issue, d: { processPath: string; stepsByProto: Record<string, string[]> }): Selection;
export function issuesAt(issues: Issue[], file: string, prefix: Loc): Issue[]; // prefix match on normalized loc
```

Rules for `selectionForIssue`, applied in order:

1. `issue.file !== processPath` → `{step}` for the first step key using that proto path.
2. `["steps", name, ...]` → step.
3. `["edges", e, "to", b, ...]` → branch.
4. `["edges", e, ...]` → edge.
5. `["outputs", exit, ...]` → exit.
6. `["inputs", ...]` or `["entry"]` → inputs.
7. Anything else → process.

After a **structural** op (anything that adds or removes steps, edges or branches, or reorders branches), the session marks `issuesStale = true`, and `toFlow` gets `issues: null` until the next save response. This prevents index-shifted issues from attaching to the wrong element. Stale issues are still listed in the overview, greyed.

---

## 8. Autosave and auto-commit (`src/state/design.ts`, owner web-graph)

### 8.1 State and API

```ts
export type CommitReason = "blur" | "hidden" | "process_switch" | "before_job" | "before_chat" | "before_integrate" | "unload";
export interface FileState { path: string; revision: string | null; doc: Json; savedDoc: Json; yaml: string | null; deleted?: boolean; }
export interface DesignState {
  processId: string;
  processPath: string;
  process: FileState;
  protos: Record<string, FileState>;          // by path
  steps: Record<string, StepInfo>;
  iface: Interface | null;                    // process-level interface for examples/run form
  report: ValidationReport | null;
  issuesStale: boolean;
  conventions: DesignConventions;
  availableLocal: AvailableLocalStep[];
  parseError: ParseError | null;
  saveState: "clean" | "pending" | "saving" | "error" | "conflict";
  uncommitted: boolean;                       // saved to working tree since the last commit
  opLabels: string[];                         // labels since the last commit, de-duplicated, in order
  lockedBy: { chat_id: string; turn_id: string } | null;
  lastCommit: { sha: string; message: string } | null;
  error: ApiError | null;
}
export interface Drafts { process: Json; protos: Record<string, Json | null>; } // null = delete that file
export interface DesignSession {
  store: Store<DesignState | null>;
  open(pid: string): Promise<void>;                     // GET design; replaces state (caller commits old first)
  apply(label: string, change: (d: Drafts) => Drafts, opts?: { structural?: boolean }): void;
  flush(): Promise<void>;                               // cancel debounce, save dirty files now
  commit(reason: CommitReason): Promise<{ sha: string } | null>; // save dirty + commit in ONE request
  reload(): Promise<void>;                              // GET design; only when not dirty (else no-op + conflict check)
  resolveConflict(choice: "theirs" | "mine"): Promise<void>;
  lock(turn: { chat_id: string; turn_id: string }): void;
  unlock(turnId: string): void;
  processDoc(): Json;                                   // current in-memory process doc
  dirtyProtoDocs(): Record<string, Json>;               // in-memory protos differing from savedDoc
  close(): void;
}
export function createDesignSession(api: Api, clock?: { setTimeout: typeof setTimeout; clearTimeout: typeof clearTimeout; now(): number }): DesignSession;
```

A file is **dirty** iff `doc !== savedDoc` by reference. Ops always produce new references for changed files only.

### 8.2 Save algorithm (debounced autosave to the working tree)

Constants: `DEBOUNCE_MS = 800` (trailing) and `MAX_WAIT_MS = 5000` (from the first unsaved change).

```
apply(label, change, {structural}):
  if lockedBy or parseError: return                      # read-only
  drafts' = change(drafts)
  for each file whose doc reference changed: file.doc = new doc
  opLabels.pushIfAbsent(label); if structural: issuesStale = true
  saveState = "pending"; schedule save at min(now+DEBOUNCE, firstPendingAt+MAX_WAIT)

save(commit: CommitSpec | null):                          # internal
  if inFlight: await inFlight (then continue with fresh snapshot)
  writes = [ {path, base_revision: f.revision, doc: f.doc} for dirty non-deleted files ]
         + [ {path, base_revision: f.revision, delete: true} for files marked deleted ]
  if writes empty and commit null: return
  snapshot = map path -> f.doc
  inFlight = api.processes.save(pid, {writes, commit}); saveState = "saving"
  on success r:
     for each r.files: f.revision = r.revision; f.yaml = r.yaml; if f.doc === snapshot[path]: f.savedDoc = f.doc
     report = r.validation; issuesStale = false (if nothing structural applied since snapshot); steps = r.steps
     uncommitted = r.commit ? false : (uncommitted || writes non-empty)
     if r.commit: lastCommit = r.commit; opLabels = []; invalidate("processes")
     saveState = any dirty ? "pending" (reschedule) : "clean"
  on 409 revision_conflict: saveState = "conflict" (keep docs; §8.4)
  on 423 design_locked:     lockedBy = r.details; saveState = "pending" (retry after unlock)
  on other error:           saveState = "error"; error = e; retry with backoff 2s,5s,10s (docs kept)
```

The commit summary is `opLabels.slice(0,5).join("; ")`, with `"; +N more"` appended when there are more.

### 8.3 Commit boundaries

`commit(reason)` performs one request: `save({reason, summary})`. It includes any dirty writes and asks the controller to commit **only this process's design-scope paths** (C-CTL-COMMIT). It is a no-op (returns null without a request) when nothing is dirty and `uncommitted === false`.

| Boundary | Trigger | Reason |
|---|---|---|
| Blur after a manual edit | `DesignSurface` (canvas + inspector + settings tab root). This is a `<div tabIndex={-1} onBlurCapture>`. It focuses itself on `pointerdown` when the target is not focusable, so clicks inside keep focus inside. On blur, when `relatedTarget` is null or outside the surface **and** (`dirty \|\| uncommitted`), it commits. Moving between inputs inside the surface is **not** a boundary. | `blur` |
| Window or tab loses focus | `window` `blur`, which fires as a blur with `relatedTarget=null` and is covered above. `document.visibilitychange` to hidden. | `blur` / `hidden` |
| Switching process | `openProcess()` (§8.5) | `process_switch` |
| Submitting compile, build or release | §6.3 | `before_job` |
| Sending a chat message | §9.4 | `before_chat` |
| Integrating a job | §9.9 | `before_integrate` |
| Page unload | `pagehide`: if dirty or uncommitted, `api.processes.save(pid, {writes, commit:{reason:"unload",...}}, {keepalive:true})`. Best effort, since keepalive bodies are limited to 64 KB and design docs are small. `beforeunload` shows the native prompt only while `saveState` is `error` or `conflict`. | `unload` |
| End of a chat turn that edited the process | **Done by the controller**, which made the edits (C-CTL-CHAT-COMMIT). The web only reloads (§8.6). | n/a |

Guarantees:

- **No commit per keystroke.** Keystrokes only call `apply()`. Commits happen only at the boundaries above, and a boundary with nothing new is a no-op.
- **Never two processes.** A session holds one `processId`. Commit requests are process-scoped, and the controller stages only that process's design paths (`git commit -- <paths>`).
- **Clean before jobs.** Compile, build and integration always commit first, and build still gets the controller's dirty-tree check.

### 8.4 Conflicts

A 409 `revision_conflict` happens when the file changed on disk after our load (external editor, git operation). The UI shows a banner in the design surface: "`<path>` changed outside the editor." It offers two choices:

- **[Use the version on disk]** → `resolveConflict("theirs")` runs `GET design` and replaces docs, discarding unsaved local changes.
- **[Keep my version]** → `resolveConflict("mine")` runs `GET design` to learn the current revisions, keeps local docs, sets `revision = server revision` for each conflicting file, and saves.

On window `focus` with the session clean, `reload()` compares revisions and silently refreshes if they changed. When dirty, it defers to the next save, which may 409.

### 8.5 Opening a process

```ts
async function openProcess(next: string | null): Promise<void> {
  const cur = design.store.get();
  if (cur && cur.processId !== next) {
    try { await design.commit("process_switch"); }
    catch (e) { return showSwitchBlocked(e); } // dialog: [Retry] [Discard my changes and switch]
  }
  setUrl({ process: next, sel: null, run: null }, "push");
  if (next) await design.open(next); else design.close();
}
```

### 8.6 Chat turns editing the open process (lock)

- Before sending, the web commits (`before_chat`). The send response has `turn_id`. If `acting_on === openProcess`, it calls `design.lock({chat_id, turn_id})`. The editor becomes read-only with the notice "Assistant is working on this process…".
- The controller also enforces this: it rejects design saves for a process with a running turn acting on it (423 `design_locked`, `details: {chat_id, turn_id}`). `DesignDoc.locked_by` also reports it, so a page reload shows the lock.
- On the chat `turn` event with `status !== "running"` for that turn: `unlock`. If `edited.includes(pid)`, it runs `design.reload()` and `invalidate("processes")`. The controller has already committed (a `commit` item appears in the chat).

---

## 9. Chats

### 9.1 Components

```
ChatPanel (aside)
 ├ ChatList          chats sorted by updated_at desc; badge for job-bound chats (kind + status, "needs answers (n)"); [+ New]
 └ ChatView(chatId)
    ├ header: title (click to rename → PATCH), [Delete], reconnecting pill
    ├ items (virtualised? no — cap render to last 300 items + "show earlier")
    │   MessageItem(user|assistant) · ToolActivityGroup · CommitItem · JobCard · NoticeItem
    └ Composer: ActingOnChip + textarea + [Send] / [Stop]
```

### 9.2 Chat store (`src/state/chat.ts`, owner web-chat)

```ts
export interface ChatViewState {
  chat: ChatSummary; items: ChatItem[]; cursor: number;
  runningTurn: string | null; connection: "open" | "connecting" | "closed";
}
export function useChat(chatId: string): ChatViewState | null; // snapshot + SSE; one stream per mounted ChatView
```

Algorithm:

1. `GET /api/chats/{id}` returns `{chat, items, cursor}`.
2. Open SSE at `GET /api/chats/{id}/events?since=<cursor>`.
3. Handle events:
   - `item {item}`: upsert by `item.id` (replace entirely), keeping `items` sorted by `seq`.
   - `delta {item_id, text}`: append to that assistant item's `text`. If the item is unknown, ignore it; the next `item` snapshot fixes it.
   - `turn {turn_id, status, acting_on, edited, error?}`: `runningTurn = status === "running" ? turn_id : null`, then forward to the design session (§8.6) and `invalidate("chats")`.
   - `reset {}`: close, refetch the snapshot, and reopen with the new cursor.
4. Every SSE event carries `id: <cursor>`. Native reconnect resumes from `Last-Event-ID`, and the server either resumes or sends `reset`.

### 9.3 Items rendering

- **user**: right-aligned bubble. A small chip shows `acting on: <acting_on>`, or "no process" when null. This is the pointer that was sent.
- **assistant**: `Markdown` (safe subset, §9.10) with `aria-busy` while `status === "streaming"`. Error status shows `error` inline with [Retry], which resends the previous user text.
- **tool**: consecutive tool items in a turn are grouped as `ToolActivityGroup`: "Used 3 tools ▸". An expanded row shows:
  - a read or write icon (inline SVG), the `tool` name, and a one-line args summary (`k=v` pairs truncated at 80 characters)
  - a status of spinner, ✓ or ✕, and the duration
  - write tools add a chip `acting on: <acting_on>`
  - expanding shows the args JSON (`JsonView`) and the `summary` text
  - a write tool is expanded by default and its group header counts writes.
- **commit**: "Committed `<short>` <message first line>". The design is reloaded via the turn event.
- **job**: `JobCard(job_id)` (§9.7).
- **notice**: coloured by `level`.

### 9.4 Sending

```ts
async function send(chatId: string, text: string) {
  const pid = url.process;                        // the open process — pointer sent with every message
  if (pid) await design.commit("before_chat");   // throws → toast, message not sent, text kept in composer
  const r = await api.chats.send(chatId, { text, acting_on: pid, client_id: crypto.randomUUID() });
  if (pid) design.lock({ chat_id: chatId, turn_id: r.turn_id });
}
```

- Enter sends and Shift+Enter inserts a newline. The composer is disabled while `runningTurn`, and [Stop] calls `POST /api/chats/{id}/cancel`.
- A 409 `turn_in_progress` shows a toast.
- The `ActingOnChip` shows `acting on: <open process>`, or `acting on: nothing (read-only)` when none is open. Clicking it focuses the process search.
- A new chat is `POST /api/chats {}`. The title defaults to "New chat", and the controller retitles from the first message (first 60 chars). The chat panel remembers the last chat per browser in `localStorage["wynd.chat"]`.

### 9.5 What the assistant can do (controller-owned; listed so the UI labels match)

The controller's chat agent (claude-code provider via claude-agent-sdk) exposes these tools. The web only needs `write: boolean` and a readable `tool` name, so this list is informative (C-CTL-CHAT-TOOLS):

- **Read** (any process): `list_processes`, `search_processes`, `read_design`, `read_proto`, `read_step_source`, `get_status`, `list_runs`, `read_trace`, `list_jobs`, `read_job`.
- **Write** (acting-on process only): `edit_design`, `edit_proto`, `start_compile`, `start_build`.

`start_compile` and `start_build` add a `job` item to the chat.

### 9.6 Compile and build buttons open job-bound chats

`POST /api/processes/{pid}/compile|build` returns `{job, chat}`. The controller creates the job, and a chat titled `Compile <pid>` / `Build <pid>` with `chat.job = {id, ...}`, whose first item is `{type:"job", job_id}`. The web sets `url.chat = chat.id`, opens the chat panel, and calls `setQueryData("job:"+job.id, job)`. The job card is thus "a chat bound to the job's serialised session". The user can also ask the assistant about the job in the same chat, because the controller passes the bound job id into the assistant's context.

### 9.7 `JobCard` (`components/chat/JobCard.tsx`, owner web-chat)

`useQuery("job:"+id, () => api.jobs.get(id), {pollMs: j => isActive(j) ? 1000 : null})`, where active means `queued | running`. In `awaiting_input` it polls every 5000ms.

Content:

- **Header**: kind icon, `Compile | Build | Live test` `<process>` at `<ref short>`, a status pill (`queued | running | awaiting input | succeeded | failed | cancelled`), elapsed time, and `usage.cost_usd` when present. Buttons: [Cancel] while active, and [Logs ▸].
- **Logs**: `JobLogs` polls `GET /api/jobs/{id}/logs?offset=N` every 1s while active, appends `text`, and stops when `done`. It shows a monospace tail (last 500 lines kept).
- **Compile session** (`CompileSessionView`, when `job.session`):
  - **Per-step decisions table** with one row per `session.steps[]`:
    - step name, `use`, and a phase spinner or check
    - a decision badge: `deterministic`, `agentic · <tier> · <thinking>`, `shell`, `process`, or **`split → <deterministic> + <agentic>`** for the two-step fallback (§9 rule 3). The split is shown with a highlighted explainer "Deterministic code passed N of M examples; the rest go to an agentic step via the error exit".
    - tests `passed/total`, and attempts
    - a [Why] disclosure showing `decision.reason`
    - `skipped` rows are muted, with `skipped_reason` (for example "proto-step unchanged").
  - **Inferred schemas**: for each step in `session.inferred_schemas`, `stepSentence(...)` labelled "Inferred from examples".
  - **Questions** (`session.questions[]`, pending first, then answered collapsed):
    - `kind: "clarification"` (`QuestionCard`): step chip, `text`, a textarea, and [Answer], which calls `POST /api/jobs/{id}/answers {question_id, text}`. Answered ones show the answer.
    - `kind: "example_proposal"` (`ProposalCard`): step chip, `text` (for example "What should happen if due_date is missing?"), and the proposed example rendered with `exampleSentence` plus a read-only `ExamplesEditor` card.
      - [Confirm] sends `{question_id, decision:"confirm"}`.
      - [Correct…] opens an editable `ExampleCard` prefilled with the proposal (exits from the step's interface), then [Save correction] sends `{question_id, decision:"correct", example}`.
      - [Not a real case] sends `{question_id, decision:"reject", text?}`.
    - After each answer the returned Job replaces the cache. The controller resubmits the job when **no pending questions remain** (status goes back to `queued`), so answers can be given one at a time.
  - **Events timeline** (`session.events`), collapsed by default: time, step and text.
- **Build result** (`BuildResult`, when `job.build`): the image ref (copy button), commit, tests (`passed/total`, and "reused result recorded for this commit" when `source === "registry"`), `artefact_dir`, and a [Create release…] button that opens `NewReleaseDialog` preselected to this build.
- **Integration** (`IntegrationOutcome`, when `job.integration`):
  - `fast_forward`: "Fast-forwarded `<branch>` onto `<target>`, now at `<head short>`".
  - `rebased`: "Rebased onto `<target>` past N unrelated commits, now at `<head short>`".
  - `pr_branch`: "Left on branch `<branch>` for review: commits inside this process's closure moved since the job started", followed by the conflicting paths list, the `pr_url` link if present, and a copy button for `git switch <branch>`.
  - `noop`: "Nothing to integrate".
- **Error**: `job.error.message` and an expandable `detail`.

### 9.8 Integration and the per-commit status refresh

The **web triggers integration** via the controller (the spec says "the CLI or web integrates it") using an idempotent endpoint (§9.9).

### 9.9 `JobWatcher` (`src/state/jobs.ts`, owner web-chat), mounted once in `App`

1. Poll `jobs:active` (`GET /api/jobs?active=true`) every 2000ms while the result is non-empty, and every 15000ms otherwise. It also polls on focus and immediately after any compile or build submission. `active=true` returns jobs that are `queued | running | awaiting_input`, **plus** jobs that are `succeeded` with `integration == null` and `kind ∈ {compile, test_live}`.
2. For each job, call `setQueryData("job:"+id, job)` so visible cards update without extra requests.
3. When a job is `succeeded`, `kind ∈ {compile, test_live}` and `integration == null`, and it is not already in `integrating: Set<id>`:
   - If `job.process_id === openProcess`, `await design.commit("before_integrate")`.
   - Call `api.jobs.integrate(id)`. On 409 `dirty_tree`, commit once more and retry once. If that fails, show a toast listing the paths.
   - Then `setQueryData`, `invalidate("processes")`, and `design.reload()` if the process is open.
4. When a job goes `running → succeeded` for `build`, run `invalidate("builds:"+pid)` and `invalidate("processes")`.
5. When a job goes to `failed`, show a toast linking to its chat (`job.chat_id`).

### 9.10 `Markdown` (`components/chat/Markdown.tsx`)

This is a safe subset rendered to React elements, with no HTML passthrough.

- **Block level**: fenced code blocks (an unclosed fence renders as code while streaming), `#`/`##`/`###` headings, `-` / `*` / `1.` lists (one level), `>` quote, and paragraphs.
- **Inline**: `` `code` ``, `**bold**`, `*em*`, and links `[t](http(s)://…)` with `target=_blank rel="noopener noreferrer"`. Other schemes render as text.

---

## 10. Releases UI (`components/releases/*`, owner web-ops)

The Releases tab for the open process lists `releases:<pid>`, and a card per release shows:

- `commit short`, with `HEAD` or `N behind`, plus the image (copy) and a state pill (`starting | serving | stopped | error`, with `state_detail`).
- Trigger summary:
  - `manual`
  - `schedule · <cron> (<timezone>) · next <next_fire_at local>`
  - `webhook · POST <webhook_url>` with a copy button. The body is the process inputs JSON.
- Env check: `GET /api/releases/{id}/env-check`, either `✓ environment complete` or `✕ missing: A, B` (as text).
- Buttons:
  - [Run now] opens `RunForm` targeted at this release, then `POST /api/releases/{id}/trigger {inputs}`, and switches to Runs with `run=<id>`.
  - [Edit trigger]
  - [Edit env]
  - [Enable/Disable], which calls `PATCH {enabled}`
  - [Delete], with confirmation.

`NewReleaseDialog`:

1. **Build**: a `<select>` from `builds:<pid>`, defaulting to the HEAD build (`at_head`), labelled `<short> · <built_at> · HEAD|N behind`. If there are no builds, the dialog shows "Only built processes can be released: Build this process first." with a [Build] button.
2. **Trigger**: a radio of manual, schedule or webhook (`TriggerEditor`):
   - schedule: a cron text input (5 fields; the client only checks that there are 5 space-separated fields, and the server validates), an optional timezone, and fixed inputs via `SchemaForm` from `iface:<pid>:<commit>`
   - webhook: an optional `secret_env` name (an env var holding the shared secret)
3. **Env binding** (`EnvBindingEditor`): one row per `build.env[]` var, showing name, description, `used_by`, and required and secret markers. The source is either **From environment** (`{from_env: NAME}`, default same name, editable) or **Value** (`{value: "..."}`). Value is disabled for `secret: true` vars, since secrets are always references.
4. [Create] calls `POST /api/releases`. A 409 `not_built` is shown inline.

---

## 11. Run panel (`components/runs/*`, owner web-ops)

### 11.1 Layout

The Runs tab has two columns. The left column is `RunList` (`runs:<pid>`, polled every 3s while any run is `queued`/`running`) with a [New run] button at the top. The right column is `RunForm` (new) or `RunDetail(url.run)`.

### 11.2 `RunForm`

- **Target** `<select>`:
  - `Local (source at HEAD)`, which gives `{kind:"local"}`
  - `Image <short>` for each build, which gives `{kind:"image", commit}`
  - `Release <short> (<trigger>)` for each release, which gives `{kind:"release", release_id}`
- **Inputs**: `SchemaForm(schema = iface.inputs)`, using `iface:<pid>:<commit|HEAD>`, which is `GET /api/processes/{pid}/interface?commit=`.
- **Fill from example**: a `<select>` over `iface.examples[]` that copies `inputs` into the form.
- [Start]:
  - Local and image targets call `POST /api/runs {process_id, target, inputs}`.
  - Release targets call `POST /api/releases/{id}/trigger {inputs}`.
  - Afterwards the URL gets `run=<id>`.
- Local target runs from committed HEAD per the controller. The form first calls `design.commit("before_job")` so the run matches what the user sees.

### 11.3 `SchemaForm` and uploads

```ts
interface SchemaFormProps { schema: JsonSchema | null; value: Record<string, Json>; onChange(v: Record<string, Json>): void; allowUpload?: boolean; }
```

- There is one `ValueInput` per property of `schema.properties`, in order, marked required per `schema.required`. With no schema, it uses free key/value rows.
- [Start] is disabled until required fields are set and JSON fields are valid.
- For `x-wynd-type: path` fields, **Upload** calls `POST /api/uploads` with the raw file body, header `X-Wynd-Filename: <name>`, and `Content-Type` of the file. The response `{path}` is written into the field. The controller makes that path resolvable for the run target (C-CTL-UPLOAD).

### 11.4 `RunDetail` and the trace stream

- The header comes from `GET /api/runs/{id}` and is refreshed on `run_finished`. It shows status, exit (a badge), mode (`local`/`image`), commit, trigger, duration, and `usage` (tokens and cost).
- Final `outputs` are shown in a `JsonView`. If the exit is `error`, `ProcessErrorView` shows `step`, `cause`, `inputs`, `partial_outputs` and the `trace` pointer, and notes "Workspace kept for inspection".
- The trace uses `openEventStream(api.runs.eventsUrl(id, 0), {events: {trace, end}})`. The server replays all persisted events with `seq > since`, then streams live, then sends `end` after `run_finished`. Reconnects resume from `Last-Event-ID`, which is the event `seq`.

### 11.5 Trace tree (`src/model/trace.ts`)

```ts
export interface StepRun {
  key: string;           // `${path}#${n}` (n = occurrence under the same parent)
  path: string; step: string; attempt: number; kind: string | null;
  status: "running" | "done" | "error";
  startedAt: string; finishedAt: string | null; durationMs: number | null;
  inputs: Json | null; outputs: Json | null; exit: string | null;
  summary: { step: string; exit: string; key_outputs: Json; note: string } | null;
  usage: Usage | null;
  edge: { from: string; branch: number; name: string | null; to: string } | null;
  events: TraceEvent[];  // tool_call, model_call, log, unknown types under this step
  children: StepRun[];   // ProcessStep children (path "parent.child")
}
export interface RunTree { roots: StepRun[]; open: Map<string, StepRun>; last: Map<string, StepRun>; finished: TraceEvent | null; started: TraceEvent | null; }
export function emptyTree(): RunTree;
export function applyTraceEvent(t: RunTree, ev: TraceEvent): RunTree; // incremental, O(1) per event (copy-on-write of touched path)
```

Algorithm:

- `step_started{path}`:
  - The parent path is `path` without its last `.segment`. The parent is `open.get(parentPath)`; for a top-level step, the parent is the root list.
  - If the parent is missing, attach to the roots and flag it as `orphan`.
  - Create a node, append it to the parent's children, and set `open.set(path, node)`.
- `step_finished{path}`: `node = open.get(path)`. Fill `exit`, `outputs`, `summary`, `usage`, `duration_ms` and `status` (`exit === "error"` means `"error"`). Then `open.delete(path)` and `last.set(path, node)`.
- `edge_taken{path}`: `last.get(path).edge = {from, branch, name, to}`.
- `tool_call` / `model_call` / `log`: push to `(open.get(path) ?? last.get(path)).events`. With no path, push to run-level events.
- `run_started` / `run_finished`: store them on the tree.

This works because execution is strictly sequential (§2), so at most one step run per path is open.

`StepRunRow` renders:

- **Collapsed**: indent by depth, then step (path), attempt count if more than 1, exit badge, duration, cost, and `→ <to>` via `[branch n name]`.
- **Expanded**: **Inputs**, **Outputs** and **Summary** (`key_outputs` JsonView plus `note` text), usage (provider, model, tokens in/out, cost, latency), tool calls and model calls (counts plus a list), and logs.
- The running step auto-expands. `ProcessStep` rows show a `process: <id>` tag, and their children render nested.

`JsonView` is a collapsible tree. Strings over 200 characters are truncated with [show all]. It has a copy-JSON button, and it has no dependencies.

---

## 12. Controller HTTP API required by the web (the contract)

The web **consumes** these endpoints. This section is the exact spec the controller owner implements. All paths start with `/api`. Bodies are JSON unless stated. Timestamps are ISO-8601 UTC with `Z`. Ids are opaque strings.

### 12.0 Conventions

- **Process ids contain `/`**. Routes use the Starlette `{process_id:path}` converter. **Suffixed routes (`.../design`, `.../compile`, `.../build`, `.../builds`, `.../interface`) must be registered before `GET /api/processes/{process_id:path}`.** The client sends each segment `encodeURIComponent`-encoded and keeps the `/`.
- Errors: HTTP status plus `{"error": {"code": "<snake_case>", "message": "<human>", "details": <any|null>}}`. Codes the web branches on:

| Code | Status |
|---|---|
| `not_found` | 404 |
| `out_of_scope` | 403 |
| `revision_conflict` | 409 |
| `turn_in_progress` | 409 |
| `dirty_tree` | 409, `details: {paths: [...]}` |
| `not_built` | 409 |
| `design_locked` | 423, `details: {chat_id, turn_id}` |
| `invalid` | 422 |

- SSE responses: `Content-Type: text/event-stream`, `Cache-Control: no-cache`, `X-Accel-Buffering: no`. The first line is `retry: 2000`. Every event has `id:`, `event:` and a single-line JSON `data:`.
- Revisions are `"sha256:<hex of file bytes>"`. `null` means the file does not exist.

### 12.1 Route table

| # | Method and path | Request | Response |
|---|---|---|---|
| 1 | `GET /api/health` | – | `{"ok": true}` |
| 2 | `GET /api/meta` | – | `Meta` |
| 3 | `GET /api/processes?q=&flags=` | `flags` is comma-separated (`design,compiled,built,released`) | `{"processes": ProcessSummary[]}` (search §6.1) |
| 4 | `POST /api/processes` | `{"id": "finance/invoices", "goal"?: str, "root"?: str}` | 201 `ProcessSummary`. Scaffolds via `wynd new` and commits `design(<id>): new process`. |
| 5 | `GET /api/processes/{pid}` | – | `ProcessSummary` |
| 6 | `GET /api/processes/{pid}/design` | – | `DesignDoc` |
| 7 | `POST /api/processes/{pid}/design` | `SaveRequest` | `SaveResult` (§12.3) |
| 8 | `POST /api/expressions/validate` | `ExprCheckRequest` | `ExprCheck` |
| 9 | `GET /api/steps` | – | `{"steps": StepCatalogEntry[]}` (all step-root steps in the workspace) |
| 10 | `GET /api/providers` | – | `{"providers": ProviderEntry[]}` (user registry) |
| 11 | `POST /api/processes/{pid}/compile` | `{}` | 201 `{"job": Job, "chat": ChatSummary}` |
| 12 | `POST /api/processes/{pid}/build` | `{}` | 201 `{"job": Job, "chat": ChatSummary}`. 409 `dirty_tree`. |
| 13 | `GET /api/processes/{pid}/builds` | – | `{"builds": Build[]}` (newest first) |
| 14 | `GET /api/processes/{pid}/interface?commit=` | `commit` omitted means working tree | `ProcessInterface` |
| 15 | `GET /api/jobs?process_id=&active=` | `active=true` semantics in §9.9 | `{"jobs": Job[]}` |
| 16 | `GET /api/jobs/{job_id}` | – | `Job` |
| 17 | `GET /api/jobs/{job_id}/logs?offset=` | byte offset | `{"text": str, "offset": int, "done": bool}` (`offset` is the next offset) |
| 18 | `POST /api/jobs/{job_id}/answers` | `AnswerRequest` | `Job`. Resubmits when no pending questions remain. |
| 19 | `POST /api/jobs/{job_id}/integrate` | `{}` | `Job` with `integration` set. **Idempotent**. 409 `dirty_tree`. |
| 20 | `POST /api/jobs/{job_id}/cancel` | `{}` | `Job` |
| 21 | `GET /api/chats` | – | `{"chats": ChatSummary[]}` (updated_at desc) |
| 22 | `POST /api/chats` | `{"title"?: str}` | 201 `ChatSummary` |
| 23 | `GET /api/chats/{chat_id}` | – | `ChatSnapshot` |
| 24 | `PATCH /api/chats/{chat_id}` | `{"title": str}` | `ChatSummary` |
| 25 | `DELETE /api/chats/{chat_id}` | – | 204 |
| 26 | `POST /api/chats/{chat_id}/messages` | `SendMessageRequest` | 202 `{"turn_id": str, "item": ChatItem}`. 409 `turn_in_progress`. |
| 27 | `POST /api/chats/{chat_id}/cancel` | `{}` | `{"ok": true}` |
| 28 | `GET /api/chats/{chat_id}/events?since=<cursor>` | SSE | events §12.4 |
| 29 | `POST /api/runs` | `CreateRunRequest` | 201 `Run` |
| 30 | `GET /api/runs?process_id=&release_id=&limit=` | default limit 50 | `{"runs": Run[]}` (newest first) |
| 31 | `GET /api/runs/{run_id}` | – | `Run` |
| 32 | `GET /api/runs/{run_id}/events?since=<seq>` | SSE | `event: trace` (data = TraceEvent), then a final `event: end` |
| 33 | `POST /api/uploads` | raw body; header `X-Wynd-Filename` | 201 `{"path": str}` |
| 34 | `GET /api/releases?process_id=` | – | `{"releases": Release[]}` |
| 35 | `POST /api/releases` | `CreateReleaseRequest` | 201 `Release`. 409 `not_built`. |
| 36 | `PATCH /api/releases/{release_id}` | `{"trigger"?, "env"?, "enabled"?}` | `Release` |
| 37 | `DELETE /api/releases/{release_id}` | – | 204 |
| 38 | `POST /api/releases/{release_id}/trigger` | `{"inputs": {...}}` | 201 `Run` |
| 39 | `GET /api/releases/{release_id}/env-check` | – | `EnvCheck` |

### 12.2 DTO types (`src/api/types.ts`, verbatim contract)

```ts
export type Json = null | boolean | number | string | Json[] | { [k: string]: Json };
export type Loc = (string | number)[];
export type StatusFlag = "design" | "compiled" | "built" | "released";

export interface JsonSchema {
  type?: string | string[]; format?: string; "x-wynd-type"?: string;
  properties?: Record<string, JsonSchema>; required?: string[]; items?: JsonSchema;
  enum?: Json[]; anyOf?: JsonSchema[]; oneOf?: JsonSchema[];
  additionalProperties?: boolean | JsonSchema; description?: string; title?: string; default?: Json;
}
export interface Interface {
  inputs: JsonSchema | null;                                  // object schema
  exits: { name: string; schema: JsonSchema | null }[];       // declared exits (implicit `error` excluded)
  source: "declared" | "inferred" | "compiled" | "process" | null;
}
export interface ProcessInterface {
  commit: string | null;
  inputs: JsonSchema | null;
  outputs: Record<string, JsonSchema | null>;                 // by exit
  examples: { inputs: Record<string, Json>; outputs?: Json; exit: string }[];
}

export interface Meta {
  version: string;
  workspace: { root: string; branch: string; head: string; process_roots: string[]; step_roots: Record<string, string> };
  proto_types: string[];            // e.g. ["string","number","integer","boolean","date","path","object","list"]
  bases: string[];                  // ["debian-slim-python","alpine-python"]
  latency: string[];                // ["fast"]
  edge_kinds: string[];             // v1: ["deterministic"]; M5: ["deterministic","agentic"]
  expr_functions: string[];         // ["len","lower","upper","contains","startswith","join","split","default","coalesce","now"]
  limit_fields: string[];           // ["max_traversals","timeout","retries"]
  default_max_traversals: number;   // 10
  default_provider: string;         // "claude-code"
  llm: { provider: string; ready: boolean; detail: string };
}

export interface ProcessStatus {
  head: { commit: string; short: string; at: string; subject: string };
  design: boolean; compiled: boolean; built: boolean; released: boolean;
  tests: "passed" | "failed" | "unknown";           // at HEAD
  design_steps: string[];                           // step keys that are design-only at HEAD
  releases: { id: string; commit: string; short: string; behind: number; trigger: "manual" | "schedule" | "webhook"; state: Release["state"] }[];
}
export interface ProcessSummary {
  id: string; name: string; goal: string | null; path: string;   // path = workspace-relative dir
  status: ProcessStatus;
  matches: { field: "id" | "name" | "goal" | "instruction"; step: string | null; snippet: string }[];
}

export interface StepInfo {
  name: string; use: string;
  ref_kind: "local" | "root" | "process";
  resolved: boolean;
  proto_path: string | null;        // workspace-relative
  source_path: string | null;       // compiled package dir
  kind: "deterministic" | "agentic" | "shell" | "process" | null;
  phase: "design" | "compiled" | "handwritten" | "missing";
  lock: { provider: string | null; tier: string | null; thinking: string | null; effects: string[]; env: string[] } | null;
  used_by: string[];                // process ids referencing the same step (incl. this one)
  instruction: string | null;
  interface: Interface;
}
export interface FileDoc { path: string; revision: string | null; doc: Json; yaml: string }
export interface ParseError { message: string; line: number | null; column: number | null }
export interface DesignConventions { local_use: string; local_proto_path: string } // "{name}" placeholder
export interface AvailableLocalStep { use: string; proto_path: string | null; source_path: string | null; phase: StepInfo["phase"] }
export interface DesignDoc {
  process_id: string; head: string;
  process_file: FileDoc & { parse_error: ParseError | null };   // doc null when parse_error
  protos: Record<string, FileDoc>;                              // every proto in the process's reference set (local + root)
  steps: Record<string, StepInfo>;                              // by step key
  interface: ProcessInterface;                                  // of the working tree
  available_local: AvailableLocalStep[];                        // local steps/protos not referenced by `steps`
  conventions: DesignConventions;
  validation: ValidationReport;
  locked_by: { chat_id: string; turn_id: string } | null;
}
export interface Issue { severity: "error" | "warning"; code: string; message: string; file: string; loc: Loc; span: [number, number] | null }
export interface ValidationReport { ok: boolean; issues: Issue[] }

export type CommitReason = "blur" | "hidden" | "process_switch" | "before_job" | "before_chat" | "before_integrate" | "unload";
export interface SaveRequest {
  writes: { path: string; base_revision: string | null; doc?: Json; delete?: true }[];
  commit: { reason: CommitReason; summary: string } | null;
}
export interface SaveResult {
  files: { path: string; revision: string | null; yaml: string | null }[];
  validation: ValidationReport; steps: Record<string, StepInfo>; interface: ProcessInterface;
  commit: { sha: string; message: string } | null; head: string;
}
export interface ExprCheckRequest { process_id: string; process: Json; protos?: Record<string, Json>; loc: Loc; expr: string; scope: boolean }
export interface ExprCheck {
  ok: boolean;
  errors: { message: string; start: number; end: number }[];
  warnings: { message: string; start: number; end: number }[];
  scope: string[] | null;           // refs valid at loc (exit-aware), when requested
}
export interface StepCatalogEntry { use: string; root: string; path: string; kind: StepInfo["kind"]; phase: StepInfo["phase"]; instruction: string | null; used_by: string[] }
export interface ProviderEntry { name: string; kind: "model" | "agent"; tiers: Record<string, string> }

export type JobKind = "compile" | "build" | "test_live";
export type JobStatus = "queued" | "running" | "awaiting_input" | "succeeded" | "failed" | "cancelled";
export interface Usage { input_tokens: number | null; output_tokens: number | null; cost_usd: number | null; latency_ms: number | null; provider?: string | null; model?: string | null }
export interface CompileStep {
  step: string; use: string;
  phase: "pending" | "generating" | "testing" | "revising" | "done" | "skipped" | "failed";
  decision: { kind: "deterministic" | "agentic" | "shell" | "process"; tier: string | null; thinking: string | null; reason: string;
              split: { deterministic: string; agentic: string } | null } | null;
  tests: { passed: number; failed: number; total: number } | null;
  attempts: number; skipped_reason: string | null;
}
export type Question =
  | { id: string; kind: "clarification"; step: string | null; text: string; status: "pending" | "answered"; answer: { text: string } | null; asked_at: string }
  | { id: string; kind: "example_proposal"; step: string; text: string; proposed: { inputs: Record<string, Json>; outputs?: Json; exit: string };
      status: "pending" | "answered"; answer: { decision: "confirm" | "correct" | "reject"; example?: Json; text?: string } | null; asked_at: string };
export interface CompileSession {
  state: "compiling" | "awaiting_input" | "done" | "failed";
  steps: CompileStep[];
  questions: Question[];
  inferred_schemas: Record<string, Interface>;
  events: { at: string; type: string; step: string | null; text: string }[];
}
export interface BuildResult { commit: string; image: string; artefact_dir: string; tests: { passed: number; failed: number; total: number; source: "ran" | "registry" } }
export interface Integration {
  mode: "fast_forward" | "rebased" | "pr_branch" | "noop";
  branch: string; target: string; head: string | null; skipped_commits: number;
  conflicts: string[]; pr_url: string | null; at: string;
}
export interface Job {
  id: string; kind: JobKind; process_id: string; ref: string; status: JobStatus;
  created_at: string; started_at: string | null; finished_at: string | null;
  branch: string | null; result_commit: string | null;
  session: CompileSession | null; build: BuildResult | null; integration: Integration | null;
  error: { message: string; detail: string | null } | null;
  usage: Usage | null; chat_id: string | null;
}
export type AnswerRequest =
  | { question_id: string; text: string }
  | { question_id: string; decision: "confirm" | "correct" | "reject"; example?: Json; text?: string };

export interface ChatSummary {
  id: string; title: string; created_at: string; updated_at: string;
  running_turn: string | null;
  job: { id: string; kind: JobKind; process_id: string; status: JobStatus; pending_questions: number } | null;
}
export type ChatItem =
  | { id: string; seq: number; type: "user"; created_at: string; text: string; acting_on: string | null }
  | { id: string; seq: number; type: "assistant"; created_at: string; turn_id: string; text: string; status: "streaming" | "done" | "error" | "cancelled"; error: string | null }
  | { id: string; seq: number; type: "tool"; created_at: string; turn_id: string; tool: string; args: Json; write: boolean;
      acting_on: string | null; status: "running" | "ok" | "error"; summary: string | null; duration_ms: number | null }
  | { id: string; seq: number; type: "commit"; created_at: string; turn_id: string; process_id: string; sha: string; message: string }
  | { id: string; seq: number; type: "job"; created_at: string; job_id: string; kind: JobKind; process_id: string }
  | { id: string; seq: number; type: "notice"; created_at: string; level: "info" | "warning" | "error"; text: string };
export interface ChatSnapshot { chat: ChatSummary; items: ChatItem[]; cursor: number }
export interface SendMessageRequest { text: string; acting_on: string | null; client_id: string }

export type RunTarget = { kind: "local" } | { kind: "image"; commit: string } | { kind: "release"; release_id: string };
export interface ProcessError { step: string; cause: string; inputs: Json; partial_outputs: Json; trace: string }
export interface Run {
  id: string; process_id: string; commit: string | null; mode: "local" | "image";
  target: RunTarget; release_id: string | null; trigger: "manual" | "schedule" | "webhook" | "api";
  status: "queued" | "running" | "finished" | "failed_to_start";
  inputs: Json; exit: string | null; outputs: Json | null; error: ProcessError | null;
  started_at: string | null; finished_at: string | null; duration_ms: number | null; usage: Usage | null;
}
export interface CreateRunRequest { process_id: string; target: Exclude<RunTarget, { kind: "release" }>; inputs: Record<string, Json> }
export interface TraceEvent { seq: number; ts: string; run_id: string; mode: "local" | "image"; type: string; path?: string; [k: string]: Json | undefined }

export interface EnvVar { name: string; description: string; required: boolean; secret: boolean; used_by: string[] }
export interface Build { process_id: string; commit: string; short: string; image: string; built_at: string; job_id: string; at_head: boolean; behind: number; env: EnvVar[] }
export type Trigger =
  | { kind: "manual" }
  | { kind: "schedule"; cron: string; timezone: string | null; inputs: Record<string, Json> }
  | { kind: "webhook"; secret_env: string | null };
export type EnvBinding = { value: string } | { from_env: string };
export interface Release {
  id: string; process_id: string; commit: string; short: string; image: string; behind: number;
  trigger: Trigger; env: Record<string, EnvBinding>; enabled: boolean;
  state: "starting" | "serving" | "stopped" | "error"; state_detail: string | null;
  created_at: string; next_fire_at: string | null; webhook_url: string | null;
}
export interface CreateReleaseRequest { process_id: string; commit: string; trigger: Trigger; env: Record<string, EnvBinding>; enabled: boolean }
export interface EnvCheck { ok: boolean; missing: string[]; unbound: string[] }
```

### 12.3 Design save semantics (`POST /api/processes/{pid}/design`), required behaviour

1. **Scope check.** Each `writes[].path` must be in the **design scope** of `pid`:
   - its `process.yaml`
   - any proto-step YAML under its own directory (created or existing)
   - the proto-step YAML of any **step-root** step its `steps` reference
   - otherwise the response is 403 `out_of_scope` and nothing is written

   Compiled source (`steps/<name>/`) and other processes' files are never in scope.
2. **Lock check.** If a chat turn acting on `pid` is running, the response is 423 `design_locked`.
3. **Revision check.** Each `base_revision` must equal the current file revision (null means the file must not exist). On mismatch the response is 409 `revision_conflict` with `details: {path, current_revision}`, and nothing is written. All writes in the request are atomic from the client's view.
4. **Writing.**
   - Each `doc` is dumped with the spec YAML dumper (C-SPEC-1). `delete` removes the file.
   - **Validation errors never block a save**. The design tree may be mid-edit. The only requirement is that `doc` is a JSON object.
5. **Validation.** The response `validation` is computed from the resulting working-tree files via the process loader and validator. `steps` and `interface` are recomputed.
6. **Commit** (when `commit` is non-null):
   - Stage exactly the design-scope paths of `pid` that differ from HEAD (including deletions). Commit with `git commit -m <msg> -- <paths>`, so unrelated staged or dirty files are excluded.
   - The message is `design(<pid>): <summary>`, followed by a blank line, `Wynd-Origin: web`, and `Wynd-Reason: <reason>`.
   - If no path differs, `commit: null`.
   - This is the mechanism for "never spanning two processes".
7. `head` is the workspace HEAD after the operation.

### 12.4 Chat SSE events (`GET /api/chats/{id}/events?since=`)

```
retry: 2000

id: 41
event: item
data: {"item": {"id":"it_7","seq":7,"type":"user","created_at":"2026-09-22T10:00:00Z","text":"Add a step that emails the finance team when escalating","acting_on":"process_supplier_invoice"}}

id: 42
event: turn
data: {"turn_id":"t_3","status":"running","acting_on":"process_supplier_invoice","edited":[],"error":null}

id: 43
event: item
data: {"item": {"id":"it_8","seq":8,"type":"assistant","created_at":"…","turn_id":"t_3","text":"","status":"streaming","error":null}}

id: 44
event: delta
data: {"item_id":"it_8","text":"I'll add a `notify_finance` step "}

id: 45
event: item
data: {"item": {"id":"it_9","seq":9,"type":"tool","created_at":"…","turn_id":"t_3","tool":"edit_design","args":{"op":"add_step","name":"notify_finance"},"write":true,"acting_on":"process_supplier_invoice","status":"ok","summary":"Added step notify_finance (./steps/notify_finance) and routed escalate.done → notify_finance","duration_ms":38}}

id: 46
event: item
data: {"item": {"id":"it_10","seq":10,"type":"commit","created_at":"…","turn_id":"t_3","process_id":"process_supplier_invoice","sha":"b4d2e19…","message":"design(process_supplier_invoice): add step notify_finance"}}

id: 47
event: turn
data: {"turn_id":"t_3","status":"done","acting_on":"process_supplier_invoice","edited":["process_supplier_invoice"],"error":null}
```

- Cursor semantics: `id` is a per-chat monotonically increasing event counter. `ChatSnapshot.cursor` is the last id reflected in the snapshot.
- The server keeps an in-memory ring buffer of the last 1000 events per chat. With `Last-Event-ID` or `since` inside the buffer it resumes. Otherwise it sends `event: reset` with `data: {}`.
- Deltas are not persisted. The assistant item's `text` in snapshots is the accumulated text so far.

### 12.5 Run SSE (`GET /api/runs/{id}/events?since=`)

```
retry: 2000

id: 1
event: trace
data: {"seq":1,"ts":"…","run_id":"run_01J9","mode":"local","type":"run_started","process":"process_supplier_invoice","commit":"a1b2c3d…","inputs":{"pdf_path":"examples/invoices/inv1.pdf"}}

id: 2
event: trace
data: {"seq":2,"ts":"…","run_id":"run_01J9","mode":"local","type":"step_started","path":"read","step":"read","kind":"deterministic","attempt":1,"inputs":{"pdf_path":"examples/invoices/inv1.pdf"}}

id: 3
event: trace
data: {"seq":3,"ts":"…","run_id":"run_01J9","mode":"local","type":"step_finished","path":"read","step":"read","exit":"done","outputs":{"exit":"done","text":"INVOICE INV-1042 …"},"summary":{"step":"read","exit":"done","key_outputs":{"text":"INVOICE INV-1042 …"},"note":""},"duration_ms":41,"usage":null,"attempts":1}

id: 4
event: trace
data: {"seq":4,"ts":"…","run_id":"run_01J9","mode":"local","type":"edge_taken","path":"read","from":"read.done","branch":0,"name":null,"to":"extract"}

… (extract: model_call with usage {input_tokens, output_tokens, cost_usd, latency_ms, provider:"claude-code", model:"haiku"})

id: 19
event: trace
data: {"seq":19,"ts":"…","run_id":"run_01J9","mode":"local","type":"run_finished","exit":"done","outputs":{"record":{"invoice_number":"INV-1042","total":1200.5,"currency":"GBP","due_date":"2026-10-01"}},"error":null,"duration_ms":2380,"usage":{"input_tokens":1840,"output_tokens":212,"cost_usd":0.0031,"latency_ms":1900}}

id: 20
event: end
data: {}
```

A nested child step has `"path": "sub.read"`, where `sub` is the `ProcessStep` key in the parent.

### 12.6 Example payloads (fixtures; excerpts)

`GET /api/processes?q=invoice&flags=` gives:

```json
{"processes": [{
  "id": "process_supplier_invoice", "name": "process_supplier_invoice",
  "goal": "Turn a supplier invoice PDF into a validated record in the records store.",
  "path": "processes/process_supplier_invoice",
  "status": {
    "head": {"commit": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678", "short": "a1b2c3d", "at": "2026-09-22T09:58:12Z", "subject": "design(process_supplier_invoice): edit branch validate.done[1]"},
    "design": true, "compiled": false, "built": false, "released": true, "tests": "unknown",
    "design_steps": ["fix"],
    "releases": [{"id": "rel_01J9Q", "commit": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432", "short": "9f8e7d6", "behind": 3, "trigger": "schedule", "state": "serving"}]
  },
  "matches": [{"field": "goal", "step": null, "snippet": "Turn a supplier invoice PDF into a validated record…"},
              {"field": "instruction", "step": "extract", "snippet": "Given the text of a supplier invoice, extract the invoice number…"}]
}]}
```

`GET /api/processes/process_supplier_invoice/design` (abridged: one proto, three steps shown):

```json
{
  "process_id": "process_supplier_invoice",
  "head": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
  "process_file": {
    "path": "processes/process_supplier_invoice/process.yaml",
    "revision": "sha256:5e0f…",
    "parse_error": null,
    "yaml": "kind: process\nname: process_supplier_invoice\n…",
    "doc": {
      "kind": "process", "name": "process_supplier_invoice",
      "goal": "Turn a supplier invoice PDF into a validated record in the records store.",
      "provider": "claude-code", "env": {"base": "debian-slim-python"},
      "entry": "read",
      "inputs": {"pdf_path": "path"},
      "outputs": {"done": {"record": "object"}, "not_an_invoice": {}, "needs_review": {}},
      "examples": [{"inputs": {"pdf_path": "examples/inv1.pdf"}, "outputs": {"record": {"invoice_number": "INV-1042"}}, "exit": "done"}],
      "steps": {
        "read": {"use": "./steps/read_pdf"}, "extract": {"use": "./steps/extract_invoice_fields"},
        "validate": {"use": "./steps/validate_fields"}, "fix": {"use": "./steps/fix_fields"},
        "save": {"use": "./steps/save_record"}, "escalate": {"use": "./steps/escalate_to_human"}
      },
      "edges": [
        {"from": "read.done", "to": "extract", "with": {"invoice_text": "steps.read.outputs.text"}},
        {"from": "extract.done", "to": "validate", "with": {"fields": "steps.extract.outputs"}},
        {"from": "extract.not_an_invoice", "to": "$exit.not_an_invoice"},
        {"from": "validate.done", "to": [
          {"step": "save", "when": "steps.validate.outputs.valid",
           "with": {"record": "steps.validate.outputs.record",
                    "dest": "if steps.validate.outputs.fields.total > 10000 then env.REVIEW_DIR else env.RECORDS_DIR"}},
          {"step": "fix", "when": "steps.validate.outputs.fixable and steps.fix.runs < 3",
           "with": {"fields": "steps.validate.outputs.fields", "errors": "steps.validate.outputs.errors"}},
          {"step": "escalate", "with": {"fields": "steps.validate.outputs.fields", "errors": "steps.validate.outputs.errors"}}
        ]},
        {"from": "fix.done", "to": "validate", "with": {"fields": "steps.fix.outputs"}},
        {"from": "save.done", "to": "$exit.done", "with": {"record": "steps.save.outputs.record"}},
        {"from": "escalate.done", "to": "$exit.needs_review"}
      ]
    }
  },
  "protos": {
    "processes/process_supplier_invoice/proto/extract_invoice_fields.yaml": {
      "path": "processes/process_supplier_invoice/proto/extract_invoice_fields.yaml",
      "revision": "sha256:91ab…", "yaml": "kind: proto_step\n…",
      "doc": {
        "kind": "proto_step", "name": "extract_invoice_fields",
        "instruction": "Given the text of a supplier invoice, extract the invoice number, total, currency and due date.\n",
        "inputs": {"invoice_text": "string"},
        "outputs": {"done": {"invoice_number": "string", "total": "number", "currency": "string", "due_date": "date"}, "not_an_invoice": {}},
        "exits": ["done", "not_an_invoice"],
        "examples": [
          {"inputs": {"invoice_text": "INVOICE INV-1042 … Total GBP 1,200.50 … Due 2026-10-01"},
           "outputs": {"invoice_number": "INV-1042", "total": 1200.5, "currency": "GBP", "due_date": "2026-10-01"}, "exit": "done"},
          {"inputs": {"invoice_text": "Dear customer, your order has shipped"}, "exit": "not_an_invoice"}
        ],
        "env": {"deps": []}
      }
    }
  },
  "steps": {
    "extract": {
      "name": "extract", "use": "./steps/extract_invoice_fields", "ref_kind": "local", "resolved": true,
      "proto_path": "processes/process_supplier_invoice/proto/extract_invoice_fields.yaml",
      "source_path": "processes/process_supplier_invoice/steps/extract_invoice_fields",
      "kind": "agentic", "phase": "compiled",
      "lock": {"provider": null, "tier": "cheap", "thinking": "low", "effects": [], "env": []},
      "used_by": ["process_supplier_invoice"],
      "instruction": "Given the text of a supplier invoice, extract the invoice number, total, currency and due date.\n",
      "interface": {
        "source": "compiled",
        "inputs": {"type": "object", "properties": {"invoice_text": {"type": "string"}}, "required": ["invoice_text"]},
        "exits": [
          {"name": "done", "schema": {"type": "object", "properties": {
            "invoice_number": {"type": "string"}, "total": {"type": "number"},
            "currency": {"type": "string"}, "due_date": {"type": "string", "format": "date"}},
            "required": ["invoice_number", "total", "currency", "due_date"]}},
          {"name": "not_an_invoice", "schema": {"type": "object", "properties": {}}}
        ]
      }
    },
    "read": {"name": "read", "use": "./steps/read_pdf", "ref_kind": "local", "resolved": true, "proto_path": "processes/process_supplier_invoice/proto/read_pdf.yaml", "source_path": "processes/process_supplier_invoice/steps/read_pdf", "kind": "deterministic", "phase": "compiled", "lock": {"provider": null, "tier": null, "thinking": null, "effects": ["filesystem"], "env": []}, "used_by": ["process_supplier_invoice"], "instruction": "Read the PDF at pdf_path and return its text.", "interface": {"source": "compiled", "inputs": {"type": "object", "properties": {"pdf_path": {"type": "string", "x-wynd-type": "path"}}, "required": ["pdf_path"]}, "exits": [{"name": "done", "schema": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]}},
    "fix": {"name": "fix", "use": "./steps/fix_fields", "ref_kind": "local", "resolved": true, "proto_path": "processes/process_supplier_invoice/proto/fix_fields.yaml", "source_path": null, "kind": null, "phase": "design", "lock": null, "used_by": ["process_supplier_invoice"], "instruction": "Correct the invoice fields using the validation errors.", "interface": {"source": "declared", "inputs": {"type": "object", "properties": {"fields": {"type": "object"}, "errors": {"type": "array", "items": {"type": "string"}}}}, "exits": [{"name": "done", "schema": {"type": "object"}}]}}
  },
  "interface": {"commit": null, "inputs": {"type": "object", "properties": {"pdf_path": {"type": "string", "x-wynd-type": "path"}}, "required": ["pdf_path"]}, "outputs": {"done": {"type": "object", "properties": {"record": {"type": "object"}}}, "not_an_invoice": {"type": "object", "properties": {}}, "needs_review": {"type": "object", "properties": {}}}, "examples": [{"inputs": {"pdf_path": "examples/inv1.pdf"}, "exit": "done"}]},
  "available_local": [],
  "conventions": {"local_use": "./steps/{name}", "local_proto_path": "processes/process_supplier_invoice/proto/{name}.yaml"},
  "validation": {"ok": true, "issues": [{"severity": "warning", "code": "max_traversals_filled", "message": "max_traversals defaulted to 10 on validate.done → fix and fix.done → validate (on a cycle)", "file": "processes/process_supplier_invoice/process.yaml", "loc": ["edges", 3, "to", 1, "limits", "max_traversals"], "span": null}]},
  "locked_by": null
}
```

A save request after editing the second branch (index 1) and blurring out:

```json
{"writes": [{"path": "processes/process_supplier_invoice/process.yaml", "base_revision": "sha256:5e0f…",
             "doc": {"…": "full process doc"}}],
 "commit": {"reason": "blur", "summary": "edit branch validate.done[1]"}}
```

It returns:

```json
{"files": [{"path": "processes/process_supplier_invoice/process.yaml", "revision": "sha256:77c1…", "yaml": "kind: process\n…"}],
 "validation": {"ok": true, "issues": []}, "steps": {"…": "StepInfo by key"}, "interface": {"…": "ProcessInterface"},
 "commit": {"sha": "c3d4e5f…", "message": "design(process_supplier_invoice): edit branch validate.done[1]"},
 "head": "c3d4e5f…"}
```

`POST /api/expressions/validate`:

```json
{"process_id": "process_supplier_invoice", "process": {"…": "in-memory doc"}, "loc": ["edges", 3, "to", 0, "with", "dest"],
 "expr": "if steps.validate.outputs.fields.totl > 10000 then env.REVIEW_DIR else env.RECORDS_DIR", "scope": true}
```

It returns:

```json
{"ok": false,
 "errors": [{"message": "steps.validate.outputs.fields has no field 'totl' (did you mean 'total'?)", "start": 3, "end": 37}],
 "warnings": [],
 "scope": ["process.inputs.pdf_path", "run.id", "previous.outputs", "previous.summary",
           "steps.read.outputs.text", "steps.read.exit", "steps.read.runs",
           "steps.extract.outputs.invoice_number", "steps.extract.outputs.total", "steps.extract.outputs.currency",
           "steps.extract.outputs.due_date", "steps.validate.outputs.valid", "steps.validate.outputs.fixable",
           "steps.validate.outputs.fields", "steps.validate.outputs.errors", "steps.validate.outputs.record",
           "steps.fix.runs", "edges[\"validate.done\"][0].taken", "edges[\"validate.done\"][1].taken",
           "env.REVIEW_DIR", "env.RECORDS_DIR"]}
```

The compile job while awaiting input:

```json
{
  "id": "job_01J9R2", "kind": "compile", "process_id": "process_supplier_invoice",
  "ref": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678", "status": "awaiting_input",
  "created_at": "2026-09-22T10:01:00Z", "started_at": "2026-09-22T10:01:01Z", "finished_at": null,
  "branch": null, "result_commit": null, "build": null, "integration": null, "error": null,
  "usage": {"input_tokens": 48211, "output_tokens": 9120, "cost_usd": 0.41, "latency_ms": 71200},
  "chat_id": "chat_01J9R2",
  "session": {
    "state": "awaiting_input",
    "steps": [
      {"step": "read", "use": "./steps/read_pdf", "phase": "skipped", "decision": null, "tests": null, "attempts": 0, "skipped_reason": "proto-step unchanged"},
      {"step": "fix", "use": "./steps/fix_fields", "phase": "testing",
       "decision": {"kind": "agentic", "tier": "cheap", "thinking": "low", "reason": "Correcting OCR-garbled totals needs judgement about which digits are wrong; no pure function of the inputs reproduces examples 2 and 3.", "split": null},
       "tests": {"passed": 2, "failed": 1, "total": 3}, "attempts": 2, "skipped_reason": null}
    ],
    "questions": [
      {"id": "q1", "kind": "clarification", "step": "fix", "text": "When the currency is missing, should fix_fields assume GBP or escalate?", "status": "pending", "answer": null, "asked_at": "2026-09-22T10:03:10Z"},
      {"id": "q2", "kind": "example_proposal", "step": "fix", "text": "What should happen if due_date is missing?",
       "proposed": {"inputs": {"fields": {"invoice_number": "INV-9", "total": 10, "currency": "GBP"}, "errors": ["due_date missing"]}, "outputs": {"invoice_number": "INV-9", "total": 10, "currency": "GBP", "due_date": null}, "exit": "done"},
       "status": "pending", "answer": null, "asked_at": "2026-09-22T10:03:10Z"}
    ],
    "inferred_schemas": {},
    "events": [{"at": "2026-09-22T10:02:40Z", "type": "test_run", "step": "fix", "text": "2/3 examples pass after revision 2"}]
  }
}
```

Answering the proposal with a correction:

```json
{"question_id": "q2", "decision": "correct",
 "example": {"inputs": {"fields": {"invoice_number": "INV-9", "total": 10, "currency": "GBP"}, "errors": ["due_date missing"]}, "exit": "cannot_fix"}}
```

A release:

```json
{"id": "rel_01J9Q", "process_id": "process_supplier_invoice", "commit": "9f8e7d6c5b4a39281706f5e4d3c2b1a098765432", "short": "9f8e7d6",
 "image": "localhost:5001/wynd/process_supplier_invoice:9f8e7d6", "behind": 3,
 "trigger": {"kind": "schedule", "cron": "0 7 * * 1-5", "timezone": "Europe/London", "inputs": {"pdf_path": "/inbox/latest.pdf"}},
 "env": {"RECORDS_DIR": {"value": "/data/records"}, "REVIEW_DIR": {"value": "/data/review"},
         "CLAUDE_CODE_OAUTH_TOKEN": {"from_env": "CLAUDE_CODE_OAUTH_TOKEN"}},
 "enabled": true, "state": "serving", "state_detail": null, "created_at": "2026-09-20T16:00:00Z",
 "next_fire_at": "2026-09-23T06:00:00Z", "webhook_url": null}
```

All of these are stored as fixtures (P-FIXTURES).

---

## 13. Interfaces consumed (for the synthesizer to reconcile)

Each is named so the owning architect can confirm it or change the name in one place.

| Id | From | What web needs |
|---|---|---|
| **C-CTL-API** | controller | Every route in §12.1 with the DTOs in §12.2 and the semantics in §12.3–12.5. |
| **C-CTL-STATIC** | controller | `web_dist_dir()`/`mount_web()` per §3.5, and the hatch `artifacts` entry for `src/wynd/controller/web_dist/**`. |
| **C-CTL-COMMIT** | controller | Design save and commit scoped to one process's design paths, with message format §12.3.6. |
| **C-CTL-CHAT-COMMIT** | controller | At the end of a chat turn whose write tools edited the acting-on process, commit those paths (same format with `Wynd-Origin: chat`) and emit a `commit` item plus `turn.edited`. Reject design saves with 423 while such a turn runs. Expose `DesignDoc.locked_by`. |
| **C-CTL-CHAT-TOOLS** | controller | The chat agent is backed by the `claude-code` provider, by default through the dev key or the logged-in CLI. Tool items carry `write` and `acting_on`. Write tools refuse when `acting_on` is null or when they target another process. Deltas are streamed if the SDK exposes partial messages, otherwise one `item` at the end. |
| **C-CTL-STATUS** | controller | `ProcessStatus` computed per §11 over the reference closure (flags, `tests`, `design_steps`, and `releases[].behind`, which counts closure commits between the release commit and HEAD). |
| **C-CTL-SEARCH** | controller | The search semantics in §6.1. |
| **C-CTL-INTEGRATE** | controller | Idempotent `POST /jobs/{id}/integrate` implementing §6.5 fast-forward, rebase or PR branch, returning `Integration`. `jobs?active=true` includes succeeded-but-unintegrated compile and test_live jobs. |
| **C-CTL-ANSWERS** | controller + compiler | `AnswerRequest` union. Resubmit the job when no pending questions remain. |
| **C-CTL-UPLOAD** | controller | `POST /api/uploads` stores the bytes under `.wynd/uploads/<sha256>/<filename>` and returns a path that local runs can read. For image and release runs it copies the file into the run workspace and rewrites the input value. |
| **C-CTL-RUNS** | controller | `POST /api/runs` for local and image targets. The run SSE replays persisted trace events from `since` and ends with `end`. |
| **C-PROC-1** | process | Step resolution yielding `StepInfo` (kind, phase, lock summary, interface JSON Schemas, used_by) for local, root and `process:` refs. The design scope (writable paths) for a process. |
| **C-PROC-2** | process | Validator `Issue{severity, code, message, file, loc, span}` where `loc` is a path into the **normalized** document of `file` (edges always as `to: [branches]`; shorthand edge-level `with`/`limits` reported as `to[0].with`/`to[0].limits`). |
| **C-PROC-3** | process | `expr_scope(process_doc, loc) -> list[str]`: the exit-aware set of references valid at a `when`/`with`/`limits` location, and `check_expr(process_doc, loc, expr) -> (errors, warnings)` with character spans. |
| **C-PROC-4** | process | `max_traversals` filling happens at load or validate time **in memory** and is **not written back** to `process.yaml`. The web shows it as a placeholder. |
| **C-SPEC-1** | spec | YAML load and dump used by the controller for design files. It must round-trip data without loss through JSON: load with a SafeLoader **without the timestamp resolver** so dates stay strings (`2026-10-01` → `"2026-10-01"` → dumped unquoted), dump with `sort_keys=False`, and write `exit_codes` keys that are decimal-digit strings as ints (or have the model accept both). |
| **C-SPEC-2** | spec | Proto type strings → JSON Schema (`path` → `{"type":"string","x-wynd-type":"path"}`, `date` → `{"type":"string","format":"date"}`) and the vocabulary list for `meta.proto_types`. |
| **C-SPEC-3** | spec | Structural `infer_schema(examples) -> Interface` (deterministic), used when a proto has examples but no schemas (`interface.source = "inferred"`). If spec or compiler does not provide it, the controller returns `source: null` and the UI says the compiler will infer it. |
| **C-SPEC-4** | spec | Branch target key is `step`, and its value may be `$exit.<name>`. Step names are identifiers. The expression language string literals use `"…"` and/or `'…'` with backslash escapes (the lexer handles both). |
| **C-SPEC-5** | spec | Limit field names (`max_traversals`, `timeout`, `retries`), exposed via `meta.limit_fields`. |
| **C-SPEC-6** | spec / M5 | Edge `kind` values (`meta.edge_kinds`). Agentic-edge fields are edited through the generic extra-fields editor until M5 defines them. |
| **C-RT-TRACE** | runtime | Trace event fields: `seq, ts, run_id, mode, type, path` on all events, plus the per-type fields listed in §12.5 (`step_started{step, kind, attempt, inputs}`, `step_finished{step, exit, outputs, summary, duration_ms, usage, attempts}`, `edge_taken{from, branch, name, to}`, `run_started{process, commit, inputs}`, `run_finished{exit, outputs, error, duration_ms, usage}`, `model_call{provider, model, usage}`, `tool_call{tool, args, ok, duration_ms}`, `log{level, message}`). Child events carry the parent `run_id` and a dotted `path`. |
| **C-COMP-SESSION** | compiler | `CompileSession` JSON (§12.2) as the serialised session in the job record: per-step `decision` (with `split` for the two-step fallback), `questions` (clarification and example_proposal with `proposed` example), `inferred_schemas`, and `events`. |
| **C-CLI-1** | cli | `wynd serve-api` binds `127.0.0.1:8780` by default (`--host`, `--port`), runs from a workspace (found by `wynd.yaml` upward search), and mounts the web bundle (C-CTL-STATIC). |

## 14. Interfaces provided

| Id | To | What |
|---|---|---|
| **P-BUNDLE** | controller, cli | The static bundle (`index.html`, `assets/`) written by `npm --prefix packages/web run build` into `packages/controller/src/wynd/controller/web_dist/`. It only needs same-origin `/api`. |
| **P-HTTP-SPEC** | controller | §12 as the normative web-facing API spec, including the SSE formats. |
| **P-FIXTURES** | controller tests | `packages/web/src/api/fixtures/*.json`: one golden payload per DTO, with `index.json` mapping `file → DTO name` (for example `{"design.process_supplier_invoice.json": "DesignDoc", "job.compile.awaiting.json": "Job", …}`). The controller contract test (`packages/controller/tests/test_web_contract.py`, owned by controller) validates each fixture against its pydantic response model with `Model.model_validate(json)`. Web tests use the same fixtures, so drift fails on both sides. |
| **P-COMMIT-SEMANTICS** | controller | `SaveRequest.commit.{reason, summary}`, the boundary reasons, and the rule that the web never asks for a commit mid-typing. |
| **P-ACTING-ON** | controller | `SendMessageRequest.acting_on`: the open process id or null, sent with every message and never stored as a chat property. |

---

## 15. Interpretations (where the spec is ambiguous)

1. **"Blur after a manual edit"** means focus leaving the design surface (canvas, inspector and settings/proto editors), the window losing focus, or the tab going hidden. Tabbing between fields inside the surface is not a boundary. Explicit boundaries are also added before process switch, job submission, chat send, integration and unload. There is no idle-timer commit.
2. **"End of a chat turn that edited something"**: the controller commits, since it performed the edits. The web commits its own pending edits **before** sending and locks the editor for the duration of the turn, which avoids two writers.
3. **"Never spanning two processes"**: each commit is scoped by the controller to one process's design paths via pathspec. Editing a shared step-root proto from process A is allowed. It is in A's closure and design scope, is committed under A, and a warning is shown. Compiled source is never edited in the web (§11: "code review of compiled steps happens in git").
4. **YAML round-trip**: the browser edits JSON docs. The controller dumps YAML. Comments and flow style are lost, which is allowed. Data, key order and unknown keys are preserved because ops patch paths in place. Dates survive because of C-SPEC-1.
5. **Shorthand edges** stay shorthand until an op needs a list (second branch, `when`, `name`). Lists are never collapsed back.
6. **New branch on an edge that already has an else** is inserted before the else with `when: ""` and focused. An empty condition is shown as a validation hint, and saving it is allowed (design may be mid-edit).
7. **Validation never blocks autosave**. The validator report is advisory in design. Compile and build still gate on it.
8. **"Explicitly ignored" exits (§8)**: the spec defines no syntax. The UI shows the validator's "unrouted exit" issue and offers "Route to…". Nothing is invented.
9. **Integration** is triggered by the web through an idempotent controller endpoint when it observes a succeeded compile or live-test job ("the CLI or web integrates it").
10. **Compile/build "chat bound to the session"** is a normal chat whose first item is a job card rendered from the job record. Questions are answered inline, and the controller resubmits after the last pending answer. The composer still talks to the assistant.
11. **Status display**: the four flags are shown as independent badges, never merged into one phase. A "tests failing" badge is added only when neither design nor compiled holds and tests failed. Every release shows `released <commit>` with `HEAD` or `N behind`.
12. **Search** is server-side. Terms are ANDed, and flag filters are ANDed.
13. **Node positions** are auto-laid out, with per-browser drag overrides in localStorage. `process.yaml` never gets UI keys.
14. **Removing a step** removes its edges and branches that target it. It optionally deletes an unshared local proto file, and it never deletes compiled source.
15. **Run targets**: local at committed HEAD, image of a build, or a release. Local runs commit pending design first.
16. **Webhook trigger** body = the process inputs JSON. **Env binding** kinds are `value` and `from_env`. Secrets are `from_env` only (§4 "nothing baked in"). A Kubernetes trigger backend resolves `from_env` from its pod env (Secrets).
17. **Limits values** that look numeric are written as YAML numbers. Everything else is written as a string (expression or duration).
18. **`use:` forms**: §5.1 lists three and §13 says "four". The UI treats the §5.1 three as complete, and the server validator is authoritative.
19. **Tests "unknown"**: the web offers no replay-test button. Build runs tests (§6.5), and `wynd test` exists in the CLI. This keeps M4 to the spec's list.

---

## 16. File list with owners

Owners are four parallel web agents. **web-core** covers the shell, API, state and theming. **web-graph** covers the design model, graph, inspector, editors and autosave. **web-chat** covers chats, job cards and the job watcher. **web-ops** covers the selector/search/status, releases and runs. Files outside `packages/web` are owned by the named Python owners.

| Path (under `packages/web/`) | Owner | Contents |
|---|---|---|
| `LICENSE`, `README.md`, `package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`, `index.html` | web-core | §3 |
| `src/main.tsx` | web-core | Theme bootstrap (applied before React renders, to avoid a flash), CSS imports, `createRoot`. |
| `src/App.tsx` | web-core | Providers (Api, Meta, Design), layout grid, tabs, `ConnectionBanner`, `JobWatcher` mount, toasts. |
| `src/styles/tokens.css`, `src/styles/app.css` | web-core | §4.7. Component sections are appended by each owner under their prefix: `.wg-*` graph, `.wc-*` chat, `.wo-*` ops. |
| `src/api/types.ts` | web-core | §12.2 (verbatim) |
| `src/api/client.ts` | web-core | §4.4 |
| `src/api/sse.ts` | web-core | §4.5 |
| `src/api/context.tsx` | web-core | `ApiContext`, `useApi()`, `MetaContext`, `useMeta()` |
| `src/api/fixtures/*.json`, `src/api/fixtures/index.json` | web-core (payloads authored with each area's owner) | §12.6 and P-FIXTURES: `meta`, `processes.list`, `design.process_supplier_invoice`, `save.result`, `expr.check`, `steps.catalog`, `providers`, `job.compile.awaiting`, `job.compile.integrated`, `job.build.succeeded`, `chat.snapshot`, `chat.events.txt`, `runs.list`, `run.finished`, `run.events.txt` (incl. a nested child and a validate↔fix loop), `builds`, `releases`, `envcheck` |
| `src/state/store.ts`, `src/state/query.ts`, `src/state/url.ts`, `src/state/connection.ts`, `src/state/toasts.ts`, `src/state/theme.ts` | web-core | §4.2–4.7 |
| `src/components/shell/Header.tsx`, `Sidebar.tsx`, `StatusBar.tsx`, `ThemeToggle.tsx`, `ConnectionBanner.tsx`, `Tabs.tsx`, `Toasts.tsx` | web-core | Shell. `StatusBar` renders the design save state from the design store. |
| `src/components/common/Dialog.tsx`, `ErrorBox.tsx`, `Spinner.tsx`, `CopyButton.tsx`, `Icon.tsx` (inline SVG set), `JsonView.tsx` | web-core | Shared UI |
| `src/model/json.ts` | web-graph | §7.1 |
| `src/model/processDoc.ts` | web-graph | §7.2 |
| `src/model/protoDoc.ts` | web-graph | §7.9 |
| `src/model/expr.ts` | web-graph | §7.3 |
| `src/model/cycles.ts`, `src/model/layout.ts`, `src/model/graph.ts` | web-graph | §7.4–7.5 |
| `src/model/schemaText.ts`, `src/model/values.ts`, `src/model/issues.ts` | web-graph | §7.10–7.12 |
| `src/state/design.ts` | web-graph | §8 |
| `src/state/openProcess.ts` | web-graph | §8.5 (`openProcess`, `showSwitchBlocked`) |
| `src/components/graph/GraphEditor.tsx`, `StepNode.tsx`, `TerminalNode.tsx`, `InputsNode.tsx`, `BranchEdge.tsx`, `GraphToolbar.tsx`, `AddStepDialog.tsx`, `RenameStepDialog.tsx`, `RemoveStepDialog.tsx`, `DesignSurface.tsx`, `ConflictBanner.tsx`, `positions.ts` | web-graph | §7.6, §8.3–8.4 |
| `src/components/inspector/Inspector.tsx`, `ProcessOverview.tsx`, `StepInspector.tsx`, `EdgeInspector.tsx`, `BranchCard.tsx`, `WithMappingEditor.tsx`, `LimitsEditor.tsx`, `ExpressionInput.tsx`, `TerminalInspector.tsx`, `InputsInspector.tsx`, `ProtoStepEditor.tsx`, `FieldTableEditor.tsx`, `ExitsEditor.tsx`, `SchemaSummary.tsx`, `ExtraFieldsEditor.tsx` | web-graph | §7.7, §7.9 |
| `src/components/process-settings/ProcessSettings.tsx`, `src/components/yaml/YamlView.tsx` | web-graph | §7.8 |
| `src/components/examples/ExamplesEditor.tsx`, `ExampleCard.tsx`, `ValueInput.tsx` | web-graph | §7.11 (`ValueInput` is reused by web-ops' `SchemaForm`) |
| `src/state/chat.ts`, `src/state/jobs.ts` | web-chat | §9.2, §9.9 |
| `src/components/chat/ChatPanel.tsx`, `ChatList.tsx`, `ChatView.tsx`, `Composer.tsx`, `ActingOnChip.tsx`, `MessageItem.tsx`, `ToolActivity.tsx`, `CommitItem.tsx`, `NoticeItem.tsx`, `Markdown.tsx` | web-chat | §9.1–9.4, §9.10 |
| `src/components/chat/JobCard.tsx`, `JobLogs.tsx`, `CompileSessionView.tsx`, `QuestionCard.tsx`, `ProposalCard.tsx`, `BuildResult.tsx`, `IntegrationOutcome.tsx` | web-chat | §9.6–9.8 |
| `src/model/status.ts` | web-ops | §6.2 |
| `src/components/process/ProcessList.tsx`, `ProcessSearch.tsx`, `StatusBadges.tsx`, `NewProcessDialog.tsx`, `ProcessActions.tsx` | web-ops | §6 (`ProcessActions` calls `design.commit` and `api.processes.compile/build`) |
| `src/model/trace.ts` | web-ops | §11.5 |
| `src/components/runs/RunPanel.tsx`, `RunList.tsx`, `RunForm.tsx`, `SchemaForm.tsx`, `RunDetail.tsx`, `StepRunRow.tsx`, `ProcessErrorView.tsx` | web-ops | §11 |
| `src/components/releases/ReleasesPanel.tsx`, `ReleaseCard.tsx`, `NewReleaseDialog.tsx`, `TriggerEditor.tsx`, `EnvBindingEditor.tsx` | web-ops | §10 |
| `src/test/setup.ts`, `src/test/fakeApi.ts`, `src/test/fakeEventSource.ts`, `src/test/render.tsx`, `src/test/fixtures.ts` | web-core | §17.1 |
| `*.test.ts(x)` colocated next to each module | owner of the module | §17 |

Outside `packages/web`:

| Path | Owner | Contents |
|---|---|---|
| `packages/controller/src/wynd/controller/api/static.py` | controller | C-CTL-STATIC |
| `packages/controller/src/wynd/controller/api/routes_*.py`, `models_web.py` (pydantic response models mirroring §12.2) | controller | C-CTL-API |
| `packages/controller/tests/test_web_contract.py` | controller | Validates every `packages/web/src/api/fixtures/*.json` against its model per `index.json` |
| `packages/controller/tests/test_static_mount.py` | controller | A temp dist with `index.html` is served at `/`. Missing dist gives the hint page. `/api/health` is unaffected. |
| `packages/controller/pyproject.toml` (hatch `artifacts`) | controller | §3.5 |
| root `.gitignore` entries | repo scaffolding | §3.5 |

---

## 17. Test plan

### 17.1 Harness (`src/test/*`)

- **`setup.ts`**: `afterEach(cleanup)`. It installs the React Flow jsdom shims:
  - a no-op `ResizeObserver`
  - `DOMMatrixReadOnly` parsing `scale(n)` into `m22`
  - `HTMLElement.prototype.offsetWidth/offsetHeight` returning `parseFloat(style.width/height) || 1`
  - `SVGElement.prototype.getBBox`

  It also installs `FakeEventSource` as `globalThis.EventSource` via `setEventSourceImpl`, and an in-memory `localStorage` guard.
- **`fakeEventSource.ts`**: records instances with `url` and supports `emit(event, data, id)`, `error()` and `close()`, plus `instances()`.
- **`fakeApi.ts`**: `createFakeApi(overrides?: DeepPartial<Api>): Api & {calls}`. Each method is a `vi.fn` resolving to fixture data by default.
- **`render.tsx`**: `renderApp(ui, {api, url, design})` wraps components in the providers.
- Timers: `vi.useFakeTimers()` in autosave, poll and debounce tests.
- **Offline and deterministic**: no network (fakeApi only), no real timers where timing matters, and fixed `crypto.randomUUID` via `vi.spyOn`.

### 17.2 Unit tests (pure model)

| File | Key cases |
|---|---|
| `model/processDoc.test.ts` | `edgesOf` on the dogfood fixture: 7 edges; `validate.done` has 3 branches, and branch 2 `isElse`. A shorthand edge's `with` maps via `branchLoc`. `routeExit` on an unrouted exit gives shorthand. On a routed exit with an else, the new branch is inserted before the else with `when: ""`. Without an else it is appended as the else. `expandShorthand` moves `with`/`limits` into the branch and keeps `kind` and unknown keys on the edge. `removeStep("fix")` removes `fix.done` and `validate.done[1]`, leaving the other branches. Removing the last branch removes the edge. `entry` and `on_error` are cleared. `renameStep("validate","check")` rewrites the key (position preserved), `from`, targets, all `steps.validate.*` expressions, and `edges["validate.done"]`. It leaves a string literal `"steps.validate"` untouched and reports `rewritten` count. Unknown top-level and branch keys survive every op (`deepEqual` on untouched subtrees). **No-op identity**: applying then undoing a `when` edit returns a deep-equal doc with original key order. `renameProcessExit` updates `$exit` targets and examples. |
| `model/protoDoc.test.ts` | Flat versus nested detection. `exits()` for flat, nested and `exits`-only skeletons. `addExit` converts flat to nested, preserving fields. `renameExit` updates `exits`, `outputs` and `examples[].exit`. `renameField` updates example keys. `removeField` keeps example values. `setExitCode("0","done")` produces string keys. |
| `model/expr.test.ts` | `lex` of the spec's `if/elif/else` example. Unterminated string gives null. Rename cases: `steps.a.outputs.x` changes; `steps . a . exit` (spaces) changes; `x.steps.a` is unchanged; `"steps.a"` literal is unchanged; `edges["a.done"][0].taken` and `edges['a.done'].retry.taken` change; `steps.ab` is unchanged when renaming `a`. `completionPrefix` works mid-token, returns null inside a string, and handles `edges["x.y"].` suffixes. |
| `model/cycles.test.ts` | Dogfood: exactly `validate.done[1]` and `fix.done[0]` are on a cycle. A self-loop is on a cycle. An exit target is never on a cycle. |
| `model/layout.test.ts` | Dogfood layers as in §7.4, with deterministic coordinates (snapshot of the rounded positions map). Back edge `fix→validate`. Unreachable steps are still placed. All terminals share the last layer. The same input always gives the same output (run twice). |
| `model/graph.test.ts` | Dogfood `toFlow`: 6 step nodes, `in`, 3 terminals, 9 branch edges plus `entry`. `extract` has handles `o:done`, `o:not_an_invoice` and `o:error` (implicit). Labels are `"1. if steps.validate.outputs.valid"`, `"2. if steps.validate.outputs.fixable and s…"` and `"3. else"`. A branch after the else is `ignored`. An undeclared `$exit.foo` target gives a `declared:false` terminal. Proto exits in the session override `stepInfo` exits. `readOnly` propagates. |
| `model/schemaText.test.ts` | Each row of the §7.10 table. `stepSentence` for extract gives the exact string in §7.10. Optional fields are marked. Arrays of objects are described. |
| `model/values.test.ts` | `parseLoose` (`"123"`→123, `"abc"`→"abc", `"{\"a\":1}"`→object, `"true"`→true). `parseTyped` for number, integer, date and invalid JSON. `exampleSentence` truncation. |
| `model/issues.test.ts` | Each `loc` rule maps to the right selection. A proto file issue maps to its step. |
| `model/status.test.ts` | Flags map to badges. Released at HEAD shows `· HEAD`. Behind 3 shows `· 3 behind`. `tests_failed` appears only when neither design nor compiled. |
| `model/trace.test.ts` | `run.events` fixture gives the tree: read, extract, validate, fix, validate, save (two `validate` runs, with keys `validate#0` and `validate#1`). `edge_taken` is attached to the right run. Nested `sub.read` sits under `sub`. An orphan child attaches to roots. `run_finished` is captured. Incremental application equals batch application. |

### 17.3 State and component tests

| File | Key cases |
|---|---|
| `api/client.test.ts` | Error envelope becomes `ApiError`. FastAPI 422 maps to `invalid`. A network TypeError gives status 0 and notifies the connection store. `pidPath("finance/invoices")` keeps the slash and encodes segments. |
| `api/sse.test.ts` | Named events are dispatched with parsed data. `end` closes. A malformed JSON event is dropped. The close function works. |
| `state/query.test.ts` | Dedupe. `invalidate` prefix refetch. Poll stops when `pollMs` returns null. Focus refetch. |
| `state/design.test.ts` (fake timers, fakeApi) | 10 `apply` calls within 500ms give **one** save request after 800ms. Continuous typing for 6s gives a save at 5s (maxWait), then one more. **No commit request from `apply`/`save` alone.** `commit("blur")` with dirty docs sends one request containing writes plus commit. `commit` with nothing dirty or uncommitted sends no request. `commit` after a save sends a commit-only request with `writes: []`. A 409 sets `conflict`: "theirs" reloads, and "mine" resends with the new base revision. On a 500 the docs are kept and retried with backoff. `lock` makes `apply` a no-op. A turn end with `edited` triggers `reload`. `opLabels` summary is de-duplicated and capped with `+N more`. A proto `renameExit` across two files gives one request with two writes. Structural ops set `issuesStale`. |
| `components/graph/DesignSurface.test.tsx` | Focus moving between two inputs inside gives no commit. Focus moving to a button outside gives `commit("blur")`. `visibilitychange` hidden gives `commit("hidden")`. A dialog inside the surface does not commit. |
| `components/graph/GraphEditor.test.tsx` | Renders the dogfood fixture (node labels present, and the `aria-label` of `extract`). Selecting an edge opens the inspector for `validate.done` with 3 branch cards. The read-only lock hides connect handles. The delete flow calls `removeStep` via the confirm dialog, with the proto-delete checkbox shown only for unshared local protos. Drag-connect is simulated by calling the `onConnect` prop through a test hook (`GraphEditor` exports `handleConnect` for tests), because pointer drags in jsdom are unreliable. |
| `components/inspector/BranchCard.test.tsx` | Toggling "Otherwise" deletes and restores `when`. Up and down reorder the branches. Target select retargets. A name is written and cleared. |
| `components/inspector/ExpressionInput.test.tsx` | Validation is debounced (300ms). An out-of-order response is ignored. An error renders with `aria-invalid` and a column. Scope suggestions filter by prefix, and Enter or Tab inserts. Esc closes. Suggestions are hidden inside a string literal. |
| `components/inspector/WithMappingEditor.test.tsx` | Rows per target input with types in plain language. Extra keys are flagged. Clearing an input deletes the key. The shorthand edge writes edge-level `with`. |
| `components/inspector/ProtoStepEditor.test.tsx` | Editing the instruction calls `apply`. Add exit converts flat to nested. The exit rename also updates the process edge `from`. The shared banner shows `used_by`. The plain-language summary renders from the fixture interface. |
| `components/examples/ExamplesEditor.test.tsx` | Typed inputs (date and number) write typed values. Invalid JSON in an object field shows an error without propagating. The exit change swaps the output fields. Values not in the schema are flagged and removable. |
| `components/chat/ChatView.test.tsx` | The snapshot renders. `delta` events stream text into the assistant item (`aria-busy`). Tool items group, and a write tool shows its acting-on chip. `reset` refetches. Send calls `design.commit("before_chat")` **before** `chats.send` with `acting_on` = the open process (null when none). The composer is disabled while running and Stop calls cancel. |
| `components/chat/JobCard.test.tsx` | Awaiting fixture: decisions table, the split badge explainer, and 2 pending questions. Answering the clarification posts `{question_id, text}`. Confirm and Correct on a proposal post the right payloads (Correct with an edited example). The integration outcome renders all 4 modes. Build result shows [Create release…]. |
| `state/jobs.test.ts` | A succeeded compile with null integration gives `design.commit("before_integrate")` (only when the open process matches), then `integrate` once even across repeated polls, then invalidation. `dirty_tree` retries once. A failed job gives a toast. |
| `components/process/ProcessList.test.tsx` | Search is debounced (250ms), with query params `q` and `flags`. Filter chips toggle. Badges show `released 9f8e7d6 · 3 behind` together with `design`. `<mark>` highlighting appears. Clicking calls `openProcess`. |
| `components/process/ProcessActions.test.tsx` | Build is disabled with a tooltip listing design steps. Compile commits and then opens the returned chat. `dirty_tree` shows the paths dialog. |
| `components/runs/RunPanel.test.tsx` | The form is generated from the interface (a path field has Upload, which posts the file and fills the returned path). Fill-from-example works. Start posts the target. The SSE fixture renders the step tree with nested child and loop runs. Expanding a step shows inputs, outputs, summary and usage. The ProcessError view renders on the error exit. |
| `components/releases/ReleasesPanel.test.tsx` | No builds shows the "Only built processes can be released" hint. Create posts trigger and env. Secret vars cannot use `value`. Run now triggers and navigates to the run. The env-check missing list renders. |
| `App.test.tsx` | Smoke test. With the controller unreachable, the connection banner shows and recovers. A theme toggle cycle sets `data-theme`. URL state round-trips (`?process=…&tab=runs&run=…` opens that run). |

### 17.4 Why no Playwright

The web is a thin view over a contract that is tested on both sides by shared fixtures. The pure model modules (ops, layout, trace and autosave) carry the risk and are unit-tested. The component tests cover the interactions that matter through RTL. A browser E2E suite would need a live controller plus git plus (for compile) an LLM, which makes it neither offline nor deterministic. The M4 acceptance run below is manual.

### 17.5 Commands and CI

- `npm --prefix packages/web ci && npm --prefix packages/web run check` runs typecheck, vitest and build.
- The controller's pytest (`uv run pytest packages/controller`) includes `test_web_contract.py`. It needs no Node.

### 17.6 M4 manual acceptance (dogfood)

Run these in `examples/invoices` with `uv run wynd serve-api` and open http://127.0.0.1:8780.

1. `process_supplier_invoice` shows its badges. The graph matches §7.4: validate⇄fix loop, 3 terminals, and "10 (auto)" on the cycle branches.
2. Edit branch 2's `when` and tab through fields. `git log` shows no commit. Click into the chat composer and exactly one `design(process_supplier_invoice): edit branch validate.done[1]` commit appears.
3. A typo `totl` in `with.dest` shows an inline error from the controller.
4. Chat "add a step that emails finance on escalation" (claude-code provider). Tool activity shows. The editor locks, then the turn commits, the graph reloads, and the design badge appears.
5. Compile streams per-step decisions. Answer a question and confirm a proposal. The job integrates by fast-forward and the badge becomes compiled.
6. Build shows the image, then create a manual release and Run now with an uploaded `inv1.pdf`. The trace streams, and each step's inputs, outputs and summary are inspectable.
7. Edit the design again. The header shows `[design] [released <X> · 1 behind]`.

---

## 18. Out of scope for web (explicit)

- A compiled-source viewer or editor (code review happens in git, §11).
- Running replay tests from the UI, and M5 `optimise` suggestions (a CLI concern, with no spec requirement for UI).
- An MCP or provider registry UI. `wynd mcp` and `wynd provider` stay in the CLI. §3.8 mentions "the equivalent OAuth flow in the web UI" for MCP. That is noted as a follow-up, and the provider dropdown reads the registry.
- Multi-user, auth, and telemetry. The UI sends none.

## 19. Risks

- **React Flow in jsdom** is brittle for drag gestures. Mitigation: gesture handlers are thin wrappers over tested pure ops, and tests call handlers directly.
- **Stale validator locs after structural edits.** Mitigation: `issuesStale` hides attachments until the next save.
- **SSE through proxies.** Mitigation: the required headers (§12.0), and replay by `since` or `Last-Event-ID`.
- **C-SPEC-1 date and int-key round-trip** is the one data-loss risk in the pipeline. It must be implemented by the spec owner, and a controller test (`test_design_roundtrip.py`, controller-owned) should load, JSON-encode, save and reload the dogfood files and compare them.
