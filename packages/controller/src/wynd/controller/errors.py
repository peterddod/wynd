"""The controller's error hierarchy (PLAN §8.1; `$DRAFTS/06 §5.2` verbatim). Written complete by W0; owner CTL-CORE.

Every class carries `code`, `http` and `exit` (PLAN §3.22), with PLAN §8.1's `ValidationFailed` (422/1, mapped from
`wynd.process.errors.ValidationFailed`) and `EnvMissing` (`env_missing`, 412/3). The HTTP body is
`{"error": {"code", "message", "details", "hint"}}`; the CLI prints `error: <message>` (and `hint: <hint>`) to stderr
and exits with `exit`.
"""

from typing import Any, ClassVar


class WyndError(Exception):
    code: ClassVar[str] = "error"
    http: ClassVar[int] = 500
    exit: ClassVar[int] = 1

    def __init__(self, message: str, *, details: Any = None, hint: str | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details
        self.hint = hint


class Invalid(WyndError):
    code = "invalid"
    http = 422
    exit = 2


class ValidationFailed(WyndError):
    """details = the validation report."""

    code = "validation_failed"
    http = 422
    exit = 1


class Unauthorized(WyndError):
    code = "unauthorized"
    http = 401
    exit = 3


class OutOfScope(WyndError):
    code = "out_of_scope"
    http = 403
    exit = 3


class NotFound(WyndError):
    code = "not_found"
    http = 404
    exit = 3


class NotAWorkspace(NotFound):
    code = "not_a_workspace"


class Conflict(WyndError):
    code = "conflict"
    http = 409
    exit = 3


class DirtyTree(Conflict):
    """details = {"paths": [...]}"""

    code = "dirty_tree"


class RevisionConflict(Conflict):
    """details = {"path", "current_revision"}"""

    code = "revision_conflict"


class NotBuilt(Conflict):
    code = "not_built"


class EnvMissing(Conflict):
    """The env gate refused a run (PLAN §8.1 runs/service.py); details = {"missing": [...]}."""

    code = "env_missing"
    http = 412


class EnvUnbound(Conflict):
    """details = {"unbound": [...]}"""

    code = "env_unbound"


class JobState(Conflict):
    code = "job_state"


class DetachedHead(Conflict):
    code = "detached_head"


class TurnInProgress(Conflict):
    code = "turn_in_progress"


class VersionMismatch(Conflict):
    code = "version_mismatch"


class DesignLocked(WyndError):
    """details = {"chat_id", "turn_id"}"""

    code = "design_locked"
    http = 423
    exit = 3


class Unavailable(WyndError):
    """Docker, a provider or a run API is unreachable."""

    code = "unavailable"
    http = 503
    exit = 3
