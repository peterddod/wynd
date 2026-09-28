"""`JobService` over a real in-process runner: submit preconditions and refs, per-kind inputs, answering (with a faked
`compile_view.apply_answers`, CTL-M3), listing, logs, waiting and cancelling (PLAN §3.18, §8.1)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from support.ctl_jobs_workspace import (
    make_context,
    make_runner,
    make_workspace,
    run_git,
    settle,
    wait_status,
    write_files,
)
from support.ctl_jobs_workspace import commit as commit_files
from wynd.controller.api.models_web import CompileSession, TextAnswer
from wynd.controller.errors import DetachedHead, DirtyTree, JobState, NotFound, Unavailable, ValidationFailed
from wynd.controller.jobs import records
from wynd.controller.jobs.inprocess import InProcessJobRunner
from wynd.controller.jobs.service import JobService
from wynd.process.jobs import new_job_record

H = "support.ctl_jobs_handlers"
HANDLERS = {
    "compile": f"{H}:ask_then_finish",
    "test_live": f"{H}:commit_file",
    "build": f"{H}:succeed",
    "bake": f"{H}:succeed",
    "optimise": f"{H}:wait_gate",
}


def service_for(ws: Path, env: dict[str, str] | None = None) -> JobService:
    runner = make_runner(ws, InProcessJobRunner, HANDLERS, env=env)
    return JobService(make_context(ws, runner, env=env), ctl=None)


@pytest.fixture
def ws(tmp_path: Path) -> Path:
    return make_workspace(tmp_path)


@pytest.fixture
def service(ws: Path) -> JobService:
    return service_for(ws)


@pytest.fixture(autouse=True)
def fake_session_dto(monkeypatch: pytest.MonkeyPatch) -> None:
    """`compile_view.session_dto` is CTL-M3's; the projection is faked so compile jobs can be read back."""
    monkeypatch.setattr("wynd.controller.compile_view.session_dto",
                        lambda s: CompileSession(state="done" if s.get("state") == "done" else "awaiting_input"))


