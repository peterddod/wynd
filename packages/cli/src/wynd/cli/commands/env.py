"""`wynd env check` (PLAN §9, §3.22, §3.10; `$DRAFTS/06 §9.2`, §10.4 `EnvCheckReport`).

One row per variable of the process's env manifest (the build's at the process HEAD when built, else assembled from
the step locks): whether it is required, set (values are never printed) and where it would come from — `env` (the
process environment), `file` (an `--env-file`), `dotenv` (the workspace `.env`), `registry` (user-registry secrets),
`default`, else `missing` (required) or `unset` (optional). Local mode: the claude-code auth group is only a warning.
Exit 1 when a required variable is missing.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from wynd.cli import context
from wynd.cli.output import echo, print_json, table

if TYPE_CHECKING:
    from wynd.controller import Controller
    from wynd.controller.models import EnvCheckRow, EnvVar

LABELS = {"error": "ERROR", "warning": "WARN", "info": "INFO"}

env_app = typer.Typer(no_args_is_help=True, help="The environment variables a process needs.")


def register(app: typer.Typer) -> None:
    app.add_typer(env_app, name="env")


@env_app.command("check")
def check(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID"),
    env_files: list[Path] | None = typer.Option(None, "--env-file", help="Extra KEY=VALUE file (repeatable)."),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document on stdout."),
) -> None:
    """Check that every variable the process needs is set (locally)."""
    from wynd.controller.models import EnvCheckReport

    ctl = context.get_controller(ctx)
    files = list(env_files or [])
    manifest = ctl.env.manifest(pid)
    result = ctl.env.check(pid, extra_files=files, mode="local")
    rows = env_rows(ctl, manifest.vars, files)
    if json_output:
        print_json(EnvCheckReport(process=pid, manifest="build" if manifest.commit else "assembled",
                                  commit=manifest.commit, ok=result.ok, vars=rows))
    else:
        echo(table(["VAR", "REQUIRED", "SET", "SOURCE", "USED BY", "DESCRIPTION"],
                   [[row.name, _required(var), "yes" if row.set else "no", row.source, ", ".join(row.used_by) or "-",
                     row.description or "-"] for row, var in zip(rows, manifest.vars)]))
        echo(f"manifest: build @{manifest.commit[:7]}" if manifest.commit else "manifest: assembled from step locks")
        for issue in result.issues:
            echo(f"{LABELS[issue.severity]:<5}  {issue.code}  {issue.message}")
        echo("ok" if result.ok else f"missing: {', '.join(result.missing)}")
    if not result.ok:
        raise typer.Exit(1)


def env_rows(ctl: Controller, variables: Sequence[EnvVar], files: Sequence[Path]) -> list[EnvCheckRow]:
    """One `EnvCheckRow` per variable, against `EnvService.resolve(files)`."""
    from wynd.controller.models import EnvCheckRow

    resolved = ctl.env.resolve(files)
    layers = _layers(ctl, files)
    return [
        EnvCheckRow(name=var.name, required=var.required, secret=var.secret, description=var.description,
                    used_by=list(var.used_by), set=bool(resolved.get(var.name)), source=_source(var, layers))
        for var in variables
    ]


def _layers(ctl: Controller, files: Sequence[Path]) -> list[tuple[str, Mapping[str, str]]]:
    """The sources of `EnvService.resolve`, highest first. A process-environment value equal to the workspace
    `.env` value came from `.env` (`Controller.open` loads it into the environment)."""
    from wynd.controller.envfile import ENV_FILE, read_env_file

    dotenv = read_env_file(ctl.ctx.root / ENV_FILE)
    environ = {name: value for name, value in ctl.ctx.env.items() if dotenv.get(name) != value}
    return [("env", environ), *[("file", read_env_file(path)) for path in files], ("dotenv", dotenv),
            ("registry", ctl.ctx.stores.registry.secrets())]


def _source(var: EnvVar, layers: list[tuple[str, Mapping[str, str]]]) -> str:
    for label, values in layers:
        if values.get(var.name):
            return label
    if var.default is not None:
        return "default"
    return "missing" if var.required and var.one_of is None else "unset"


def _required(var: EnvVar) -> str:
    if var.one_of is not None:
        return f"one of {var.one_of}"
    return "yes" if var.required and var.default is None else "no"
