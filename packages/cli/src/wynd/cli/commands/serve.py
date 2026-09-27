"""`wynd serve <image>` (PLAN §9, §3.22, §15 item 49; `$DRAFTS/06 §9.2`).

Runs a process image as a warm container through `ctl.serve` (env gate: `EnvMissing`, exit 3) and waits for its
run API to be ready. `--detach` leaves it running and registered for `wynd run --image`; otherwise the container's
log is followed until Ctrl-C, then the container is stopped (exit 0; exit 1 if its log ends on its own).
`--stop` stops the containers matching a name or image (none: exit 3).

Outside a workspace (and without `--workspace`) the image is served by a `ServeService` over the process environment
and the `--env-file`s only: nothing is registered, no `.env` is read, and `--stop` matches nothing.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING

import typer

from wynd.cli import context
from wynd.cli.output import echo, err, fmt_sha, print_json

if TYPE_CHECKING:
    from wynd.controller.serving import ServeService

EXITED = 1


def register(app: typer.Typer) -> None:
    app.command("serve")(serve)


def serve(
    ctx: typer.Context,
    image: str = typer.Argument(..., metavar="IMAGE", help="Image reference, or a container name with --stop."),
    port: int | None = typer.Option(None, "--port", help="Host port (default: any free port)."),
    env_files: list[Path] | None = typer.Option(None, "--env-file", metavar="PATH", help="Extra .env file (repeatable)."),
    detach: bool = typer.Option(False, "--detach", "-d", help="Leave the container running and return."),
    timeout: float = typer.Option(120.0, "--timeout", help="Seconds to wait for readiness."),
    name: str | None = typer.Option(None, "--name", help="Container name (default: wynd-<process>-<commit7>)."),
    stop: bool = typer.Option(False, "--stop", help="Stop the containers serving IMAGE (or named IMAGE)."),
    json_output: bool = typer.Option(False, "--json", help="Print one JSON document on stdout."),
) -> None:
    """Serve a process image locally: its run API on 127.0.0.1."""
    from wynd.controller import docker
    from wynd.controller.errors import NotAWorkspace, NotFound

    try:
        service = context.get_controller(ctx).serve
    except NotAWorkspace:
        if ctx.ensure_object(context.CliState).workspace is not None:
            raise
        service = None
    if stop:
        stopped = service.stop(image) if service is not None else []
        if not stopped:
            hint = None if service is not None else "outside a workspace nothing is registered; use `docker rm -f`"
            raise NotFound(f"no served container matches {image!r}", hint=hint)
        if json_output:
            print_json(stopped)
            return
        for container in stopped:
            echo(f"stopped {container}")
        return
    extra = [p.absolute() for p in env_files or []]
    if service is not None:
        served = service.serve(image, port=port, extra_env_files=extra, timeout=timeout, name=name)
        stop_container: Callable[[str], object] = service.stop
    else:
        with tempfile.TemporaryDirectory(prefix="wynd-serve-") as scratch:
            served = _unregistered_service(Path(scratch)).serve(image, port=port, extra_env_files=extra,
                                                                timeout=timeout, register=False, name=name)
        stop_container = docker.rm
    if json_output:
        print_json(served)
    else:
        echo(f"serving {served.process}@{fmt_sha(served.commit)} at {served.url} (container {served.name})")
    if detach:
        return
    _follow(stop_container, served.name)


def _follow(stop: Callable[[str], object], name: str) -> None:
    """Print the container's log until Ctrl-C (exit 0) or until it ends on its own (exit 1); stop it either way."""
    from wynd.controller import docker

    logs = docker.logs_follow(name)
    try:
        for line in logs.stdout or ():
            err((line.decode() if isinstance(line, bytes) else line).rstrip("\n"))
        logs.wait()
    except KeyboardInterrupt:
        logs.terminate()
        stop(name)
        err(f"stopped {name}")
        return
    stop(name)
    err(f"container {name} stopped serving")
    raise typer.Exit(EXITED)


class _ProcessEnv:
    """The env outside a workspace: `os.environ` over the extra files (earlier files win), as `EnvService.resolve`
    orders them, without a workspace `.env`, registry secrets or a registry snapshot."""

    def resolve_image(self, pid: str, extra_files: Sequence[Path] = ()) -> dict[str, str]:
        from wynd.controller.envfile import read_env_file

        values: dict[str, str] = {}
        for path in reversed(list(extra_files)):
            values.update(read_env_file(Path(path)))
        values.update(os.environ)
        return values


def _unregistered_service(scratch: Path) -> ServeService:
    """A `ServeService` with no workspace: its record directory is an empty scratch dir that `serve(register=False)`
    only reads, and its env is `_ProcessEnv`."""
    from wynd.controller.serving import ServeService

    ctx = SimpleNamespace(state_dir=scratch, clock=lambda: datetime.now(UTC))
    return ServeService(ctx, SimpleNamespace(env=_ProcessEnv()))  # type: ignore[arg-type]
