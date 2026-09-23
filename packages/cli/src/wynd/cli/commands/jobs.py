"""`wynd jobs list|show|logs|answer|cancel|integrate` (PLAN §9, §3.22; `$DRAFTS/06 §9.2`).

Also the end of every waited-for job command (`wait_for_job`, `conclude`, `print_integration`), shared with
`wynd test --live` and the later `compile`/`optimise` commands: exit 1 for a failed or cancelled job, 4 for a job
awaiting input, 5 when integration left the branch for review (`pr_branch`).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import typer

from wynd.cli import context
from wynd.cli.inputs import parse_answers
from wynd.cli.output import echo, err, fmt_age, fmt_duration, fmt_sha, print_json, table
from wynd.controller.errors import Invalid

if TYPE_CHECKING:
    from wynd.controller import Controller
    from wynd.controller.models import Job

FAILED, AWAITING_INPUT, LEFT_FOR_REVIEW = 1, 4, 5
FOLLOW_POLL_S = 0.5
JSON = typer.Option(False, "--json", help="Print one JSON document on stdout.")

jobs_app = typer.Typer(no_args_is_help=True, help="Background jobs: compile, test --live, build, bake, optimise.")


def register(app: typer.Typer) -> None:
    app.add_typer(jobs_app, name="jobs")


@jobs_app.command("list")
def list_jobs(
    ctx: typer.Context,
    process: str | None = typer.Option(None, "--process", help="Only jobs of this process."),
    kind: str | None = typer.Option(None, "--kind", help="compile | test_live | build | bake | optimise"),
    status: str | None = typer.Option(None, "--status", help="queued | running | awaiting_input | succeeded | …"),
    active: bool = typer.Option(False, "--active", help="Unfinished jobs and succeeded ones not yet integrated."),
    limit: int = typer.Option(50, "--limit", min=1),
    json_output: bool = JSON,
) -> None:
    """Jobs, newest first."""
    jobs = context.get_controller(ctx).jobs.list(process_id=process, kind=kind, status=status, active=active,
                                                 limit=limit)
    if json_output:
        print_json(jobs)
        return
    now = datetime.now(UTC)
    rows = [[job.id, job.kind, job.process_id, job.status, fmt_sha(job.ref), fmt_age(job.created_at, now),
             fmt_duration(_duration_ms(job)), job.integration.mode if job.integration else "-"] for job in jobs]
    echo(table(["JOB", "KIND", "PROCESS", "STATUS", "REF", "AGE", "DURATION", "INTEGRATION"], rows))


@jobs_app.command("show")
def show(ctx: typer.Context, job_id: str = typer.Argument(..., metavar="JOB"), json_output: bool = JSON) -> None:
    """One job."""
    job = context.get_controller(ctx).jobs.get(job_id)
    if json_output:
        print_json(job)
        return
    print_job(job)


@jobs_app.command("logs")
def logs(
    ctx: typer.Context,
    job_id: str = typer.Argument(..., metavar="JOB"),
    follow: bool = typer.Option(False, "--follow", "-f", help="Keep printing until the job finishes."),
) -> None:
    """The job's log."""
    ctl = context.get_controller(ctx)
    offset = 0
    while True:
        chunk = ctl.jobs.logs(job_id, offset)
        if chunk.text:
            typer.echo(chunk.text, nl=False)
        offset = chunk.offset
        if chunk.done or not follow:
            return
        time.sleep(FOLLOW_POLL_S)


@jobs_app.command("answer")
def answer(
    ctx: typer.Context,
    job_id: str = typer.Argument(..., metavar="JOB"),
    answers_file: Path | None = typer.Option(None, "--answers", help="YAML mapping of question id -> answer."),
    pairs: list[str] | None = typer.Option(None, "--answer", metavar="QID=TEXT", help="One answer (repeatable)."),
    json_output: bool = JSON,
) -> None:
    """Answer a compile job's questions; it resumes once none is pending (no waiting here)."""
    answers = parse_answers(answers_file, pairs or [])
    if not answers:
        raise Invalid("no answers given: use --answers FILE or --answer QID=TEXT")
    job = context.get_controller(ctx).jobs.answer(job_id, answers)
    if json_output:
        print_json(job)
        return
    echo(f"{job.id}: {job.status}")
    for question in pending_questions(job):
        echo(f"[{question.get('id')}] {question.get('text')}")


@jobs_app.command("cancel")
def cancel(ctx: typer.Context, job_id: str = typer.Argument(..., metavar="JOB"), json_output: bool = JSON) -> None:
    """Cancel a queued, running or waiting job."""
    job = context.get_controller(ctx).jobs.cancel(job_id)
    if json_output:
        print_json(job)
        return
    echo(f"{job.id}: {job.status}")


