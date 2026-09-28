"""`wynd jobs list|show|logs|answer|cancel|integrate` and the shared end of waited-for jobs (PLAN §9, §3.22, §3.18;
`$DRAFTS/06 §9.2`) over a real `JobService` and in-process runner with fake handlers (`support.ctl_jobs_handlers`:
`succeed` logs and succeeds; the compile handler `ask_then_finish` commits WIP and asks `read.example1`, then
finishes with a commit once resumed). CTL-M3's `compile_view.session_dto`/`apply_answers` are faked."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest

from wynd.cli.commands.jobs import conclude


@pytest.fixture
def ctl(workspace, make_controller, use_controller, job_handlers):
    return use_controller(make_controller(workspace, handlers=job_handlers))


class FakeCompileView:
    """`apply_answers` answers every question when `ready`, else leaves them pending; `session_dto` projects the
    handler's session onto the web `CompileSession`."""

    def __init__(self) -> None:
        self.ready = True
        self.calls: list[tuple[str, dict]] = []

    def apply_answers(self, job, answers):
        self.calls.append((job.id, dict(answers)))
        status = "answered" if self.ready else "pending"
        return {**job.session, "questions": [dict(q, status=status) for q in job.session["questions"]]}, self.ready

    @staticmethod
    def session_dto(session):
        from wynd.controller.api.models_web import ClarificationQuestion, CompileSession

        questions = [ClarificationQuestion(id=q["id"], text=q["text"], status=q["status"],
                                           asked_at=datetime(2026, 9, 22, tzinfo=UTC))
                     for q in session.get("questions", [])]
        state = session.get("state") if session.get("state") in ("awaiting_input", "done") else "compiling"
        return CompileSession(state=state, questions=questions)


@pytest.fixture
def compile_view(monkeypatch) -> FakeCompileView:
    fake = FakeCompileView()
    monkeypatch.setattr("wynd.controller.compile_view.apply_answers", fake.apply_answers)
    monkeypatch.setattr("wynd.controller.compile_view.session_dto", fake.session_dto)
    return fake


@pytest.fixture
def bake_job(ctl):
    job = ctl.jobs.submit_bake("p1")
    assert ctl.jobs.wait(job.id, poll=0.02).status == "succeeded"
    return job.id


@pytest.fixture
def waiting_job(ctl, compile_view):
    job = ctl.jobs.submit_compile("p1")
    assert ctl.jobs.wait(job.id, poll=0.02).status == "awaiting_input"
    return job.id


def test_list_show_and_logs(ctl, cli, bake_job):
    lines = cli("jobs", "list").stdout.splitlines()
    assert lines[0].split() == ["JOB", "KIND", "PROCESS", "STATUS", "REF", "AGE", "DURATION", "INTEGRATION"]
    row = lines[1].split()
    assert row[:4] == [bake_job, "bake", "p1", "succeeded"] and len(row[4]) == 7 and row[-1] == "-"
    assert len(lines) == 2

    shown = cli("jobs", "show", bake_job).stdout.splitlines()
    assert shown[:4] == [f"job          {bake_job}", "kind         bake", "process      p1", "status       succeeded"]
    assert any(line.startswith("duration     ") for line in shown)

    log = cli("jobs", "logs", bake_job).stdout
    messages = [line.split(" ", 1)[1] for line in log.splitlines()]            # after the "HH:MM:SS " prefix
    assert messages[1:] == [f"hello from {bake_job}", "line one", "line two", "job succeeded"]
    assert cli("jobs", "logs", bake_job, "-f").stdout == log

    items = json.loads(cli("jobs", "list", "--json").stdout)["items"]
    assert [(i["id"], i["kind"], i["status"]) for i in items] == [(bake_job, "bake", "succeeded")]
    assert json.loads(cli("jobs", "show", bake_job, "--json").stdout)["artefacts"]["answer"] == 42


