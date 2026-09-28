"""`wynd optimise` (PLAN §9, §3.22; `$DRAFTS/08 §3.8`).

Without `--apply` it prints the report (fixed-width text, or the report JSON with `--json`) and exits 0: no model
call, no job. With `--apply` it submits an `optimise` job with the report's applicable tier changes (`--unit` keeps
only those units' changes; with none left it prints "nothing to apply" — the report under `--json` — and exits 0),
waits for it (its log on stderr), prints what was applied and rejected, then integrates the branch like `compile`:
exit 1 when the job failed, 5 when the branch is left for review. `--no-wait` prints the job id and returns.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import typer

from wynd.cli import context
from wynd.cli.commands.jobs import conclude, wait_for_job
from wynd.cli.output import echo, err, fmt_usage, print_json, table
from wynd.controller.errors import Invalid

if TYPE_CHECKING:
    from wynd.controller.models import Job


def register(app: typer.Typer) -> None:
    app.command("optimise")(optimise)


def optimise(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID"),
    apply: bool = typer.Option(False, "--apply", help="Test and commit the applicable tier changes in a job."),
    units: list[str] | None = typer.Option(None, "--unit", metavar="NAME",
                                           help="--apply: only this unit's change (repeatable)."),
    max_runs: int = typer.Option(200, "--max-runs", min=1, help="How many of the newest runs to read."),
    min_runs: int = typer.Option(20, "--min-runs", min=1,
                                 help="Live executions a unit needs at its tier before a change is recommended."),
    no_wait: bool = typer.Option(False, "--no-wait", help="--apply: submit the job and return."),
    no_integrate: bool = typer.Option(False, "--no-integrate", help="--apply: leave the job's branch unintegrated."),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document on stdout."),
) -> None:
    """Recommend model tiers for a process's agentic steps and branches from the traces of its runs."""
    from wynd.controller.optimise import NothingToApply, optimise_report, render_report_text, submit_optimise

    if not apply and (units or no_wait or no_integrate):
        raise Invalid("--unit, --no-wait and --no-integrate need --apply")
    ctl = context.get_controller(ctx)
    if not apply:
        report = optimise_report(ctl, pid, max_runs=max_runs, min_runs=min_runs)
        if json_output:
            print_json(report)
        else:
            typer.echo(render_report_text(report), nl=False)
        return
    try:
        job_id, report = submit_optimise(ctl, pid, units=units or None, max_runs=max_runs, min_runs=min_runs)
    except NothingToApply as nothing:
        if json_output:
            print_json(nothing.details)
        else:
            echo(nothing.message)
        return
    changes = ", ".join(f"{c.unit} {c.from_tier} -> {c.to_tier}" for c in report.changes)
    err(f"submitted {job_id} (optimise): {changes}")
    if no_wait:
        if json_output:
            print_json(ctl.jobs.get(job_id))
        else:
            echo(job_id)
        return
    job = wait_for_job(ctl, job_id)
    code = conclude(ctl, job, integrate=not no_integrate, json_output=json_output, summary=print_result)
    if code:
        raise typer.Exit(code)


def print_result(job: Job) -> None:
    """One row per change (applied with its live test counts, or rejected with the reason), then the process
    examples and the model usage of the job's live tests."""
    result = job.report or {}
    rows = [[a["unit"], _change(a), "applied", _tests(a.get("tests")), None] for a in result.get("applied", [])]
    rows += [[r["unit"], _change(r), "rejected", None, r["reason"]] for r in result.get("rejected", [])]
    if rows:
        echo(table(["UNIT", "CHANGE", "RESULT", "TESTS", "REASON"], rows))
    if result.get("process_tests"):
        echo(f"process examples  {_tests(result['process_tests'])}")
    usage = fmt_usage(job.usage)
    if usage:
        echo(f"usage: {usage}")


def _change(entry: dict[str, Any]) -> str:
    return f"{entry['from_tier']} -> {entry['to_tier']}"


def _tests(counts: dict[str, int] | None) -> str | None:
    if not counts:
        return None
    return f"{counts.get('passed', 0)}/{counts.get('passed', 0) + counts.get('failed', 0)}"
