"""M5 agentic edges (PLAN §5.6; `$DRAFTS/08 §4.4–§4.5`): the worker-side verifier and the executor-side
`WorkerEdgeChecker`. The seam types are imported from `wynd.runtime.executor.edges` and never redefined here.

A check is one structured completion through the same loop agentic steps use (`complete_structured`: provider,
cassette record/replay, validation retries that continue the conversation, `model.call` events with
`step = "edge:<branch_key>"`), with no tools and no MCP. It runs in the venv worker the plan assigns to the branch
(`RunPlan.edge_venvs`), reached through the worker RPC method `edge.check`. Only the executor emits `edge.check`.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from wynd.runtime.cassettes import CassetteError
from wynd.runtime.errors import StepFailure
from wynd.runtime.executor.edges import (
    EdgeCheckCall,
    EdgeCheckCause,
    EdgeChecker,
    EdgeCheckError,
    EdgeCheckResult,
    EdgeVerdict,
    make_edge_check_call,
)
from wynd.runtime.handle import RuntimeHandle, StepCache, StepTrace
from wynd.runtime.policy import CassetteConfig, ExecPolicy
from wynd.runtime.worker.client import StepTimeout, WorkerCrashed, WorkerRpcError
from wynd.runtime.worker.protocol import EDGE_CHECK_ERROR, INVALID_PARAMS
from wynd.spec.lockfiles import DEFAULT_RETRIES, RetryPolicy

if TYPE_CHECKING:
    from wynd.runtime.worker.pool import WorkerPool
    from wynd.spec.plan import RunPlan

__all__ = [
    "EDGE_VERDICT_SCHEMA",
    "VERIFIER_INSTRUCTION",
    "EdgeCheckCall",
    "EdgeCheckCause",
    "EdgeCheckError",
    "EdgeCheckResult",
    "EdgeChecker",
    "EdgeVerdict",
    "WorkerEdgeChecker",
    "handle_edge_check",
    "make_edge_check_call",
    "run_edge_check",
]

EDGE_VERDICT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"take": {"type": "boolean"}, "reason": {"type": "string", "maxLength": 500}},
    "required": ["take", "reason"],
    "additionalProperties": False,
}

VERIFIER_INSTRUCTION = """\
You are the transition verifier for one edge of an automated process.
Decide whether the process should take the transition described in the input.

Check: {check}