def test_list_filters(ctl, cli, bake_job, waiting_job):
    def ids(*args):
        return [line.split()[0] for line in cli("jobs", "list", *args).stdout.splitlines()[1:]]

    assert ids() == [waiting_job, bake_job]                                        # newest first
    assert ids("--kind", "bake") == [bake_job]
    assert ids("--status", "awaiting_input") == [waiting_job]
    assert ids("--active") == [waiting_job]
    assert ids("--process", "p2") == []
    assert ids("--limit", "1") == [waiting_job]


def test_answer_partially_then_fully_and_integrate(ctl, workspace, cli, git, compile_view, waiting_job, tmp_path):
    shown = cli("jobs", "show", waiting_job).stdout.splitlines()
    assert "status       awaiting_input" in shown and shown[-1] == "[read.example1] Empty text?"

    compile_view.ready = False
    partial = cli("jobs", "answer", waiting_job, "--answer", "read.example1=later")
    assert partial.stdout.splitlines() == [f"{waiting_job}: awaiting_input", "[read.example1] Empty text?"]

    compile_view.ready = True
    answers = tmp_path / "answers.yaml"
    answers.write_text("read.example1: accept\n")
    resumed = cli("jobs", "answer", waiting_job, "--answers", answers)
    assert resumed.stdout.split()[1] in ("queued", "running", "succeeded")
    assert compile_view.calls[-1] == (waiting_job, {"read.example1": "accept"})
    job = ctl.jobs.wait(waiting_job, poll=0.02)
    assert (job.status, job.report["answers"]) == ("succeeded", {"read.example1": "accept"})

    result = cli("jobs", "integrate", waiting_job)
    head = git(workspace, "rev-parse", "HEAD").strip()
    assert result.stdout.splitlines() == [f"fast-forwarded main to {head[:7]}"]
    assert (workspace / "processes/p1/DONE.md").is_file()
    assert cli("jobs", "list", "--active").stdout.splitlines()[1:] == []


def test_answer_refusals(ctl, cli, bake_job, waiting_job):
    assert "no answers given" in cli("jobs", "answer", waiting_job, code=2).stderr
    refused = cli("jobs", "answer", bake_job, "--answer", "q=a", code=3)
    assert "only a compile job awaiting input takes answers" in refused.stderr
    assert "no job 'job_nope'" in cli("jobs", "answer", "job_nope", "--answer", "q=a", code=3).stderr


def test_cancel(ctl, cli, bake_job, waiting_job):
    assert cli("jobs", "cancel", waiting_job).stdout == f"{waiting_job}: cancelled\n"
    assert json.loads(cli("jobs", "cancel", waiting_job, "--json").stdout)["status"] == "cancelled"
    assert "already succeeded" in cli("jobs", "cancel", bake_job, code=3).stderr


def test_integrate_refusals(ctl, cli, bake_job):
    assert "only succeeded compile, test_live and optimise jobs" in cli("jobs", "integrate", bake_job, code=3).stderr
    assert "no job 'job_nope'" in cli("jobs", "show", "job_nope", code=3).stderr
    assert "no job 'job_nope'" in cli("jobs", "logs", "job_nope", code=3).stderr


def test_conclude_maps_job_outcomes_to_exit_codes(ctl, capsys, bake_job, waiting_job):
    waiting = ctl.jobs.get(waiting_job)
    assert conclude(ctl, waiting, integrate=True, json_output=False) == 4
    assert f"job {waiting_job} is awaiting input" in capsys.readouterr().err

    failed = waiting.model_copy(update={"status": "failed", "error": None, "branch": "wynd/compile/p1/x"})
    assert conclude(ctl, failed, integrate=True, json_output=False) == 1
    assert capsys.readouterr().err.splitlines() == [f"job {waiting_job} failed", "branch: wynd/compile/p1/x"]

    done = ctl.jobs.get(bake_job)
    assert conclude(ctl, done, integrate=False, json_output=True) == 0
    assert json.loads(capsys.readouterr().out)["id"] == bake_job
