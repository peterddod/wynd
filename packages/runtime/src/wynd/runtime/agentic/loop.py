"""Structured completion (PLAN §5.5; `$DRAFTS/03 §6.2, §6.5–§6.8`).

`complete(step, call)` is a thin wrapper over `complete_structured(StructuredCall(...))`; M5 edge checks call
`complete_structured` with `tools=None`. Validation retries continue the conversation; transport restarts happen only
before any tool call, at most `retries.run` times (PLAN §3.9). Every failure leaves as `StepFailure(cause)` carrying
the running usage and the attempt count.
"""

from __future__ import annotations

import functools
import time
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, TypeAdapter, ValidationError

from wynd.runtime.agentic.checks import step_instruction
from wynd.runtime.agentic.errors import (
    AgentLoopError,
    McpConfigError,
    McpSnapshotMismatch,
    MissingEnvVar,
    OutputValidationFailed,
    ProviderError,
    ToolFailure,
)
from wynd.runtime.agentic.prompt import (
    RETRY_NO_OUTPUT,
    RETRY_TRUNCATED,
    RETRY_VALIDATION,
    format_validation_errors,
    render_prompt,
)
from wynd.runtime.cassettes import CassetteError, CassetteMissError
from wynd.runtime.cassettes.wrap import CassetteSession
from wynd.runtime.errors import StepFailure
from wynd.runtime.middleware import AgentResult
from wynd.runtime.providers import load_provider, provider_info
from wynd.runtime.providers.types import AgentRequest, Continuation, GenerateRequest, GenerateResponse, Message
from wynd.runtime.tools.toolset import ToolSet
from wynd.runtime.usage import ModelInfo, Usage

if TYPE_CHECKING:
    from wynd.runtime.handle import RuntimeHandle
    from wynd.runtime.middleware import AgentCall
    from wynd.runtime.policy import ExecPolicy
    from wynd.runtime.providers import ProviderInfo
    from wynd.runtime.step import AgenticStep
    from wynd.spec.records import StepErrorCause

BACKOFF_S = (1.0, 4.0)          # sleep before transport restart 1, 2 (and every later one)
NOTE_LIMIT = 500

_CAUSE: dict[str, StepErrorCause] = {
    "transport": "transport", "auth": "config", "unavailable": "config",
    "invalid_request": "model", "refusal": "model", "max_turns": "model",
}


@dataclass(frozen=True)
class StructuredCall:
    """One structured completion: an agentic step run (`complete`) or an M5 edge check.

    `cassette` is the session of this call, built by the caller from its `CassetteConfig` with the run id, workspace
    and the policy's provider/tier/thinking (`CassetteSession(config, run_id=..., workspace=..., provider=...,
    tier=..., thinking=..., step=unit)`); a `ToolSet` passed in `tools` must share it, so tool recordings land beside
    the model recordings."""

    unit: str                          # `step` of its model.call events: the step path, or "edge:<branch_key>"
    instruction: str                   # the step docstring (the system prompt's task)
    context: dict[str, Any] | None     # assembled context, keyed by the declared entries
    input: dict[str, Any]              # JSON mode
    adapter: TypeAdapter               # validates the structured output (discriminated on "exit")
    output_schema: dict[str, Any]      # the UNWRAPPED Output JSON schema (providers wrap it)
    tools: ToolSet | None              # None: no tools and no MCP
    policy: ExecPolicy                 # provider, model_id, tier, thinking, retries, max_turns, builtin_tools
    cassette: CassetteSession          # record/replay at the provider boundary
    runtime: RuntimeHandle             # run_id, workspace (harness cwd), trace.emit


