"""Runtime records embedded in outputs, traces and errors (PLAN §3.8; $DRAFTS/01 §6.9)."""

from typing import Any, Literal

from wynd.spec.base import SpecModel


class Summary(SpecModel):  # SPEC §3.6 step 5, derived deterministically (never a model call)
    step: str
    exit: str
    key_outputs: dict[str, Any] = {}
    note: str = ""


StepErrorCause = Literal[
    "input_validation", "exception", "hook", "output_validation", "tool", "transport", "model", "config",
    "cassette_miss", "shell_exit", "child_process", "worker_crash", "import",
]


class StepError(SpecModel):  # payload of EVERY step's implicit `error` exit
    exit: Literal["error"] = "error"
    cause: StepErrorCause
    message: str
    type: str | None = None  # exception class name
    traceback: str | None = None
    inputs: dict[str, Any] = {}  # the bound inputs as received (lets `X.error` edges re-bind X's inputs)
    partial_outputs: dict[str, Any] | None = None
    attempts: int = 1
    child: "ProcessError | None" = None  # cause == "child_process"


ProcessErrorCause = Literal[
    "step_error", "unrouted_exit", "ignored_exit", "no_branch_matched", "max_traversals", "timeout",
    "expression_error", "invalid_process_outputs", "edge_check", "internal",
]


class TracePointer(SpecModel):
    run_id: str
    uri: str
    seq: int  # seq of the process.error event


class ProcessError(SpecModel):  # SPEC §3.5: step, cause, inputs, partial outputs, trace pointer
    run_id: str
    process: str  # process id of the failing instance
    step: str | None  # full step path; None if not step-specific
    cause: ProcessErrorCause
    message: str
    edge: str | None = None  # edge key "validate.done", or branch_key(...) when branch-specific
    inputs: dict[str, Any] = {}  # the failing step's bound inputs (process inputs when step is None)
    partial_outputs: dict[str, Any] | None = None
    step_error: StepError | None = None  # cause == "step_error"
    handler_error: StepError | None = None  # a custom on_error handler itself failed
    detail: dict[str, Any] = {}  # e.g. edge_check {"branch_key", "error_cause"}
    trace: TracePointer | None = None
    workspace: str | None = None  # URI when the workspace was kept


StepError.model_rebuild()

STEP_ERROR_SCHEMA: dict = StepError.model_json_schema()
