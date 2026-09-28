"""Chat tools (PLAN §8.1 chat row, §15 item 52; `$DRAFTS/06 §7.4`).

Read tools take any process and have no effects. Write tools (`edit_design`, `edit_proto`, `start_compile`,
`start_build`) exist only when `acting_on` is set: they are closures bound to it, take no process argument, say
"acts on <pid>" in their descriptions and have the `filesystem` effect. Every tool is a `@tool` function turned into a
`ToolHandle` by `wynd.runtime.tools.handles_for`, behind a guard that checks `turn.cancelled` first and turns a
`WyndError` into the error result `{"error": {"code", "message", "details"}}`. The guard raises it as a
`ToolInputError`, so the model sees that JSON as an error result it can recover from and the tool item shows `error`.

Design writes go through `ctl.design.save` (the one design write path) with the file's current revision read just
before writing (`sha256:<hex of the file bytes>`) and the turn as lock owner.
"""

from __future__ import annotations

import functools
import hashlib
import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wynd.controller.errors import Invalid, NotFound, OutOfScope, WyndError
from wynd.controller.models import StatusFlag
from wynd.runtime.agentic.errors import ToolInputError
from wynd.runtime.tools import handles_for, tool
from wynd.spec.workspace import PROCESS_FILE, PROTO_DIR, STEP_PROTO_FILE, parse_use
from wynd.spec.yamlio import yaml_to_json

if TYPE_CHECKING:
    from wynd.controller.chat.engine import TurnState
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import Job
    from wynd.runtime.providers.types import ToolHandle

READ_TOOLS = ("list_processes", "search_processes", "read_design", "read_proto", "read_step_source", "get_status",
              "validate_process", "list_runs", "read_trace", "list_jobs", "read_job")
WRITE_TOOLS = ("edit_design", "edit_proto", "start_compile", "start_build")
RUN_OUTPUTS_LIMIT = 2048        # characters of a run's outputs JSON in list_runs
TRACE_LIMIT = 16384             # characters of read_trace's tree text and of its run.end JSON
MAX_LIST = 50
PROTO_NAME = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")       # a `./steps/<name>` segment (spec `parse_use`)


def build_tools(ctl: Controller, acting_on: str | None, turn: TurnState) -> list[ToolHandle]:
    """The tool handles for one turn."""
    fns = read_tools(ctl)
    if acting_on is not None:
        fns += write_tools(ctl, acting_on, turn)
    return handles_for(_guarded(fn, turn) for fn in fns)


