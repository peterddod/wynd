"""`wynd init, new, validate [--sync-interfaces], status` (PLAN §9, §3.22; `$DRAFTS/06 §9.2`)."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import typer

from wynd.cli import context
from wynd.cli.output import echo, err, fmt_sha, print_json, table, yes

if TYPE_CHECKING:
    from wynd.controller.models import Issue, ProcessStatus, ProcessSummary, ValidationReportDTO

LABELS = {"error": "ERROR", "warning": "WARN", "info": "INFO"}
JSON = typer.Option(False, "--json", help="Print one JSON document on stdout.")


def register(app: typer.Typer) -> None:
    app.command("init")(init)
    app.command("new")(new)
    app.command("validate")(validate)
    app.command("status")(status)


def init(
    path: Path = typer.Argument(Path("."), help="Directory to turn into a workspace."),
    no_commit: bool = typer.Option(False, "--no-commit", help="Write the files without committing them."),
    json_output: bool = JSON,
) -> None:
    """Create a workspace: wynd.yaml, the process and step roots, .gitignore/.gitattributes lines; commit them."""
    from wynd.controller.workspace import init_workspace

    result = init_workspace(path, commit=not no_commit)
    if json_output:
        print_json(result)
        return
    echo(f"initialised workspace {result.root}")
    for rel in result.created:
        echo(f"  created {rel}")
    echo(f"committed {fmt_sha(result.commit)}" if result.commit else "not committed")


def new(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID", help="Process id, e.g. finance/invoices."),
    root: str | None = typer.Option(None, "--root", help="Process root (default: the first one)."),
    goal: str | None = typer.Option(None, "--goal", help="The outcome the process achieves."),
    no_commit: bool = typer.Option(False, "--no-commit", help="Write the files without committing them."),
    json_output: bool = JSON,
) -> None:
    """Create a process from the template and commit it."""
    from wynd.controller.workspace import new_process_files

    summary = context.get_controller(ctx).processes.new(pid, goal=goal, root=root, commit=not no_commit)
    if json_output:
        print_json(summary)
        return
    files = [f"{summary.path}/{rel}" for rel in new_process_files(pid, goal=goal)]
    for rel in files:
        echo(f"created {rel}")
    proto = next(rel for rel in files if "/proto/" in rel)
    echo(f"next: edit {proto}, then wynd validate {pid}")


def validate(
    ctx: typer.Context,
    pid: str | None = typer.Argument(None, metavar="[ID]", help="Process id (default: every process)."),
    sync_interfaces: bool = typer.Option(
        False, "--sync-interfaces", help="First refresh the interface snapshots in step.lock.yaml from the code."
    ),
    json_output: bool = JSON,
) -> None:
    """Validate one process or every process; exit 1 on any error."""
    ctl = context.get_controller(ctx)
    if sync_interfaces:
        for each in [pid] if pid else ctl.ctx.workspace().process_ids():
            changed = ctl.processes.sync_interfaces(each)
            for rel in changed:
                err(f"updated {rel}")
            if not changed:
                err(f"{each}: interface snapshots in sync")
    reports = {pid: ctl.processes.validate(pid)} if pid else ctl.processes.validate_all()
    if json_output:
        print_json(reports[pid] if pid else reports)
    else:
        _print_reports(reports, single=pid is not None)
    if any(not report.ok for report in reports.values()):
        raise typer.Exit(1)


def status(
    ctx: typer.Context,
    pid: str | None = typer.Argument(None, metavar="[ID]", help="Process id (default: a table of every process)."),
    json_output: bool = JSON,
) -> None:
    """Derived status at the process HEAD: design, compiled, built, released."""
    ctl = context.get_controller(ctx)
    if pid is None:
        summaries = ctl.processes.list()
        if json_output:
            print_json(summaries)
            return
        headers = ["PROCESS", "HEAD", "DIRTY", "DESIGN", "COMPILED", "BUILT", "RELEASED", "TESTS"]
        echo(table(headers, [_status_row(s) for s in summaries]))
        for summary in summaries:
            if summary.error is not None:
                err(f"{summary.id}: {summary.error.splitlines()[0]}")
        return
    state = ctl.processes.status(pid)
    if json_output:
        print_json(state)
        return
    builds = [b for b in ctl.processes.builds(pid) if b.at_head]
    _print_status(pid, state, [f"{b.image} @{b.short}" for b in builds])


def _print_reports(reports: dict[str, ValidationReportDTO], *, single: bool) -> None:
    issues = [issue for report in reports.values() for issue in report.issues]
    for pid, report in reports.items():
        if single:
            for issue in report.issues:
                echo(_issue_line(issue))
            continue
        if not report.issues:
            echo(f"{pid}: ok")
            continue
        echo(f"{pid}:")
        for issue in report.issues:
            echo(f"  {_issue_line(issue)}")
    errors = sum(issue.severity == "error" for issue in issues)
    warnings = sum(issue.severity == "warning" for issue in issues)
    echo(f"{errors} errors, {warnings} warnings")


def _issue_line(issue: Issue) -> str:
    where = " ".join(part for part in (issue.file, _format_loc(issue.loc)) if part) or "-"
    return f"{LABELS[issue.severity]:<5}  {issue.code}  {where}  {issue.message}"


def _format_loc(loc: list[str | int]) -> str:
    """("edges", 3, "to", 0) -> "edges[3].to[0]"."""
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else f".{part}" if out else str(part)
    return out


def _status_row(summary: ProcessSummary) -> list[str]:
    state = summary.status
    if state is None:
        return [summary.id, "-", "-", "-", "-", "-", "-", "error"]
    return [
        summary.id,
        fmt_sha(state.head.commit) if state.head else "-",
        yes(bool(state.dirty)),
        yes(state.design),
        yes(state.compiled),
        yes(state.built),
        _released(state),
        state.tests,
    ]


def _released(state: ProcessStatus) -> str:
    if not state.released:
        return "-"
    ref = next((r for r in state.releases if r.state == "serving"), state.releases[0] if state.releases else None)
    return "yes" if ref is None else f"yes ({ref.short}, {ref.behind} behind)"


def _print_status(pid: str, state: ProcessStatus, builds: list[str]) -> None:
    head = f"{state.head.short}  {state.head.subject}" if state.head else "- (no commit yet)"
    rows = [
        ["process", pid],
        ["head", head],
        ["design", "yes" if state.design else "no"],
        ["compiled", "yes" if state.compiled else "no"],
        ["built", "yes" if state.built else "no"],
        ["released", "yes" if state.released else "no"],
        ["tests", state.tests],
    ]
    if state.design_steps:
        rows.append(["design steps", ", ".join(state.design_steps)])
    rows += [["build", build] for build in builds]
    rows += [["release", f"{r.id} {r.trigger} @{r.short} behind={r.behind} {r.state}"] for r in state.releases]
    rows += [["dirty", path] for path in state.dirty]
    for name, value in rows:
        echo(f"{name:<13} {value}")
