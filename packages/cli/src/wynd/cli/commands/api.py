"""`wynd serve-api` (PLAN §9, §3.21, §3.22; `$DRAFTS/06 §9.2`, §8.1).

Serves the workspace's controller over HTTP for the web UI (`wynd.controller.api.serve`, one uvicorn worker) until
Ctrl-C. `--token` falls back to `WYND_API_TOKEN` in the controller's resolved env (the process env, the workspace
`.env`, registry secrets); the token found is passed explicitly. A non-loopback `--host` without a token prints a
warning and still serves. Schedules fire only while this command runs (the default `scheduler` trigger backend);
`--no-scheduler` turns that off.
"""

from __future__ import annotations

import ipaddress
from pathlib import Path

import typer

from wynd.cli import context
from wynd.cli.output import err

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8780
TOKEN_VAR = "WYND_API_TOKEN"


def register(app: typer.Typer) -> None:
    app.command("serve-api")(serve_api)


def serve_api(
    ctx: typer.Context,
    host: str = typer.Option(DEFAULT_HOST, "--host", help="Interface to listen on."),
    port: int = typer.Option(DEFAULT_PORT, "--port", min=1, max=65535),
    web_dist: Path | None = typer.Option(
        None, "--web-dist", exists=True, file_okay=False, resolve_path=True,
        help="Built web UI to serve at / (default: the one packaged with the controller).",
    ),
    no_scheduler: bool = typer.Option(False, "--no-scheduler", help="Do not fire scheduled releases."),
    token: str | None = typer.Option(None, "--token", help=f"Bearer token for /api (default: {TOKEN_VAR})."),
    cors_origins: list[str] | None = typer.Option(
        None, "--cors-origin", metavar="ORIGIN", help="Allowed CORS origin (repeatable; default: the Vite dev server)."
    ),
    log_level: str = typer.Option("info", "--log-level", help="critical | error | warning | info | debug | trace"),
) -> None:
    """Run the controller as a service: the HTTP API and web UI."""
    from wynd.controller import api

    ctl = context.get_controller(ctx)
    api_token = token or ctl.env.resolve().get(TOKEN_VAR) or None
    if api_token is None and not _loopback(host):
        err(f"warning: serving on {host} without an API token: anyone who can reach it can read, edit, run and "
            f"release processes (set --token or {TOKEN_VAR})")
    err(f"wynd serve-api: {_url(host, port)} (workspace {ctl.ctx.root})")
    api.serve(ctl, host=host, port=port, web_dist=web_dist, api_token=api_token, cors_origins=cors_origins or None,
              scheduler=not no_scheduler, log_level=log_level)


def _loopback(host: str) -> bool:
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _url(host: str, port: int) -> str:
    return f"http://[{host}]:{port}" if ":" in host else f"http://{host}:{port}"
