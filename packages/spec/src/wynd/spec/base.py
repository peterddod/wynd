"""Model base class, identifier types and shared constants (PLAN §3.1, §3.3, §4.1; $DRAFTS/01 §4.1)."""

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, PrivateAttr, StringConstraints


class SpecModel(BaseModel):
    """Base of every document model: unknown keys are errors; YAML keys that are Python keywords use aliases."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    # The yamlio.SourceMap of the loaded document; set by `load_model`, None for in-code instances. There is no public
    # `source` property ($DRAFTS/01 §4.1): it would shadow the `source` fields of ToolSnapshot and FragmentRecord.
    _source: Any = PrivateAttr(default=None)


Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
StepKey = Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]*$")]
ExitName = Annotated[str, StringConstraints(pattern=r"^[a-z_][a-z0-9_]*$")]
FieldName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
EnvName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
Alias = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]*$")]
ProcessId = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_-]*(/[A-Za-z0-9_][A-Za-z0-9_-]*)*$")]
BranchName = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")]
HashStr = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]

DEFAULT_PROVIDER = "claude-code"
DEFAULT_TIER = "cheap"
DEFAULT_THINKING = "low"
DEFAULT_MAX_TRAVERSALS = 10
TIERS = ("cheap", "standard", "strong")
RESERVED_EXIT = "error"
EXIT_TARGET_PREFIX = "$exit."
IGNORE_TARGET = "$ignore"
EDGE_KINDS = ("deterministic", "agentic")
PROTO_TYPES = ("string", "number", "integer", "boolean", "date", "datetime", "path", "object")
BASES = ("debian-slim-python", "alpine-python")
LATENCIES = ("fast", "normal")
LIMIT_FIELDS = ("max_traversals", "timeout", "retries")