class _Loop:
    """State both loops share: running usage, the attempt/restart counters and the `model.call` events."""

    kind = ""

    def __init__(self, call: StructuredCall, provider: Any, tools: ToolSet) -> None:
        self.call = call
        self.provider = provider
        self.tools = tools
        self.usage = Usage()
        self.attempt = 1                # structured-output attempt within the current conversation
        self.failures = 0               # failed structured-output attempts within the current conversation
        self.restarts = 0
        self.calls = 0                  # provider calls so far (model.call `n`)
        self.last_output: Any = None    # the latest structured output that failed validation

    def failure(self, cause: StepErrorCause, err: BaseException) -> StepFailure:
        partial = self.last_output if cause == "output_validation" and isinstance(self.last_output, dict) else None
        return StepFailure(cause, str(err), partial_outputs=partial, usage=self.usage, attempts=self.attempt)

    def _call(self, fn: Any, req: Any, *, reason: str, request: dict[str, Any],
              messages_new: list[Message] | None = None) -> Any:
        """One provider call; a raised error is traced as outcome `error` and re-raised."""
        self.calls += 1
        try:
            return fn(req)
        except Exception as err:
            self._emit(reason=reason, outcome="error", request=request, messages_new=messages_new, response=None,
                       usage=None, model_id=self.call.policy.model_id, errors=f"{type(err).__name__}: {err}")
            raise

    def _check(self, structured: Any) -> tuple[BaseModel | None, str, str, str]:
        """-> (valid output | None, outcome, retry message for the model, problem for the step error)."""
        if structured is None:
            return None, "no_output", RETRY_NO_OUTPUT, "the reply had no structured output"
        try:
            return self.call.adapter.validate_python(structured), "valid", "", ""
        except ValidationError as err:
            self.last_output = structured
            errors = format_validation_errors(err)
            return None, "invalid", RETRY_VALIDATION.format(errors=errors), f"it did not validate:\n{errors}"

    def _failed(self, detail: str) -> None:
        """Count a failed structured-output attempt; give up once `retries.validation` continuations are spent."""
        self.failures += 1
        if self.failures > self.call.policy.retries.validation:
            raise OutputValidationFailed(f"no valid structured output after {self.attempt} attempt(s); {detail}")
        self.attempt += 1

    def _restart(self, err: ProviderError, *, called: bool) -> None:
        """Full restart for a transport failure before any tool call, at most `retries.run` times; else re-raise."""
        if not err.retryable or called or self.restarts >= self.call.policy.retries.run:
            raise err
        time.sleep(BACKOFF_S[min(self.restarts, len(BACKOFF_S) - 1)])
        self.restarts += 1

    def _model(self, model_id: str) -> ModelInfo:
        policy = self.call.policy
        return ModelInfo(provider=policy.provider, model_id=model_id or policy.model_id, tier=policy.tier,
                         thinking=policy.thinking)

    def _emit(self, *, reason: str, outcome: str, request: dict[str, Any], response: dict[str, Any] | None,
              usage: Usage | None, model_id: str | None, messages_new: list[Message] | None = None,
              errors: str | None = None, startup_ms: float | None = None, cost_basis: str | None = None) -> None:
        policy = self.call.policy
        key = getattr(self.provider, "last_key", None)
        self.call.runtime.trace.emit(
            "model.call",
            step=self.call.unit,
            provider=policy.provider,
            kind=self.kind,
            tier=policy.tier,
            thinking=policy.thinking,
            model_id=model_id,
            n=self.calls,
            attempt=self.attempt,
            restart=self.restarts,
            reason=reason,
            outcome=outcome,
            errors=errors or None,
            request_hash=key[:32] if key else None,
            request=request,
            messages_new=messages_new,
            response=response,
            usage=usage.model_dump(mode="json") if usage is not None else None,
            startup_ms=startup_ms,
            cost_basis=cost_basis,
            cassette=self.call.cassette.mode,
        )


