"""`wynd.yaml`, layout constants, `use:` references and step identifiers (PLAN §3.1, §3.3; $DRAFTS/01 §6.1)."""

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from pydantic import field_validator, model_validator
from pydantic_core import PydanticCustomError

from wynd.spec.base import Alias, SpecModel
from wynd.spec.yamlio import load_model

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


def _roots_error(message: str, loc: tuple = ()) -> PydanticCustomError:
    return PydanticCustomError("E-ROOTS", "{message}", {"message": message, "loc": loc})


def _root_path_problem(path: str) -> str | None:
    if not path or path.startswith("/") or path.endswith("/") or "\\" in path:
        return "must be a relative POSIX path without a leading or trailing '/'"
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "must not contain empty, '.' or '..' segments"
    if parts[0] == STATE_DIR:
        return f"must not be {STATE_DIR} or inside it"
    return None


class StepRoot(SpecModel):
    path: str | None = None
    url: str | None = None  # reserved; any value is E-ROOTS in v1


class WorkspaceConfig(SpecModel):
    process_roots: list[str] = ["processes"]  # relative POSIX, no '..', not under .wynd
    step_roots: dict[Alias, StepRoot] = {}  # "alias: path" shorthand accepted; alias != "process"
    cassette_warn_mb: float = 5.0  # compile job warns above this per step (SPEC §6.5)

    @field_validator("step_roots", mode="before")
    @classmethod
    def _shorthand_roots(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        return {alias: {"path": root} if isinstance(root, str) else root for alias, root in value.items()}

    @model_validator(mode="after")
    def _check_roots(self) -> "WorkspaceConfig":
        roots: list[tuple[str, tuple]] = [(path, ("process_roots", i)) for i, path in enumerate(self.process_roots)]
        for alias, root in self.step_roots.items():
            if alias == "process":
                raise _roots_error("step-root alias 'process' is reserved (process:<id> is a use: form)",
                                   ("step_roots", alias))
            if root.url is not None:
                raise _roots_error("url: roots are reserved; v1 resolves local paths only",
                                   ("step_roots", alias, "url"))
            if root.path is None:
                raise _roots_error(f"step root '{alias}' needs a path", ("step_roots", alias))
            roots.append((root.path, ("step_roots", alias)))
        for path, loc in roots:
            problem = _root_path_problem(path)
            if problem:
                raise _roots_error(f"root '{path}' {problem}", loc)
        for i, (a, loc_a) in enumerate(roots):
            for b, _ in roots[i + 1:]:
                if a == b or a.startswith(b + "/") or b.startswith(a + "/"):
                    raise _roots_error(f"roots '{a}' and '{b}' overlap: roots must not be equal or nested", loc_a)
        return self

    def to_authoring(self) -> dict:
        """Plain dict for dump_yaml; step roots with only `path` are written as shorthand."""
        out: dict[str, Any] = {
            "process_roots": list(self.process_roots),
            "step_roots": {alias: root.path if root.url is None else root.model_dump(exclude_none=True)
                           for alias, root in self.step_roots.items()},
        }
        if self.cassette_warn_mb != 5.0:
            out["cassette_warn_mb"] = self.cassette_warn_mb
        return out


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
    return load_model(path, WorkspaceConfig)


_SEG = r"[A-Za-z0-9_][A-Za-z0-9_-]*"
_PATH = rf"{_SEG}(?:/{_SEG})*"
_LOCAL_USE = re.compile(rf"^\./{STEPS_DIR}/({_SEG})$")
_PROCESS_USE = re.compile(rf"^process:({_PATH})$")
_ROOT_USE = re.compile(rf"^([a-z][a-z0-9_-]*):({_PATH})$")
_USE_FORMS = "./steps/<name>, <alias>:<path>, process:<id>"


def parse_use(text: str) -> UseRef:
    """One of ./steps/<name>, <alias>:<path>, process:<id>; ValueError (E-USE) otherwise."""
    if isinstance(text, str):
        if match := _LOCAL_USE.match(text):
            return UseRef("local", match.group(1))
        if match := _PROCESS_USE.match(text):
            return UseRef("process", match.group(1))
        match = _ROOT_USE.match(text)
        if match and match.group(1) != "process":
            return UseRef("root", match.group(2), match.group(1))
    message = f"use: must be one of {_USE_FORMS} (got {text!r})"
    hint = _use_hint(text) if isinstance(text, str) else None
    raise ValueError(f"{message}; {hint}" if hint else message)


def _use_hint(text: str) -> str | None:
    if ".." in re.split(r"[/:\\]", text):
        return "relative traversal ('..') is not permitted"
    if text.startswith(("/", "~")) or re.match(r"^[A-Za-z]:[\\/]", text):
        return "absolute paths are not permitted"
    if "\\" in text:
        return "use forward slashes"
    if text.startswith("./"):
        return "process-local steps are ./steps/<name> (a single segment)"
    if text.startswith("process:"):
        return "invalid process id"
    return None


def find_workspace_root(start: Path) -> Path | None:
    """Nearest ancestor of `start` (inclusive) containing wynd.yaml; upward search only."""
    start = Path(start).absolute()
    if start.is_file():
        start = start.parent
    for directory in (start, *start.parents):
        if (directory / WORKSPACE_FILE).is_file():
            return directory
    return None


def local_step_id(process_id: str, name: str) -> str:
    """f"{process_id}#{name}"."""
    return f"{process_id}#{name}"


def root_step_id(alias: str, path: str) -> str:
    """f"{alias}:{path}"."""
    return f"{alias}:{path}"


def step_module_name(step_id: str) -> str:
    """f"{slug}_{sha256(step_id)[:10]}" — the package imports as wynd_steps.<step_module_name>."""
    name = re.split(r"[#:/]", step_id)[-1].lower()
    name = re.sub(r"\W", "_", name)
    if not name:
        name = "step"
    elif name[0].isdigit():
        name = "s_" + name
    return f"{name}_{hashlib.sha256(step_id.encode()).hexdigest()[:10]}"


def slug(text: str) -> str:
    text = text.lower().replace("/", "-")
    text = re.sub(r"[^a-z0-9_.-]", "-", text)
    text = re.sub(r"-{2,}", "-", text)
    text = text.strip("-._")
    return text or "x"
