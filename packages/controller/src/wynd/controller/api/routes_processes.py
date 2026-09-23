"""Process routes: list/create/get, design, builds, interface, status, validate, test, history, files, env
(PLAN §3.21 incl. amendment 14; `$DRAFTS/06 §8.3`). Stub; CTL-API.

Suffixed `{process_id:path}` routes are registered before `GET /api/processes/{process_id:path}`.
"""

from fastapi import APIRouter

router = APIRouter()
