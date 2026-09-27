"""`wynd search <query…> [--flag design|compiled|built|released]…` (PLAN §9, §3.22; `$DRAFTS/06 §9.2`, §5.6).

Searches every process (id, name, goal, step names and instructions; every query word must match somewhere) through
`ctl.processes.list(q, flags)`, whose hits come sorted by score. Each requested derived-status flag must hold at the
process HEAD. Prints `PROCESS  FLAGS  MATCH` (the flags that hold, the first match with its field); no hit prints a
note on stderr and exits 0. An unknown flag exits 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import typer

from wynd.cli import context
from wynd.cli.output import echo, err, print_json, table

if TYPE_CHECKING:
    from wynd.controller.models import ProcessSummary


def register(app: typer.Typer) -> None:
    app.command("search")(search)


def search(
    ctx: typer.Context,
    query: list[str] = typer.Argument(..., metavar="QUERY...", help="Words that must all match."),
    flags: list[str] | None = typer.Option(
        None, "--flag", metavar="FLAG", help="design | compiled | built | released (repeatable; all must hold)."
    ),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document on stdout."),
) -> None:
    """Search processes by name, goal and step instructions, filtered by status flags."""
    text = " ".join(query)
    hits = context.get_controller(ctx).processes.list(text, flags or [])
    if json_output:
        print_json(hits)
        return
    if not hits:
        err(f"no process matches {text!r}" + (f" with {', '.join(flags or [])}" if flags else ""))
        return
    echo(table(["PROCESS", "FLAGS", "MATCH"], [[hit.id, _flags(hit), _first_match(hit)] for hit in hits]))


def _flags(hit: ProcessSummary) -> str:
    from wynd.controller.search import FLAGS

    if hit.status is None:
        return "-"
    return ",".join(flag for flag in FLAGS if getattr(hit.status, flag)) or "-"


def _first_match(hit: ProcessSummary) -> str:
    if not hit.matches:
        return "-"
    match = hit.matches[0]
    where = f"{match.field} {match.step}" if match.step else match.field
    return f"{where}: {match.snippet}"
