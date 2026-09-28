"""Job types and helpers (PLAN §3.18, SPEC §6.6): result branches, `new_job_record`, `wait_for`, and the harness
contract proven with the reference harness in `support/proc_git_harness.py` (built only from `wynd.process.git`)."""

import re

import pytest
from support.proc_git_harness import WORKSPACE, proto_yaml, requeue, run_job

from wynd.process.errors import GitError
from wynd.process.git import integrate, is_ancestor, result_branch, rev_parse
from wynd.process.jobs import TERMINAL, JobContext, JobOutcome, JobRecord, JobUsage, new_job_record, wait_for
from wynd.runtime.storage.local import FileRunRegistry
from wynd.runtime.usage import Usage

HANDLER = "wynd.compiler.jobs:run_compile_job"


@pytest.mark.parametrize("kind, branch", [
    ("compile", "wynd/compile/finance/invoices/job_1"),
    ("test_live", "wynd/test-live/finance/invoices/job_1"),
    ("optimise", "wynd/optimise/finance/invoices/job_1"),
])
def test_result_branch_names(kind, branch):
    assert result_branch(kind, "finance/invoices", "job_1") == branch


@pytest.mark.parametrize("kind", ["build", "bake"])
def test_build_and_bake_have_no_result_branch(kind):
    with pytest.raises(ValueError, match=f"{kind} jobs produce no commits"):
        result_branch(kind, "p", "job_1")


def test_new_job_record_resolves_the_ref_and_takes_the_target_from_inputs(make_repo, commit):
    ws = make_repo(files=WORKSPACE, subdir="examples/ws")
    first = rev_parse(ws, "HEAD")
    head = commit(ws, "second", {"README.md": "2\n"})
    inputs = {"process": "beta", "target_branch": "main"}
    record = new_job_record("compile", "HEAD", inputs, ws_root=ws, runner="inprocess", handler=HANDLER)
    assert re.fullmatch(r"job_\d{8}T\d{9}_[0-9a-f]{6}", record.id)
    assert (record.kind, record.job_kind, record.process, record.status) == ("job", "compile", "beta", "queued")
    assert record.ref == record.base_commit == head
    assert (record.target_branch, record.workspace_rel) == ("main", "examples/ws")
    assert (record.runner, record.handler, record.attempt) == ("inprocess", HANDLER, 1)
    assert record.inputs == inputs and record.inputs is not inputs
    assert record.created_at == record.updated_at and record.created_at.tzinfo is not None
    assert new_job_record("test_live", first[:9], inputs, ws_root=ws, runner="r", handler="h").ref == first
    assert new_job_record("optimise", "main", inputs, ws_root=ws, runner="r", handler="h").ref == head


def test_new_job_record_never_reads_a_branch(make_repo, git):
    ws = make_repo(files=WORKSPACE)
    git(ws, "checkout", "-q", "--detach")                    # no branch to read, and none is needed
    record = new_job_record("compile", "HEAD", {"process": "beta", "target_branch": "release"}, ws_root=ws,
                            runner="r", handler="h")
    assert (record.target_branch, record.workspace_rel) == ("release", "")
    for kind in ("build", "bake"):
        built = new_job_record(kind, "HEAD", {"process": "beta", "target_branch": "main"}, ws_root=ws, runner="r",
                               handler="h")
        assert built.target_branch is None


@pytest.mark.parametrize("kind", ["compile", "test_live", "optimise"])
def test_commit_producing_jobs_need_a_target_branch(make_repo, kind):
    ws = make_repo(files=WORKSPACE)
    with pytest.raises(ValueError, match="need 'target_branch'"):
        new_job_record(kind, "HEAD", {"process": "beta"}, ws_root=ws, runner="r", handler="h")


def test_jobs_need_a_process_and_a_commit(make_repo):
    ws = make_repo(files=WORKSPACE)
    with pytest.raises(ValueError, match="need 'process'"):
        new_job_record("build", "HEAD", {}, ws_root=ws, runner="r", handler="h")
    with pytest.raises(GitError):
        new_job_record("build", "no-such-ref", {"process": "beta"}, ws_root=ws, runner="r", handler="h")


