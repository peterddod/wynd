"""Step base classes: what step authors and the compiler write against (SPEC §3.1–§3.2, PLAN §5.2).

Kind is found with `issubclass` (`step_kind`); there is no `kind` attribute.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from types import UnionType
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

if TYPE_CHECKING:
    from wynd.runtime.handle import RuntimeHandle
    from wynd.runtime.mcp import McpServer
    from wynd.runtime.shell import ShellResult
    from wynd.spec.lockfiles import TraceStepKind


class Step(ABC):
    Input: ClassVar[type[BaseModel]]
    Output: ClassVar[type[BaseModel] | UnionType]   # one model per exit, each `exit: Literal["<name>"] = "<name>"`;
                                                    # a plain model is allowed only for the "done" exit
    runtime: RuntimeHandle                          # set by middleware on each fresh instance before pre()

    def pre(self) -> None:
        return None

    @abstractmethod
    def run(self, input: Any) -> Any: ...

    def post(self) -> None:
        return None


class DeterministicStep(Step):
    """A pure function of its inputs; no model calls."""


class AgenticStep(Step):
    """The class docstring is the instruction; `run` is completed by the agentic loop and never called."""

    context: ClassVar[list[str]] = []
    tools: ClassVar[list[Callable[..., Any]]] = []
    mcp: ClassVar[list[McpServer]] = []

    def run(self, input: Any) -> Any: ...


class ShellStep(Step):
    """Runs `command()` (an argv, never a shell string) and maps its exit code through `exit_codes`."""

    exit_codes: ClassVar[dict[int | str, str]] = {0: "done", "*": "error"}

    def command(self, input: Any) -> list[str]:
        raise NotImplementedError

    def outputs(self, exit: str, result: ShellResult) -> BaseModel:
        raise NotImplementedError("PLAN §5.2")

    def run(self, input: Any) -> Any:
        from wynd.runtime.shell import run_shell

        return run_shell(self, input)


class ProcessStep(Step):
    """A whole process used as a step (`use: process:<id>`); executed by the executor, never by a worker."""

    process_id: ClassVar[str]

    def run(self, input: Any) -> Any:
        raise NotImplementedError("PLAN §5.2")


def step_kind(cls: type[Step]) -> TraceStepKind:
    raise NotImplementedError("PLAN §5.2")
