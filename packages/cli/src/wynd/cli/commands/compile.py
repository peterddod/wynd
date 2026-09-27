"""`wynd compile` (PLAN §9, §3.22, §7 item 8; `$DRAFTS/06 §9.2`, `$DRAFTS/05 §6.7`).

Submits a compile job (pre-answers from `--answers FILE`/`--answer QID=TEXT` go in with the submit, where the
compiler keeps them for questions not asked yet), or with `--resume JOB` answers that job's pending questions (it
requeues once none is pending) and follows it. Session events stream to stderr as `[<node>] <text>`. Then:
`awaiting_input` prints the questions block and exits 4 (the CLI never prompts); a failed job prints the report,
the error and its branch and exits 1; a succeeded job prints the report and is integrated unless `--no-integrate`
(exit 5 when its branch is left for review, 3 when integrating would overwrite local changes).
`--accept-proposals` answers every example proposal with `accept`; clarifications have no default.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer
import yaml

from wynd.cli import context
from wynd.cli.commands.jobs import conclude, pending_questions
from wynd.cli.inputs import parse_answers
from wynd.cli.output import echo, err, fmt_sha, fmt_usage, print_json, table
from wynd.controller.errors import Invalid

if TYPE_CHECKING:
    from wynd.controller import Controller
    from wynd.controller.models import Job

ACCEPT = "accept"
PROPOSAL = "example_proposal"
EXAMPLE_KEYS = ("inputs", "exit", "outputs")               # the order examples are written in proto-steps


def register(app: typer.Typer) -> None:
    app.command("compile")(compile_process)


def compile_process(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID"),
    answers_file: Path | None = typer.Option(None, "--answers", help="YAML mapping of question id -> answer."),
    pairs: list[str] | None = typer.Option(None, "--answer", metavar="QID=TEXT", help="One answer (repeatable)."),
    accept_proposals: bool = typer.Option(False, "--accept-proposals",
                                          help="Answer every proposed edge-case example with 'accept'."),
    resume: str | None = typer.Option(None, "--resume", metavar="JOB", help="Answer and follow an existing job."),
    max_revisions: int | None = typer.Option(None, "--max-revisions", min=0,
                                             help="Revisions per step after a failing attempt."),
    no_wait: bool = typer.Option(False, "--no-wait", help="Submit (or answer) the job and return."),
    no_integrate: bool = typer.Option(False, "--no-integrate", help="Leave the job's branch unintegrated."),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document on stdout."),
) -> None:
    """Compile a process's proto-steps into steps, in a job whose output is a commit on a branch."""
    ctl = context.get_controller(ctx)
    answers = parse_answers(answers_file, pairs or [])
    if resume is None:
        job = ctl.jobs.submit_compile(pid, answers=answers, accept_proposals=accept_proposals,
                                      max_revisions=max_revisions)
        err(f"submitted {job.id} (compile) at {fmt_sha(job.ref)}")
    else:
        job = _resume(ctl, pid, resume, answers, accept_proposals=accept_proposals, max_revisions=max_revisions)
        err(f"resumed {job.id} (compile): {job.status}")
    if no_wait:
        if json_output:
            print_json(job)
        else:
            echo(job.id)
        return
    job = _wait(ctl, job)
    code = conclude(ctl, job, integrate=not no_integrate, json_output=json_output, summary=print_compile_outcome)
    if code:
        raise typer.Exit(code)


def _resume(
    ctl: Controller, pid: str, job_id: str, answers: dict[str, str], *, accept_proposals: bool,
    max_revisions: int | None,
) -> Job:
    """Answer the job's pending questions (`--accept-proposals` fills in `accept` for proposals the answers leave
    open); with nothing to answer, just follow the job."""
    if max_revisions is not None:
        raise Invalid("--max-revisions applies to a new compile, not to --resume")
    job = ctl.jobs.get(job_id)
    if job.kind != "compile" or job.process_id != pid:
        raise Invalid(f"job {job_id} is a {job.kind} job of '{job.process_id}', not a compile of '{pid}'")
    if accept_proposals:
        defaults = {q["id"]: ACCEPT for q in pending_questions(job) if q.get("kind") == PROPOSAL}
        answers = {**defaults, **answers}
    if not answers:
        return job
    return ctl.jobs.answer(job_id, answers)


