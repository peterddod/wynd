"""Fixtures for the wynd.controller tests (owner CTL-CORE; `$DRAFTS/06 §11.1`).

- `make_workspace(subdir="", files=None)` copies `fixtures/ws_basic` into a fresh git repository under `tmp_path`
  (at `subdir` inside it), writes `files` over it, commits everything on `main`, links `<ws>/.wynd/venvs` to the
  session's `shared_venvs` and sets `UV_OFFLINE=1`; `workspace` / `subdir_workspace` are the two usual calls. The
  fixture has `processes/p1` (runnable: `upper` -> `count`, `RECORDS_DIR` from an edge), `processes/p2` (the
  step-root `shared:normalise` plus the design-phase proto `tag`), `processes/parent` (`process:p1`) and
  `shared/steps/normalise`.
- `commit(ws, message="change", files=None) -> sha` writes `files` (text, bytes, or None to delete) and commits the
  workspace directory; `write_files(ws, files)` writes without committing; `git(cwd, *args) -> stdout`.
- `make_controller(root, env=None, **ctx)` builds `Controller(ControllerContext(...))` with the real runtime file
  stores under `<root>/.wynd`, the local artefact store, a `FileDocStore`, a `FakeRunner`, a `FakeGitLock` and a
  `FakeClock`; `controller` is `make_controller(workspace)`. `env` overlays the process environment (None
  removes a variable).
- `agentic_files(pid="p4", *, provider=None, mcp=None)` returns the files of a process whose one step is a lock-only
  agentic package (optionally with its own provider and an MCP server snapshot).
- `fake_git_lock` replaces `wynd.controller.git.GitLock` (CTL-JOBS, same sub-wave) with `FakeGitLock`, which counts
  entries, for code that constructs its own lock (`init_workspace`, `Controller.open`).
"""

from __future__ import annotations

import itertools
import os
import shutil
import subprocess
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
COPY_IGNORE = shutil.ignore_patterns(".wynd", "__pycache__", ".pytest_cache", ".env")
GIT_CONFIG = {
    "user.name": "Wynd Test",
    "user.email": "test@wynd.invalid",
    "commit.gpgsign": "false",
    "tag.gpgsign": "false",
    "core.hooksPath": "/dev/null",
}


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout


def write_tree(root: Path, files: dict[str, str | bytes | None] | None) -> None:
    for rel, content in (files or {}).items():
        path = root / rel
        if content is None:
            if path.is_dir():
                shutil.rmtree(path)
            else:
                path.unlink(missing_ok=True)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(content, bytes):
            path.write_bytes(content)
        else:
            path.write_text(content)


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    run_git(repo, "init", "-q", "-b", "main")
    for key, value in GIT_CONFIG.items():
        run_git(repo, "config", key, value)


class FakeGitLock:
    """`GitLock` stand-in: a re-entrant lock that counts how often it was entered."""

    instances: list[FakeGitLock] = []

    def __init__(self, path: Path | None = None) -> None:
        self.path = path
        self.entered = 0
        self._lock = threading.RLock()
        FakeGitLock.instances.append(self)

    def __enter__(self) -> FakeGitLock:
        self._lock.acquire()
        self.entered += 1
        return self

    def __exit__(self, *exc: object) -> None:
        self._lock.release()


class FakeRunner:
    """`JobRunner` stand-in; the controller core never submits jobs."""

    name = "fake"

    def __init__(self, **kw: Any) -> None:
        self.kw = kw


class FakeClock:
    def __init__(self, start: datetime = datetime(2026, 9, 22, 12, 0, tzinfo=UTC)) -> None:
        self.now = start

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def git():
    return run_git


@pytest.fixture
def write_files():
    return write_tree


@pytest.fixture
def git_repo(tmp_path) -> Path:
    """An empty repository on `main` with one initial commit."""
    repo = tmp_path / "repo"
    init_repo(repo)
    run_git(repo, "commit", "-q", "--allow-empty", "-m", "initial")
    return repo


