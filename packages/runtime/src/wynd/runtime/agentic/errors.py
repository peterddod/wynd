"""Exceptions of the agentic loop, tools and MCP (PLAN §3.9, §5.1). Cause mapping in `complete()`:
`ToolFailure` -> `tool`, `OutputValidationFailed` -> `output_validation`, `AgentLoopError` -> `model`,
`McpSnapshotMismatch`/`McpConfigError`/`MissingEnvVar` -> `config`, `ProviderError` by kind.

`ProviderError` is defined in `wynd.runtime.providers.types` and only re-exported here.
"""

from __future__ import annotations

from wynd.runtime.providers.types import ProviderError

__all__ = [
    "AgentLoopError",
    "McpConfigError",
    "McpSnapshotMismatch",
    "MissingEnvVar",
    "OutputValidationFailed",
    "ProviderError",
    "ToolFailure",
    "ToolInputError",
]


class ToolFailure(Exception):
    """A tool raised after its retries; the step resolves with cause `tool` (a harness is aborted first)."""


class ToolInputError(Exception):
    """Raised by a tool for bad arguments (incl. a `shell` argv[0] outside its allowlist). The message goes back to
    the model as an error result and the loop continues; it is never a tool failure."""


class OutputValidationFailed(Exception):
    """Structured output still invalid, missing or truncated after the validation retries."""


class AgentLoopError(Exception):
    """The runtime-owned loop exceeded `max_turns`."""


class McpSnapshotMismatch(Exception):
    """A live MCP server no longer matches the snapshot in step.lock.yaml."""


class McpConfigError(Exception):
    """An MCP server cannot be configured at run time (e.g. a referenced env var is unset)."""


class MissingEnvVar(Exception):
    """An env var read through `env()` / `self.runtime.env()` is unset and has no default."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} is not set; it is listed in process.env.yaml — run `wynd env check`")
        self.name = name
