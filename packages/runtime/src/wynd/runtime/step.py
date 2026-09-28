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

_SHELL_FIELDS = ("stdout", "stderr", "returncode")


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

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        from wynd.runtime.agentic.checks import check_agentic_class

        check_agentic_class(cls)

    def run(self, input: Any) -> Any: ...


class ShellStep(Step):
    """Runs `command()` (an argv, never a shell string) and maps its exit code through `exit_codes`."""

    exit_codes: ClassVar[dict[int | str, str]] = {0: "done", "*": "error"}

    def command(self, input: Any) -> list[str]:
        raise NotImplementedError(f"{type(self).__name__} must define command(input) -> argv")

    def outputs(self, exit: str, result: ShellResult) -> BaseModel:
        """Default: the exit's model filled from its fields named stdout/stderr/returncode. Override when the model
        has other required fields (e.g. parse `result.stdout`)."""
        from wynd.runtime.interface import interface_of

        model = interface_of(type(self)).exit_model(exit)
        fields = {name: info for name, info in model.model_fields.items() if name != "exit"}
        unfilled = [name for name, info in fields.items() if name not in _SHELL_FIELDS and info.is_required()]
        if unfilled:
            raise NotImplementedError(
                f"{type(self).__name__}.outputs() must be defined: exit '{exit}' has fields "
                f"{', '.join(unfilled)} that are not stdout/stderr/returncode"
            )
        return model(**{name: getattr(result, name) for name in fields if name in _SHELL_FIELDS})

    def run(self, input: Any) -> Any:
        from wynd.runtime.shell import run_shell

        return run_shell(self, input)


class ProcessStep(Step):
    """A whole process used as a step (`use: process:<id>`); executed by the executor, never by a worker."""

    process_id: ClassVar[str]

    def run(self, input: Any) -> Any:
        raise RuntimeError("ProcessStep nodes are executed by the executor, not called")


def step_kind(cls: type[Step]) -> TraceStepKind:
    """ProcessStep > ShellStep > AgenticStep > DeterministicStep; a bare Step subclass is "deterministic"."""
    if issubclass(cls, ProcessStep):
        return "process"
    if issubclass(cls, ShellStep):
        return "shell"
    if issubclass(cls, AgenticStep):
        return "agentic"
    return "deterministic"