def _wait(ctl: Controller, job: Job) -> Job:
    """Wait, printing the session events not seen yet to stderr; on Ctrl-C the job keeps running."""
    skip = len((job.session or {}).get("events", []))
    seen = 0

    def on_event(event: dict) -> None:
        nonlocal seen
        seen += 1
        if seen > skip:
            err(f"[{event.get('step') or event.get('process') or job.process_id}] {event.get('text', '')}")

    try:
        return ctl.jobs.wait(job.id, on_event=on_event)
    except KeyboardInterrupt:
        err(f"job continues: wynd jobs logs {job.id} -f")
        raise


def print_compile_outcome(job: Job) -> None:
    if job.status == "awaiting_input":
        print_questions(job)
        return
    if job.report:
        print_report(job.report, job.process_id)
    usage = fmt_usage(job.usage)
    if usage:
        echo(f"usage: {usage}")


def print_report(report: dict[str, Any], pid: str) -> None:
    """The summary, one row per step (decision, tier, tests, reason), then the report's warnings."""
    if report.get("summary"):
        echo(report["summary"])
    rows = [_step_row(step, pid) for step in report.get("steps", [])]
    if rows:
        echo(table(["STEP", "RESULT", "KIND", "RULE", "TIER", "TESTS", "REASON"], rows))
    tests = report.get("integration_tests")
    if tests:
        echo(f"process examples  {tests.get('passed', 0)}/{tests.get('passed', 0) + tests.get('failed', 0)}")
    for warning in report.get("warnings", []):
        echo(f"warning: {warning}")


def _step_row(step: dict[str, Any], pid: str) -> list[Any]:
    decision = step.get("decision") or {}
    kind = "split" if decision.get("split") else decision.get("kind")
    tests = step.get("tests")
    passed = f"{tests.get('passed', 0)}/{tests.get('passed', 0) + tests.get('failed', 0)}" if tests else None
    node = step.get("node", "")
    name = node if step.get("process", pid) == pid else f"{step['process']}:{node}"
    return [name, step.get("action"), kind, decision.get("rule"), decision.get("tier"), passed,
            decision.get("why") or decision.get("reason") or step.get("reason") or None]


def print_questions(job: Job) -> None:
    """Id, text and default of every pending question, its detail (and proposed example) indented, then the exact
    resume commands (`$DRAFTS/05 §6.7`)."""
    questions = pending_questions(job)
    echo(f"Compile of {job.process_id} needs input (job {job.id}, {len(questions)} question(s)):")
    for question in questions:
        default = question.get("default")
        echo()
        echo(f"[{question.get('id')}] {question.get('text')}" + (f"   (default: {default})" if default else ""))
        if question.get("detail"):
            echo(_indent(question["detail"], 4))
        proposed = question.get("proposed")
        if proposed:
            ordered = {key: proposed[key] for key in EXAMPLE_KEYS if key in proposed} | proposed
            echo("    Proposed example:")
            echo(_indent(yaml.safe_dump(ordered, sort_keys=False, allow_unicode=True), 6))
    resume = f"wynd compile {job.process_id} --resume {job.id}"
    echo()
    echo("Answer in a file (question id -> answer) and resume:")
    echo(f"    {resume} --answers answers.yaml")
    if any(q.get("kind") == PROPOSAL for q in questions):
        echo("Or accept every proposed example:")
        echo(f"    {resume} --accept-proposals")


def _indent(text: str, width: int) -> str:
    return "\n".join(" " * width + line if line else line for line in text.rstrip().splitlines())
