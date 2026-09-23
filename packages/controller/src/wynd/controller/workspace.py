"""Workspace scaffolding: `init_workspace`, `new_process_files` (PLAN §8.1; `$DRAFTS/06 §5.4`).

`init_workspace` writes `wynd.yaml`, the root `.gitkeep`s, the `.gitignore` lines `.wynd/`, `.env`, `__pycache__/`,
`.pytest_cache/` and the `.gitattributes` cassette LFS line (each appended only when missing), runs `git init -b
main` outside a repository and commits exactly what it created. `new_process_files` returns the process and proto
templates, relative to the process directory.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from wynd.controller.errors import Conflict
from wynd.spec.workspace import PROCESS_FILE, PROTO_DIR, STATE_DIR, WORKSPACE_FILE

if TYPE_CHECKING:
    from wynd.controller.models import InitResult

WYND_YAML = "process_roots: [processes]\nstep_roots: {shared: shared/steps}\n"
KEEP_FILES = ("processes/.gitkeep", "shared/steps/.gitkeep")
GITIGNORE_LINES = (f"{STATE_DIR}/", ".env", "__pycache__/", ".pytest_cache/")
GITATTRIBUTES_LINES = ("**/cassettes/** filter=lfs diff=lfs merge=lfs -text",)
INIT_MESSAGE = "chore(wynd): init workspace"
FIRST_STEP = "first"


def init_workspace(path: Path, *, commit: bool = True) -> InitResult:
    from wynd.controller.git import GitLock
    from wynd.controller.models import InitResult
    from wynd.process.git import commit_only

    root = Path(path).absolute()
    if (root / WORKSPACE_FILE).exists():
        raise Conflict(f"{root} is already a wynd workspace ({WORKSPACE_FILE} exists)")
    root.mkdir(parents=True, exist_ok=True)
    created = [WORKSPACE_FILE, *KEEP_FILES]
    (root / WORKSPACE_FILE).write_text(WYND_YAML)
    for rel in KEEP_FILES:
        (root / rel).parent.mkdir(parents=True, exist_ok=True)
        (root / rel).touch()
    created += [rel for rel, lines in ((".gitignore", GITIGNORE_LINES), (".gitattributes", GITATTRIBUTES_LINES))
                if _append_missing(root / rel, lines)]
    if not _inside_git(root):
        subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True, capture_output=True)
    sha = None
    if commit:
        with GitLock(root / STATE_DIR / "locks" / "git.lock"):
            sha = commit_only(root, created, INIT_MESSAGE)
    return InitResult(root=str(root), created=created, commit=sha)


def new_process_files(pid: str, *, goal: str | None) -> dict[str, str]:
    """`{"process.yaml": …, "proto/first.yaml": …}`; `name` is the last id segment."""
    name = pid.rpartition("/")[2]
    text = goal or "TODO: describe the outcome of this process"
    process = f"""kind: process
name: {name}
goal: {json.dumps(text)}
provider: claude-code
env:
  base: debian-slim-python
entry: {FIRST_STEP}
inputs:
  text: string
outputs:
  done:
    result: string
steps:
  {FIRST_STEP}: {{ use: ./steps/{FIRST_STEP} }}
edges:
  - from: {FIRST_STEP}.done
    to: $exit.done
    with: {{ result: steps.{FIRST_STEP}.outputs.result }}
"""
    proto = f"""kind: proto_step
name: {FIRST_STEP}
instruction: |
  TODO: describe what this step does.
inputs:
  text: string
outputs:
  result: string
examples: []
env:
  deps: []
"""
    return {PROCESS_FILE: process, f"{PROTO_DIR}/{FIRST_STEP}.yaml": proto}


def _append_missing(path: Path, lines: tuple[str, ...]) -> bool:
    """Append the lines `path` lacks; True when the file changed."""
    text = path.read_text() if path.exists() else ""
    present = {line.strip() for line in text.splitlines()}
    missing = [line for line in lines if line not in present]
    if not missing:
        return False
    prefix = "" if not text or text.endswith("\n") else "\n"
    path.write_text(text + prefix + "".join(f"{line}\n" for line in missing))
    return True


def _inside_git(path: Path) -> bool:
    args = ["git", "-C", str(path), "rev-parse", "--is-inside-work-tree"]
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc.returncode == 0 and proc.stdout.strip() == "true"
