"""The `wynd` command line: root app, `--workspace/-C`, `--version` (PLAN §9, §3.22). CLI-M1 adds error mapping.

Every `commands/<group>.py` exposes `register(app)`; `main.py` calls each one.
"""

import importlib
from pathlib import Path

import typer

from wynd.cli import __version__
from wynd.cli.context import CliState

_COMMAND_MODULES = (
    "workspace", "test", "run", "env", "registries", "jobs", "build", "serve", "compile", "api", "release", "search",
    "optimise",
)

app = typer.Typer(no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)


def _print_version(value: bool) -> None:
    if not value:
        return
    typer.echo(f"wynd {__version__}")
    raise typer.Exit()


@app.callback()
def root(
    ctx: typer.Context,
    workspace: Path | None = typer.Option(
        None, "--workspace", "-C", envvar="WYND_WORKSPACE", help="Workspace root (default: search from the cwd)."
    ),
    version: bool = typer.Option(
        False, "--version", is_eager=True, callback=_print_version, help="Print the version and exit."
    ),
) -> None:
    """Wynd: design processes as graphs of steps, compile, test, build and run them."""
    ctx.obj = CliState(workspace=workspace)


for _name in _COMMAND_MODULES:
    importlib.import_module(f"wynd.cli.commands.{_name}").register(app)


def main() -> None:
    app()
