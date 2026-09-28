"""Release routes and webhooks: `/api/releases/*`, `POST /hooks/releases/{id}` (PLAN §3.21 amendment 5;
`$DRAFTS/06 §8.3` routes 34-39 and the (+) release routes).

`POST /api/releases/{id}/trigger` takes `{"inputs"?, "source"?: "manual"|"schedule"}` (kube CronJobs send
`{"source": "schedule"}`). A webhook presents its secret as `Authorization: Bearer <secret>` or `X-Wynd-Token` and is
checked before its body is read (401 wrong secret, 404 unknown release or no webhook trigger, 409 disabled); the body
is the run's inputs, a JSON object (empty body: `{}`; not JSON: 415; JSON but not an object: 422).
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Request, Response
from starlette.concurrency import run_in_threadpool

from wynd.controller.api.app import Ctl, UnsupportedMediaType
from wynd.controller.api.models_web import CreateReleaseRequest, ReleasePatch, TriggerRequest
from wynd.controller.errors import Invalid

router = APIRouter()


@router.get("/api/releases")
def list_releases(ctl: Ctl, process_id: str | None = None):
    return {"releases": ctl.releases.list(process_id=process_id or None)}


@router.post("/api/releases", status_code=201)
def create_release(body: CreateReleaseRequest, ctl: Ctl):
    return ctl.releases.create(body)


@router.patch("/api/releases/{release_id}")
def update_release(release_id: str, body: ReleasePatch, ctl: Ctl):
    return ctl.releases.update(release_id, body)


@router.delete("/api/releases/{release_id}", status_code=204)
def delete_release(release_id: str, ctl: Ctl):
    ctl.releases.delete(release_id)
    return Response(status_code=204)


@router.post("/api/releases/{release_id}/trigger", status_code=201)
def trigger_release(release_id: str, ctl: Ctl, body: TriggerRequest | None = None):
    body = body or TriggerRequest()
    return ctl.releases.trigger(release_id, body.inputs, source=body.source)


@router.get("/api/releases/{release_id}/env-check")
def release_env_check(release_id: str, ctl: Ctl):
    return ctl.releases.env_check(release_id)


@router.get("/api/releases/{release_id}/fires")
def release_fires(release_id: str, ctl: Ctl, limit: int = 50):
    return {"fires": ctl.releases.fires(release_id, limit)}


@router.post("/hooks/releases/{release_id}", status_code=201)
async def webhook(release_id: str, request: Request, ctl: Ctl):
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    presented = value.strip() if scheme.lower() == "bearer" else request.headers.get("x-wynd-token")
    await run_in_threadpool(ctl.releases.verify_webhook, release_id, presented)
    inputs = _inputs(await request.body())
    return await run_in_threadpool(ctl.releases.trigger, release_id, inputs, source="webhook")


def _inputs(body: bytes) -> dict:
    if not body.strip():
        return {}
    try:
        inputs = json.loads(body)
    except ValueError:
        raise UnsupportedMediaType("a webhook body must be JSON (the process inputs as an object)") from None
    if not isinstance(inputs, dict):
        raise Invalid("a webhook body must be a JSON object of process inputs")
    return inputs
