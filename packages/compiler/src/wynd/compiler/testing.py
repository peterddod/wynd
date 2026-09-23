"""Offline doubles for compiler, controller, CLI and web tests (`$DRAFTS/05 §13.5, §16.2`).

Scripts are keyed by `(kind, node, n)`: the n-th call with a given (kind, node) gets the n-th entry, so they survive
prompt wording changes and break only when the pipeline's call sequence changes. A response's special key
`module_source_file` is resolved relative to the script file.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from wynd.compiler.calls import CallKind
    from wynd.compiler.llm import CompilerLLM, LLMResult, Thinking, Tier

T = TypeVar("T")


class ScriptMiss(Exception):
    """No scripted response for this call; names exactly which one is missing."""

    def __init__(self, kind: str, node: str, n: int, prompt_head: str):
        super().__init__(f"no scripted response for call {n} of ({kind}, {node}); prompt starts: {prompt_head}")
        self.kind = kind
        self.node = node
        self.n = n
        self.prompt_head = prompt_head


class ScriptedLLM:
    """Implements CompilerLLM from a script (a YAML file path or the loaded mapping)."""

    def __init__(self, script: Path | dict):
        self.script = script

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        raise NotImplementedError("PLAN §7")


class RecordingLLM:
    """Wraps a real CompilerLLM and writes every call as a script entry to `out` (live tests refresh the offline
    scripts with it)."""

    def __init__(self, inner: CompilerLLM, out: Path):
        self.inner = inner
        self.out = out

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        raise NotImplementedError("PLAN §7")


class FakeJobContext:
    """A `wynd.process.jobs.JobContext` over a temporary git repo: a real `git worktree` under
    `<tmp>/.wynd/jobs/<id>`, with `commit` given the harness's semantics (PLAN §3.18). Stub: lands with CMP-D."""
