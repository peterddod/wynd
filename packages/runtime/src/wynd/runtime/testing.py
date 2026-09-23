"""Helpers for step tests (PLAN §3.20): load a step package, run it in-process through the full chain, compare
outputs with the shared comparison semantics (`$DRAFTS/05 §9.3.2`, also used for process examples).

`run_step` environment (tests only, never in manifests): `WYND_CASSETTE_MODE` (default mode, else "replay"),
`WYND_CASSETTE_RECORD_DIR` (record target, else the cassettes dir), `WYND_CASSETTE_LITERALS` (JSON object of extra
Normaliser literals), `WYND_DEFAULT_PROVIDER` (else `DEFAULT_PROVIDER`), `WYND_EVENTS_FILE` (every emitted event is
appended as a JSON line).
"""

from __future__ import annotations

import inspect
import json
import math
import os
import tempfile
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel
from pydantic_core import to_jsonable_python

from wynd.runtime.handle import StepCache
from wynd.runtime.middleware import run_chain
from wynd.runtime.policy import CassetteConfig, build_policy
from wynd.runtime.step import step_kind
from wynd.runtime.storage import registry_from_env
from wynd.runtime.worker.protocol import RunStepParams
from wynd.spec.base import DEFAULT_PROVIDER, RESERVED_EXIT
from wynd.spec.lockfiles import StepLock, load_step_lock
from wynd.spec.workspace import CASSETTES_DIR, STEP_LOCK_FILE

if TYPE_CHECKING:
    from wynd.runtime.step import Step
    from wynd.runtime.usage import ModelInfo, Usage
    from wynd.spec.lockfiles import RetryPolicy
    from wynd.spec.records import Summary

RUN_ID = "run-step"
TMP = "{tmp}"
PRESENT = "<present>"      # Mismatch.expected of a `present` field that is missing or empty


@dataclass
class StepResult:
    exit: str
    output: BaseModel                    # exit model instance, or StepError
    outputs: dict[str, Any]              # JSON mode, without "exit"
    summary: Summary
    attempts: int
    usage: Usage | None
    model: ModelInfo | None
    events: list[dict[str, Any]]         # emitted during the run (step.log, step.event, model.call, tool.call, ...)
    workspace: Path


@dataclass(frozen=True)
class Mismatch:
    field: str                           # "record.total", "errors[2].code"
    expected: Any
    actual: Any                          # None when the field is absent


def load_step(step_dir: str | Path, cls: str | None = None) -> type[Step]:
    """Mount `step_dir` like the worker does and import its entrypoint (`cls` "<module>:<Class>", else the lock's)."""
    from wynd.runtime.worker.loader import load_step_class

    step_dir = Path(step_dir).resolve()
    entrypoint = cls or load_step_lock(step_dir / STEP_LOCK_FILE).entrypoint
    return load_step_class(str(step_dir), entrypoint, step_dir)


def run_step(
    step: type[Step],
    input: BaseModel | Mapping[str, Any],
    *,
    mode: Literal["live", "record", "replay"] | None = None,
    cassettes: str | Path | None = None,
    lock: StepLock | Mapping[str, Any] | None = None,
    context: Mapping[str, Any] | None = None,
    workspace: str | Path | None = None,
    retry: RetryPolicy | None = None,
) -> StepResult:
    """Run `step` in-process through the full middleware chain. Defaults: cassettes `<package>/cassettes`, lock
    `<package>/step.lock.yaml` when present, a fresh temp workspace (kept). A `cassette_miss` error re-raises
    `CassetteMissError` so pytest shows the exact text."""
    kind = step_kind(step)
    if kind == "process":
        raise TypeError(f"{step.__name__} is a ProcessStep; processes run through the executor, not run_step")
    package_dir = Path(inspect.getfile(step)).parent
    mode = mode or os.environ.get("WYND_CASSETTE_MODE") or "replay"
    cassettes = Path(cassettes).absolute() if cassettes is not None else package_dir / CASSETTES_DIR
    workspace = Path(workspace).absolute() if workspace is not None else Path(tempfile.mkdtemp(prefix="wynd-step-"))
    workspace.mkdir(parents=True, exist_ok=True)
    policy = build_policy(
        kind, _lock(lock, package_dir),
        default_provider=os.environ.get("WYND_DEFAULT_PROVIDER") or DEFAULT_PROVIDER,
        registry=registry_from_env(), environ=os.environ, retry_override=retry,
    )
    record_dir = (os.environ.get("WYND_CASSETTE_RECORD_DIR") or str(cassettes)) if mode == "record" else None
    literals = {**json.loads(os.environ.get("WYND_CASSETTE_LITERALS") or "{}"), str(workspace): "<workspace>"}
    params = RunStepParams(
        run_id=RUN_ID, step_path=step.__name__, step_run=1, step_id=f"{step.__module__}:{step.__qualname__}",
        inputs=_json_inputs(input), context=dict(context) if context is not None else None,
        workspace=str(workspace), policy=policy,
        cassette=CassetteConfig(mode=mode, dir=str(cassettes), record_dir=record_dir, literals=literals),
    )
    events: list[dict[str, Any]] = []
    result = run_chain(step, params, emit=_recorder(events, os.environ.get("WYND_EVENTS_FILE")), cache=StepCache())
    if result.exit == RESERVED_EXIT and result.outputs["cause"] == "cassette_miss":
        from wynd.runtime.cassettes import CassetteMissError

        raise CassetteMissError(result.outputs["message"])
    return StepResult(
        exit=result.exit, output=result.output, outputs=result.outputs, summary=result.summary,
        attempts=result.attempts, usage=result.usage, model=result.model, events=events, workspace=workspace,
    )