@pytest.fixture
def make_workspace(tmp_path, monkeypatch, shared_venvs):
    monkeypatch.setenv("UV_OFFLINE", "1")
    numbers = itertools.count(1)

    def make(subdir: str = "", files: dict[str, str | bytes | None] | None = None) -> Path:
        repo = tmp_path / f"repo{next(numbers)}"
        ws = repo / subdir if subdir else repo
        shutil.copytree(FIXTURES / "ws_basic", ws, ignore=COPY_IGNORE)
        write_tree(ws, files)
        init_repo(repo)
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "initial")
        (ws / ".wynd").mkdir(exist_ok=True)
        (ws / ".wynd" / "venvs").symlink_to(shared_venvs, target_is_directory=True)
        return ws

    return make


@pytest.fixture
def workspace(make_workspace) -> Path:
    return make_workspace()


@pytest.fixture
def subdir_workspace(make_workspace) -> Path:
    return make_workspace("examples/ws")


@pytest.fixture
def commit():
    def commit(ws: Path, message: str = "change", files: dict[str, str | bytes | None] | None = None) -> str:
        write_tree(ws, files)
        run_git(ws, "add", "-A", "--", ".")                     # `.wynd/` is git-ignored by the fixture
        run_git(ws, "commit", "-q", "--allow-empty", "-m", message)
        return run_git(ws, "rev-parse", "HEAD").strip()

    return commit


@pytest.fixture
def agentic_files():
    """`files` for a process `<pid>` whose one step `draft` is a lock-only agentic package (no code: enough for
    loading, manifests and registry snapshots), optionally with its own `provider` and an MCP server snapshot."""
    import yaml

    from wynd.spec.hashing import hash_obj
    from wynd.spec.lockfiles import McpSnapshot, McpToolSnapshot, StepLock, dump_lock

    def files(pid: str = "p4", *, provider: str | None = None, mcp: str | None = None) -> dict[str, str]:
        snapshots = []
        if mcp is not None:
            tools = [McpToolSnapshot(name="search", input_schema={"type": "object"})]
            snapshots = [McpSnapshot(server=mcp, allow=["search"], tools=tools,
                                     hash=hash_obj([t.model_dump(mode="json") for t in tools]))]
        lock = StepLock(name="draft", kind="agentic", entrypoint="draft:Draft", provider=provider, tier="cheap",
                        thinking="low", effects=["network"] if snapshots else [], mcp=snapshots)
        process = {
            "kind": "process", "name": pid, "provider": "fake", "entry": "draft",
            "inputs": {"text": "string"}, "outputs": {"done": {"summary": "string"}},
            "steps": {"draft": {"use": "./steps/draft"}},
            "edges": [{"from": "draft.done", "to": "$exit.done", "with": {"summary": "steps.draft.outputs.summary"}}],
        }
        return {
            f"processes/{pid}/process.yaml": yaml.safe_dump(process, sort_keys=False),
            f"processes/{pid}/steps/draft/pyproject.toml": (FIXTURES / "ws_basic/processes/p1/steps/upper/"
                                                            "pyproject.toml").read_text(),
            f"processes/{pid}/steps/draft/step.lock.yaml": dump_lock(lock),
        }

    return files


@pytest.fixture
def fake_git_lock(monkeypatch):
    import wynd.controller.git as git_module

    FakeGitLock.instances = []
    monkeypatch.setattr(git_module, "GitLock", FakeGitLock)
    return FakeGitLock


@pytest.fixture
def fake_clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def make_controller():
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.store import FileDocStore
    from wynd.process.artefacts import LocalArtefactStore
    from wynd.process.git import prefix
    from wynd.runtime.providers import load_provider
    from wynd.runtime.storage import stores_from_env

    def make(root: Path, env: dict[str, str | None] | None = None, **fields: Any) -> Controller:
        environ = {k: v for k, v in {**os.environ, **(env or {})}.items() if v is not None}
        state_dir = root / ".wynd"
        stores = stores_from_env(environ, data_dir=state_dir)
        ctx = ControllerContext(**{
            "root": root,
            "state_dir": state_dir,
            "subdir": prefix(root).rstrip("/"),
            "env": environ,
            "stores": stores,
            "artefacts": LocalArtefactStore(state_dir),
            "docs": FileDocStore(state_dir / "controller"),
            "runner": FakeRunner(),
            "git_lock": FakeGitLock(),
            "load_provider": lambda name: load_provider(name, registry=stores.registry),
            "clock": FakeClock(),
            **fields,
        })
        return Controller(ctx)

    return make


@pytest.fixture
def controller(workspace, make_controller):
    return make_controller(workspace)
