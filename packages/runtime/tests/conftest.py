"""Runtime test fixtures (owner RT-EXEC): a `RunPlan` built from process YAML, a dispatcher double and local stores.

`graph(...)` follows `$DRAFTS/02 §13` (`conftest.graph(yaml_str, classes) -> (RunPlan, dispatcher, stores)`). The
dispatcher double follows the `InProcessDispatcher` contract of PLAN §5.3 (RT-WORKER is a same-sub-wave unit, PLAN §0
rule 3): it runs RT-STEP's real `run_chain` in-process, and a test may override any step with a function of the params
(to capture policies, fake an agentic result, or raise `StepTimeout`/`WorkerCrashed`).

Only fixtures live here; runtime imports happen inside them so a broken leaf never breaks unrelated test modules.
"""

from __future__ import annotations

import sys
import textwrap
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest


@dataclass(frozen=True)
class DispatchCall:
    step_id: str
    params: Any                                  # RunStepParams
    timeout: float | None


@dataclass
class StepDispatcher:
    """`Dispatcher` double: `run_chain` in-process per step id; `overrides[step_id](params, on_event)` replaces
    a run."""

    classes: dict[str, type]
    overrides: dict[str, Callable[[Any, Callable[[dict], None]], Any]] = field(default_factory=dict)
    calls: list[DispatchCall] = field(default_factory=list)
    started: int = 0
    _caches: dict[str, Any] = field(default_factory=dict)

    def start(self) -> None:
        self.started += 1

    def describe(self, step_id: str) -> Any:
        from wynd.runtime.interface import describe_step

        return describe_step(self.classes[step_id], step_id)

    def dispatch(self, step_id: str, params: Any, *, on_event: Callable[[dict], None], timeout: float | None) -> Any:
        from wynd.runtime.handle import StepCache
        from wynd.runtime.middleware import run_chain

        self.calls.append(DispatchCall(step_id, params, timeout))
        override = self.overrides.get(step_id)
        if override is not None:
            return override(params, on_event)
        cache = self._caches.setdefault(step_id, StepCache())
        return run_chain(self.classes[step_id], params, emit=on_event, cache=cache).to_result()

    def close(self) -> None:
        pass

    def params_of(self, step_id: str) -> list[Any]:
        return [call.params for call in self.calls if call.step_id == step_id]


