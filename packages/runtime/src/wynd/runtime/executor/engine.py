"""`Executor` and `ProcessResult` (PLAN §5.4; algorithm `$DRAFTS/02 §7` over the spec `Scope` and the §3.5 routing
rules). An `Executor` is reusable and thread-safe across concurrent `run()` calls (all run state is local)."""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlparse
from urllib.request import url2pathname

from pydantic import BaseModel, ValidationError
from pydantic_core import to_jsonable_python

from wynd.runtime import __version__
from wynd.runtime.errors import InvalidProcessInputs, StepFailure
from wynd.runtime.executor.context import assemble_context
from wynd.runtime.executor.edges import EdgeCheckError, EdgeVerdict, make_edge_check_call
from wynd.runtime.executor.instance import (
    Deadline,
    DeadlineExceeded,
    Instance,
    LocalTimeout,
    RunCtx,
    StepRecord,
    earliest,
)
from wynd.runtime.ids import new_id, valid_id
from wynd.runtime.interface import process_interface
from wynd.runtime.policy import CassetteConfig, build_policy
from wynd.runtime.storage.models import RunRecord
from wynd.runtime.summary import summarise
from wynd.runtime.trace import TraceEmitter
from wynd.runtime.usage import ModelInfo, Usage
from wynd.runtime.worker.client import StepTimeout, WorkerCrashed, WorkerRpcError
from wynd.runtime.worker.protocol import RunStepParams, RunStepResult, StepTimings
from wynd.spec.base import IGNORE_TARGET, RESERVED_EXIT
from wynd.spec.context import parse_context_entry
from wynd.spec.errors import format_loc
from wynd.spec.expr.errors import ExprError
from wynd.spec.expr.evaluator import evaluate_condition, evaluate_limit, evaluate_with, parse
from wynd.spec.expr.scope import new_scope
from wynd.spec.lockfiles import branch_key
from wynd.spec.process_doc import Limits, expression_sites
from wynd.spec.records import ProcessError, ProcessErrorCause, StepError, StepErrorCause, Summary, TracePointer
from wynd.spec.workspace import step_module_name

if TYPE_CHECKING:
    from wynd.runtime.executor.edges import EdgeChecker
    from wynd.runtime.interface import StepInterface
    from wynd.runtime.storage import Stores
    from wynd.runtime.worker.pool import Dispatcher
    from wynd.spec.plan import PlanNode, PlanProcess, PlanStep, RunPlan
    from wynd.spec.process_doc import Branch, Edge, RetryOverride

_ENVELOPE = frozenset({"v", "seq", "ts", "run_id", "type"})
_NO_CHECKER = "agentic edges need an EdgeChecker"


class ProcessResult(BaseModel):
    run_id: str
    process: str
    exit: str
    outputs: dict[str, Any]
    error: ProcessError | None                     # set whenever the top-level error handler ran
    status: Literal["succeeded", "failed"]         # failed iff exit == "error"
    trace: str
    workspace: str | None
    duration_ms: float
    usage: Usage
    finally_errors: list[StepError] = []


@dataclass
class _Outcome:
    """How one process instance ended."""

    exit: str
    outputs: dict[str, Any]                        # validated process outputs of `exit` ({} for "error")
    error: ProcessError | None = None
    handled: bool = False                          # the instance ended in its error handler (default or custom)
    finally_errors: list[StepError] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)


@dataclass(frozen=True)
class _Hop:
    """A taken branch into a step node."""

    step: str
    bound: dict[str, Any]
    via: str | None                                # branch key; None for the entry step
    timeout: float | None = None
    retries: RetryOverride | None = None


@dataclass
class _Result:
    """One node run as the executor records it (a worker result, a child process, or an executor-side error)."""

    exit: str
    outputs: dict[str, Any]
    summary: Summary
    attempts: int = 1
    validation_failures: int = 0
    worker: StepTimings | None = None
    usage: Usage | None = None
    model: ModelInfo | None = None
    replayed: bool = False

    @classmethod
    def from_wire(cls, res: RunStepResult) -> _Result:
        return cls(res.exit, res.outputs, res.summary, res.attempts, res.validation_failures, res.timings,
                   res.usage, res.model, res.replayed)