class ModelLoop(_Loop):
    """The runtime-owned loop over a ModelProvider (`$DRAFTS/03 §6.5`): send, run the requested tools in order,
    append the results, repeat until the structured output validates. Invalid, missing or truncated output continues
    the conversation with the error; the partial assistant turn of a truncated reply is dropped."""

    kind = "model"

    def run(self) -> AgentResult:
        call, policy = self.call, self.call.policy
        system, user = render_prompt(call.instruction, call.context or {}, call.input)
        schemas = self.tools.schemas()
        request = {"system": system, "tools": [asdict(s) for s in schemas], "output_schema": call.output_schema}
        while True:
            messages: list[Message] = [_user_text(user)]
            shown: list[Message] = []
            self.attempt, self.failures, note, reason = 1, 0, "", "initial"
            try:
                for _ in range(policy.max_turns):
                    new = _new_messages(shown, messages)
                    shown = list(messages)
                    req = GenerateRequest(policy.model_id, policy.thinking, system, list(messages), schemas,
                                          call.output_schema)
                    resp: GenerateResponse = self._call(self.provider.generate, req, reason=reason, request=request,
                                                        messages_new=new)
                    self.usage += resp.usage
                    if resp.text.strip() and resp.structured_output is None:
                        note = resp.text.strip()[:NOTE_LIMIT]
                    emit = functools.partial(
                        self._emit, reason=reason, request=request, messages_new=new, response=_generate_json(resp),
                        usage=resp.usage, model_id=resp.model_id, cost_basis=resp.cost_basis,
                    )
                    if resp.stop == "refusal":
                        emit(outcome="refusal")
                        text = resp.text.strip()
                        raise ProviderError(f"the model refused: {text}" if text else "the model refused",
                                            kind="refusal", retryable=False)
                    if resp.stop == "max_tokens":
                        emit(outcome="truncated", errors=RETRY_TRUNCATED)
                        self._failed("the reply was cut off at the output token limit")
                        messages[-1] = _with_text(messages[-1], RETRY_TRUNCATED)
                        reason = "validation_retry"
                        continue
                    messages.append(resp.message)
                    if resp.tool_calls:
                        emit(outcome="tool_calls")
                        results = [self.tools.invoke(tc.name, tc.arguments) for tc in resp.tool_calls]
                        messages.append({"role": "user", "content": [
                            {"type": "tool_result", "tool_use_id": tc.id, "content": r.text, "is_error": r.is_error}
                            for tc, r in zip(resp.tool_calls, results, strict=True)
                        ]})
                        reason = "tool_results"
                        continue
                    out, outcome, text, problem = self._check(resp.structured_output)
                    if out is not None:
                        emit(outcome="valid")
                        return AgentResult(out, note, self.usage, self.attempt, self._model(resp.model_id))
                    emit(outcome=outcome, errors=text)
                    self._failed(problem)
                    messages.append(_user_text(text))
                    reason = "validation_retry"
                raise AgentLoopError(f"agent loop exceeded max_turns={policy.max_turns}")
            except ProviderError as err:
                self._restart(err, called=self.tools.calls > 0)


class AgentLoop(_Loop):
    """The loop around an AgentProvider harness (`$DRAFTS/03 §6.6`): the harness runs the tools; the runtime
    validates, continues the same session with the error on a failed attempt, and traces harness built-ins."""

    kind = "agent"

    def run(self) -> AgentResult:
        call, policy = self.call, self.call.policy
        handles, refs = self.tools.handles(), self.tools.mcp_refs()
        base = {
            "instruction": call.instruction,
            "context": call.context or {},
            "input": call.input,
            "output_schema": call.output_schema,
            "tools": [{"name": h.name, "description": h.description, "input_schema": h.input_schema} for h in handles],
            "mcp": [{"name": r.name, "allow": list(r.allow)} for r in refs],
            "builtin_tools": list(policy.builtin_tools),
            "max_turns": policy.max_turns,
        }
        while True:
            continuation: Continuation | None = None
            harness_calls, reason = 0, "initial"
            self.attempt, self.failures = 1, 0
            try:
                while True:
                    req = AgentRequest(
                        model_id=policy.model_id, thinking=policy.thinking, instruction=call.instruction,
                        context=call.context or {}, input=call.input, output_schema=call.output_schema,
                        tools=handles, mcp_servers=refs, workspace=call.runtime.workspace,
                        max_turns=policy.max_turns, builtin_tools=list(policy.builtin_tools),
                        continuation=continuation,
                    )
                    request = {**base, "continuation": continuation.message if continuation else None}
                    resp = self._call(self.provider.run, req, reason=reason, request=request)
                    self.usage += resp.usage
                    harness_calls += resp.tool_calls
                    self.tools.trace_harness_calls(resp.transcript)
                    emit = functools.partial(
                        self._emit, reason=reason, request=request,
                        response={"structured_output": resp.structured_output, "note": resp.note,
                                  "transcript": resp.transcript},
                        usage=resp.usage, model_id=resp.model_id, startup_ms=resp.startup_ms,
                        cost_basis=resp.cost_basis,
                    )
                    out, outcome, text, problem = self._check(resp.structured_output)
                    if out is not None:
                        emit(outcome="valid")
                        return AgentResult(out, resp.note[:NOTE_LIMIT], self.usage, self.attempt,
                                           self._model(resp.model_id))
                    emit(outcome=outcome, errors=text)
                    self._failed(problem)
                    continuation = Continuation(session=resp.session, message=text)
                    reason = "validation_retry"
            except ProviderError as err:
                self._restart(err, called=err.tool_called or self.tools.calls > 0 or harness_calls > 0)