def test_job_record_round_trips_through_the_run_registry(make_repo, tmp_path):
    ws = make_repo(files=WORKSPACE)
    runs = FileRunRegistry(tmp_path / "registry")
    record = new_job_record("compile", "HEAD", {"process": "beta", "target_branch": "main"}, ws_root=ws,
                            runner="inprocess", handler=HANDLER)
    record.usage = JobUsage(input_tokens=5, calls=1, by={"fake/cheap": Usage(input_tokens=5, calls=1)})
    runs.create(record.model_dump(mode="json"))
    assert JobRecord.model_validate(runs.get(record.id)) == record
    assert [r["id"] for r in runs.list(kind="job", process="beta")] == [record.id]


class ScriptedRunner:
    name = "scripted"

    def __init__(self, record: JobRecord, statuses: list[str]):
        self.record, self.statuses, self.calls = record, statuses, 0

    def status(self, job_id: str) -> JobRecord:
        status = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        return self.record.model_copy(update={"status": status})


@pytest.fixture
def queued(make_repo):
    return new_job_record("compile", "HEAD", {"process": "beta", "target_branch": "main"},
                          ws_root=make_repo(files=WORKSPACE), runner="scripted", handler=HANDLER)


@pytest.mark.parametrize("final", sorted(TERMINAL | {"awaiting_input"}))
def test_wait_for_returns_at_a_terminal_or_waiting_status(queued, final):
    runner = ScriptedRunner(queued, ["queued", "running", "running", final])
    assert wait_for(runner, queued.id, poll_s=0.001).status == final
    assert runner.calls == 4


def test_wait_for_times_out(queued):
    runner = ScriptedRunner(queued, ["running"])
    with pytest.raises(TimeoutError, match=f"job {queued.id} is still running after 0.05s"):
        wait_for(runner, queued.id, poll_s=0.01, timeout_s=0.05)
    assert runner.calls >= 2


# --- the harness contract, with the reference harness ----------------------------------------------------------------

@pytest.fixture
def jobs(make_repo, tmp_path):
    """(workspace, run registry, submit(process) -> job id) for compile jobs targeting main."""
    ws = make_repo(files=WORKSPACE)
    runs = FileRunRegistry(tmp_path / "registry")

    def submit(process: str = "beta") -> str:
        record = new_job_record("compile", "HEAD", {"process": process, "target_branch": "main"}, ws_root=ws,
                                runner="reference", handler=HANDLER)
        runs.create(record.model_dump(mode="json"))
        return record.id

    return ws, runs, submit


def test_a_succeeded_job_publishes_its_commit_as_the_result_branch(jobs, git, tmp_path):
    ws, runs, submit = jobs
    job_id = submit()
    base = rev_parse(ws, "HEAD")
    seen = {}

    def handler(ctx: JobContext) -> JobOutcome:
        seen.update(worktree=ctx.worktree, head=rev_parse(ctx.worktree, "HEAD"))
        (ctx.workspace / "processes/beta/steps/own").mkdir(parents=True)
        (ctx.workspace / "processes/beta/steps/own/own.py").write_text("print('compiled')\n")
        (ctx.workspace / "processes/alpha/proto/read.yaml").write_text("outside the closure\n")
        ctx.log("compiled beta")
        return JobOutcome(status="succeeded", commit=ctx.commit("wynd compile beta", None), report={"steps": 1})

    record = run_job(runs, job_id, handler, ws_root=ws)
    branch = f"wynd/compile/beta/{job_id}"
    assert (record.status, record.result_branch, record.report) == ("succeeded", branch, {"steps": 1})
    assert seen["head"] == base and record.result_commit == rev_parse(ws, branch)
    assert rev_parse(ws, f"{branch}~1") == base
    assert git(ws, "show", "--name-only", "--format=%B", branch).split("\n") == [
        "wynd compile beta", "", f"Wynd-Job: {job_id}", "", "", "processes/beta/steps/own/own.py", "",
    ]
    assert not seen["worktree"].exists()
    assert "compiled beta" in (ws / ".wynd" / "jobs" / job_id / "job.log").read_text()
    assert rev_parse(ws, "main") == base                     # nothing reaches the user's branch until integration
    r = integrate(ws, process_id="beta", base_sha=record.base_commit, branch=branch, target_branch="main",
                  scratch_dir=tmp_path / "integrate" / job_id)
    assert (r.mode, r.head) == ("fast_forward", record.result_commit)
    assert (ws / "processes/beta/steps/own/own.py").read_text() == "print('compiled')\n"


