"""`wynd.yaml`, layout constants, `use:` references and step identifiers (PLAN §3.1, §3.3; $DRAFTS/01 §6.1)."""

from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wynd.spec.base import Alias, SpecModel

WORKSPACE_FILE = "wynd.yaml"
PROCESS_FILE = "process.yaml"
PROTO_DIR = "proto"
STEPS_DIR = "steps"
STEP_PROTO_FILE = "proto.yaml"  # proto inside a step-root package dir
STEP_LOCK_FILE = "step.lock.yaml"
EDGES_LOCK_FILE = "edges.lock.yaml"
PROCESS_LOCK_FILE = "process.lock.yaml"
ENV_MANIFEST_FILE = "process.env.yaml"
STATE_DIR = ".wynd"
CASSETTES_DIR = "cassettes"

# Last process-id segments that would collide with controller HTTP routes (PLAN §3.21).
RESERVED_PROCESS_SEGMENTS = frozenset({
    "design", "compile", "build", "builds", "interface", "status", "validate", "test", "test-live", "bake",
    "optimise", "history", "env", "files",
})


class StepRoot(SpecModel):
    path: str | None = None
    url: str | None = None  # reserved; any value is E-ROOTS in v1


class WorkspaceConfig(SpecModel):
    process_roots: list[str] = ["processes"]  # relative POSIX, no '..', not under .wynd
    step_roots: dict[Alias, StepRoot] = {}  # "alias: path" shorthand accepted; alias != "process"
    cassette_warn_mb: float = 5.0  # compile job warns above this per step (SPEC §6.5)

    def to_authoring(self) -> dict:
        """Plain dict for dump_yaml; step roots with only `path` are written as shorthand."""
        raise NotImplementedError("PLAN §3.3")


@dataclass(frozen=True)
class UseRef:
    form: Literal["local", "root", "process"]
    target: str  # local: step dir name; root: path inside the root; process: process id
    alias: str | None = None  # root form only

    def __str__(self) -> str:
        match self.form:
            case "local":
                return f"./{STEPS_DIR}/{self.target}"
            case "root":
                return f"{self.alias}:{self.target}"
            case "process":
                return f"process:{self.target}"


def load_workspace_config(path: Path) -> WorkspaceConfig:
    raise NotImplementedError("PLAN §3.3")


def parse_use(text: str) -> UseRef:
    """One of ./steps/<name>, <alias>:<path>, process:<id>; ValueError (E-USE) otherwise."""
    raise NotImplementedError("PLAN §3.3")


def find_workspace_root(start: Path) -> Path | None:
    """Nearest ancestor of `start` (inclusive) containing wynd.yaml; upward search only."""
    raise NotImplementedError("PLAN §4.1")


def local_step_id(process_id: str, name: str) -> str:
    """f"{process_id}#{name}"."""
    raise NotImplementedError("PLAN §3.1")


def root_step_id(alias: str, path: str) -> str:
    """f"{alias}:{path}"."""
    raise NotImplementedError("PLAN §3.1")


def step_module_name(step_id: str) -> str:
    """f"{slug}_{sha256(step_id)[:10]}" — the package imports as wynd_steps.<step_module_name>."""
    raise NotImplementedError("PLAN §3.1")


def slug(text: str) -> str:
    raise NotImplementedError("PLAN §3.1")