def expect(
    result: StepResult, *, exit: str, outputs: Mapping[str, Any] | None = None, present: Iterable[str] = ()
) -> None:
    """Assert the exit and (subset) outputs. The AssertionError's first line is `WYND-EXPECT <json>` with
    `expected_exit, actual_exit, mismatches, error` (the compiler classifies failures from it)."""
    mismatches = match_outputs(outputs or {}, result.outputs, present=present) if result.exit == exit else []
    if result.exit == exit and not mismatches:
        return
    error = None
    if result.exit == RESERVED_EXIT:
        error = {key: result.outputs.get(name) for key, name in
                 (("cause", "cause"), ("message", "message"), ("error_type", "type"))}
    payload = {"expected_exit": exit, "actual_exit": result.exit, "mismatches": [asdict(m) for m in mismatches],
               "error": error}
    lines = ["WYND-EXPECT " + json.dumps(payload, ensure_ascii=False, default=str)]
    if result.exit != exit:
        lines.append(f"exit: expected {exit!r}, got {result.exit!r}")
    if error is not None:
        lines.append(f"error ({error['cause']}): {error['message']}")
    lines += [f"{m.field}: expected {m.expected!r}, got {m.actual!r}" for m in mismatches]
    raise AssertionError("\n".join(lines))


def match_outputs(expected: Any, actual: Any, *, present: Iterable[str] = ()) -> list[Mismatch]:
    """Subset match of `expected` against JSON-mode `actual`: mappings recursively by the expected keys, lists
    element-wise (same length), bools exact, numbers `isclose(rel_tol=1e-9, abs_tol=1e-9)`, strings exact (dates as
    ISO), `None` expected = actual None or absent; each `present` field must exist and be non-empty."""
    found: list[Mismatch] = []
    _match(expected, actual, "", found)
    for name in present:
        value = actual.get(name) if isinstance(actual, Mapping) else None
        if value is None or value == "":
            found.append(Mismatch(name, PRESENT, value))
    return found


def _match(expected: Any, actual: Any, where: str, found: list[Mismatch]) -> None:
    here = where or "<outputs>"
    match expected:
        case Mapping():
            if not isinstance(actual, Mapping):
                found.append(Mismatch(here, expected, actual))
                return
            for key, value in expected.items():
                field = f"{where}.{key}" if where else str(key)
                if key in actual:
                    _match(value, actual[key], field, found)
                elif value is not None:
                    found.append(Mismatch(field, value, None))
        case list() | tuple():
            if not isinstance(actual, (list, tuple)) or len(actual) != len(expected):
                found.append(Mismatch(here, expected, actual))
                return
            for index, (item, got) in enumerate(zip(expected, actual)):
                _match(item, got, f"{where}[{index}]", found)
        case None:
            if actual is not None:
                found.append(Mismatch(here, expected, actual))
        case bool():
            if actual is not expected:
                found.append(Mismatch(here, expected, actual))
        case int() | float():
            number = isinstance(actual, (int, float)) and not isinstance(actual, bool)
            if not (number and math.isclose(actual, expected, rel_tol=1e-9, abs_tol=1e-9)):
                found.append(Mismatch(here, expected, actual))
        case str():
            if actual != expected:
                found.append(Mismatch(here, expected, actual))
        case _:  # dates, datetimes, paths, ...: compare in the JSON form pydantic emits
            _match(to_jsonable_python(expected), actual, where, found)


def sub_tmp(value: Any, tmp: str | Path) -> Any:
    """Replace a leading `{tmp}` in every string (recursively through mappings and lists) with `str(tmp)`."""
    match value:
        case str() if value.startswith(TMP):
            return str(tmp) + value[len(TMP):]
        case Mapping():
            return {key: sub_tmp(item, tmp) for key, item in value.items()}
        case list():
            return [sub_tmp(item, tmp) for item in value]
        case tuple():
            return tuple(sub_tmp(item, tmp) for item in value)
    return value


def _lock(lock: StepLock | Mapping[str, Any] | None, package_dir: Path) -> StepLock | None:
    match lock:
        case StepLock():
            return lock
        case Mapping():
            return StepLock.model_validate(dict(lock))
    path = package_dir / STEP_LOCK_FILE
    return load_step_lock(path) if path.is_file() else None


def _json_inputs(input: BaseModel | Mapping[str, Any]) -> dict[str, Any]:
    if isinstance(input, BaseModel):
        return input.model_dump(mode="json")
    return to_jsonable_python(dict(input))


def _recorder(events: list[dict[str, Any]], events_file: str | None):
    def emit(event: dict[str, Any]) -> None:
        events.append(event)
        if events_file:
            with open(events_file, "a", encoding="utf-8") as out:
                out.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")

    return emit