@dataclass
class Graph:
    plan: Any                                    # RunPlan
    dispatcher: StepDispatcher
    stores: Any                                  # Stores
    env: dict[str, str]
    edge_checker: Any = None

    def executor(self) -> Any:
        from wynd.runtime.executor import Executor

        return Executor(self.plan, self.dispatcher, self.stores, env=self.env, edge_checker=self.edge_checker)

    def run(self, inputs: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.executor().run(inputs or {}, **kwargs)

    def events(self, run_id: str, type: str | None = None) -> list[dict[str, Any]]:
        events = self.stores.traces.read(run_id)
        return [e for e in events if type is None or e["type"] == type]

    def record(self, run_id: str) -> dict[str, Any] | None:
        return self.stores.runs.get(run_id)


def build_plan(
    processes: Mapping[str, str],
    *,
    root: str | None = None,
    provider: str = "fake",
    kinds: Mapping[str, str] | None = None,
    locks: Mapping[str, Any] | None = None,
    edges_locks: Mapping[str, Any] | None = None,
    dirs: Mapping[str, str] | None = None,
) -> Any:
    """`{pid: process.yaml text}` -> RunPlan; step nodes get ids `<pid>#<node>` (kind from `kinds`, default
    deterministic; lock from `locks`, default minimal); `use: process:<id>` nodes become ProcessStep nodes."""
    from wynd.spec.lockfiles import EdgesLock, StepLock
    from wynd.spec.plan import PlanNode, PlanProcess, PlanStep, PlanVenv, RunPlan
    from wynd.spec.process_doc import ProcessDoc
    from wynd.spec.yamlio import parse_model

    kinds, locks, edges_locks, dirs = kinds or {}, locks or {}, edges_locks or {}, dirs or {}
    steps: dict[str, PlanStep] = {}
    procs: dict[str, PlanProcess] = {}
    for pid, text in processes.items():
        doc = parse_model(textwrap.dedent(text), ProcessDoc)
        nodes = {}
        for key, ref in doc.steps.items():
            use = ref.use_ref
            if use.form == "process":
                nodes[key] = PlanNode(process=use.target)
                continue
            sid = f"{pid}#{key}"
            kind = kinds.get(sid, "deterministic")
            lock = locks.get(sid) or StepLock(name=key, kind=kind, entrypoint=f"{key}:Step")
            steps[sid] = PlanStep(id=sid, kind=kind, entrypoint=lock.entrypoint, venv="test", package_dir=None,
                                  lock=lock)
            nodes[key] = PlanNode(step=sid)
        procs[pid] = PlanProcess(id=pid, dir=dirs.get(pid), definition=doc, nodes=nodes,
                                 edges_lock=edges_locks.get(pid) or EdgesLock())
    return RunPlan(
        mode="local", root=root or next(iter(processes)), provider=provider, venv_root="/nonexistent/venvs",
        venvs=[PlanVenv(id="test", python=sys.executable, steps=sorted(steps))], steps=steps, processes=procs,
    )


def local_stores(root: Path) -> Any:
    from wynd.runtime.storage import Stores
    from wynd.runtime.storage.local import FileRegistry, FileRunRegistry, FileWorkspaceStore, JsonlTraceSink

    return Stores(
        workspaces=FileWorkspaceStore(root / "data" / "workspaces"),
        traces=JsonlTraceSink(root / "data" / "traces"),
        runs=FileRunRegistry(root / "data" / "registry"),
        registry=FileRegistry(root / "home"),
    )


@pytest.fixture
def graph(tmp_path: Path) -> Callable[..., Graph]:
    """`graph(processes, classes, *, overrides=None, env=None, edge_checker=None, stores=None, **plan_kwargs)`.

    `processes` is one process.yaml text (process id "p") or `{pid: text}` (the first is the root unless `root=`).
    `classes`/`overrides` are keyed by step id `<pid>#<node>`; a bare node name means the root process."""

    def make(
        processes: str | Mapping[str, str],
        classes: Mapping[str, type] | None = None,
        *,
        overrides: Mapping[str, Callable] | None = None,
        env: Mapping[str, str] | None = None,
        edge_checker: Any = None,
        stores: Any = None,
        **plan_kwargs: Any,
    ) -> Graph:
        procs = {"p": processes} if isinstance(processes, str) else dict(processes)
        root = plan_kwargs.get("root") or next(iter(procs))

        def sid(key: str) -> str:
            return key if "#" in key else f"{root}#{key}"

        plan = build_plan(procs, **plan_kwargs)
        dispatcher = StepDispatcher(
            classes={sid(k): v for k, v in (classes or {}).items()},
            overrides={sid(k): v for k, v in (overrides or {}).items()},
        )
        return Graph(plan, dispatcher, stores or local_stores(tmp_path), dict(env or {}), edge_checker)

    return make


@pytest.fixture
def make_step() -> Callable[..., type]:
    """`make_step(fn=None, *, inputs={field: type}, **exits)` -> a DeterministicStep class.

    `exits` maps exit -> {field: type} (default `done` with no fields); the Input forbids extra fields. `fn(input)`
    returns the output mapping, with an "exit" key for a non-done exit; the default returns {}."""

    def make(fn: Callable[[Any], Any] | None = None, *, inputs: Mapping[str, Any] | None = None,
             **exits: Mapping[str, Any]) -> type:
        from typing import Literal, Union

        from pydantic import ConfigDict, create_model

        from wynd.runtime.step import DeterministicStep

        input_model = create_model("Input", __config__=ConfigDict(extra="forbid"),
                                   **{name: (tp, ...) for name, tp in (inputs or {}).items()})
        models = [
            create_model(f"Out_{exit}", exit=(Literal[exit], exit), **{name: (tp, ...) for name, tp in fields.items()})
            for exit, fields in (exits or {"done": {}}).items()
        ]
        output = models[0] if len(models) == 1 else Union[tuple(models)]  # noqa: UP007 — built at run time
        body = fn or (lambda input: {})
        return type("TestStep", (DeterministicStep,),
                    {"Input": input_model, "Output": output, "run": lambda self, input: body(input)})

    return make


@pytest.fixture
def step_result() -> Callable[..., Any]:
    """`step_result(path, exit="done", outputs=None, *, usage=None, model=None, attempts=1)` -> RunStepResult."""

    def make(path: str, exit: str = "done", outputs: dict | None = None, *, usage: Any = None, model: Any = None,
             attempts: int = 1) -> Any:
        from wynd.runtime.summary import summarise
        from wynd.runtime.worker.protocol import RunStepResult, StepTimings

        outputs = dict(outputs or {})
        now = datetime.now(UTC)
        return RunStepResult(
            exit=exit, outputs=outputs, summary=summarise(path, exit, outputs), attempts=attempts,
            timings=StepTimings(started_at=now, ended_at=now, worker_ms=1.0), usage=usage, model=model,
        )

    return make