def read_tools(ctl: Controller) -> list[Callable[..., Any]]:
    @tool
    def list_processes() -> dict[str, Any]:
        """List every process of the workspace with its derived status (design, compiled, built, released)."""
        return {"processes": _dump(ctl.processes.list())}

    @tool
    def search_processes(query: str, flags: list[StatusFlag] | None = None) -> dict[str, Any]:
        """Search processes by id, name, goal and step instructions (every word must match). `flags` keeps only
        processes that have every given status flag."""
        return {"processes": _dump(ctl.processes.list(query, flags or ()))}

    @tool
    def read_design(process: str) -> dict[str, Any]:
        """Read the design of any process: its `process.yaml` as YAML text and as a JSON document (`doc`, null when
        the YAML does not parse), plus its validation report."""
        path, text = process_file(ctl.ctx, process)
        doc, _ = yaml_to_json(text, path)
        return {"path": path, "yaml": text, "doc": doc,
                "validation": ctl.processes.validate(process).model_dump(mode="json")}

    @tool
    def read_proto(process: str, step: str) -> dict[str, Any]:
        """Read the proto-step of step `step` (a key under `steps`) of any process, as YAML text and as a JSON
        document (`doc`)."""
        info = ctl.processes.steps(process).get(step)
        if info is None:
            raise NotFound(f"process '{process}' has no step '{step}'")
        if info.proto_path is None:
            raise NotFound(f"step '{step}' of process '{process}' ({info.use}) has no proto-step")
        text = (ctl.ctx.root / info.proto_path).read_text(encoding="utf-8")
        doc, _ = yaml_to_json(text, info.proto_path)
        return {"path": info.proto_path, "yaml": text, "doc": doc}

    @tool
    def read_step_source(process: str, step: str) -> dict[str, Any]:
        """Read the compiled package of step `step` of any process: its files and `step.lock.yaml` (cassettes are
        listed, not read)."""
        return ctl.processes.step_source(process, step).model_dump(mode="json")

    @tool
    def get_status(process: str) -> dict[str, Any]:
        """The derived status of any process at its HEAD: design, compiled, built, released, tests, releases."""
        return ctl.processes.status(process).model_dump(mode="json")

    @tool
    def validate_process(process: str) -> dict[str, Any]:
        """Validate any process as it is on disk now; -> `{ok, issues}` (errors, warnings and infos)."""
        return ctl.processes.validate(process).model_dump(mode="json")

    @tool
    def list_runs(process: str, limit: int = 10) -> dict[str, Any]:
        """The newest runs of a process (outputs longer than 2 KB are truncated)."""
        runs = [run.model_dump(mode="json") for run in ctl.runs.list(process_id=process, limit=_bounded(limit))]
        return {"runs": [{**run, "outputs": _clip_json(run["outputs"], RUN_OUTPUTS_LIMIT)} for run in runs]}

    @tool
    def read_trace(run_id: str) -> dict[str, Any]:
        """The trace of a run as an indented step tree (exits, durations, usage, key outputs, errors), plus the
        run's end (status, exit, outputs, error) once it finished."""
        from wynd.controller.runs.tree import render_events

        events = ctl.runs.events(run_id)
        end = next((e for e in reversed(events) if e.get("type") == "run.end"), None)
        if end is not None:
            end = _clip_json({key: end.get(key) for key in ("status", "exit", "outputs", "error")}, TRACE_LIMIT)
        return {"run_id": run_id, "tree": _clip_text(render_events(events), TRACE_LIMIT), "end": end}

    @tool
    def list_jobs(process: str | None = None, limit: int = 10) -> dict[str, Any]:
        """The newest jobs (compile, test_live, build, bake, optimise), of one process or of all."""
        return {"jobs": _dump(ctl.jobs.list(process_id=process, limit=_bounded(limit)))}

    @tool
    def read_job(job_id: str) -> dict[str, Any]:
        """One job: status, compile session (steps, decisions, questions, events), build result, error, usage."""
        return ctl.jobs.get(job_id).model_dump(mode="json")

    return [list_processes, search_processes, read_design, read_proto, read_step_source, get_status,
            validate_process, list_runs, read_trace, list_jobs, read_job]


def write_tools(ctl: Controller, acting_on: str, turn: TurnState) -> list[Callable[..., Any]]:
    def edit_design(doc: dict[str, Any]) -> dict[str, Any]:
        path, _ = process_file(ctl.ctx, acting_on)
        return _save(ctl, acting_on, turn, path, doc)

    def edit_proto(step: str, doc: dict[str, Any]) -> dict[str, Any]:
        return _save(ctl, acting_on, turn, proto_path(ctl, acting_on, step), doc)

    def start_compile(accept_proposals: bool = False) -> dict[str, Any]:
        ctl.design.commit(acting_on, reason="before_job", summary="", origin="chat")
        return _bind_job(ctl, turn, ctl.jobs.submit_compile(acting_on, accept_proposals=accept_proposals))

    def start_build() -> dict[str, Any]:
        ctl.design.commit(acting_on, reason="before_job", summary="", origin="chat")
        return _bind_job(ctl, turn, ctl.jobs.submit_build(acting_on))

    edit_design.__doc__ = (
        f"Acts on {acting_on}: replace its process.yaml with `doc`, the whole document as a JSON object (read it "
        "first with read_design and keep every key you do not change). Returns the new revision and the validation "
        "report; validation errors do not block the save."
    )
    edit_proto.__doc__ = (
        f"Acts on {acting_on}: replace the proto-step of its step `step` (a key under `steps`) with `doc`, the whole "
        "proto-step as a JSON object. For a name that is not a step yet, writes the new process-local proto-step "
        f"proto/<step>.yaml of {acting_on} (then reference it as `use: ./steps/<step>` with edit_design)."
    )
    start_compile.__doc__ = (
        f"Acts on {acting_on}: commit its pending design edits and start a compile job. `accept_proposals` accepts "
        "the compiler's proposed edge-case examples without asking."
    )
    start_build.__doc__ = f"Acts on {acting_on}: commit its pending design edits and start a build job (image)."
    return [tool(fn, effects=["filesystem"]) for fn in (edit_design, edit_proto, start_compile, start_build)]


