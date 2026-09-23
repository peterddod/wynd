"""The `wynd` command line: root app, `--workspace/-C`, `--version`, error mapping (PLAN §9, §3.22).

Every `commands/<group>.py` exposes `register(app)`; `main.py` calls each one. Commands raise controller errors
(`wynd.controller.errors.WyndError`); the root group prints `error: <message>` (the `paths` of a `DirtyTree` and
`hint: <hint>` too) on stderr — and, under `--json`, `{"error": {code, message, details, hint}}` as the one stdout
document — and exits with the error's `exit` (PLAN §3.22). Ctrl-C exits 130.
"""

import importlib
from pathlib import Path

import typer
from typer.core import TyperGroup

from wynd.cli import __version__
from wynd.cli.context import CliState
from wynd.cli.output import err, jsonable, print_json
from wynd.controller.errors import WyndError

_COMMAND_MODULES = (
    "workspace", "test", "run", "env", "registries", "jobs", "build", "serve", "compile", "api", "release", "search",
    "optimise",
)
JSON_FLAG = "--json"
INTERRUPTED = 130


def report_error(error: WyndError, *, as_json: bool) -> None:
    err(f"error: {error.message}")
    paths = error.details.get("paths") if isinstance(error.details, dict) else None
    for path in paths or []:
        err(f"  {path}")
    if error.hint:
        err(f"hint: {error.hint}")
    if as_json:
        print_json({"error": {"code": error.code, "message": error.message, "details": jsonable(error.details),
                              "hint": error.hint}})


class WyndGroup(TyperGroup):
    """The root group: every command, whichever module registered it, runs inside its `invoke`."""

    def parse_args(self, ctx: typer.Context, args: list[str]) -> list[str]:
        ctx.meta["wynd.json"] = JSON_FLAG in args
        return super().parse_args(ctx, args)

    def invoke(self, ctx: typer.Context):
        try:
            return super().invoke(ctx)
        except WyndError as error:
            report_error(error, as_json=ctx.meta.get("wynd.json", False))
            raise typer.Exit(error.exit) from None
        except KeyboardInterrupt:
            err("interrupted")
            raise typer.Exit(INTERRUPTED) from None


app = typer.Typer(cls=WyndGroup, no_args_is_help=True, add_completion=False, pretty_exceptions_enable=False)


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
