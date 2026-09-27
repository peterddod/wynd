"""`wynd compile` (PLAN §9, §3.22, §7 item 8; `$DRAFTS/06 §9.2`, `$DRAFTS/05 §6.7`). A real controller over an
in-process runner whose compile handler is a fake (`support.cli_m3_handlers`); CTL-M3's `compile_view` is faked
over the compiler's session JSON. Exit 0 integrated, 1 failed job, 3 dirty checkout or bad precondition, 4 awaiting
input (questions block, no prompt), 5 left for review."""

from __future__ import annotations

import json

import pytest
from support.cli_m3_handlers import FakeCompileView, session_dto

H = "support.cli_m3_handlers"
COMPILED = "processes/p1/COMPILED.md"


@pytest.fixture
def compile_view(monkeypatch) -> FakeCompileView:
    fake = FakeCompileView()
    monkeypatch.setattr("wynd.controller.compile_view.session_dto", session_dto)
    monkeypatch.setattr("wynd.controller.compile_view.apply_answers", fake.apply_answers)
    return fake


@pytest.fixture
def make_ctl(workspace, make_controller, use_controller, job_handlers, compile_view):
    def make(handler: str = "ask_then_compile"):
        return use_controller(make_controller(workspace, handlers={**job_handlers, "compile": f"{H}:{handler}"}))

    return make


@pytest.fixture
def ctl(make_ctl):
    return make_ctl()


def job_id_of(result) -> str:
    first = result.stderr.splitlines()[0].split()
    assert first[0] in ("submitted", "resumed")
    return first[1].rstrip(":")


# --- questions ---------------------------------------------------------------------------------------------------------

def test_questions_block_exits_4_without_prompting(ctl, workspace, cli, git):
    head = git(workspace, "rev-parse", "HEAD").strip()
    result = cli("compile", "p1", code=4)
    job_id = job_id_of(result)
    assert result.stderr.splitlines()[0] == f"submitted {job_id} (compile) at {head[:7]}"
    assert "[p1] Compiling p1: 2 steps, 2 with changed proto-steps." in result.stderr    # session events
    assert "[upper] What should happen if the text is empty?" in result.stderr
    assert f"job {job_id} is awaiting input" in result.stderr
    assert result.stdout.splitlines() == [
        f"Compile of p1 needs input (job {job_id}, 2 question(s)):",
        "",
        "[upper.example1] What should happen if the text is empty?   (default: accept)",
        "    Why: an empty message has nothing to upper-case.",
        "    Proposed example:",
        "      inputs:",
        "        text: ''",
        "      exit: done",
        "      outputs:",
        "        text: ''",
        "",
        "[count.clarify1] Do hyphenated words count as one word?",
        "",
        "Answer in a file (question id -> answer) and resume:",
        f"    wynd compile p1 --resume {job_id} --answers answers.yaml",
        "Or accept every proposed example:",
        f"    wynd compile p1 --resume {job_id} --accept-proposals",
    ]
    assert git(workspace, "rev-parse", "HEAD").strip() == head              # the user's branch is untouched
    assert ctl.jobs.get(job_id).status == "awaiting_input"


def test_accept_proposals_still_stops_on_a_clarification(ctl, cli):
    result = cli("compile", "p1", "--accept-proposals", code=4)
    job_id = job_id_of(result)
    assert "(job " + job_id + ", 1 question(s))" in result.stdout.splitlines()[0]
    assert "[count.clarify1] Do hyphenated words count as one word?" in result.stdout
    assert "upper.example1" not in result.stdout
    assert "--accept-proposals" not in result.stdout                        # no proposal left to accept


def test_awaiting_input_as_json_exits_4(ctl, cli):
    doc = json.loads(cli("compile", "p1", "--json", code=4).stdout)
    assert (doc["kind"], doc["status"]) == ("compile", "awaiting_input")
    assert [q["id"] for q in doc["session"]["questions"]] == ["upper.example1", "count.clarify1"]


# --- pre-answers, straight through --------------------------------------------------------------------------------------

