"""Graph validation of a loaded process and its reference closure (PLAN §6.3; owner PROC-VAL).

Pass order per closure process (memoised): loader diagnostics + spec `check_process_doc`; structure; reachability;
cycles; bindings; dataflow + expression walk (then `typecheck.check_types`); child/provider rules, runtime lint and
`agentic.check_agentic_edges`; `latency.latency_warnings` when `stats` is given. Closure-wide on the root: W206.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from wynd.spec.errors import Diagnostic
from wynd.spec.process_doc import ProcessDoc

if TYPE_CHECKING:
    from wynd.runtime.providers import ProviderInfo

    from ..loader import LoadedProcess
    from ..optimise import ProcessStats
    from ..workspace import Workspace


class ValidationReport(BaseModel):
    process: str
    diagnostics: list[Diagnostic]                        # sorted (process, file, line, code)
    normalized: dict[str, ProcessDoc]                    # per closure process: to-lists, max_traversals filled, after-else dropped
    env_refs: dict[str, list[str]]                       # env var -> ["edge:<pid>:<branch_key>.<field>", …]

    @property
    def ok(self) -> bool:
        return not any(d.severity == "error" for d in self.diagnostics)


def validate(
    lp: LoadedProcess,
    *,
    providers: Callable[[str], ProviderInfo] | None = None,
    stats: ProcessStats | None = None,
) -> ValidationReport:
    raise NotImplementedError("PLAN §6.3 validate")


def validate_process(ws: Workspace, pid: str, **kw: Any) -> ValidationReport:
    raise NotImplementedError("PLAN §6.1 validate_process")


def format_diagnostic(d: Diagnostic) -> str:
    raise NotImplementedError("PLAN §6.1 format_diagnostic")
