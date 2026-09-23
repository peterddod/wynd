"""Closure iteration, the skip rule and the per-node pipeline (`$DRAFTS/05 §6.3, §7.1–§7.2`, PLAN §7).

Children compile first (post-order over the closure), then the target. A node is re-touched only when its proto hash
changed; hand-written packages whose lock carries the current proto hash are never touched.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.compiler.llm import CompilerLLM
    from wynd.compiler.session import CompileSession, SessionData, SessionState
    from wynd.compiler.split import RemoveSplit, SplitPlan
    from wynd.process.loader import LoadedProcess, ResolvedStep
    from wynd.process.testing import SuiteResult
    from wynd.runtime.mcp.entry import McpServerEntry
    from wynd.runtime.mcp.snapshot import McpToolSpec
    from wynd.runtime.storage.base import Registry


@dataclass
class CompileDeps:
    """Every boundary of the pipeline, injected so tests can fake it (PLAN §7 item 6)."""
    llm: CompilerLLM
    registry: Registry
    step_python: Callable[[Sequence[str]], Path]              # wynd.process.venvs.step_python
    lock_requirements: Callable[[Sequence[str]], list[str]]   # UvResolver().compile(..., universal=True, python_version="3.12") minus wynd-spec/runtime
    run_step_suite: Callable[..., SuiteResult]                # wynd.process.testing.run_step_suite
    run_process_examples: Callable[..., SuiteResult]          # wynd.process.testing.run_process_examples
    describe: Callable[[Path, Path, str], dict]               # (python, pkg_dir, entrypoint) -> `python -m wynd.runtime.describe` JSON
    list_mcp_tools: Callable[[McpServerEntry], list[McpToolSpec]]   # wynd.runtime.mcp.list_tools
    now: Callable[[], datetime]


@dataclass
class CompileEnv:
    """Everything `CompileSession.next` needs besides the session data."""
    checkout: Path                               # workspace root inside the job checkout (ctx.workspace)
    worktree: Path                               # git top level (ctx.worktree)
    scratch: Path                                # ctx.scratch / "compile" (outside the checkout)
    deps: CompileDeps
    checkpoint: Callable[[SessionData], None]    # -> ctx.save_session
    commit: Callable[[str], str | None]          # -> ctx.commit(message, None)
    log: Callable[[str], None]


@dataclass
class NodeOutcome:
    status: Literal["compiled", "skipped", "awaiting", "failed"]
    reason: str
    split: SplitPlan | RemoveSplit | None = None     # process.yaml change to apply


@dataclass
class Constraints:
    """What the process graph fixes about a node's interface ($DRAFTS/05 §7.2.1)."""
    input_names: list[str]                       # keys of `with:` on every branch targeting this node
    input_types: dict[str, str]                  # entry node: process.inputs types (names must match)
    output_fields: dict[str, list[str]]          # exit -> fields used as steps.<node>.outputs.<f> in edges from <node>.<exit>
    downstream_fields: list[str]                 # fields referenced by other edges (exit unknown)
    whole_output_targets: list[str]              # "validate.fields" for `fields: steps.extract.outputs`


def compile_closure(session: CompileSession, env: CompileEnv) -> SessionState:
    raise NotImplementedError("PLAN §7")


def compile_node(session: CompileSession, env: CompileEnv, lp: LoadedProcess, rs: ResolvedStep) -> NodeOutcome:
    raise NotImplementedError("PLAN §7")
