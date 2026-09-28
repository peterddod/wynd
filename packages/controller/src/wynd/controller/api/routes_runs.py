"""Run routes: create, list, get, events SSE (`id: <seq>`, `event: trace`, then `event: end`) (PLAN §3.21 amendment 4;
`$DRAFTS/06 §8.3` routes 29-32).

`POST /api/runs` starts the run in the background (`RunService.start`, trigger `api`); the env gate (412
`env_missing`) and the input check (422 `invalid`) answer before any run exists. The event stream sends every PLAN
§3.13 event with `seq > since` unchanged, then `end {}` once the run record is finished (the executor writes `run.end`
before it finishes the record, so the stream always carries it).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from wynd.controller.api.app import Ctl
from wynd.controller.api.sse import Frame, event_stream, resume_cursor
from wynd.controller.controller import Controller
from wynd.controller.models import CreateRunRequest
from wynd.controller.runs.service import TERMINAL

router = APIRouter()


@router.post("/api/runs", status_code=201)
def create_run(body: CreateRunRequest, ctl: Ctl):
    return ctl.runs.start(body)


@router.get("/api/runs")
def list_runs(ctl: Ctl, process_id: str | None = None, release_id: str | None = None, limit: int = 50):
    return {"runs": ctl.runs.list(process_id=process_id or None, release_id=release_id or None,
                                 limit=limit)}


@router.get("/api/runs/{run_id}")
def get_run(run_id: str, ctl: Ctl):
    return ctl.runs.get(run_id)


@router.get("/api/runs/{run_id}/events")
async def run_events(run_id: str, request: Request, ctl: Ctl, since: int = 0):
    await run_in_threadpool(ctl.runs.get, run_id)
    return event_stream(request, _run_frames(ctl, run_id), resume_cursor(request, since))


def _run_frames(ctl: Controller, run_id: str):
    def fetch(since: int) -> tuple[list[Frame], bool]:
        finished = ctl.runs.get(run_id).status in TERMINAL          # read before the events
        frames: list[Frame] = [(event["seq"], "trace", event) for event in ctl.runs.events(run_id, since)]
        if finished:
            frames.append((frames[-1][0] if frames else since, "end", {}))
        return frames, finished

    return fetch