def complete_structured(call: StructuredCall) -> AgentResult:
    """Run the loop for the provider kind. MCP servers are connected and verified before the first model call
    (skipped in replay) and closed afterwards. Failures -> `StepFailure` with the PLAN §3.9 cause."""
    info = _provider_info(call.policy.provider)
    provider = call.cassette.wrap(lambda: load_provider(call.policy.provider), kind=info.kind)
    tools = call.tools if call.tools is not None else ToolSet([], runtime=call.runtime, cassettes=call.cassette)
    loop = (ModelLoop if info.kind == "model" else AgentLoop)(call, provider, tools)
    try:
        if call.cassette.mode != "replay":
            tools.connect_mcp()
        return loop.run()
    except StepFailure as err:
        err.usage = err.usage or loop.usage
        err.attempts = err.attempts if err.attempts is not None else loop.attempt
        raise
    except CassetteMissError as err:
        raise loop.failure("cassette_miss", err) from err
    except (McpSnapshotMismatch, McpConfigError, MissingEnvVar, CassetteError) as err:
        raise loop.failure("config", err) from err
    except ToolFailure as err:
        raise loop.failure("tool", err) from err
    except OutputValidationFailed as err:
        raise loop.failure("output_validation", err) from err
    except ProviderError as err:
        raise loop.failure(_CAUSE[err.kind], err) from err
    except AgentLoopError as err:
        raise loop.failure("model", err) from err
    finally:
        tools.close()


def complete(step: AgenticStep, call: AgentCall) -> AgentResult:
    """The agentic step's `run`: its docstring, context and input through `complete_structured`, with its tools
    (`@tool` methods, `tools`, MCP snapshots) sharing the step run's cassette session."""
    policy, rt = call.policy, call.runtime
    try:
        session = CassetteSession(
            call.cassette, run_id=rt.run_id, workspace=rt.workspace, provider=policy.provider, tier=policy.tier,
            thinking=policy.thinking, package=_package(type(step)), step=call.step_path,
        )
    except CassetteError as err:
        raise StepFailure("config", str(err)) from err
    return complete_structured(StructuredCall(
        unit=call.step_path,
        instruction=step_instruction(type(step)),
        context=call.context,
        input=call.input.model_dump(mode="json"),
        adapter=call.interface.output_adapter,
        output_schema=call.interface.output_json_schema(),
        tools=ToolSet.for_step(step, call, session),
        policy=policy,
        cassette=session,
        runtime=rt,
    ))


def _provider_info(name: str | None) -> ProviderInfo:
    if not name:
        raise StepFailure("config", "no provider was resolved for this agentic call")
    try:
        return provider_info(name)
    except KeyError:
        raise StepFailure("config", f"provider {name!r} is not installed (entry point group wynd.providers)") from None


def _package(cls: type) -> str | None:
    """The step package (`wynd_steps.<module name>.<module>` -> `<module name>`), recorded in cassette entries."""
    parts = cls.__module__.split(".")
    return parts[1] if len(parts) > 2 and parts[0] == "wynd_steps" else None


def _user_text(text: str) -> Message:
    return {"role": "user", "content": [{"type": "text", "text": text}]}


def _with_text(message: Message, text: str) -> Message:
    """A copy of `message` with a text block appended (earlier requests keep the original)."""
    return {**message, "content": [*message["content"], {"type": "text", "text": text}]}


def _new_messages(shown: list[Message], messages: list[Message]) -> list[Message]:
    """The messages not in the previous model.call of this conversation (a replaced message counts as new)."""
    same = 0
    while same < min(len(shown), len(messages)) and shown[same] is messages[same]:
        same += 1
    return messages[same:]


def _generate_json(resp: GenerateResponse) -> dict[str, Any]:
    return {
        "text": resp.text,
        "tool_calls": [asdict(tc) for tc in resp.tool_calls],
        "structured_output": resp.structured_output,
        "stop": resp.stop,
    }