def test_pre_answers_compile_and_fast_forward(ctl, workspace, cli, git, tmp_path):
    answers = tmp_path / "answers.yaml"
    answers.write_text("upper.example1: accept\ncount.clarify1: no, split on hyphens\n")
    result = cli("compile", "p1", "--answers", answers, "--answer", "count.clarify1=yes, one word",
                 "--max-revisions", "1")
    job_id = job_id_of(result)
    new_head = git(workspace, "rev-parse", "HEAD").strip()
    lines = result.stdout.splitlines()
    assert lines[0] == "Compiled 2 steps: 2 deterministic. All 3 step tests pass."
    assert lines[1].split() == ["STEP", "RESULT", "KIND", "RULE", "TIER", "TESTS", "REASON"]
    assert lines[2].split("  ")[0] == "upper" and lines[2].split()[1:6] == ["compiled", "deterministic", "1", "-",
                                                                         "2/2"]
    assert lines[2].endswith("Upper-casing is a pure function.")
    assert lines[3].split()[:6] == ["count", "compiled", "agentic", "2", "cheap", "1/1"]
    assert lines[4].split()[:3] == ["child:tag", "skipped", "-"] and lines[4].endswith("proto-step unchanged")
    assert lines[5:] == [
        "process examples  2/2",
        "warning: cassettes of count are 6.1 MB (above 5 MB)",
        "usage: 812→64 tok $0.0011",
        f"fast-forwarded main to {new_head[:7]}",
    ]
    assert "[upper] upper: deterministic (rule 1). Upper-casing is a pure function." in result.stderr
    job = ctl.jobs.get(job_id)
    assert job.artefacts["inputs"] == {"answers": {"upper.example1": "accept", "count.clarify1": "yes, one word"},
                                       "accept_proposals": False, "max_revisions": 1, "process": "p1",
                                       "target_branch": "main"}
    assert (job.status, job.integration.mode) == ("succeeded", "fast_forward")
    assert git(workspace, "log", "-1", "--format=%s").strip() == "wynd compile: p1"
    assert (workspace / COMPILED).is_file()


# --- resume ---------------------------------------------------------------------------------------------------------------

def test_resume_with_answers_requeues_the_same_job(ctl, workspace, cli, git, compile_view, tmp_path):
    job_id = job_id_of(cli("compile", "p1", code=4))
    answers = tmp_path / "answers.yaml"
    answers.write_text("upper.example1: accept\ncount.clarify1: yes, one word\n")
    result = cli("compile", "p1", "--resume", job_id, "--answers", answers)
    assert result.stderr.splitlines()[0] == f"resumed {job_id} (compile): queued"
    assert compile_view.calls == [(job_id, {"upper.example1": "accept", "count.clarify1": "yes, one word"})]
    assert "Compiling p1" not in result.stderr                              # events of the first attempt not repeated
    assert "[upper] upper: deterministic (rule 1)." in result.stderr
    new_head = git(workspace, "rev-parse", "HEAD").strip()
    assert result.stdout.splitlines()[-1] == f"fast-forwarded main to {new_head[:7]}"
    job = ctl.jobs.get(job_id)
    assert job.status == "succeeded" and job.artefacts["inputs"]["answers"] == {
        "upper.example1": "accept", "count.clarify1": "yes, one word"}
    assert (workspace / COMPILED).read_text() == "answers: ['count.clarify1', 'upper.example1']\n"
    assert ctl.jobs.list(kind="compile") == [job]                           # no second job


def test_resume_with_partial_answers_stops_again(ctl, cli, compile_view):
    job_id = job_id_of(cli("compile", "p1", code=4))
    result = cli("compile", "p1", "--resume", job_id, "--accept-proposals", code=4)
    assert compile_view.calls == [(job_id, {"upper.example1": "accept"})]
    assert result.stdout.splitlines()[0] == f"Compile of p1 needs input (job {job_id}, 1 question(s)):"
    assert "[count.clarify1]" in result.stdout and "[upper.example1]" not in result.stdout
    assert ctl.jobs.get(job_id).status == "awaiting_input"


def test_resume_without_answers_follows_the_job(ctl, cli, compile_view):
    job_id = job_id_of(cli("compile", "p1", code=4))
    result = cli("compile", "p1", "--resume", job_id, code=4)
    assert compile_view.calls == []
    assert f"(job {job_id}, 2 question(s))" in result.stdout.splitlines()[0]


def test_resume_of_another_process_is_a_usage_error(ctl, cli):
    job_id = job_id_of(cli("compile", "p1", code=4))
    result = cli("compile", "p2", "--resume", job_id, "--answer", "upper.example1=accept", code=2)
    assert f"error: job {job_id} is a compile job of 'p1', not a compile of 'p2'" in result.stderr


