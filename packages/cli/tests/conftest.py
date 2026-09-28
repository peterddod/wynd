"""Fixtures for the `wynd` CLI tests (owner CLI-M1; PLAN §9, §14 CLI-M1).

- `workspace`: a git copy (branch `main`, one commit) of the controller's `ws_basic` fixture workspace under
  `tmp_path`, plus `EXTRA_FILES` (process `p3`: design phase, path-typed inputs); `.wynd/venvs` links to the
  session's `shared_venvs` and `UV_OFFLINE=1`. Processes: `p1` (runnable: `upper` -> `count`, which writes to
  `RECORDS_DIR`; exits `done`/`empty`), `p2` (design phase), `parent` (`process:p1`), `p3`.
- `make_controller(root, env=None, handlers=None)`: a real `Controller` over the real file stores, the local
  artefact store and the doc store under `<root>/.wynd`, with an `InProcessJobRunner` whose handler table is
  `handlers` (default: the controller's `DEFAULT_HANDLERS`). `env` overlays the process environment (None removes a
  variable). `job_handlers` is a table of fake job handlers (`support.ctl_jobs_handlers`).
- `use_controller(ctl)`: `wynd.cli.context.get_controller` returns `ctl` for the rest of the test.
- `cli(*args, input=None, code=0) -> Result`: run the `wynd` app with typer's `CliRunner` (stdout and stderr are
  separate); asserts the exit code unless `code` is None.
- `git(cwd, *args) -> stdout`, `commit(ws, message, files) -> sha`, `write_files(ws, files)`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

WS_BASIC = Path(__file__).resolve().parents[2] / "controller" / "tests" / "fixtures" / "ws_basic"
COPY_IGNORE = shutil.ignore_patterns(".wynd", "__pycache__", ".pytest_cache", ".env")
GIT_CONFIG = {"user.name": "Wynd Test", "user.email": "test@wynd.invalid", "commit.gpgsign": "false",
              "core.hooksPath": "/dev/null"}
H = "support.ctl_jobs_handlers"
JOB_HANDLERS = {
    "compile": f"{H}:ask_then_finish",
    "test_live": f"{H}:commit_file",
    "build": f"{H}:succeed",
    "bake": f"{H}:succeed",
    "optimise": f"{H}:failed_outcome",
}
P3_YAML = """kind: process
name: p3
goal: Read a document.
provider: fake
entry: read
inputs:
  doc: path
  attachments: list[path]
  cover: path?
  pages: integer
outputs:
  done:
    text: string
steps:
  read: { use: ./steps/read }
edges:
  - from: read.done
    to: $exit.done
    with: { text: steps.read.outputs.text }
"""
P3_PROTO = """kind: proto_step
name: read
instruction: Read the document.
inputs:
  doc: path
  attachments: list[path]
  cover: path?
  pages: integer
outputs:
  text: string
"""
EXTRA_FILES = {"processes/p3/process.yaml": P3_YAML, "processes/p3/proto/read.yaml": P3_PROTO}


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def write_tree(root: Path, files: dict[str, str | None]) -> None:
    for rel, content in files.items():
        path = root / rel
        if content is None:
            path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)


@pytest.fixture
def git():
    return run_git


@pytest.fixture
def write_files():
    return write_tree


@pytest.fixture
def commit():
    def commit(ws: Path, message: str = "change", files: dict[str, str | None] | None = None) -> str:
        write_tree(ws, files or {})
        run_git(ws, "add", "-A", "--", ".")
        run_git(ws, "commit", "-q", "--allow-empty", "-m", message)
        return run_git(ws, "rev-parse", "HEAD").strip()

    return commit


@pytest.fixture
def workspace(tmp_path, monkeypatch, shared_venvs) -> Path:
    monkeypatch.setenv("UV_OFFLINE", "1")
    ws = tmp_path / "repo"
    shutil.copytree(WS_BASIC, ws, ignore=COPY_IGNORE)
    write_tree(ws, EXTRA_FILES)
    run_git(ws, "init", "-q", "-b", "main")
    for key, value in GIT_CONFIG.items():
        run_git(ws, "config", key, value)
    run_git(ws, "add", "-A")
    run_git(ws, "commit", "-q", "-m", "initial")
    (ws / ".wynd").mkdir()
    (ws / ".wynd" / "venvs").symlink_to(shared_venvs, target_is_directory=True)
    return ws


@pytest.fixture
def job_handlers() -> dict[str, str]:
    return dict(JOB_HANDLERS)


@pytest.fixture
def make_controller():
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.git import GitLock
    from wynd.controller.jobs.inprocess import InProcessJobRunner
    from wynd.controller.store import FileDocStore
    from wynd.process.artefacts import LocalArtefactStore
    from wynd.process.git import prefix
    from wynd.runtime.providers import load_provider
    from wynd.runtime.storage import stores_from_env

    def make(root: Path, env: dict[str, str | None] | None = None,
             handlers: dict[str, str] | None = None) -> Controller:
        environ = {k: v for k, v in {**os.environ, **(env or {})}.items() if v is not None}
        state_dir = root / ".wynd"
        stores = stores_from_env(environ, data_dir=state_dir)
        runner = InProcessJobRunner(env=environ, workspace_root=root, state_dir=state_dir, stores=stores,
                                    handlers=handlers)
        return Controller(ControllerContext(
            root=root,
            state_dir=state_dir,
            subdir=prefix(root).rstrip("/"),
            env=environ,
            stores=stores,
            artefacts=LocalArtefactStore(state_dir),
            docs=FileDocStore(state_dir / "controller"),
            runner=runner,
            git_lock=GitLock(state_dir / "locks" / "git.lock"),
            load_provider=lambda name: load_provider(name, registry=stores.registry),
            clock=lambda: datetime.now(UTC),
        ))

    return make


@pytest.fixture
def use_controller(monkeypatch):
    def use(ctl: Any) -> Any:
        monkeypatch.setattr("wynd.cli.context.get_controller", lambda ctx: ctl)
        return ctl

    return use


@pytest.fixture
def cli():
    from typer.testing import CliRunner

    from wynd.cli.main import app

    runner = CliRunner()

    def invoke(*args: str, input: str | None = None, code: int | None = 0):
        result = runner.invoke(app, [str(a) for a in args], input=input)
        if code is not None and result.exit_code != code:
            trace = "".join(traceback.format_exception(result.exception)) if result.exception else ""
            raise AssertionError(f"wynd {' '.join(map(str, args))} exited {result.exit_code}, expected {code}\n"
                                 f"--- stdout\n{result.stdout}\n--- stderr\n{result.stderr}\n{trace}")
        return result

    return invoke

