"""The FastAPI app factory internals: auth, CORS, error envelope, lifespan, router order (PLAN §3.21;
`$DRAFTS/06 §8.1`). Stub; CTL-API.

Includes `wynd.controller.optimise.router` and every suffixed `{process_id:path}` route before
`GET /api/processes/{process_id:path}`; the static web bundle is mounted last.
"""
