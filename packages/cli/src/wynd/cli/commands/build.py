"""`wynd build`, `wynd bake`, `wynd base build|publish` (PLAN §9, §3.22, §6.5, §15 item 50; `$DRAFTS/06 §9.2`).

`build` and `bake` submit a job (the controller refuses a dirty workspace, exit 3), stream its log to stderr and
wait; a failed or cancelled job exits 1. `base build|publish` are plain controller functions (no job, no workspace);
a version other than the runtime's is `VersionMismatch` (exit 3).
"""

from __future__ import annotations

import shutil
from pathlib import Path
from typing import TYPE_CHECKING

import typer

from wynd.cli import context
from wynd.cli.commands.jobs import wait_for_job
from wynd.cli.output import echo, err, fmt_sha, print_json

if TYPE_CHECKING:
    from wynd.controller import Controller
    from wynd.controller.models import Job

FAILED = 1
VARIANTS = {"slim": ["slim"], "alpine": ["alpine"], "all": ["slim", "alpine"]}
TESTS_SOURCE = {"ran": "ran", "registry": "reused recorded result"}
JSON = typer.Option(False, "--json", help="Print one JSON document on stdout.")
NO_WAIT = typer.Option(False, "--no-wait", help="Submit the job and return.")
VARIANT = typer.Option("all", "--variant", help="slim | alpine | all")

base_app = typer.Typer(no_args_is_help=True, help="Wynd base images (one per runtime version and variant).")


def register(app: typer.Typer) -> None:
    app.command("build")(build)
    app.command("bake")(bake)
    app.add_typer(base_app, name="base")


def build(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID"),
    registry: str | None = typer.Option(None, "--registry", metavar="NAME",
                                        help="Image registry (default: the default registry, if any)."),
    push: bool | None = typer.Option(None, "--push/--no-push", help="Push the image (default: when a registry is set)."),
    platform: str | None = typer.Option(None, "--platform", metavar="P", help="e.g. linux/amd64"),
    no_wait: bool = NO_WAIT,
    json_output: bool = JSON,
) -> None:
    """Build the process image at its process HEAD (tests must pass there)."""
    ctl = context.get_controller(ctx)
    job = ctl.jobs.submit_build(pid, registry=registry, push=push, platform=platform)
    job = _wait(ctl, job, no_wait=no_wait, json_output=json_output)
    if job is None:
        return
    if json_output:
        print_json(job)
    elif job.status == "succeeded":
        print_build(ctl, job)
    _exit_unless_succeeded(job)


def bake(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID"),
    output: Path | None = typer.Option(None, "--output", help="Copy the .pyz here (a file or a directory)."),
    no_wait: bool = NO_WAIT,
    json_output: bool = JSON,
) -> None:
    """Bake the process into a self-contained .pyz (deterministic processes with one environment only)."""
    ctl = context.get_controller(ctx)
    job = ctl.jobs.submit_bake(pid)
    job = _wait(ctl, job, no_wait=no_wait, json_output=json_output)
    if job is None:
        return
    copied = None
    if job.status == "succeeded" and output is not None:
        copied = Path(shutil.copy(job.artefacts["path"], output.absolute()))
    if json_output:
        print_json(job)
    elif job.status == "succeeded":
        echo(f"baked {job.process_id}@{fmt_sha(job.artefacts.get('commit') or job.ref)}")
        echo(f"artefact {job.artefacts['path']} ({job.artefacts.get('size_bytes', '?')} bytes)")
        if copied is not None:
            echo(f"copied to {copied}")
    _exit_unless_succeeded(job)


@base_app.command("build")
def base_build(
    version: str = typer.Argument(..., metavar="VERSION"),
    variant: str = VARIANT,
    json_output: bool = JSON,
) -> None:
    """Build the base image(s) locally."""
    from wynd.controller import base

    _images(base.build_base(version, _variants(variant), log=err), json_output=json_output)


@base_app.command("publish")
def base_publish(
    version: str = typer.Argument(..., metavar="VERSION"),
    registry: str = typer.Option(..., "--registry", metavar="NAME", help="Image registry from `wynd registry`."),
    variant: str = VARIANT,
    json_output: bool = JSON,
) -> None:
    """Build the base image(s) for linux/amd64 and linux/arm64 and push them."""
    from wynd.controller import base

    _images(base.publish_base(version, _variants(variant), registry, log=err), json_output=json_output)


def print_build(ctl: Controller, job: Job) -> None:
    result = job.build
    if result is None:
        echo(f"built {job.process_id}@{fmt_sha(job.ref)}")
        return
    echo(f"built {job.process_id}@{fmt_sha(result.commit)}")
    echo(f"image {result.image}")
    if result.pushed and result.image_digest:
        echo(f"digest {result.image_digest}")
    echo(f"tests {result.tests.passed}/{result.tests.total} ({TESTS_SOURCE[result.tests.source]})")
    echo(f"artefacts {_relative(Path(result.build_dir), ctl.ctx.root)}")


def _wait(ctl: Controller, job: Job, *, no_wait: bool, json_output: bool) -> Job | None:
    """Wait for the submitted job; None when `--no-wait` printed it already."""
    err(f"submitted {job.id} ({job.kind}) at {fmt_sha(job.ref)}")
    if not no_wait:
        return wait_for_job(ctl, job.id)
    if json_output:
        print_json(job)
    else:
        echo(job.id)
    return None


def _exit_unless_succeeded(job: Job) -> None:
    if job.status == "succeeded":
        return
    err(f"job {job.id} {job.status}" + (f": {job.error.message}" if job.error else ""))
    raise typer.Exit(FAILED)


def _variants(variant: str) -> list[str]:
    from wynd.controller.errors import Invalid

    if variant not in VARIANTS:
        raise Invalid(f"--variant must be one of {', '.join(VARIANTS)}, got {variant!r}")
    return VARIANTS[variant]


def _images(refs: list[str], *, json_output: bool) -> None:
    if json_output:
        print_json(refs)
        return
    for ref in refs:
        echo(ref)


def _relative(path: Path, root: Path) -> str:
    return f"{path.relative_to(root)}/" if path.is_relative_to(root) else str(path)
