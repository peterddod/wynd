"""The middleware chain around one step run (SPEC §3.6, PLAN §5.1–§5.2; `$DRAFTS/02 §4.2–§4.3`).

Fresh instance per attempt; env/cwd snapshotted before `pre` and restored after `post`; `run` retries for non-agentic
steps only (agentic validation/transport retries live in the loop, `wynd.runtime.agentic.loop.complete`).

`run_chain` itself turns records of the step's logger (`wynd.step.<path>`) into `step.log` events through `emit`;
callers (the worker, `run_step`) must not forward those records a second time.
"""

from __future__ import annotations

import logging
import os
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from wynd.runtime.errors import StepDefinitionError, StepFailure
from wynd.runtime.handle import RuntimeHandle, StepCache, StepTrace
from wynd.runtime.interface import StepInterface, interface_of
from wynd.runtime.policy import CassetteConfig, ExecPolicy
from wynd.runtime.step import step_kind
from wynd.runtime.summary import summarise
from wynd.runtime.usage import ModelInfo, Usage
from wynd.runtime.worker.protocol import RunStepParams, RunStepResult, StepTimings
from wynd.spec.records import StepError, StepErrorCause, Summary

if TYPE_CHECKING:
    from wynd.runtime.step import Step

Emit = Callable[[dict[str, Any]], None]
RETRYABLE: tuple[StepErrorCause, ...] = ("exception", "hook", "shell_exit")   # PLAN §3.9 `retries.run`


@dataclass(frozen=True)
class AgentCall:
    """What the chain hands the agentic loop."""

    step_path: str
    input: BaseModel                     # validated Input instance
    context: dict[str, Any] | None       # assembled by the executor; keys are the declared context strings
    interface: StepInterface             # exits, output_adapter, output_json_schema()
    policy: ExecPolicy                   # provider, model_id, tier, thinking, retries (validation, tool), mcp
    cassette: CassetteConfig
    runtime: RuntimeHandle               # workspace, logger, cache, http; runtime.trace.emit("model.call", ...)


@dataclass(frozen=True)
class AgentResult:
    """What the agentic loop returns."""

    output: BaseModel                    # a validated declared-exit model instance
    note: str                            # final free text ("" if none) -> Summary.note
    usage: Usage
    attempts: int                        # structured-output attempts (1 + validation retries used)
    model: ModelInfo


@dataclass
class ChainResult:
    exit: str
    output: BaseModel                    # validated exit model instance, or StepError
    outputs: dict[str, Any]              # output.model_dump(mode="json") without "exit"
    summary: Summary
    attempts: int
    timings: StepTimings
    usage: Usage | None = None
    model: ModelInfo | None = None
    validation_failures: int = 0
    replayed: bool = False

    def to_result(self) -> RunStepResult:
        return RunStepResult(
            exit=self.exit, outputs=self.outputs, summary=self.summary, attempts=self.attempts,
            validation_failures=self.validation_failures, timings=self.timings, usage=self.usage, model=self.model,
            replayed=self.replayed,
        )


def run_chain(cls: type[Step], params: RunStepParams, *, emit: Emit, cache: StepCache) -> ChainResult:
    """SPEC §3.6 for one step run: validate inputs; (the executor assembled the context); snapshot env/cwd and run
    pre/run/post; validate outputs; summarise; return the exit. Every failure is the `error` exit (PLAN §3.9)."""
    clock = _Clock()
    try:
        iface = interface_of(cls)
    except StepDefinitionError as err:
        return _error(params, clock, "import", err, attempts=0)
    try:
        inp = iface.input_model.model_validate(params.inputs)
    except ValidationError as err:
        return _error(params, clock, "input_validation", err, attempts=0)

    kind = step_kind(cls)
    workspace = Path(params.workspace)
    workspace.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger(f"wynd.step.{params.step_path}")
    logger.setLevel(logging.DEBUG)
    handler = _LogEvents(emit)
    logger.addHandler(handler)
    handle = RuntimeHandle(
        run_id=params.run_id, step_path=params.step_path, step_run=params.step_run, workspace=workspace,
        logger=logger, trace=StepTrace(emit), cache=cache, effects=tuple(params.policy.effects),
    )
    runs_left = params.policy.retries.run if kind != "agentic" else 0
    attempts = 0
    try:
        while True:
            attempts += 1
            raw, agent, failure = _attempt(cls, kind, inp, iface, params, handle, clock)
            if failure is not None:
                cause, err = failure
                if cause in RETRYABLE and runs_left > 0:
                    runs_left -= 1
                    continue
                return _failed(params, clock, kind, cause, err, raw, agent, attempts)
            try:
                out = iface.validate_output(raw)
            except (ValidationError, TypeError) as err:
                return _failed(params, clock, kind, "output_validation", err, raw, agent, attempts)
            break
    finally:
        logger.removeHandler(handler)

    outputs = out.model_dump(mode="json")
    exit = outputs.pop("exit")
    if agent is None:
        return ChainResult(exit, out, outputs, summarise(params.step_path, exit, outputs), attempts, clock.done())
    return ChainResult(
        exit, out, outputs, summarise(params.step_path, exit, outputs, note=agent.note), agent.attempts,
        clock.done(), usage=agent.usage, model=agent.model, validation_failures=agent.attempts - 1,
        replayed=params.cassette.mode == "replay",
    )


