"""Per-run and per-process-instance executor state (PLAN §5.4; `$DRAFTS/02 §7.2`): `Instance{plan, prefix,
parent_span, scope, deadline, history}`, completed-run records, deadlines with owner tokens, the run context."""

from __future__ import annotations

from typing import Any


class StepRecord:
    """One completed node run: name, path, run number, exit, inputs, outputs, summary."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.4")


class Deadline:
    """A monotonic deadline plus the token of the frame that owns it (`$DRAFTS/02 §7.7`)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.4")


class RunCtx:
    """State shared by every instance of one run: run id, workspace, emitter, env, cassette settings, usage."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.4")


class Instance:
    """One process instance (the root, and each ProcessStep invocation) with its own spec `Scope`."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        raise NotImplementedError("PLAN §5.4")
