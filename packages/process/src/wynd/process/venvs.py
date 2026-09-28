"""Venv groups and local step venvs (PLAN §6.1, §6.4; owner PROC-ENV; `$DRAFTS/04 §6.2–§6.4`).

Steps with an identical normalised requirement set share one venv. Local venvs live at
`<ws>/.wynd/venvs/<local_venv_id>/`: `uv venv --relocatable --python 3.12 --no-project` + `uv pip install
<runtime.install_args()> <requirements> "pytest>=8,<10"` into a temp dir, marker `wynd-venv.json` written last, atomic
rename. `RuntimeSource` is editable `packages/spec` + `packages/runtime` from this monorepo (`direct_url.json` or
`WYND_RUNTIME_SOURCE=<dir>`), else pinned `wynd-spec==V wynd-runtime==V`.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from secrets import token_hex
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse
from urllib.request import url2pathname

from wynd.spec.hashing import canonical_json, dependency_set_hash, hash_bytes
from wynd.spec.interface import Interface
from wynd.spec.lockfiles import StepLock
from wynd.spec.workspace import STEP_LOCK_FILE
from wynd.spec.yamlio import dump_yaml, parse_model

from . import _proc
from .fragments import requirement_set, step_env

if TYPE_CHECKING:
    from .fragments import MergedEnv
    from .workspace import Workspace

PYTHON = "3.12"
TOOLS = ("pytest>=8,<10",)                         # local venvs only: they run `wynd test`
MARKER = "wynd-venv.json"


@dataclass(frozen=True)
class RuntimeSource:
    version: str                                   # wynd-runtime version == wynd-base version
    editable: tuple[Path, Path] | None             # (packages/spec, packages/runtime) when running from the monorepo

    def install_args(self) -> list[str]:
        if self.editable is not None:
            spec, runtime = self.editable
            return ["-e", str(spec), "-e", str(runtime)]
        return [f"wynd-spec=={self.version}", f"wynd-runtime=={self.version}"]

    def descriptor(self) -> dict[str, Any]:
        """Hash input of local venv ids: a dependency change of spec/runtime re-keys editable venvs."""
        if self.editable is None:
            return {"version": self.version, "editable": None, "pyprojects": None}
        return {
            "version": self.version,
            "editable": [str(path) for path in self.editable],
            "pyprojects": [hash_bytes((path / "pyproject.toml").read_bytes()) for path in self.editable],
        }


def detect_runtime_source(environ: Mapping[str, str]) -> RuntimeSource:
    """`WYND_RUNTIME_SOURCE` (an existing directory = monorepo root, else a version), else how `wynd-runtime` itself is
    installed: editable (both `wynd-spec` and `wynd-runtime` from `direct_url.json`) or pinned at its version."""
    override = environ.get("WYND_RUNTIME_SOURCE", "")
    if override:
        root = Path(override)
        if root.is_dir():
            root = root.absolute()
            return RuntimeSource(metadata.version("wynd-runtime"), (root / "packages/spec", root / "packages/runtime"))
        return RuntimeSource(override, None)
    runtime = metadata.distribution("wynd-runtime")
    spec_dir, runtime_dir = _editable_dir(metadata.distribution("wynd-spec")), _editable_dir(runtime)
    if spec_dir is not None and runtime_dir is not None:
        return RuntimeSource(runtime.version, (spec_dir, runtime_dir))
    return RuntimeSource(runtime.version, None)


def _editable_dir(dist: metadata.Distribution) -> Path | None:
    text = dist.read_text("direct_url.json")
    if not text:
        return None
    info = json.loads(text)
    if not info.get("dir_info", {}).get("editable"):
        return None
    url = urlparse(info["url"])
    return Path(url2pathname(url.path)) if url.scheme == "file" else None


@dataclass(frozen=True)
class VenvGroup:
    key: str                                       # dependency_set_hash of the requirements
    requirements: tuple[str, ...]                  # the input set (not yet resolved)
    steps: tuple[str, ...]                         # sorted step ids


def group_key(requirements: Sequence[str]) -> str:
    """Venv id of a dependency set in image mode: the hex of `dependency_set_hash`, 16 characters."""
    return dependency_set_hash(requirements).removeprefix("sha256:")[:16]


def venv_groups(env: MergedEnv) -> list[VenvGroup]:
    by_set: dict[tuple[str, ...], list[str]] = {}
    for sid, step in env.steps.items():
        by_set.setdefault(step.requirements, []).append(sid)
    groups = [VenvGroup(group_key(reqs), reqs, tuple(sorted(sids))) for reqs, sids in by_set.items()]
    return sorted(groups, key=lambda group: group.key)


def local_venv_id(group: VenvGroup, runtime: RuntimeSource) -> str:
    return hash_bytes(canonical_json(_id_inputs(group, runtime))).removeprefix("sha256:")[:16]


def _id_inputs(group: VenvGroup, runtime: RuntimeSource) -> dict[str, Any]:
    return {"schema": 1, "python": PYTHON, "requirements": list(group.requirements),
            "runtime": runtime.descriptor(), "tools": list(TOOLS)}


def ensure_local_venv(
    group: VenvGroup, *, venv_root: Path, runtime: RuntimeSource, log: Callable[[str], None] | None
) -> str:
    """Create `<venv_root>/<id>` unless its marker exists; returns the id. Built in a temp dir and renamed into place,
    so a crash never leaves a half venv and a concurrent creator of the same id simply wins."""
    venv_id = local_venv_id(group, runtime)
    path = Path(venv_root) / venv_id
    if (path / MARKER).exists():
        return venv_id
    Path(venv_root).mkdir(parents=True, exist_ok=True)
    tmp = Path(venv_root) / f".tmp-{venv_id}-{os.getpid()}-{token_hex(3)}"
    uv = _proc.require_tool("uv")
    python = tmp / "bin" / "python"
    if log is not None:
        log(f"creating venv {venv_id} for {', '.join(group.requirements) or 'no dependencies'}")
    try:
        _proc.run([uv, "venv", "--relocatable", "--python", PYTHON, "--no-project", str(tmp)], cwd=venv_root, log=log)
        _proc.run([uv, "pip", "install", "--python", str(python), *runtime.install_args(), *group.requirements,
                   *TOOLS], cwd=venv_root, log=log)
        freeze = _proc.run([uv, "pip", "freeze", "--python", str(python)], cwd=venv_root)
        marker = {"id": venv_id, "inputs": _id_inputs(group, runtime), "freeze": freeze.splitlines(),
                  "created_at": datetime.now(UTC).isoformat()}
        (tmp / MARKER).write_text(json.dumps(marker, indent=2) + "\n")
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    try:
        os.rename(tmp, path)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)
        if not (path / MARKER).exists():
            raise
    return venv_id


def step_python(
    requirements: Sequence[str],
    *,
    venv_root: Path,
    runtime: RuntimeSource | None = None,
    log: Callable[[str], None] | None = None,
) -> Path:
    """Interpreter of the local venv for one requirement set (created on first use; shared with the plan's venvs)."""
    reqs = requirement_set(requirements)
    runtime = runtime or detect_runtime_source(os.environ)
    venv_id = ensure_local_venv(VenvGroup(group_key(reqs), reqs, ()), venv_root=venv_root, runtime=runtime, log=log)
    return Path(venv_root) / venv_id / "bin" / "python"


def sync_interfaces(
    ws: Workspace, pid: str, *, venv_root: Path, log: Callable[[str], None] | None = None
) -> list[str]:
    """Refresh the `interface`, `context` and (shell) `shell.exit_codes` snapshots of every closure step that has a
    lock from `python -m wynd.runtime.describe`, run in the step's venv. Only those top-level blocks of
    `step.lock.yaml` are replaced (comments and everything else survive). Returns the changed workspace-relative
    lock paths."""
    lp = ws.load_process(pid)
    runtime = detect_runtime_source(os.environ)
    changed: list[str] = []
    for sid, pkg in sorted(lp.closure_packages().items()):
        if pkg.lock is None:
            continue
        env = step_env(sid, pkg.lock, lp.doc.effective_provider)
        python = step_python(env.requirements, venv_root=venv_root, runtime=runtime, log=log)
        described = describe(python, ws.root / pkg.dir, pkg.lock.entrypoint)
        path = ws.root / pkg.dir / STEP_LOCK_FILE
        text = path.read_text()
        updated = _refresh(text, pkg.lock, described)
        if updated == text:
            continue
        rel = f"{pkg.dir}/{STEP_LOCK_FILE}"
        parse_model(updated, StepLock, rel)                          # SpecError instead of writing a broken lock
        path.write_text(updated)
        changed.append(rel)
        if log is not None:
            log(f"updated {rel}")
    return changed


def describe(python: Path, package_dir: Path, entrypoint: str) -> dict[str, Any]:
    """`PackageDescription` JSON of a step package, from its own venv; `RuntimeError` with the traceback on failure."""
    proc = subprocess.run([str(python), "-m", "wynd.runtime.describe", str(package_dir), entrypoint],
                          capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    if proc.returncode != 0:
        raise RuntimeError(f"cannot describe {package_dir} ({entrypoint}):\n{proc.stderr.strip()}")
    return json.loads(proc.stdout)


def _refresh(text: str, lock: StepLock, described: Mapping[str, Any]) -> str:
    interface = Interface.model_validate(described["interface"])
    if lock.interface != interface:
        text = _replace_block(text, "interface", interface.model_dump(mode="json"))
    if lock.kind == "agentic" and list(lock.context) != list(described.get("context") or []):
        text = _replace_block(text, "context", list(described.get("context") or []))
    codes = described.get("exit_codes")
    if lock.kind == "shell" and codes is not None:
        current = {str(code): exit for code, exit in (lock.shell.exit_codes if lock.shell else {}).items()}
        if current != codes:
            typed = {int(code) if code.isdigit() else code: exit for code, exit in codes.items()}
            text = _replace_block(text, "shell", {"exit_codes": typed})
    return text


_TOP_KEY = re.compile(r"^(?P<key>[A-Za-z_][A-Za-z0-9_]*)\s*:")


def _replace_block(text: str, key: str, value: Any) -> str:
    """Replace the top-level `key:` block (its line and every following indented, blank or comment line up to the
    next top-level line) with freshly dumped YAML; append it when the key is absent."""
    lines = text.splitlines(keepends=True)
    block = dump_yaml({key: value})
    start = next((i for i, line in enumerate(lines) if (m := _TOP_KEY.match(line)) and m["key"] == key), None)
    if start is None:
        prefix = text if not text or text.endswith("\n") else text + "\n"
        return prefix + block
    end = start + 1
    while end < len(lines) and (not lines[end].strip() or lines[end][0] in " \t"):
        end += 1
    while end > start + 1 and not lines[end - 1].strip():           # keep blank lines before the next key
        end -= 1
    return "".join(lines[:start]) + block + "".join(lines[end:])
