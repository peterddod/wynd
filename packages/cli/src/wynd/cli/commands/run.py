"""`wynd run --local|--image, wynd trace` (PLAN §9, §3.22, §3.13; `$DRAFTS/06 §9.2`, tree format §5.10).

`run` streams one tree line per finished step (indented by step-path depth), then prints the run id, the exit and
the outputs (or the `ProcessError` and `wynd trace <run_id>`); exit 1 iff the run ended in `$exit.error`. A run the
env gate refuses (`EnvMissing`), an image that is not built (`NotBuilt`) or an unreachable backend exit 3.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import typer

from wynd.cli import context
from wynd.cli.inputs import parse_inputs
from wynd.cli.output import echo, err, fmt_duration, fmt_usage, print_json
from wynd.controller.errors import Invalid

NAME_WIDTH, EXIT_WIDTH, TIME_WIDTH = 19, 7, 8
EXTRAS_LIMIT, FULL_LIMIT = 80, 400
JSON = typer.Option(False, "--json", help="Print one JSON document on stdout.")


def register(app: typer.Typer) -> None:
    app.command("run")(run)
    app.command("trace")(trace)


def run(
    ctx: typer.Context,
    pid: str = typer.Argument(..., metavar="ID"),
    local: bool = typer.Option(True, "--local/--image", help="Run locally (default) or in the built image."),
    commit: str | None = typer.Option(None, "--commit", metavar="SHA", help="--image: the build at this commit."),
    pairs: list[str] | None = typer.Option(None, "--input", metavar="KEY=VALUE", help="One input (repeatable)."),
    inputs_json: str | None = typer.Option(None, "--inputs", metavar="JSON", help="Inputs as a JSON object."),
    inputs_file: str | None = typer.Option(None, "--inputs-file", metavar="PATH|-", help="Inputs as JSON or YAML."),
    full: bool = typer.Option(False, "--full", help="Also print every step's inputs and outputs."),
    json_output: bool = JSON,
) -> None:
    """Run a process and print its steps as they finish."""
    from wynd.controller.models import ImageTarget, LocalTarget

    if local and commit is not None:
        raise Invalid("--commit applies to --image runs only")
    ctl = context.get_controller(ctx)
    inputs = parse_inputs(ctl, pid, pairs=pairs or [], inputs_json=inputs_json, inputs_file=inputs_file)
    target = LocalTarget() if local else ImageTarget(commit=commit)
    on_event = None if json_output else StepLines(echo, full=full)
    result = ctl.runs.run(pid, inputs, target=target, on_event=on_event, trigger="manual")
    if json_output:
        print_json(result)
    else:
        echo(f"run: {result.id}")
        echo(f"exit: {result.exit}")
        if result.error is not None:
            error = result.error
            echo(f"error: {error.cause}{f' at {error.step}' if error.step else ''}: {error.message}")
            echo(f"trace: wynd trace {result.id}")
        else:
            echo(json.dumps(result.outputs or {}, indent=2, ensure_ascii=False))
    if result.status == "failed":
        raise typer.Exit(1)


def trace(
    ctx: typer.Context,
    run_id: str = typer.Argument(..., metavar="RUN_ID"),
    full: bool = typer.Option(False, "--full", help="Also print every step's inputs and outputs."),
    follow: bool = typer.Option(False, "--follow", help="Wait for a running run to end (steps on stderr)."),
    json_output: bool = typer.Option(False, "--json", help="Print the raw trace events as one JSON document."),
) -> None:
    """Print a run's trace as a tree."""
    from wynd.controller.runs.tree import render_events

    ctl = context.get_controller(ctx)
    if follow:
        ctl.runs.follow(run_id, on_event=StepLines(err, full=False))
    events = ctl.runs.events(run_id)
    if json_output:
        print_json(events)
        return
    echo(render_events(events, full=full))


class StepLines:
    """`on_event` that writes one line per `step.end`: name (`#n` for a repeated run), exit, duration, usage and key
    outputs, indented by step-path depth; a `cause:` line for an `error` exit; `in:`/`out:` JSON with `full`."""

    def __init__(self, write: Callable[[str], None], *, full: bool) -> None:
        self.write = write
        self.full = full
        self.inputs: dict[int, Any] = {}

    def __call__(self, event: dict[str, Any]) -> None:
        match event.get("type"):
            case "step.start" if self.full:
                self.inputs[event["span"]] = event.get("inputs")
            case "step.end":
                self._step_end(event)

    def _step_end(self, event: dict[str, Any]) -> None:
        path = str(event.get("step", ""))
        indent = "  " * path.count(".")
        name = path.rsplit(".", 1)[-1]
        run = event.get("run") or 1
        exit = "timeout" if event.get("timed_out") else event.get("exit") or "-"
        duration = fmt_duration((event.get("timings") or {}).get("duration_ms"))
        model = (event.get("model") or {}).get("model_id")
        extras = " ".join(x for x in (fmt_usage(event.get("usage"), model), _key_outputs(event)) if x)
        label = indent + (f"{name} #{run}" if run > 1 else name)
        self.write(f"{_pad(label, NAME_WIDTH)}{_pad(exit, EXIT_WIDTH)}{_pad(duration, TIME_WIDTH)}{extras}".rstrip())
        outputs = event.get("outputs") or {}
        if event.get("exit") == "error":
            message = str(outputs.get("message", "")).splitlines()
            self.write(f"{indent}  cause: {outputs.get('cause')}: {message[0] if message else ''}")
        if self.full:
            self.write(f"{indent}  in: {_clip(_json(self.inputs.pop(event.get('span'), None)), FULL_LIMIT)}")
            self.write(f"{indent}  out: {_clip(_json(outputs), FULL_LIMIT)}")


def _key_outputs(event: dict[str, Any]) -> str:
    keys = (event.get("summary") or {}).get("key_outputs") or {}
    text = " ".join(f"{k}={v if isinstance(v, str) else _json(v)}" for k, v in keys.items())
    return _clip(text, EXTRAS_LIMIT)


def _pad(text: str, width: int) -> str:
    return text.ljust(width) if len(text) < width else text + " "


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit - 1] + "…"


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