def _attempt(
    cls: type[Step], kind: str, inp: BaseModel, iface: StepInterface, params: RunStepParams, handle: RuntimeHandle,
    clock: _Clock,
) -> tuple[Any, AgentResult | None, tuple[StepErrorCause, BaseException] | None]:
    """One attempt on a fresh instance: pre, run (or the agentic loop), post — post runs iff pre returned.
    Returns (raw output, agent result, failure)."""
    env_before, cwd_before = dict(os.environ), os.getcwd()
    raw = agent = failure = None
    clock.phases.clear()
    try:
        os.chdir(handle.workspace)
        try:
            step = cls()
            step.runtime = handle
        except (Exception, SystemExit) as err:
            return None, None, (_cause(err), err)
        with clock.phase("pre"):
            try:
                step.pre()
            except (Exception, SystemExit) as err:
                return None, None, ("hook", err)
        with clock.phase("run"):
            try:
                if kind == "agentic":
                    from wynd.runtime.agentic.loop import complete

                    agent = complete(step, AgentCall(
                        params.step_path, inp, params.context, iface, params.policy, params.cassette, handle,
                    ))
                    raw = agent.output
                else:
                    raw = step.run(inp)
            except (Exception, SystemExit) as err:
                failure = (_cause(err), err)
        with clock.phase("post"):
            try:
                step.post()
            except (Exception, SystemExit) as err:
                failure = failure or ("hook", err)
        return raw, agent, failure
    finally:
        _restore(env_before, cwd_before)


def _cause(err: BaseException) -> StepErrorCause:
    if isinstance(err, StepFailure):
        return err.cause
    from wynd.runtime.agentic.errors import MissingEnvVar

    return "config" if isinstance(err, MissingEnvVar) else "exception"


def _restore(env_before: dict[str, str], cwd_before: str) -> None:
    """Undo what pre/run/post did to the process: cwd, then only the env keys that differ."""
    os.chdir(cwd_before)
    for name in [name for name in os.environ if name not in env_before]:
        del os.environ[name]
    for name, value in env_before.items():
        if os.environ.get(name) != value:
            os.environ[name] = value


def _failed(
    params: RunStepParams, clock: _Clock, kind: str, cause: StepErrorCause, err: BaseException, raw: Any,
    agent: AgentResult | None, attempts: int,
) -> ChainResult:
    """The error exit of a run that got past input validation; a StepFailure's attempts/partial/usage win."""
    partial = _partial(raw)
    usage = agent.usage if agent else None
    if agent:
        attempts = agent.attempts
    if isinstance(err, StepFailure):
        attempts = err.attempts if err.attempts is not None else attempts
        partial = partial or err.partial_outputs
        usage = err.usage or usage
    result = _error(params, clock, cause, err, attempts=attempts, partial=partial, usage=usage)
    if kind == "agentic":
        # output_validation means the retries ran out: every structured-output attempt failed validation
        result.validation_failures = attempts if cause == "output_validation" else (agent.attempts - 1 if agent else 0)
        result.replayed = params.cassette.mode == "replay"
    return result


def _error(
    params: RunStepParams, clock: _Clock, cause: StepErrorCause, err: BaseException, *, attempts: int,
    partial: dict[str, Any] | None = None, usage: Usage | None = None,
) -> ChainResult:
    if isinstance(err, StepFailure):
        message, type_name = err.message, None
    else:
        message, type_name = f"{type(err).__name__}: {err}", type(err).__name__
    output = StepError(
        cause=cause, message=message, type=type_name, traceback="".join(traceback.format_exception(err)),
        inputs=params.inputs, partial_outputs=partial, attempts=attempts,
    )
    outputs = output.model_dump(mode="json")
    exit = outputs.pop("exit")
    return ChainResult(
        exit, output, outputs, summarise(params.step_path, exit, outputs), attempts, clock.done(), usage=usage,
    )


def _partial(raw: Any) -> dict[str, Any] | None:
    """Best-effort JSON of what run/the loop produced before the failure."""
    if raw is None:
        return None
    if isinstance(raw, BaseModel):
        return raw.model_dump(mode="json")
    try:
        value = to_jsonable_python(raw)
    except Exception:  # noqa: BLE001 — anything unserialisable is shown as its repr
        value = None
    return value if isinstance(value, dict) else {"repr": repr(raw)[:2000]}


class _LogEvents(logging.Handler):
    """Forwards the step logger's records as `step.log` events."""

    def __init__(self, emit: Emit) -> None:
        super().__init__(logging.DEBUG)
        self._emit_event = emit

    def emit(self, record: logging.LogRecord) -> None:
        self._emit_event(
            {"type": "step.log", "level": record.levelname, "stream": "logger", "message": self.format(record)}
        )


@dataclass
class _Clock:
    """Worker-side timings: the run's wall clock plus pre/run/post of the last attempt."""

    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    t0: float = field(default_factory=time.perf_counter)
    phases: dict[str, float] = field(default_factory=dict)

    @contextmanager
    def phase(self, name: str) -> Iterator[None]:
        t0 = time.perf_counter()
        try:
            yield
        finally:
            self.phases[name] = (time.perf_counter() - t0) * 1000

    def done(self) -> StepTimings:
        return StepTimings(
            started_at=self.started_at, ended_at=datetime.now(UTC), worker_ms=(time.perf_counter() - self.t0) * 1000,
            pre_ms=self.phases.get("pre"), run_ms=self.phases.get("run"), post_ms=self.phases.get("post"),
        )