def test_resume_rejects_max_revisions(ctl, cli):
    job_id = job_id_of(cli("compile", "p1", code=4))
    result = cli("compile", "p1", "--resume", job_id, "--max-revisions", "2", code=2)
    assert "error: --max-revisions applies to a new compile, not to --resume" in result.stderr


def test_resume_of_an_unknown_job_exits_3(ctl, cli):
    cli("compile", "p1", "--resume", "job_20260101T000000000_abcdef", code=3)


# --- outcomes -------------------------------------------------------------------------------------------------------------

def test_failed_job_prints_report_error_and_branch(make_ctl, workspace, cli, git):
    ctl = make_ctl("compile_fails")
    head = git(workspace, "rev-parse", "HEAD").strip()
    result = cli("compile", "p1", code=1)
    job_id = job_id_of(result)
    lines = result.stdout.splitlines()
    assert lines[0] == "Compiled 1 step: 1 deterministic."
    assert lines[3].split()[:3] == ["count", "failed", "-"]
    assert lines[3].endswith("tests still fail after 3 revisions")
    assert f"job {job_id} failed: tests failed after 3 revisions: count" in result.stderr
    assert f"branch: wynd/compile/p1/{job_id}" in result.stderr
    assert git(workspace, "rev-parse", "HEAD").strip() == head
    assert ctl.jobs.get(job_id).integration is None


def test_no_integrate_leaves_the_branch(ctl, workspace, cli, git):
    head = git(workspace, "rev-parse", "HEAD").strip()
    result = cli("compile", "p1", "--accept-proposals", "--answer", "count.clarify1=yes", "--no-integrate")
    job_id = job_id_of(result)
    branch = f"wynd/compile/p1/{job_id}"
    assert result.stdout.splitlines()[-1] == f"succeeded; branch {branch} not integrated (wynd jobs integrate {job_id})"
    assert git(workspace, "rev-parse", "HEAD").strip() == head
    assert git(workspace, "rev-parse", branch).strip() == ctl.jobs.get(job_id).result_commit


def test_left_for_review_exits_5(make_ctl, cli):
    ctl = make_ctl("concurrent_edit")
    result = cli("compile", "p1", code=5)
    job_id = job_id_of(result)
    branch = f"wynd/compile/p1/{job_id}"
    assert result.stdout.splitlines()[-1] == f"left for review: {branch} (conflicts: {COMPILED})"
    assert f"hint: git push origin {branch} && gh pr create --head {branch}" in result.stderr.splitlines()
    assert ctl.jobs.get(job_id).integration.mode == "pr_branch"


def test_integration_over_local_changes_exits_3(make_ctl, cli):
    ctl = make_ctl("dirty_checkout")
    result = cli("compile", "p1", code=3)
    job_id = job_id_of(result)
    assert "error: integrating " in result.stderr and "would overwrite uncommitted changes" in result.stderr
    job = ctl.jobs.get(job_id)
    assert (job.status, job.integration) == ("succeeded", None)             # still integrable later


def test_succeeded_as_json_is_the_integrated_job(ctl, cli):
    doc = json.loads(cli("compile", "p1", "--accept-proposals", "--answer", "count.clarify1=yes", "--json").stdout)
    assert (doc["kind"], doc["status"], doc["integration"]["mode"]) == ("compile", "succeeded", "fast_forward")
    assert doc["report"]["summary"].startswith("Compiled 2 steps")


# --- submit ---------------------------------------------------------------------------------------------------------------

def test_no_wait_prints_the_job_id(ctl, cli):
    result = cli("compile", "p1", "--no-wait")
    job_id = result.stdout.strip()
    assert job_id.startswith("job_") and ctl.jobs.wait(job_id).status == "awaiting_input"


def test_refuses_a_dirty_workspace(ctl, workspace, cli, write_files):
    write_files(workspace, {"processes/p1/notes.md": "dirty\n"})
    result = cli("compile", "p1", code=3)
    assert result.stderr.splitlines()[0].startswith("error: the workspace has uncommitted changes")
    assert ctl.jobs.list() == []


def test_unknown_process_exits_3(ctl, cli):
    result = cli("compile", "nope", code=3)
    assert "unknown process 'nope'" in result.stderr


def test_malformed_answer_is_a_usage_error(ctl, cli):
    result = cli("compile", "p1", "--answer", "no-equals-sign", code=2)
    assert "--answer expects QID=TEXT" in result.stderr
    assert ctl.jobs.list() == []
