"""Meta routes: health, meta, steps, expressions, uploads (PLAN §3.21; `$DRAFTS/06 §8.3` routes 1, 2, 8, 9, 33).

Uploads take the raw body with the file name in `X-Wynd-Filename` (percent-decoded, so a client can send non-ASCII
names), capped at 100 MB (413 `too_large`), and answer 201 `{"path"}`.
"""

from __future__ import annotations

from urllib.parse import unquote

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from wynd.controller.api.app import Ctl, TooLarge
from wynd.controller.api.models_web import UploadResult
from wynd.controller.errors import Invalid
from wynd.controller.models import ExprCheckRequest

MAX_UPLOAD_BYTES = 100 * 1024 * 1024

router = APIRouter()


@router.get("/api/health")
def health():
    return {"ok": True}


@router.get("/api/meta")
def meta(ctl: Ctl):
    return ctl.meta()


@router.get("/api/steps")
def step_catalog(ctl: Ctl):
    return {"steps": ctl.processes.step_catalog()}


@router.post("/api/expressions/validate")
def validate_expression(body: ExprCheckRequest, ctl: Ctl):
    return ctl.processes.check_expr(body)


@router.post("/api/uploads", status_code=201)
async def upload(request: Request, ctl: Ctl):
    name = unquote(request.headers.get("x-wynd-filename", "")).strip()
    if not name:
        raise Invalid("an upload needs its file name", hint="send the file's name in the X-Wynd-Filename header")
    length = request.headers.get("content-length", "")
    if length.isdigit() and int(length) > MAX_UPLOAD_BYTES:
        raise _too_large()
    data = bytearray()
    async for chunk in request.stream():
        data += chunk
        if len(data) > MAX_UPLOAD_BYTES:
            raise _too_large()
    path = await run_in_threadpool(ctl.uploads.put, name, bytes(data))
    return UploadResult(path=str(path))


def _too_large() -> TooLarge:
    return TooLarge(f"uploads are limited to {MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