class Executor:
    def __init__(
        self,
        plan: RunPlan,
        dispatcher: Dispatcher,
        stores: Stores,
        *,
        env: Mapping[str, str] | None = None,
        edge_checker: EdgeChecker | None = None,
    ) -> None:
        """Parses every expression and branch context of every process once (a syntax error is a `ValueError`)."""
        self.plan = plan
        self.dispatcher = dispatcher
        self.stores = stores
        self.env: Mapping[str, str] = dict(os.environ) if env is None else env
        self.edge_checker = edge_checker
        self._ifaces: dict[str, StepInterface] = {}
        self._edges: dict[str, dict[str, Edge]] = {}
        for pid, pp in plan.processes.items():
            doc = pp.definition
            for site in expression_sites(doc):
                try:
                    parse(site.text)
                except ExprError as err:
                    raise ValueError(f"process {pid}: {format_loc(site.loc)}: {err}") from None
            for edge in doc.edges:
                for branch in edge.to:
                    for entry in branch.context or ():
                        parse_context_entry(entry)
            self._ifaces[pid] = process_interface(pid, doc)
            self._edges[pid] = {edge.from_: edge for edge in doc.edges}

    def validate_inputs(self, inputs: Mapping[str, Any]) -> dict[str, Any]:
        """Raises `InvalidProcessInputs` (boundary check, before any run record exists)."""
        model = self._ifaces[self.plan.root].input_model
        try:
            return model.model_validate(dict(inputs)).model_dump(mode="json")
        except ValidationError as err:
            raise InvalidProcessInputs(json.loads(err.json(include_url=False))) from None

    def run(
        self,
        inputs: Mapping[str, Any],
        *,
        run_id: str | None = None,
        cassette_mode: Literal["live", "record", "replay"] = "live",
        cassette_root: Path | None = None,
        record_root: Path | None = None,
        cassette_literals: Mapping[str, str] | None = None,
        metadata: Mapping[str, Any] | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> ProcessResult:
        run_id = run_id or new_id("run")
        if not valid_id(run_id):
            raise ValueError(f"invalid run id {run_id!r}: must match ^[A-Za-z0-9][A-Za-z0-9_.-]{{0,127}}$")
        inputs = self.validate_inputs(inputs)
        t0 = time.perf_counter()
        stores, plan = self.stores, self.plan
        workspace = stores.workspaces.open(run_id)
        trace_uri = stores.traces.uri(run_id)
        meta = dict(metadata or {})
        now = _now()
        stores.runs.create(RunRecord(
            id=run_id, process=plan.root, status="running", created_at=now, updated_at=now, started_at=now,
            ref=plan.commit, mode=plan.mode, inputs=inputs, trace=trace_uri, meta=meta,
        ).model_dump(mode="json"))
        ctx = RunCtx(
            run_id=run_id, workspace=workspace, emitter=TraceEmitter(run_id, stores.traces, on_event), plan=plan,
            registry=stores.registry, cassette_mode=cassette_mode, cassette_root=cassette_root,
            record_root=record_root, cassette_literals=dict(cassette_literals or {}),
        )
        ctx.emitter.emit("run.start", process=plan.root, mode=plan.mode, inputs=inputs, commit=plan.commit,
                         runtime_version=__version__, workspace=str(workspace), cassette=cassette_mode, metadata=meta)
        try:
            self.dispatcher.start()
            out = self._run_process(plan.processes[plan.root], inputs, ctx, prefix="", parent_span=None,
                                    deadline=None)
        except Exception as err:  # noqa: BLE001 — an executor/storage fault still ends the run cleanly
            error = ProcessError(run_id=run_id, process=plan.root, step=None, cause="internal",
                                 message=f"{type(err).__name__}: {err}", inputs=inputs)
            out = _Outcome(RESERVED_EXIT, {}, error=error, handled=True)

        workspace_bytes = _tree_bytes(workspace)
        workspace_uri = stores.workspaces.close(run_id, keep=out.handled)
        error = out.error                            # set whenever the top-level handler ran (out.handled)
        if out.handled:
            error.workspace = workspace_uri
        failed = out.exit == RESERVED_EXIT
        outputs = {"error": error.model_dump(mode="json")} if failed else out.outputs
        status = "failed" if failed else "succeeded"
        duration_ms = (time.perf_counter() - t0) * 1000
        ctx.emitter.emit("run.end", exit=out.exit, outputs=outputs, error=error, status=status,
                         duration_ms=duration_ms, workspace_kept=out.handled, workspace=workspace_uri,
                         usage=ctx.usage, finally_errors=out.finally_errors)
        stores.traces.close(run_id)
        stores.runs.update(run_id, to_jsonable_python({
            "status": status, "exit": out.exit, "outputs": outputs, "error": error, "workspace": workspace_uri,
            "finished_at": _now(), "duration_ms": duration_ms, "usage": ctx.usage,
            "trace_bytes": _file_bytes(trace_uri), "workspace_bytes": workspace_bytes,
        }))
        return ProcessResult(
            run_id=run_id, process=plan.root, exit=out.exit, outputs=outputs, error=error, status=status,
            trace=trace_uri, workspace=workspace_uri, duration_ms=duration_ms, usage=ctx.usage,
            finally_errors=out.finally_errors,
        )

    # --- one process instance ---------------------------------------------------------------------------------------

    def _run_process(
        self, pp: PlanProcess, inputs: dict[str, Any], ctx: RunCtx, *, prefix: str, parent_span: int | None,
        deadline: Deadline | None,
    ) -> _Outcome:
        """Walk the graph, then run `finally` (also while an outer timeout unwinds, with no deadline)."""
        scope = new_scope(pp.definition, inputs=inputs, env=self.env, run_id=ctx.run_id)
        inst = Instance(plan=pp, ctx=ctx, prefix=prefix, parent_span=parent_span, scope=scope, deadline=deadline)
        try:
            out = self._walk(inst)
        except Exception:
            self._run_finally(inst, RESERVED_EXIT)
            raise
        out.finally_errors = self._run_finally(inst, out.exit)
        out.usage = inst.usage
        return out

    def _walk(self, inst: Instance) -> _Outcome:
        doc = inst.plan.definition
        hop = _Hop(doc.entry, self._bind(inst, doc.entry, inst.scope.process_inputs), None)
        while True:
            try:
                rec = self._run_node(inst, hop.step, hop.bound, via=hop.via, role="node", timeout=hop.timeout,
                                     retries=hop.retries)
            except LocalTimeout as err:
                return self._fail(inst, "timeout", step=hop.step, inputs=hop.bound, edge=hop.via, message=str(err))
            edge = self._edges[inst.plan.id].get(f"{rec.name}.{rec.exit}")
            if edge is None:
                return self._fail(inst, "step_error" if rec.exit == RESERVED_EXIT else "unrouted_exit", rec=rec)
            routed = self._route(inst, edge, rec)
            if isinstance(routed, _Outcome):
                return routed
            hop = routed

    def _route(self, inst: Instance, edge: Edge, rec: StepRecord) -> _Outcome | _Hop:
        """PLAN §3.5 step 2: branches top to bottom against one scope snapshot (nothing mutates it until the take)."""
        scope, key = inst.scope, edge.from_
        verdicts: list[dict[str, Any]] = []
        for index, branch in enumerate(edge.to):
            bkey = branch_key(key, index, branch.name)
            limits = branch.limits or Limits()
            try:
                if not evaluate_condition(branch.when, scope):
                    continue
                limit = evaluate_limit(limits.max_traversals, scope)
            except ExprError as err:
                return self._fail(inst, "expression_error", rec=rec, edge=bkey, message=f"branch {bkey}: {err}")
            if limit is not None and scope.edges[key].taken[index] >= limit:
                return self._fail(inst, "max_traversals", rec=rec, edge=bkey,
                                  message=f"branch {bkey} reached max_traversals ({limit:g})")
            try:
                bound = evaluate_with(branch.with_, scope)
                timeout = evaluate_limit(limits.timeout, scope)
            except ExprError as err:
                return self._fail(inst, "expression_error", rec=rec, edge=bkey, message=f"branch {bkey}: {err}")
            if branch.check is not None:
                verdict = self._check(inst, edge, index, branch, bound, rec)
                if isinstance(verdict, _Outcome):
                    return verdict
                verdicts.append({"branch": index, "take": verdict.take, "reason": verdict.reason})
                if not verdict.take:
                    continue
            taken = scope.take(key, index)
            extra = {"verdicts": verdicts} if edge.kind == "agentic" else {}
            inst.ctx.emitter.emit("edge.taken", process=inst.plan.id, parent=inst.parent_span, **{"from": key},
                                  branch=index, name=branch.name, to=branch.step, kind=edge.kind,
                                  **{"with": bound}, taken=taken, **extra)
            if branch.step == IGNORE_TARGET:
                return self._fail(inst, "ignored_exit", rec=rec, edge=bkey)
            target = branch.exit_target
            if target is None:
                return _Hop(branch.step, bound, bkey, timeout, limits.retries)
            try:
                return _Outcome(target, self._process_output(inst.plan.id, target, bound))
            except ValueError as err:
                return self._fail(inst, "invalid_process_outputs", rec=rec, edge=bkey,
                                  message=f"outputs of $exit.{target}: {err}")
        return self._fail(inst, "no_branch_matched", rec=rec, edge=key)

    def _check(
        self, inst: Instance, edge: Edge, index: int, branch: Branch, bound: dict[str, Any], rec: StepRecord
    ) -> EdgeVerdict | _Outcome:
        """One agentic check through the `EdgeChecker`; the executor alone emits `edge.check`."""
        ctx = inst.ctx
        bkey = branch_key(edge.from_, index, branch.name)
        if self.edge_checker is None:
            return self._fail(inst, "edge_check", rec=rec, edge=bkey, message=_NO_CHECKER,
                              detail={"branch_key": bkey, "error_cause": "config"})
        deadline = inst.deadline
        t0 = time.perf_counter()
        call = None
        try:
            call = make_edge_check_call(inst, edge, index, branch, bound)
            timeout = float(call.lock.timeout_s)
            if deadline is not None:                 # the check is part of an enclosing ProcessStep's time budget
                if deadline.remaining() <= 0:
                    raise DeadlineExceeded(deadline)
                timeout = min(timeout, deadline.remaining())
            result = self.edge_checker.check(
                call, cassette=self._cassette(ctx, "edges", _sub(inst.plan.dir, "cassettes", "edges")),
                workspace=str(ctx.workspace), timeout_s=timeout,
                on_event=_forward(ctx, f"edge:{bkey}", None, inst.parent_span),
            )
        except (StepFailure, EdgeCheckError) as err:
            cause = err.cause if isinstance(err, EdgeCheckError) else "config"
            ctx.emitter.emit("edge.check", process=inst.plan.id, parent=inst.parent_span, edge=edge.from_,
                             branch=index, branch_key=bkey, target=branch.step, take=None, reason=err.message,
                             attempts=None, validation_failures=None, error_cause=cause,
                             provider=call.provider if call else None, tier=call.lock.tier if call else None,
                             model_id=call.model_id if call else None, usage=None,
                             duration_ms=(time.perf_counter() - t0) * 1000, replayed=None)
            if cause == "timeout" and deadline is not None and deadline.remaining() <= 0:
                raise DeadlineExceeded(deadline) from None
            return self._fail(inst, "edge_check", rec=rec, edge=bkey, message=f"check of {bkey}: {err.message}",
                              detail={"branch_key": bkey, "error_cause": cause})
        ctx.emitter.emit("edge.check", process=inst.plan.id, parent=inst.parent_span, edge=edge.from_, branch=index,
                         branch_key=bkey, target=branch.step, take=result.verdict.take, reason=result.verdict.reason,
                         attempts=result.attempts, validation_failures=result.validation_failures, error_cause=None,
                         provider=result.provider, tier=result.tier, model_id=result.model_id, usage=result.usage,
                         duration_ms=result.duration_ms, replayed=result.replayed)
        inst.usage += result.usage
        ctx.usage += result.usage
        return result.verdict

    # --- nodes ------------------------------------------------------------------------------------------------------

    def _run_node(
        self, inst: Instance, name: str, bound: dict[str, Any], *, via: str | None, role: str,
        timeout: float | None = None, retries: RetryOverride | None = None, no_deadline: bool = False,
    ) -> StepRecord:
        """Run one node; raises `LocalTimeout` when the branch's own timeout expires, `DeadlineExceeded` when an outer
        one does (either way after a `step.end` with `timed_out`)."""
        ctx = inst.ctx
        node = inst.plan.nodes[name]
        step = self.plan.steps[node.step] if node.step is not None else None
        kind = step.kind if step is not None else "process"
        path = inst.prefix + name
        n = inst.scope.steps[name].runs + 1
        token = object()
        own = Deadline.after(timeout, token) if timeout is not None else None
        deadline = None if no_deadline else earliest(inst.deadline, own)
        span = ctx.emitter.emit(
            "step.start", step=path, name=name, process=inst.plan.id, parent=inst.parent_span,
            id=node.step if step is not None else f"process:{node.process}", kind=kind, run=n, role=role, via=via,
            inputs=bound, venv=step.venv if step is not None else None,
        )["span"]
        raw = ctx.workspace / ".wynd" / "steps" / path / str(n)
        _write_json(raw / "inputs.json", bound)
        started, t0 = _now(), time.perf_counter()
        try:
            if step is None:
                result = self._run_child(inst, node.process, path, span, bound, deadline)
            else:
                result = self._dispatch(inst, step, path, span, n, bound, deadline, retries)
        except DeadlineExceeded as err:
            ctx.emitter.emit("step.end", step=path, span=span, parent=inst.parent_span, run=n, kind=kind, exit=None,
                             timed_out=True, outputs=None, summary=None, attempts=0, validation_failures=0,
                             timings=_timings(started, t0, None), usage=None, model=None, replayed=False)
            if err.deadline.owner is token:
                raise LocalTimeout(f"step '{path}' exceeded its timeout of {timeout:g}s") from None
            raise
        rec = StepRecord(name, path, n, result.exit, bound, result.outputs, result.summary)
        inst.scope.complete(name, result.exit, result.outputs, result.summary.model_dump(mode="json"))
        inst.history.append(rec)
        if result.usage is not None:
            inst.usage += result.usage
            if step is not None:                     # a child's usage is already in the run total
                ctx.usage += result.usage
        _write_json(raw / "outputs.json", {"exit": result.exit, **result.outputs})
        ctx.emitter.emit("step.end", step=path, span=span, parent=inst.parent_span, run=n, kind=kind,
                         exit=result.exit, timed_out=False, outputs=result.outputs, summary=result.summary,
                         attempts=result.attempts, validation_failures=result.validation_failures,
                         timings=_timings(started, t0, result.worker), usage=result.usage, model=result.model,
                         replayed=result.replayed)
        return rec

    def _dispatch(
        self, inst: Instance, step: PlanStep, path: str, span: int, n: int, bound: dict[str, Any],
        deadline: Deadline | None, retries: RetryOverride | None,
    ) -> _Result:
        ctx = inst.ctx
        try:
            policy = build_policy(step.kind, step.lock, default_provider=self.plan.provider,
                                  registry=self.stores.registry, environ=self.env, retry_override=retries)
        except StepFailure as err:
            return _error(path, "config", err.message, bound, attempts=0)
        module = step_module_name(step.id)
        params = RunStepParams(
            run_id=ctx.run_id, step_path=path, step_run=n, step_id=step.id, inputs=bound,
            context=assemble_context(step.lock.context, inst, ctx) if step.lock.context else None,
            workspace=str(ctx.workspace), policy=policy,
            cassette=self._cassette(ctx, module, _sub(step.package_dir, "cassettes")),
        )
        remaining = None
        if deadline is not None:
            remaining = deadline.remaining()
            if remaining <= 0:
                raise DeadlineExceeded(deadline)
        try:
            wire = self.dispatcher.dispatch(step.id, params, on_event=_forward(ctx, path, span, inst.parent_span),
                                            timeout=remaining)
        except StepTimeout:
            if deadline is None:
                raise
            raise DeadlineExceeded(deadline) from None
        except (WorkerCrashed, WorkerRpcError) as err:
            return _error(path, "worker_crash", f"{type(err).__name__}: {err}", bound, type=type(err).__name__)
        return _Result.from_wire(wire)

    def _run_child(
        self, inst: Instance, pid: str, path: str, span: int, bound: dict[str, Any], deadline: Deadline | None
    ) -> _Result:
        """A ProcessStep node: a fresh instance (own scope and counters) sharing the run; its `$exit.error` is this
        node's `error` exit with cause `child_process`."""
        try:
            inputs = self._ifaces[pid].input_model.model_validate(bound).model_dump(mode="json")
        except ValidationError as err:
            return _error(path, "input_validation", f"ValidationError: {err}", bound, attempts=0,
                          type="ValidationError")
        out = self._run_process(self.plan.processes[pid], inputs, inst.ctx, prefix=path + ".", parent_span=span,
                                deadline=deadline)
        usage = out.usage if out.usage.calls else None
        if out.exit != RESERVED_EXIT:
            return _Result(out.exit, out.outputs, summarise(path, out.exit, out.outputs), usage=usage)
        child = out.error
        failure = StepError(cause="child_process", message=child.message, inputs=bound,
                            partial_outputs=child.partial_outputs, child=child)
        outputs = failure.model_dump(mode="json", exclude={"exit"})
        return _Result(RESERVED_EXIT, outputs, summarise(path, RESERVED_EXIT, outputs), usage=usage)

    def _run_finally(self, inst: Instance, exit: str) -> list[StepError]:
        """Each `finally` step in order, with no deadline; inputs from `with:` (final scope) or by name from
        `{**process.inputs, run_id, exit}`; exits are not routed and errors never change the process exit."""
        errors: list[StepError] = []
        for item in inst.plan.definition.finally_:
            if item.with_:
                try:
                    bound = evaluate_with(item.with_, inst.scope)
                except ExprError as err:
                    errors.append(StepError(cause="input_validation", message=f"finally step '{item.step}': {err}"))
                    continue
            else:
                source = {**inst.scope.process_inputs, "run_id": inst.ctx.run_id, "exit": exit}
                bound = self._bind(inst, item.step, source)
            rec = self._run_node(inst, item.step, bound, via="finally", role="finally", no_deadline=True)
            if rec.exit == RESERVED_EXIT:
                errors.append(StepError.model_validate(rec.outputs))
        return errors

    # --- the process error handler ----------------------------------------------------------------------------------

    def _fail(
        self, inst: Instance, cause: ProcessErrorCause, *, rec: StepRecord | None = None, step: str | None = None,
        inputs: dict[str, Any] | None = None, edge: str | None = None, message: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> _Outcome:
        """Build the `ProcessError`, emit `process.error`, then run the default handler (`$exit.error`) or the
        custom `on_error` step (its exit `x` becomes `$exit.x`; an error inside it ends with `$exit.error`)."""
        ctx = inst.ctx
        name = rec.name if rec is not None else step
        step_error = StepError.model_validate(rec.outputs) if rec is not None and rec.exit == RESERVED_EXIT else None
        if rec is None:
            partial = None
        else:
            partial = step_error.partial_outputs if step_error is not None else rec.outputs
        error = ProcessError(
            run_id=ctx.run_id, process=inst.plan.id, step=inst.prefix + name if name else None, cause=cause,
            message=message or _message(cause, inst.prefix, rec, step_error, edge), edge=edge,
            inputs=rec.inputs if rec is not None else (inputs or {}), partial_outputs=partial,
            step_error=step_error if cause == "step_error" else None, detail=detail or {},
        )
        handler = inst.plan.definition.on_error
        event = ctx.emitter.emit("process.error", process=inst.plan.id, parent=inst.parent_span, error=error,
                                 handler=handler or "default")
        error.trace = TracePointer(run_id=ctx.run_id, uri=self.stores.traces.uri(ctx.run_id), seq=event["seq"])
        if handler is None:
            return _Outcome(RESERVED_EXIT, {}, error=error, handled=True)
        bound = self._bind(inst, handler, error.model_dump(mode="json"))
        done = self._run_node(inst, handler, bound, via="on_error", role="on_error")
        if done.exit == RESERVED_EXIT:
            error.handler_error = StepError.model_validate(done.outputs)
            return _Outcome(RESERVED_EXIT, {}, error=error, handled=True)
        try:
            outputs = self._process_output(inst.plan.id, done.exit, done.outputs)
        except ValueError as err:
            error.handler_error = StepError(cause="output_validation", message=f"handler exit '{done.exit}': {err}",
                                            inputs=bound)
            return _Outcome(RESERVED_EXIT, {}, error=error, handled=True)
        return _Outcome(done.exit, outputs, error=error, handled=True)

    # --- helpers ----------------------------------------------------------------------------------------------------

    def _bind(self, inst: Instance, name: str, source: Mapping[str, Any]) -> dict[str, Any]:
        """Bind by field name: the source values whose names the node's Input declares (entry, handler, finally)."""
        fields = self._input_fields(inst.plan.nodes[name])
        return {f: source[f] for f in fields if f in source}

    def _input_fields(self, node: PlanNode) -> list[str]:
        if node.process is not None:
            return self._ifaces[node.process].input_fields()
        try:
            return self.dispatcher.describe(node.step).input_fields
        except Exception:  # noqa: BLE001 — a step that fails to describe fails its dispatch with the real cause
            return []

    def _process_output(self, pid: str, exit: str, bound: Mapping[str, Any]) -> dict[str, Any]:
        """Validate `$exit.<exit>` bindings against the process output model (ValueError / ValidationError)."""
        model = self._ifaces[pid].exits.get(exit)
        if model is None:
            raise ValueError(f"'{exit}' is not a declared exit of process {pid}")
        return model.model_validate({**bound, "exit": exit}).model_dump(mode="json", exclude={"exit"})

    def _cassette(self, ctx: RunCtx, sub: str, default_dir: str | None) -> CassetteConfig:
        """`<cassette_root>/<sub>` (else the step's or process's own cassettes dir) and `<record_root>/<sub>`."""
        root, staging = ctx.cassette_root, ctx.record_root
        return CassetteConfig(
            mode=ctx.cassette_mode,
            dir=str(Path(root) / sub) if root is not None else default_dir,
            record_dir=str(Path(staging) / sub) if staging is not None else None,
            literals=dict(ctx.cassette_literals),
        )


def _forward(ctx: RunCtx, step: str, span: int | None, parent: int | None) -> Callable[[dict[str, Any]], None]:
    """Stamps worker notifications with `step/span/parent`; the emitter adds `seq/ts/run_id`."""

    def forward(event: dict[str, Any]) -> None:
        fields = {k: v for k, v in event.items() if k not in _ENVELOPE}
        ctx.emitter.emit(event["type"], **{**fields, "step": step, "span": span, "parent": parent})

    return forward


def _error(
    path: str, cause: StepErrorCause, message: str, bound: dict[str, Any], *, attempts: int = 1,
    type: str | None = None,
) -> _Result:
    """An executor-side `error` exit (config, worker crash, child input validation)."""
    outputs = StepError(cause=cause, message=message, type=type, inputs=bound, attempts=attempts).model_dump(
        mode="json", exclude={"exit"})
    return _Result(RESERVED_EXIT, outputs, summarise(path, RESERVED_EXIT, outputs), attempts=attempts)


def _message(
    cause: ProcessErrorCause, prefix: str, rec: StepRecord | None, step_error: StepError | None, edge: str | None
) -> str:
    path = prefix + rec.name if rec is not None else None
    match cause:
        case "step_error":
            return f"step '{path}' failed ({step_error.cause}): {step_error.message}"
        case "unrouted_exit":
            return f"exit '{rec.exit}' of step '{path}' has no edge"
        case "ignored_exit":
            return f"exit '{rec.exit}' of step '{path}' is explicitly ignored ($ignore)"
        case "no_branch_matched":
            return f"no branch of '{edge}' matched"
    return cause


def _timings(started: datetime, t0: float, worker: StepTimings | None) -> dict[str, Any]:
    """Executor wall clock for the node plus the worker's own timings when it ran in one."""
    return {
        "started_at": started, "ended_at": _now(), "duration_ms": (time.perf_counter() - t0) * 1000,
        "worker_ms": worker.worker_ms if worker else None, "pre_ms": worker.pre_ms if worker else None,
        "run_ms": worker.run_ms if worker else None, "post_ms": worker.post_ms if worker else None,
    }


def _sub(base: str | None, *parts: str) -> str | None:
    return str(Path(base, *parts)) if base else None


def _write_json(path: Path, value: Any) -> None:
    """Raw inputs/outputs stay addressable in the run workspace (SPEC §3.7)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(to_jsonable_python(value), ensure_ascii=False, indent=2), encoding="utf-8")


def _now() -> datetime:
    return datetime.now(UTC)


def _tree_bytes(root: Path) -> int:
    total = 0
    for dirpath, _, files in os.walk(root):
        for name in files:
            try:
                total += os.lstat(os.path.join(dirpath, name)).st_size
            except OSError:
                pass
    return total


def _file_bytes(uri: str) -> int | None:
    """Size of a `file://` trace; None for other sinks."""
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        return None
    try:
        return Path(url2pathname(parsed.path)).stat().st_size
    except OSError:
        return None
