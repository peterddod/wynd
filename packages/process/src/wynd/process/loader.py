"""Process loader output (PLAN §6.2; owner PROC-WS).

The dataclasses below are the W0-complete contract. `load_process` (backing `Workspace.load_process`) implements
resolution, phase and interface choice (`$DRAFTS/04 §3.6`) and reference cycles (`§3.7`).

Interface choice (first match): compiled & not stale & lock interface -> `lock`; proto declares inputs and outputs ->
`proto`; proto present -> `examples` (`precise=False`); compiled lock without interface -> `none` (`W129`). A
`process:<id>` reference uses the child's `ProcessDoc.interface()` (`process`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.spec.errors import Diagnostic
    from wynd.spec.interface import Interface
    from wynd.spec.lockfiles import StepLock
    from wynd.spec.process_doc import ProcessDoc
    from wynd.spec.proto_step import ProtoStep
    from wynd.spec.yamlio import SourceMap

    from .workspace import Workspace


@dataclass(frozen=True)
class ResolvedInterface:
    interface: Interface | None           # spec Interface (outputs include the exit const); None when unknown
    source: Literal["lock", "proto", "examples", "process", "none"]
    precise: bool                         # False for "examples" (field-level findings become warnings)


@dataclass
class StepPackage:
    id: str                               # step id
    dir: str                              # workspace-relative package dir
    proto_path: str | None
    proto: ProtoStep | None
    proto_hash: str | None
    lock: StepLock | None
    phase: Literal["compiled", "design"]
    stale: bool
    interface: ResolvedInterface


@dataclass
class ResolvedStep:                       # one entry of process.yaml `steps:`
    name: str
    use: str
    ref_kind: Literal["local", "root", "process"]
    package: StepPackage | None           # None for process refs
    child: str | None                     # child process id for process refs


@dataclass
class LoadedProcess:
    id: str                               # process id
    dir: str                              # workspace-relative process dir
    path: str                             # workspace-relative process.yaml path
    doc: ProcessDoc
    source: SourceMap
    steps: dict[str, ResolvedStep]
    children: dict[str, LoadedProcess]    # memoised per Workspace (diamonds load once)
    diagnostics: list[Diagnostic]

    def closure_processes(self) -> dict[str, LoadedProcess]:
        raise NotImplementedError("PLAN §6.2 LoadedProcess.closure_processes")

    def closure_packages(self) -> dict[str, StepPackage]:
        raise NotImplementedError("PLAN §6.2 LoadedProcess.closure_packages")


def load_process(ws: Workspace, pid: str) -> LoadedProcess:
    raise NotImplementedError("PLAN §6.2 load_process")