class FakeCompileView:
    """Fakes CTL-M3's `apply_answers`/`answer_text`: every question of the session becomes answered when `ready`,
    else stays pending; `calls` records `(job id, answers)`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []
        self.ready = False

    def apply_answers(self, job, answers):
        self.calls.append((job.id, answers))
        status = "answered" if self.ready else "pending"
        questions = [dict(q, status=status) for q in job.session["questions"]]
        return {**job.session, "questions": questions, "applied": len(self.calls)}, self.ready


@pytest.fixture
def compile_view(monkeypatch: pytest.MonkeyPatch) -> FakeCompileView:
    fake = FakeCompileView()
    monkeypatch.setattr("wynd.controller.compile_view.apply_answers", fake.apply_answers)
    monkeypatch.setattr("wynd.controller.compile_view.answer_text", lambda q, a: f"{q.get('kind')}:{a.text}")
    return fake


# --- submit ---------------------------------------------------------------------------------------------------------

def test_commit_kinds_fork_from_the_branch_tip(ws: Path, service: JobService) -> None:
    head = run_git(ws, "rev-parse", "HEAD")
    job = service.submit_test_live("alpha", steps=["alpha#read"])
    assert job.kind == "test_live" and job.process_id == "alpha" and job.ref == head
    record = settle(service.ctx.runner, job.id)
    assert record.inputs == {"process": "alpha", "target_branch": "main", "steps": ["alpha#read"]}
    assert record.target_branch == "main" and record.base_commit == head
    assert record.status == "succeeded"


def test_build_and_bake_use_the_closure_head(ws: Path, service: JobService) -> None:
    closure_head = run_git(ws, "rev-parse", "HEAD")
    commit_files(ws, "unrelated", {"README.md": "changed outside alpha's closure\n"})
    assert run_git(ws, "rev-parse", "HEAD") != closure_head

    build = service.submit_build("alpha", platform="linux/arm64")
    assert build.ref == closure_head
    assert settle(service.ctx.runner, build.id).inputs == {
        "process": "alpha", "registry": None, "push": False, "platform": "linux/arm64"}
    bake = service.submit_bake("alpha")
    assert bake.kind == "bake" and bake.ref == closure_head
    assert settle(service.ctx.runner, bake.id).inputs == {"process": "alpha"}


def test_build_defaults_to_the_default_image_registry(ws: Path, service: JobService) -> None:
    registry = service.ctx.stores.registry
    registry.put("registries", "ghcr", {"name": "ghcr", "url": "ghcr.io/acme", "default": False})
    registry.put("registries", "local", {"name": "local", "url": "localhost:5001/wynd", "default": True})
    job = service.submit_build("alpha")
    assert settle(service.ctx.runner, job.id).inputs == {"process": "alpha", "registry": "local", "push": True}
    job = service.submit_build("alpha", registry="ghcr", push=False)
    assert settle(service.ctx.runner, job.id).inputs == {"process": "alpha", "registry": "ghcr", "push": False}


def test_compile_inputs(service: JobService) -> None:
    job = service.submit_compile("alpha", answers={"read.example1": "accept"}, accept_proposals=True,
                                 max_revisions=2)
    record = settle(service.ctx.runner, job.id)
    assert record.inputs == {"process": "alpha", "target_branch": "main", "answers": {"read.example1": "accept"},
                             "accept_proposals": True, "max_revisions": 2}


def test_validation_errors_refuse_the_job(service: JobService) -> None:
    with pytest.raises(ValidationFailed) as caught:
        service.submit_build("broken")
    details = caught.value.details
    assert details["ok"] is False
    assert {"E208", "E210"} <= {issue["code"] for issue in details["issues"]}
    assert service.list() == []


def test_unknown_process(service: JobService) -> None:
    with pytest.raises(NotFound):
        service.submit_build("nope")


def test_any_dirt_in_the_workspace_refuses_the_job(ws: Path, service: JobService) -> None:
    write_files(ws, {"processes/beta/scratch.txt": "untracked, outside alpha's closure\n"})
    with pytest.raises(DirtyTree) as caught:
        service.submit_test_live("alpha")
    assert caught.value.details == {"paths": ["processes/beta/scratch.txt"]}

    (ws / "processes/beta/scratch.txt").unlink()
    write_files(ws, {"README.md": "modified\n"})
    with pytest.raises(DirtyTree) as caught:
        service.submit_build("alpha")
    assert caught.value.details == {"paths": ["README.md"]}
    assert service.list() == []


def test_state_dir_and_ignored_files_are_not_dirt(ws: Path, service: JobService) -> None:
    commit_files(ws, "ignore logs", {".gitignore": ".wynd/\n*.log\n"})
    write_files(ws, {".wynd/notes.txt": "state\n", "debug.log": "ignored\n"})
    assert service.submit_build("alpha").kind == "build"


def test_dirt_outside_a_subdir_workspace_is_allowed(tmp_path: Path) -> None:
    ws = make_workspace(tmp_path, subdir="examples/ws")
    (ws.parent.parent / "TOP.md").write_text("outside the workspace directory\n")
    service = service_for(ws)
    job = service.submit_test_live("alpha")
    record = settle(service.ctx.runner, job.id)
    assert record.status == "succeeded" and record.workspace_rel == "examples/ws"


def test_detached_head_refuses_commit_kinds_only(ws: Path, service: JobService) -> None:
    run_git(ws, "checkout", "--quiet", "--detach")
    with pytest.raises(DetachedHead):
        service.submit_test_live("alpha")
    with pytest.raises(DetachedHead):
        service.submit_compile("alpha")
    assert service.submit_build("alpha").kind == "build"


def test_git_remote_gets_the_target_branch_before_submit(ws: Path, tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--quiet", "--bare", str(remote))
    service = service_for(ws, env={**os.environ, "WYND_GIT_REMOTE": str(remote)})
    job = service.submit_test_live("alpha")
    assert run_git(remote, "rev-parse", "refs/heads/main") == job.ref


# --- answering ------------------------------------------------------------------------------------------------------

def test_partial_answers_only_update_the_record(service: JobService, compile_view: FakeCompileView) -> None:
    job = service.submit_compile("alpha")
    assert settle(service.ctx.runner, job.id).status == "awaiting_input"

    answered = service.answer(job.id, {"read.example1": "reject"})
    assert answered.status == "awaiting_input"
    assert compile_view.calls == [(job.id, {"read.example1": "reject"})]
    record = service.ctx.runner.status(job.id)
    assert record.attempt == 1
    assert record.session["applied"] == 1
    assert [q["id"] for q in record.questions] == ["read.example1"]
    assert record.inputs["answers"] == {}                     # nothing is resubmitted until the session is ready
    assert answered.session == {"state": "awaiting_input", "steps": [], "questions": [], "inferred_schemas": {},
                                "events": []}


def test_ready_answers_requeue_the_same_job(ws: Path, service: JobService, compile_view: FakeCompileView) -> None:
    job = service.submit_compile("alpha", answers={"earlier.q": "yes"})
    first = settle(service.ctx.runner, job.id)
    assert first.status == "awaiting_input"

    compile_view.ready = True
    service.answer(job.id, {"read.example1": "accept"})
    second = settle(service.ctx.runner, job.id)
    assert second.id == job.id and second.attempt == 2 and second.status == "succeeded"
    assert second.ref == first.result_commit                  # resumed at the WIP commit
    assert second.inputs["answers"] == {"earlier.q": "yes", "read.example1": "accept"}
    assert second.inputs["target_branch"] == "main"
    assert second.report["session_seen"]["applied"] == 1      # the handler resumed from the answered session
    assert second.result_branch == first.result_branch


def test_web_answers_become_answer_text(service: JobService, compile_view: FakeCompileView) -> None:
    compile_view.ready = True
    job = service.submit_compile("alpha")
    settle(service.ctx.runner, job.id)
    service.answer(job.id, [TextAnswer(question_id="read.example1", text="only when empty")])
    record = settle(service.ctx.runner, job.id)
    assert record.inputs["answers"] == {"read.example1": "example_proposal:only when empty"}


def test_answers_need_a_compile_job_awaiting_input(service: JobService, compile_view: FakeCompileView) -> None:
    build = service.submit_build("alpha")
    settle(service.ctx.runner, build.id)
    with pytest.raises(JobState):
        service.answer(build.id, {"x": "y"})


# --- reading and waiting --------------------------------------------------------------------------------------------

def test_get_and_the_job_dto(ws: Path, service: JobService) -> None:
    job = service.submit_test_live("alpha")
    settle(service.ctx.runner, job.id)
    dto = service.get(job.id)
    record = service.ctx.runner.status(job.id)
    assert dto.status == "succeeded" and dto.kind == "test_live" and dto.process_id == "alpha"
    assert dto.branch == record.result_branch and dto.result_commit == record.result_commit
    assert dto.error is None and dto.build is None and dto.integration is None and dto.session is None
    assert dto.artefacts == record.artefacts
    with pytest.raises(NotFound):
        service.get("job_nope")


def test_list_filters_and_active(ws: Path, service: JobService) -> None:
    runs = service.ctx.stores.runs

    def make(kind: str, status: str, pid: str = "alpha", **fields) -> str:
        record = new_job_record(kind, "HEAD", {"process": pid, "target_branch": "main"}, ws_root=ws,
                                runner="inprocess", handler="x:y")
        records.create(runs, record)
        records.update(runs, record.id, status=status, **fields)
        return record.id

    queued = make("build", "queued")
    awaiting = make("compile", "awaiting_input")
    open_compile = make("compile", "succeeded")
    integrated = make("test_live", "succeeded", integration={"mode": "noop", "branch": "b", "target": "main",
                                                              "at": "2026-09-23T10:00:00Z"})
    built = make("build", "succeeded")
    failed = make("test_live", "failed")
    other = make("build", "running", pid="beta")

    assert {j.id for j in service.list(active=True)} == {queued, awaiting, open_compile, other}
    assert {j.id for j in service.list(kind="build")} == {queued, built, other}
    assert {j.id for j in service.list(process_id="beta")} == {other}
    assert {j.id for j in service.list(status="succeeded")} == {open_compile, integrated, built}
    assert {j.id for j in service.list(status="failed", kind="test_live")} == {failed}
    assert len(service.list(limit=3)) == 3
    assert [j.id for j in service.list()] == sorted([queued, awaiting, open_compile, integrated, built, failed,
                                                     other], reverse=True)


def test_logs_wait_and_events(service: JobService) -> None:
    job = service.submit_compile("alpha")
    lines: list[str] = []
    events: list[dict] = []
    waited = service.wait(job.id, poll=0.02, on_log=lines.append, on_event=events.append)
    assert waited.status == "awaiting_input"
    assert "job awaiting_input" in "".join(lines)
    assert events == [{"seq": 1, "type": "question", "text": "Empty text?"}]
    chunk = service.logs(job.id)
    assert chunk.done and chunk.text == "".join(lines) and chunk.offset == len(chunk.text.encode())


def test_wait_times_out_but_the_job_keeps_running(service: JobService, tmp_path: Path) -> None:
    gate = tmp_path / "gate"
    job = service.submit("optimise", "alpha", {"gate": str(gate)})
    with pytest.raises(Unavailable, match="timed out"):
        service.wait(job.id, timeout=0.2, poll=0.02)
    assert service.get(job.id).status == "running"
    gate.touch()
    assert service.wait(job.id, poll=0.02).status == "succeeded"


def test_cancel(service: JobService, tmp_path: Path) -> None:
    job = service.submit_compile("alpha")
    settle(service.ctx.runner, job.id)
    assert service.cancel(job.id).status == "cancelled"

    gate = tmp_path / "gate"
    running = service.submit("optimise", "alpha", {"gate": str(gate)})
    wait_status(service.ctx.runner, running.id, "running")
    with pytest.raises(JobState):
        service.cancel(running.id)
    gate.touch()
    settle(service.ctx.runner, running.id)