def test_awaiting_input_keeps_the_session_and_the_resumed_attempt_receives_it(jobs):
    ws, runs, submit = jobs
    job_id = submit()
    base = rev_parse(ws, "HEAD")
    session = {"id": job_id, "questions": [{"id": "own.example1"}]}

    def ask(ctx: JobContext) -> JobOutcome:
        (ctx.workspace / "processes/beta/proto/own.yaml").write_text(proto_yaml("own", "Work in progress."))
        wip = ctx.commit("wynd compile beta (in progress)", None)
        return JobOutcome(status="awaiting_input", commit=wip, session=session, questions=session["questions"])

    waiting = run_job(runs, job_id, ask, ws_root=ws)
    assert (waiting.status, waiting.session, waiting.questions) == ("awaiting_input", session, session["questions"])
    requeue(runs, job_id, waiting.result_commit, {**waiting.inputs, "answers": {"own.example1": "accept"}})
    received = {}

    def resume(ctx: JobContext) -> JobOutcome:
        received.update(session=ctx.session, answers=ctx.inputs["answers"], attempt=ctx.job.attempt,
                        head=rev_parse(ctx.worktree, "HEAD"), checkout=ctx.worktree.name)
        return JobOutcome(status="succeeded", commit=received["head"])

    done = run_job(runs, job_id, resume, ws_root=ws)
    assert received == {"session": session, "answers": {"own.example1": "accept"}, "attempt": 2,
                        "head": waiting.result_commit, "checkout": "checkout-2"}
    assert (done.status, done.result_branch, done.base_commit) == ("succeeded", waiting.result_branch, base)
    assert is_ancestor(ws, base, rev_parse(ws, done.result_branch))


def test_a_failing_handler_fails_the_job_and_keeps_the_checkout(jobs):
    ws, runs, submit = jobs
    job_id = submit()

    def boom(ctx: JobContext) -> JobOutcome:
        raise RuntimeError("the compiler fell over")

    record = run_job(runs, job_id, boom, ws_root=ws)
    assert record.status == "failed" and record.error == {"message": "RuntimeError: the compiler fell over"}
    assert record.result_branch is None
    assert (ws / ".wynd" / "jobs" / job_id / "checkout-1" / "wynd.yaml").is_file()
    assert "RuntimeError: the compiler fell over" in (ws / ".wynd" / "jobs" / job_id / "job.log").read_text()


def test_process_ids_with_slashes_give_nested_branch_names(jobs, git):
    ws, runs, submit = jobs
    job_id = submit("team/gamma")

    def handler(ctx: JobContext) -> JobOutcome:
        (ctx.workspace / "shared/steps/util/util.py").write_text("x = 1\n")      # a root step in the closure
        return JobOutcome(status="succeeded", commit=ctx.commit("wynd compile team/gamma", None))

    record = run_job(runs, job_id, handler, ws_root=ws)
    assert record.result_branch == f"wynd/compile/team/gamma/{job_id}"
    assert git(ws, "branch", "--list", "wynd/compile/team/gamma/*").strip() == record.result_branch
    assert git(ws, "show", "--name-only", "--format=", record.result_branch).split() == ["shared/steps/util/util.py"]