def process_file(ctx: ControllerContext, pid: str) -> tuple[str, str]:
    """-> (workspace-relative path, text) of the process's `process.yaml`."""
    entry = ctx.workspace().processes.get(pid)
    if entry is None:
        raise NotFound(f"unknown process '{pid}'")
    path = f"{entry.dir}/{PROCESS_FILE}"
    return path, (ctx.root / path).read_text(encoding="utf-8")


def proto_path(ctl: Controller, pid: str, step: str) -> str:
    """The proto-step file `edit_proto` writes for `step` of `pid`: the step's own proto (existing or to create:
    `proto/<name>.yaml` for `./steps/<name>`, `<package>/proto.yaml` for a step-root step), or a new local proto
    `proto/<step>.yaml` when `step` is not a step key. A `process:` step is out of scope."""
    info = ctl.processes.steps(pid).get(step)
    if info is None:
        if not PROTO_NAME.match(step):
            raise Invalid(f"'{step}' is neither a step of '{pid}' nor a proto-step name ({PROTO_NAME.pattern})")
        return _local_proto(ctl.ctx, pid, step)
    if info.proto_path is not None:
        return info.proto_path
    ref = parse_use(info.use)
    match ref.form:
        case "local":
            return _local_proto(ctl.ctx, pid, ref.target)
        case "process":
            raise OutOfScope(f"step '{step}' is the process '{ref.target}': its design belongs to that process, and "
                             f"this chat acts on '{pid}'")
    entry = ctl.ctx.workspace().root_steps.get(info.use)
    if entry is None:
        raise NotFound(f"step '{step}' uses '{info.use}', which is not a step of any step root")
    return f"{entry.dir}/{STEP_PROTO_FILE}"


def _local_proto(ctx: ControllerContext, pid: str, name: str) -> str:
    return f"{ctx.workspace().processes[pid].dir}/{PROTO_DIR}/{name}.yaml"


def _save(ctl: Controller, pid: str, turn: TurnState, path: str, doc: dict[str, Any]) -> dict[str, Any]:
    from wynd.controller.api.models_web import SaveRequest, SaveWrite

    turn.edited.add(pid)                    # before writing: the turn-end commit then covers a save that failed late
    write = SaveWrite(path=path, base_revision=_revision(ctl.ctx.root / path), doc=doc)
    result = ctl.design.save(pid, SaveRequest(writes=[write]), origin="chat", lock_owner=(turn.chat_id, turn.turn_id))
    revision = next((f.revision for f in result.files if f.path == path), None)
    return {"path": path, "revision": revision, "validation": result.validation.model_dump(mode="json")}


def _bind_job(ctl: Controller, turn: TurnState, job: Job) -> dict[str, Any]:
    """Store the chat on the job record and add the job item to the chat."""
    from wynd.controller.jobs import records

    job = records.to_dto(records.update(ctl.ctx.stores.runs, job.id, chat_id=turn.chat_id))
    ctl.chats.add_job_item(turn.chat_id, job)
    return {"job": job.model_dump(mode="json")}


def _guarded(fn: Callable[..., Any], turn: TurnState) -> Callable[..., Any]:
    @functools.wraps(fn)
    def run(**kwargs: Any) -> Any:
        if turn.cancelled.is_set():
            raise ToolInputError(_error("cancelled", "the turn was cancelled; nothing was done"))
        try:
            return fn(**kwargs)
        except WyndError as err:
            raise ToolInputError(_error(err.code, err.message, err.details)) from err

    return run


def _error(code: str, message: str, details: Any = None) -> str:
    return json.dumps({"error": {"code": code, "message": message, "details": details}}, ensure_ascii=False,
                      default=str)


def _revision(path: Path) -> str | None:
    """`sha256:<hex of the file bytes>`, or None when the file does not exist."""
    try:
        return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"
    except FileNotFoundError:
        return None


def _dump(models: list[Any]) -> list[dict[str, Any]]:
    return [m.model_dump(mode="json") for m in models]


def _bounded(limit: int) -> int:
    return max(1, min(limit, MAX_LIST))


def _clip_text(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"…[truncated {len(text) - limit} chars]"


def _clip_json(value: Any, limit: int) -> Any:
    """`value` itself when its JSON fits in `limit` characters, else the cut JSON text."""
    text = json.dumps(value, ensure_ascii=False, default=str)
    return value if len(text) <= limit else _clip_text(text, limit)
