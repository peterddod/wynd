"""The run plan consumed by the executor, worker pool and supervisor (PLAN §3.11)."""

from typing import Literal

from wynd.spec.base import SpecModel
from wynd.spec.lockfiles import EdgesLock, ProcessLock, StepKind, StepLock
from wynd.spec.process_doc import ProcessDoc


class PlanVenv(SpecModel):
    id: str  # directory name under venv_root
    python: str | None = None  # explicit interpreter (tests, bake); None -> f"{venv_root}/{id}/bin/python"
    steps: list[str]  # step ids served; their entrypoints are pre-imported at worker start


class PlanStep(SpecModel):
    id: str  # step id (PLAN §3.1)
    kind: StepKind
    entrypoint: str  # "<module>:<Class>" relative to the package
    venv: str  # PlanVenv.id (the builder owns assignment)
    package_dir: str | None  # local: ABSOLUTE package dir; image: None (installed as wynd_steps.<module name>)
    lock: StepLock


class PlanNode(SpecModel):
    step: str | None = None  # step id, or
    process: str | None = None  # child process id (ProcessStep)


class PlanProcess(SpecModel):
    id: str
    dir: str | None  # local: absolute process dir; image: None
    definition: ProcessDoc  # NORMALISED: to-lists, max_traversals filled on cycle branches, after-else dropped
    nodes: dict[str, PlanNode]  # every key of definition.steps
    edges_lock: EdgesLock = EdgesLock()  # M5


class RunPlan(SpecModel):
    wynd: Literal[1] = 1
    mode: Literal["local", "image"]
    root: str  # root process id
    commit: str | None = None  # closure HEAD when known
    provider: str  # effective default provider of the ROOT (applies to children, SPEC §3.2)
    venv_root: str  # local: <ws>/.wynd/venvs ; image: /opt/wynd/venvs
    venvs: list[PlanVenv]
    steps: dict[str, PlanStep]
    processes: dict[str, PlanProcess]  # root + every descendant in the reference closure
    edge_venvs: dict[str, str] = {}  # M5: "<pid>:<branch_key>" -> venv id


ProcessLock.model_rebuild()