Take the transition (take = true) only if the check is clearly satisfied by the data
provided. If the data is missing, ambiguous or contradicts the check, do not take it
(take = false). Give a one-sentence reason that cites the data.
"""

# A check has no tools, so every transport failure happens before a tool call: it restarts like an agentic step's
# loop (SPEC §3.5). The lock entry's `retries` are the validation retries; `timeout_s` bounds all of it.
_TRANSPORT_RESTARTS = DEFAULT_RETRIES["agentic"].run

_CAUSES: dict[str, EdgeCheckCause] = {
    "output_validation": "validation",
    "transport": "transport",
    "config": "config",
    "model": "model",
    "cassette_miss": "cassette_miss",
}


class _Verdict(BaseModel):
    """Validates the structured output against `EDGE_VERDICT_SCHEMA`."""

    model_config = ConfigDict(extra="forbid", strict=True)
    take: bool
    reason: str = Field(max_length=500)


class _Params(BaseModel):
    """`edge.check` params (PLAN §3.12)."""

    call: EdgeCheckCall
    cassette: CassetteConfig = CassetteConfig()
    workspace: str


_VERDICT = TypeAdapter(_Verdict)
_CALL = TypeAdapter(EdgeCheckCall)
_RESULT = TypeAdapter(EdgeCheckResult)


def run_edge_check(
    call: EdgeCheckCall,
    cassette: CassetteConfig,
    workspace: str,
    on_event: Callable[[dict[str, Any]], None],
) -> EdgeCheckResult:
    """Worker-side check via `complete_structured` (no tools, no MCP, `unit=f"edge:{branch_key}"`); raises
    `EdgeCheckError` with the cause of the failure (output validation after the lock's retries -> `validation`)."""
    from wynd.runtime.agentic.loop import StructuredCall, complete_structured
    from wynd.runtime.cassettes.wrap import CassetteSession

    t0 = time.perf_counter()
    unit = f"edge:{call.branch_key}"
    lock = call.lock
    root = Path(workspace)
    try:
        session = CassetteSession(cassette, run_id=call.run_id, workspace=root, provider=call.provider,
                                  tier=lock.tier, thinking=lock.thinking, step=unit)
    except CassetteError as err:
        raise EdgeCheckError("config", str(err)) from None
    runtime = RuntimeHandle(run_id=call.run_id, step_path=unit, step_run=1, workspace=root,
                            logger=logging.getLogger("wynd.edges"), trace=StepTrace(on_event), cache=StepCache())
    policy = ExecPolicy(kind="agentic", retries=RetryPolicy(run=_TRANSPORT_RESTARTS, validation=lock.retries),
                        provider=call.provider, model_id=call.model_id, tier=lock.tier, thinking=lock.thinking)
    try:
        agent = complete_structured(StructuredCall(
            unit=unit,
            instruction=VERIFIER_INSTRUCTION.format(check=call.check),
            context=call.context,
            input={"process_goal": call.process_goal, "transition": {"from": call.edge, "to": call.target},
                   "bindings": call.bindings},
            adapter=_VERDICT,
            output_schema=EDGE_VERDICT_SCHEMA,
            tools=None,
            policy=policy,
            cassette=session,
            runtime=runtime,
        ))
    except StepFailure as err:
        raise EdgeCheckError(_CAUSES.get(err.cause, "model"), err.message) from None
    return EdgeCheckResult(
        verdict=EdgeVerdict(take=agent.output.take, reason=agent.output.reason),
        attempts=agent.attempts,
        validation_failures=agent.attempts - 1,
        provider=call.provider,
        tier=lock.tier,
        model_id=agent.model.model_id,
        usage=agent.usage,
        replayed=cassette.mode == "replay",
        duration_ms=(time.perf_counter() - t0) * 1000,
    )


def handle_edge_check(params: dict[str, Any]) -> dict[str, Any]:
    """Worker RPC handler for `edge.check`: `{"call", "cassette", "workspace"}` -> `EdgeCheckResult` JSON. Its
    `model.call` events go out through `wynd.runtime.worker.server.notify`; a failed check raises `EdgeCheckError`,
    which the worker server answers with JSON-RPC `-32001` and `data = {"cause", "message"}`."""
    from wynd.runtime.worker import server

    try:
        parsed = _Params.model_validate(params)
    except ValidationError as err:
        raise server.RpcFault(INVALID_PARAMS, f"invalid edge.check params: {err}") from err
    result = run_edge_check(parsed.call, parsed.cassette, parsed.workspace, server.notify)
    return _RESULT.dump_python(result, mode="json")


class WorkerEdgeChecker:
    """Implements `EdgeChecker` by dispatching `edge.check` to `plan.edge_venvs[f"{pid}:{branch_key}"]`; emits
    nothing (the pool hands the worker's notifications to `on_event`). Stateless per run, so one checker serves
    concurrent runs."""

    def __init__(self, pool: WorkerPool, plan: RunPlan) -> None:
        self.pool = pool
        self.plan = plan

    def check(
        self,
        call: EdgeCheckCall,
        *,
        cassette: CassetteConfig,
        workspace: str,
        timeout_s: float | None,
        on_event: Callable[[dict[str, Any]], None],
    ) -> EdgeCheckResult:
        """Raises `EdgeCheckError`: the worker's own cause for a `-32001` reply, `timeout` when no verdict came
        within `timeout_s` (the worker is killed), `config` for a branch the plan assigns no venv, and `transport` when
        the worker crashed or answered with another JSON-RPC error."""
        key = f"{call.process}:{call.branch_key}"
        venv = self.plan.edge_venvs.get(key)
        if venv is None:
            raise EdgeCheckError("config", f"the run plan assigns no venv to agentic branch {key} (edge_venvs)")
        params = {"call": _CALL.dump_python(call, mode="json"), "cassette": cassette.model_dump(mode="json"),
                  "workspace": workspace}
        try:
            result = self.pool.call(venv, "edge.check", params, on_event=on_event, timeout=timeout_s)
        except StepTimeout:
            raise EdgeCheckError("timeout", f"no verdict within {timeout_s:g}s") from None
        except WorkerCrashed as err:
            raise EdgeCheckError("transport", f"the edge-check worker of venv {venv} crashed: {err}") from None
        except WorkerRpcError as err:
            if err.code == EDGE_CHECK_ERROR:
                raise EdgeCheckError(err.data["cause"], err.data["message"]) from None
            detail = err.data.get("traceback") if isinstance(err.data, dict) else None
            message = f"edge.check failed in the worker of venv {venv} (JSON-RPC {err.code}): {err}"
            raise EdgeCheckError("transport", f"{message}\n{detail}" if detail else message) from None
        return _RESULT.validate_python(result)
