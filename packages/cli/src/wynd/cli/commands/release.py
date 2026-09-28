"""`wynd release create|list|show|run|enable|disable|delete|fires` (PLAN §9, §3.22; `$DRAFTS/06 §9.2`, §5.14).

A release is a built process at a commit, served by the serving backend and fired by its trigger (manual, schedule
or webhook; every release can also be run with `wynd release run`). `create --commit HEAD` means the process HEAD
(the last commit touching its reference closure), which is what `wynd build` builds. `--cron`/`--tz` and the
`--input`/`--inputs`/`--inputs-file` fixed inputs apply to schedule triggers only, `--secret-env` to webhook
triggers only; `--env VAR=VALUE` binds a literal, `--env-from VAR=HOSTVAR` a variable of the controller's env.

The release service starts containers and mirrors runs in background threads of this process, so `create` and
`enable` wait until the release's container is up (or failed to start) and `run` waits until the run has ended and
been mirrored into the workspace's stores; `run --follow` also prints each step as it finishes, as `wynd run` does.
`run` exits 1 when the run ended in `$exit.error`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

import typer

from wynd.cli import context
from wynd.cli.commands.run import StepLines
from wynd.cli.inputs import parse_inputs
from wynd.cli.output import echo, err, print_json, table
from wynd.controller.errors import Conflict, Invalid

if TYPE_CHECKING:
    from wynd.controller import Controller
    from wynd.controller.api.models_web import EnvBinding, Release, Trigger
    from wynd.controller.models import Run

TRIGGERS = ("manual", "schedule", "webhook")
TRIGGER_OF_FLAG = {"--cron": "schedule", "--tz": "schedule", "--input/--inputs/--inputs-file": "schedule",
                   "--secret-env": "webhook"}
FAILED = 1
JSON = typer.Option(False, "--json", help="Print one JSON document on stdout.")
INPUT = typer.Option(None, "--input", metavar="KEY=VALUE", help="One input (repeatable).")
INPUTS_JSON = typer.Option(None, "--inputs", metavar="JSON", help="Inputs as a JSON object.")
INPUTS_FILE = typer.Option(None, "--inputs-file", metavar="PATH|-", help="Inputs as JSON or YAML.")
RELEASE = typer.Argument(..., metavar="RELEASE", help="Release id.")

release_app = typer.Typer(no_args_is_help=True, help="Releases: a built process at a commit, served and triggered.")


def register(app: typer.Typer) -> None:
    app.add_typer(release_app, name="release")


@release_app.command("create")
def create(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID"),
    commit: str = typer.Option(..., "--commit", metavar="SHA|HEAD", help="The built commit; HEAD = the process HEAD."),
    trigger: str = typer.Option(..., "--trigger", metavar="manual|schedule|webhook"),
    cron: str | None = typer.Option(None, "--cron", metavar="EXPR", help="Schedule: five-field cron or @daily etc."),
    tz: str | None = typer.Option(None, "--tz", metavar="ZONE", help="Schedule: IANA time zone (default UTC)."),
    secret_env: str | None = typer.Option(
        None, "--secret-env", metavar="VAR", help="Webhook: env var holding the secret (default: WYND_API_TOKEN)."
    ),
    pairs: list[str] | None = INPUT,
    inputs_json: str | None = INPUTS_JSON,
    inputs_file: str | None = INPUTS_FILE,
    values: list[str] | None = typer.Option(None, "--env", metavar="VAR=VALUE", help="Bind a literal (repeatable)."),
    sources: list[str] | None = typer.Option(
        None, "--env-from", metavar="VAR=HOSTVAR", help="Bind a variable of the controller's env (repeatable)."
    ),
    disabled: bool = typer.Option(False, "--disabled", help="Create it disabled (not served, never fired)."),
    json_output: bool = JSON,
) -> None:
    """Release a built process: its image, a trigger and an env binding."""
    from wynd.controller.api.models_web import CreateReleaseRequest, ManualTrigger, ScheduleTrigger, WebhookTrigger

    has_inputs = bool(pairs) or inputs_json is not None or inputs_file is not None
    given = {"--cron": cron is not None, "--tz": tz is not None, "--input/--inputs/--inputs-file": has_inputs,
             "--secret-env": secret_env is not None}
    _check_trigger_flags(trigger, given)
    env = _bindings(values or [], sources or [])
    ctl = context.get_controller(ctx)
    match trigger:
        case "schedule":
            inputs = parse_inputs(ctl, pid, pairs=pairs or [], inputs_json=inputs_json, inputs_file=inputs_file)
            chosen: Trigger = ScheduleTrigger(cron=cron, timezone=tz, inputs=inputs)
        case "webhook":
            chosen = WebhookTrigger(secret_env=secret_env)
        case _:
            chosen = ManualTrigger()
    request = CreateReleaseRequest(process_id=pid, commit=_commit(ctl, pid, commit), trigger=chosen, env=env,
                                   enabled=not disabled)
    release = _settled(ctl, ctl.releases.create(request))
    if json_output:
        print_json(release)
    else:
        print_release(release)
    match trigger:
        case "schedule":
            err(f"note: schedules fire while 'wynd serve-api' is running (trigger backend: {ctl.ctx.triggers.name})")
        case "webhook":
            err("note: webhooks are received while 'wynd serve-api' is running")


@release_app.command("list")
def list_releases(
    ctx: typer.Context,
    pid: str | None = typer.Argument(None, metavar="[ID]", help="Only the releases of this process."),
    json_output: bool = JSON,
) -> None:
    """Releases, newest first."""
    releases = context.get_controller(ctx).releases.list(process_id=pid)
    if json_output:
        print_json(releases)
        return
    rows = [[r.id, r.process_id, r.short, r.behind, r.trigger.kind, "yes" if r.enabled else "no", r.state,
             _when(r.next_fire_at)] for r in releases]
    echo(table(["RELEASE", "PROCESS", "COMMIT", "BEHIND", "TRIGGER", "ENABLED", "STATE", "NEXT FIRE"], rows))


@release_app.command("show")
def show(ctx: typer.Context, release_id: str = RELEASE, json_output: bool = JSON) -> None:
    """One release."""
    release = context.get_controller(ctx).releases.get(release_id)
    if json_output:
        print_json(release)
        return
    print_release(release)


@release_app.command("run")
def run(
    ctx: typer.Context,
    release_id: str = RELEASE,
    pairs: list[str] | None = INPUT,
    inputs_json: str | None = INPUTS_JSON,
    inputs_file: str | None = INPUTS_FILE,
    follow: bool = typer.Option(False, "--follow", help="Print each step as it finishes."),
    json_output: bool = JSON,
) -> None:
    """Fire a release manually (default inputs: a schedule's fixed inputs) and wait for the run to end."""
    ctl = context.get_controller(ctx)
    inputs = None
    if pairs or inputs_json is not None or inputs_file is not None:
        pid = ctl.releases.get(release_id).process_id
        inputs = parse_inputs(ctl, pid, pairs=pairs or [], inputs_json=inputs_json, inputs_file=inputs_file)
    started = ctl.releases.trigger(release_id, inputs, source="manual")
    if not json_output:
        echo(f"run: {started.id}")
    on_event = StepLines(echo, full=False) if follow and not json_output else _ignore
    result = ctl.runs.follow(started.id, on_event=on_event)
    _settle(ctl)
    if json_output:
        print_json(result)
    else:
        _print_result(result)
    if result.status == "failed":
        raise typer.Exit(FAILED)


@release_app.command("enable")
def enable(ctx: typer.Context, release_id: str = RELEASE, json_output: bool = JSON) -> None:
    """Enable a release: serve it and let its trigger fire."""
    _set_enabled(ctx, release_id, True, json_output)


@release_app.command("disable")
def disable(ctx: typer.Context, release_id: str = RELEASE, json_output: bool = JSON) -> None:
    """Disable a release: stop its container; its trigger no longer fires."""
    _set_enabled(ctx, release_id, False, json_output)


@release_app.command("delete")
def delete(ctx: typer.Context, release_id: str = RELEASE) -> None:
    """Delete a release and its container (its fire history is kept)."""
    context.get_controller(ctx).releases.delete(release_id)
    echo(f"deleted {release_id}")


@release_app.command("fires")
def fires(ctx: typer.Context, release_id: str = RELEASE, json_output: bool = JSON) -> None:
    """A release's trigger fires, newest first."""
    found = context.get_controller(ctx).releases.fires(release_id)
    if json_output:
        print_json(found)
        return
    rows = [[f.id, f.source, f.at.isoformat(timespec="seconds"), f.run_id, "yes" if f.ok else "no",
             f.error.splitlines()[0] if f.error else None] for f in found]
    echo(table(["FIRE", "SOURCE", "AT", "RUN", "OK", "ERROR"], rows))


def print_release(release: Release) -> None:
    behind = f" ({release.behind} behind)" if release.behind else ""
    rows = [
        ["release", release.id],
        ["process", f"{release.process_id}@{release.short}{behind}"],
        ["image", release.image],
        ["trigger", _trigger_text(release.trigger)],
        ["enabled", "yes" if release.enabled else "no"],
        ["state", release.state + (f": {release.state_detail}" if release.state_detail else "")],
    ]
    if release.trigger.kind == "schedule" and release.trigger.inputs:
        rows.append(["inputs", json.dumps(release.trigger.inputs, ensure_ascii=False)])
    rows += [["env", _binding_text(name, binding)] for name, binding in sorted(release.env.items())]
    if release.next_fire_at is not None:
        rows.append(["next fire", _when(release.next_fire_at, getattr(release.trigger, "timezone", None))])
    if release.webhook_url is not None:
        secret = release.trigger.secret_env or "WYND_API_TOKEN"
        rows.append(["webhook", f"POST {release.webhook_url} on the serve-api address "
                                f"(Authorization: Bearer ${secret})"])
    for name, value in rows:
        echo(f"{name:<10} {value}")


def _check_trigger_flags(kind: str, given: dict[str, bool]) -> None:
    if kind not in TRIGGERS:
        raise Invalid(f"--trigger must be one of {', '.join(TRIGGERS)}, got {kind!r}")
    for flag, used in given.items():
        if used and TRIGGER_OF_FLAG[flag] != kind:
            raise Invalid(f"{flag} applies to {TRIGGER_OF_FLAG[flag]} triggers only (this one is {kind})")
    if kind == "schedule" and not given["--cron"]:
        raise Invalid("a schedule trigger needs --cron EXPR")


def _bindings(values: list[str], sources: list[str]) -> dict[str, EnvBinding]:
    from wynd.controller.api.models_web import FromEnvBinding, ValueBinding

    env: dict[str, EnvBinding] = {}
    for option, pairs in (("--env", values), ("--env-from", sources)):
        for pair in pairs:
            name, sep, value = pair.partition("=")
            name = name.strip()
            if not sep or not name:
                raise Invalid(f"{option} expects {'VAR=VALUE' if option == '--env' else 'VAR=HOSTVAR'}, got {pair!r}")
            if name in env:
                raise Invalid(f"env var {name} is bound twice")
            env[name] = ValueBinding(value=value) if option == "--env" else FromEnvBinding(from_env=value.strip())
    return env


def _commit(ctl: Controller, pid: str, commit: str) -> str:
    """`HEAD` is the process HEAD (the commit a build of the current process is keyed by); anything else is passed
    through for the release service to resolve."""
    if commit != "HEAD":
        return commit
    head = ctl.processes.status(pid).head
    if head is None:
        raise Conflict(f"process '{pid}' has no commit yet", hint=f"commit it, then `wynd build {pid}`")
    return head.commit


def _set_enabled(ctx: typer.Context, release_id: str, enabled: bool, json_output: bool) -> None:
    from wynd.controller.api.models_web import ReleasePatch

    ctl = context.get_controller(ctx)
    release = _settled(ctl, ctl.releases.update(release_id, ReleasePatch(enabled=enabled)))
    if json_output:
        print_json(release)
        return
    detail = f": {release.state_detail}" if release.state_detail else ""
    echo(f"{release.id}: {'enabled' if enabled else 'disabled'} ({release.state}{detail})")


def _settled(ctl: Controller, release: Release) -> Release:
    """The release once the service's background start of its container has finished (it runs in this process)."""
    if any(thread.is_alive() for thread in ctl.releases.threads):
        err(f"starting {release.id} …")
    _settle(ctl)
    return ctl.releases.get(release.id)


def _settle(ctl: Controller) -> None:
    for thread in list(ctl.releases.threads):
        thread.join()


def _ignore(event: dict[str, Any]) -> None:
    return None


def _print_result(result: Run) -> None:
    echo(f"exit: {result.exit}")
    if result.error is not None:
        error = result.error
        echo(f"error: {error.cause}{f' at {error.step}' if error.step else ''}: {error.message}")
        echo(f"trace: wynd trace {result.id}")
        return
    echo(json.dumps(result.outputs or {}, indent=2, ensure_ascii=False))


def _trigger_text(trigger: Any) -> str:
    match trigger.kind:
        case "schedule":
            return f"schedule {trigger.cron} ({trigger.timezone or 'UTC'})"
        case "webhook" if trigger.secret_env:
            return f"webhook (secret {trigger.secret_env})"
    return trigger.kind


def _binding_text(name: str, binding: Any) -> str:
    value = getattr(binding, "value", None)
    return f"{name}={value}" if value is not None else f"{name} from ${binding.from_env}"


def _when(at: datetime | None, timezone: str | None = None) -> str:
    if at is None:
        return "-"
    return at.astimezone(ZoneInfo(timezone) if timezone else UTC).isoformat(timespec="minutes")
