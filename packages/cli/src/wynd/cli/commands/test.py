"""`wynd test [--live]` (PLAN §9, §3.22, §3.20; `$DRAFTS/06 §9.2`).

Replay runs the step suites and process examples directly (not a job) and records the results when the process
closure is clean. `--live` submits a `test_live` job, waits (log on stderr), then integrates its branch like
`compile` does: exit 1 when the job failed, 3 on a dirty checkout, 5 when the branch is left for review.
"""

from __future__ import annotations

from typing import Any

import typer

from wynd.cli import context
from wynd.cli.commands.jobs import conclude, wait_for_job
from wynd.cli.output import echo, err, fmt_duration, fmt_sha, print_json, table

MESSAGE_LINES = 12
EXAMPLES = "process:"


def register(app: typer.Typer) -> None:
    app.command("test")(test)


def test(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID"),
    live: bool = typer.Option(False, "--live", help="Re-record cassettes in a test_live job, then integrate it."),
    no_wait: bool = typer.Option(False, "--no-wait", help="--live: submit the job and return."),
    no_integrate: bool = typer.Option(False, "--no-integrate", help="--live: leave the job's branch unintegrated."),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document on stdout."),
) -> None:
    """Run the process's tests: every step suite and the examples of every process in its closure."""
    ctl = context.get_controller(ctx)
    if live:
        job = ctl.jobs.submit_test_live(pid)
        err(f"submitted {job.id} (test_live) at {fmt_sha(job.ref)}")
        if no_wait:
            if json_output:
                print_json(job)
            else:
                echo(job.id)
            return
        job = wait_for_job(ctl, job.id)
        code = conclude(ctl, job, integrate=not no_integrate, json_output=json_output,
                        summary=lambda j: print_suites(j.report) if j.report else None)
        if code:
            raise typer.Exit(code)
        return
    report = ctl.processes.test(pid, log=err)
    if json_output:
        print_json(report)
    else:
        print_suites(report.model_dump(mode="json"))
        if report.recorded:
            echo(f"recorded for {fmt_sha(report.commit)} (process HEAD)")
        else:
            dirty = ctl.processes.status(pid).dirty
            echo(f"not recorded: uncommitted changes in {', '.join(dirty)}" if dirty
                 else "not recorded: the process has no commit yet")
    if not report.passed:
        raise typer.Exit(1)


def print_suites(report: dict[str, Any]) -> None:
    """One row per suite (`<step id>`, `<process id> (examples)`), then every failing case's message."""
    rows = []
    for suite in report["suites"]:
        counts = suite["counts"]
        took = sum(case["duration_ms"] for case in suite["cases"])
        rows.append([_suite_name(suite), f"{counts.get('passed', 0)}/{sum(counts.values())}", fmt_duration(took),
                     "ok" if suite["passed"] else "FAILED"])
    echo(table(["SUITE", "PASSED", "TIME", "RESULT"], rows))
    for suite in report["suites"]:
        if suite["passed"]:
            continue
        if suite.get("problem"):
            echo(f"{_suite_name(suite)}: {_clip(suite['problem'])}")
        for case in suite["cases"]:
            if case["outcome"] in ("failed", "error"):
                echo(f"{_suite_name(suite)}: {case['outcome'].upper()} {case['name']}")
                if case.get("message"):
                    echo(_indent(_clip(case["message"])))
    echo("passed" if report["passed"] else "FAILED")


def _suite_name(suite: dict[str, Any]) -> str:
    subject = suite["subject"]
    return f"{subject.removeprefix(EXAMPLES)} (examples)" if subject.startswith(EXAMPLES) else subject


def _clip(text: str) -> str:
    lines = text.rstrip().splitlines()
    return "\n".join(lines if len(lines) <= MESSAGE_LINES else [*lines[:MESSAGE_LINES], "…"])


def _indent(text: str) -> str:
    return "\n".join(f"    {line}" for line in text.splitlines())
