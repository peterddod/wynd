"""Job routes: compile/build/test-live/bake submit, jobs list/get/logs/answers/integrate/cancel/events/artefacts
(PLAN §3.21; `$DRAFTS/06 §8.3` routes 11, 12, 15-20 and the (+) job routes, §7.6).

The compile, build and test-live buttons open a job-bound chat (`$DRAFTS/06 §7.6`): submit, create the chat with the
job's id, store the chat id on the job record, add the chat's first item (the job) -> 201 `{job, chat}`. Bake answers
201 `Job`. The job event stream (`?offset=` byte offset of the log) sends `log {text, offset}`, `status {status}` on
each change and `end {job}` once the job is finished or awaiting input.
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from wynd.controller.api.app import Ctl
from wynd.controller.api.models_web import AnswerRequest, JobWithChat, TestLiveRequest
from wynd.controller.api.sse import Frame, event_stream, resume_cursor
from wynd.controller.controller import Controller
from wynd.controller.jobs import records
from wynd.controller.models import Job

PROCESS = "/api/processes/{process_id:path}"
SETTLED = frozenset({"succeeded", "failed", "cancelled", "awaiting_input"})    # a job's stream ends: finished or asking

router = APIRouter()


@router.post(f"{PROCESS}/compile", status_code=201)
def compile_process(process_id: str, ctl: Ctl):
    return _with_chat(ctl, ctl.jobs.submit_compile(process_id), f"Compile {process_id}")


@router.post(f"{PROCESS}/build", status_code=201)
def build_process(process_id: str, ctl: Ctl):
    return _with_chat(ctl, ctl.jobs.submit_build(process_id), f"Build {process_id}")


@router.post(f"{PROCESS}/test-live", status_code=201)
def test_live(process_id: str, ctl: Ctl, body: TestLiveRequest | None = None):
    job = ctl.jobs.submit_test_live(process_id, steps=None if body is None else body.steps)
    return _with_chat(ctl, job, f"Test {process_id} live")


@router.post(f"{PROCESS}/bake", status_code=201)
def bake_process(process_id: str, ctl: Ctl):
    return ctl.jobs.submit_bake(process_id)


@router.get("/api/jobs")
def list_jobs(ctl: Ctl, process_id: str | None = None, active: bool = False):
    return {"jobs": ctl.jobs.list(process_id=process_id or None, active=active)}


@router.get("/api/jobs/{job_id}")
def get_job(job_id: str, ctl: Ctl):
    return ctl.jobs.get(job_id)


@router.get("/api/jobs/{job_id}/logs")
def job_logs(job_id: str, ctl: Ctl, offset: int = 0):
    return ctl.jobs.logs(job_id, offset)


@router.post("/api/jobs/{job_id}/answers")
def answer_job(job_id: str, body: AnswerRequest, ctl: Ctl):
    return ctl.jobs.answer(job_id, [body])


@router.post("/api/jobs/{job_id}/integrate")
def integrate_job(job_id: str, ctl: Ctl):
    return ctl.jobs.integrate(job_id)


@router.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: str, ctl: Ctl):
    return ctl.jobs.cancel(job_id)


@router.get("/api/jobs/{job_id}/artefacts")
def job_artefacts(job_id: str, ctl: Ctl):
    return {"artefacts": ctl.ctx.runner.artefacts(job_id)}


@router.get("/api/jobs/{job_id}/events")
async def job_events(job_id: str, request: Request, ctl: Ctl, offset: int = 0):
    await run_in_threadpool(ctl.jobs.get, job_id)
    return event_stream(request, _job_frames(ctl, job_id), resume_cursor(request, offset))


def _with_chat(ctl: Controller, job: Job, title: str) -> JobWithChat:
    chat = ctl.chats.create(title, job_id=job.id)
    job = records.to_dto(records.update(ctl.ctx.stores.runs, job.id, chat_id=chat.id))
    ctl.chats.add_job_item(chat.id, job)
    return JobWithChat(job=job, chat=ctl.chats.snapshot(chat.id).chat)


def _job_frames(ctl: Controller, job_id: str):
    seen: list[str] = []                                        # the last status sent

    def fetch(offset: int) -> tuple[list[Frame], bool]:
        job = ctl.jobs.get(job_id)                              # before the log: a finished job's log is complete
        chunk = ctl.jobs.logs(job_id, offset)
        frames: list[Frame] = []
        if chunk.text:
            frames.append((chunk.offset, "log", {"text": chunk.text, "offset": chunk.offset}))
        if seen[-1:] != [job.status]:
            seen.append(job.status)
            frames.append((chunk.offset, "status", {"status": job.status}))
        done = job.status in SETTLED
        if done:
            frames.append((chunk.offset, "end", {"job": job.model_dump(mode="json")}))
        return frames, done

    return fetch
