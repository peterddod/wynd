"""Process diagnostics codes and the process exception hierarchy (PLAN §3.2, §6.1; owner PROC-WS).

`CODES` maps the §3.2 process loader/validator codes to `(severity, message template)`; templates from
`$DRAFTS/04 §4.12` (with the §3.2 `W128` override and the new `E223`). A code with two messages lists both,
separated by `" / "`, as the draft table does. Some loader codes are never emitted because spec reports the same
problem first: `E102` (spec `E-YAML*`/`E-SCHEMA` on `wynd.yaml`), `E104`–`E106` (spec `E-ROOTS`/`E-SCHEMA`) and
`E120` (spec `E-USE`). They stay listed so the namespace matches PLAN §3.2.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic, Severity

    from .validation import ValidationReport

CODES: dict[str, tuple[Severity, str]] = {
    # loader and workspace ($DRAFTS/04 §4.12)
    "E100": ("error", "no wynd.yaml found in {start} or any parent directory"),
    "E101": ("error", "workspace {root} is not inside a git repository"),
    "E102": ("error", "wynd.yaml: {loc}: {msg}"),
    "E103": ("error", "{kind} root '{path}': {reason}"),
    "E104": ("error", "roots '{a}' and '{b}' overlap; roots must be disjoint"),
    "E105": ("error", "step root alias '{alias}' is invalid: {reason}"),
    "E106": ("error", "step root '{alias}': url roots are reserved and not supported in v1"),
    "E110": ("error", "process '{inner}' is nested inside process '{outer}'; processes are leaves and must not nest"),
    "E111": ("error", "step package '{inner}' is nested inside step package '{outer}'"),
    "E112": ("error", "process id '{id}' is defined under both '{a}' and '{b}'"),
    "E113": ("error", "process.yaml name '{name}' must equal its folder name '{folder}'"),
    "E114": ("error", "'{path}' is a process.yaml under step root '{alias}'; processes live only under process roots"),
    "E115": (
        "error",
        "step package '{path}' is outside any step root; process-local steps must be at <process>/steps/<name>",
    ),
    "E116": ("error", "'{path}': segment '{seg}' is not a valid id segment ([A-Za-z0-9_][A-Za-z0-9_-]*)"),
    "E119": ("error", "proto-step name '{name}' must equal '{expected}'"),
    "E120": (
        "error",
        "step '{alias}': use '{use}' is not valid; expected ./steps/<name>, <alias>:<path> or process:<id>{hint}",
    ),
    "E121": ("error", "step '{alias}': unknown step root alias '{root}' (configured: {aliases})"),
    "E122": (
        "error",
        "step '{alias}': '{use}' does not resolve to a step package or proto-step (looked for {pkg} and {proto})",
    ),
    "E123": ("error", "step package '{dir}' has {present} but not {missing}"),
    "E124": ("error", "step '{alias}': process '{id}' not found under any process root"),
    "E125": ("error", "process reference cycle: {cycle}"),
    "E127": ("error", "unknown process '{id}'"),
    "E128": (
        "error",
        "process id '{id}' ends in the reserved segment '{seg}' (reserved: {reserved}); rename the process folder",
    ),
    "W128": ("warning", "step '{id}': pyproject dependency '{name}' is missing from step.lock.yaml fragment.deps"),
    "W129": (
        "warning",
        "step '{id}': step.lock.yaml has no interface snapshot; field checks skipped "
        "(run wynd validate {pid} --sync-interfaces)",
    ),
    "I130": ("info", "step '{alias}': proto-step changed since compile ({old} → {new}); process is in design phase"),
    # graph validator ($DRAFTS/04 §4.3–§4.11)
    "E204": ("error", "edge from '{from}': step '{step}' has no exit '{exit}' (exits: {exits})"),
    "E208": ("error", "exit '{step}.{exit}' is not routed; add an edge or route it to $ignore"),
    "E209": ("error", "step '{step}' is unreachable from entry '{entry}'{extra}"),
    "E210": (
        "error",
        "process input(s) {extra} are not fields of entry step '{entry}' Input / "
        "entry step '{entry}' requires input(s) {missing} that process.inputs does not provide",
    ),
    "E211": ("error", "'$exit.{exit}' with: must bind exactly {declared}; missing {missing}, unexpected {extra}"),
    "E212": (
        "error",
        "branch to '{target}': with: binds unknown input(s) {extra} / "
        "branch to '{target}': with: does not bind required input(s) {missing}",
    ),
    "E215": ("error", "finally step '{name}' requires input(s) {missing} that process.inputs does not provide"),
    "E219": ("error", "on_error step '{name}' exit '{exit}' is not a declared process output exit"),
    "E223": ("error", "on_error step '{step}' requires input(s) {missing} that ProcessError does not provide"),
    "I201": ("info", "branch '{from}' → '{target}' lies on a cycle; max_traversals defaulted to {n}"),
    "W202": (
        "warning",
        "step '{alias}': interface inferred from examples (no declared schemas); field checks are advisory until "
        "compile",
    ),
    "W203": (
        "warning",
        "process step '{alias}' (process:{child}) declares {field} '{child_value}' but the parent's "
        "'{parent_value}' applies",
    ),
    "W204": (
        "warning",
        "process step '{alias}' (process:{child}) declares {field} '{child_value}' but the parent's "
        "'{parent_value}' applies",
    ),
    "W205": (
        "warning",
        "process step '{alias}' (process:{child}) declares {field} '{child_value}' but the parent's "
        "'{parent_value}' applies",
    ),
    "W206": (
        "warning",
        "process is latency: fast but step '{id}' uses AgentProvider '{provider}' (per-call harness startup)",
    ),
    "W207": ("warning", "provider '{provider}' is not installed (entry point group wynd.providers)"),
}


class WyndProcessError(Exception):
    """Base class of every error raised by `wynd.process`."""


class LoadError(WyndProcessError):
    """A document needed to build the process model could not be loaded (YAML or schema errors, no workspace)."""

    def __init__(self, diagnostics: Iterable[Diagnostic]) -> None:
        self.diagnostics = list(diagnostics)
        super().__init__("\n".join(d.format() for d in self.diagnostics))


class ProcessNotFound(WyndProcessError):
    """No process with the requested id exists in the workspace (`E127`)."""

    def __init__(self, process_id: str) -> None:
        self.process = process_id
        super().__init__(CODES["E127"][1].format(id=process_id))


class DesignPhase(WyndProcessError):
    """Closure steps are still in the design phase (proto-only or stale), so the process cannot be planned or built."""

    def __init__(self, step_ids: Sequence[str]) -> None:
        self.step_ids = list(step_ids)
        super().__init__(f"process is in design phase: steps {', '.join(self.step_ids)} are not compiled")


class ValidationFailed(WyndProcessError):
    """Validation produced at least one error; `report` holds every diagnostic."""

    def __init__(self, report: ValidationReport) -> None:
        self.report = report
        super().__init__(f"process '{report.process}' failed validation")


class ToolMissing(WyndProcessError):
    """A required external executable (`git`, `uv`, `docker`) is not installed."""

    def __init__(self, tool: str, hint: str = "") -> None:
        self.tool = tool
        message = f"'{tool}' is required but was not found"
        super().__init__(f"{message}: {hint}" if hint else message)


class GitError(WyndProcessError):
    """A git command failed."""


class ResolutionError(WyndProcessError):
    """Dependency resolution (`uv pip compile`) failed."""
