# @wynd/web

The Wynd web UI: a pure view over the controller HTTP API served by `wynd serve-api` (React 19, Vite 8,
TypeScript, `@xyflow/react` 12). It imports nothing from Python and never parses YAML.

## Commands (from the repository root)

```sh
npm --prefix packages/web ci                  # install the exact versions in package-lock.json
npm --prefix packages/web run dev             # http://127.0.0.1:5173, /api proxied to 127.0.0.1:8780 (WYND_API_URL overrides)
npm --prefix packages/web run typecheck       # tsc --noEmit
npm --prefix packages/web run test            # vitest (jsdom), offline
npm --prefix packages/web run test -- src/model/trace.test.ts   # selected test files, relative to packages/web
npm --prefix packages/web run build           # bundle into packages/controller/src/wynd/controller/web_dist/
npm --prefix packages/web run check           # typecheck + test + build
```

Run vitest through `npm run test` (or `vitest --root packages/web`): `npm exec` keeps the caller's working
directory, where vitest would not find `vite.config.ts` (jsdom, setup file, React plugin).

In a workspace, `uv run wynd serve-api` serves the built bundle at http://127.0.0.1:8780.

## Tests

- `src/test/setup.ts`: jsdom shims for React Flow, `FakeEventSource` as the global `EventSource`, in-memory storage.
- `src/test/fakeApi.ts`: `createFakeApi(overrides)`, every method a `vi.fn` answering from the fixtures.
- `src/test/render.tsx`: `renderApp(ui, {api, url, design, meta})` inside the app's providers.
- `src/api/fixtures/`: one golden payload per controller DTO. `index.json` maps each JSON file to its DTO name
  (`"Model"`, or `"Model[]"` for a JSON array of it); `packages/controller/tests/test_web_contract.py` validates
  every file against that pydantic model. `chat.events.txt` is a raw chat SSE transcript.

`src/api/types.ts` mirrors `wynd.controller.models` and `wynd.controller.api.models_web` (PLAN §3.21).

Licence: AGPL-3.0-or-later (`LICENSE`).