@jobs_app.command("integrate")
def integrate(ctx: typer.Context, job_id: str = typer.Argument(..., metavar="JOB"), json_output: bool = JSON) -> None:
    """Integrate a succeeded job's branch into its target branch (exit 5 when left for review)."""
    job = context.get_controller(ctx).jobs.integrate(job_id)
    if json_output:
        print_json(job)
    else:
        print_integration(job)
    code = integration_exit(job)
    if code:
        raise typer.Exit(code)


# --- shared by the commands that submit a job and wait for it ----------------------------------------------------

def wait_for_job(ctl: Controller, job_id: str) -> Job:
    """Wait, streaming the job log to stderr; on Ctrl-C say how to keep following (the job keeps running)."""
    try:
        return ctl.jobs.wait(job_id, on_log=lambda text: typer.echo(text, nl=False, err=True))
    except KeyboardInterrupt:
        err(f"job continues: wynd jobs logs {job_id} -f")
        raise


def conclude(
    ctl: Controller, job: Job, *, integrate: bool, json_output: bool, summary: Callable[[Job], None] | None = None
) -> int:
    """Finish a waited-for commit-producing job: integrate it when it succeeded (unless `integrate` is False), print
    the outcome (`summary` first); -> the exit code."""
    if job.status == "succeeded" and integrate:
        job = ctl.jobs.integrate(job.id)
    if json_output:
        print_json(job)
    else:
        if summary is not None:
            summary(job)
        _print_outcome(job)
    match job.status:
        case "succeeded":
            return integration_exit(job)
        case "awaiting_input":
            return AWAITING_INPUT
    return FAILED


def integration_exit(job: Job) -> int:
    return LEFT_FOR_REVIEW if job.integration is not None and job.integration.mode == "pr_branch" else 0


def print_integration(job: Job) -> None:
    result = job.integration
    if result is None:
        echo(f"not integrated: {job.id} is {job.status}")
        return
    match result.mode:
        case "fast_forward":
            echo(f"fast-forwarded {result.target} to {fmt_sha(result.head)}")
        case "rebased":
            echo(f"rebased onto {result.target} past {result.skipped_commits} unrelated commit(s) → "
                 f"{fmt_sha(result.head)}")
        case "noop":
            echo("nothing to integrate" + (f" ({result.reason})" if result.reason else ""))
        case "pr_branch":
            conflicts = f" (conflicts: {', '.join(result.conflicts)})" if result.conflicts else ""
            echo(f"left for review: {result.branch}{conflicts}")
            if result.reason:
                err(result.reason)
            err(f"hint: git push origin {result.branch} && gh pr create --head {result.branch}")


def print_job(job: Job) -> None:
    rows: list[tuple[str, Any]] = [
        ("job", job.id), ("kind", job.kind), ("process", job.process_id), ("status", job.status),
        ("ref", fmt_sha(job.ref)), ("created", job.created_at.isoformat(timespec="seconds")),
    ]
    if job.started_at is not None:
        rows.append(("started", job.started_at.isoformat(timespec="seconds")))
    if job.finished_at is not None:
        rows += [("finished", job.finished_at.isoformat(timespec="seconds")),
                 ("duration", fmt_duration(_duration_ms(job)))]
    if job.branch:
        rows += [("branch", job.branch), ("result", fmt_sha(job.result_commit))]
    if job.error is not None:
        rows.append(("error", job.error.message))
    if job.integration is not None:
        rows.append(("integration", f"{job.integration.mode} into {job.integration.target}"))
    for name, value in rows:
        echo(f"{name:<12} {value}")
    for question in pending_questions(job):
        echo(f"[{question.get('id')}] {question.get('text')}")


def pending_questions(job: Job) -> list[dict]:
    return [q for q in (job.session or {}).get("questions", []) if q.get("status") == "pending"]


def _print_outcome(job: Job) -> None:
    match job.status:
        case "succeeded" if job.integration is not None:
            print_integration(job)
        case "succeeded":
            echo(f"succeeded; branch {job.branch} not integrated (wynd jobs integrate {job.id})" if job.branch
                 else "succeeded")
        case "awaiting_input":
            err(f"job {job.id} is awaiting input")
        case _:
            err(f"job {job.id} {job.status}" + (f": {job.error.message}" if job.error else ""))
            if job.branch:
                err(f"branch: {job.branch}")


def _duration_ms(job: Job) -> float | None:
    if job.started_at is None or job.finished_at is None:
        return None
    return (job.finished_at - job.started_at).total_seconds() * 1000
