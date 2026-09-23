"""Fixtures for the wynd.process tests (owner PROC-WS): workspaces in temporary git repositories.

- `make_repo(name=None, *, source=None, files=None, subdir="")` copies `fixtures/workspaces/<name>` (or the directory
  `source`, e.g. `repo_root / "examples/invoices"`) into a fresh repository under `tmp_path`, at `subdir` inside it,
  writes `files` over it, commits everything on `main`, links `<ws>/.wynd/venvs` to the session's `shared_venvs` and
  sets `UV_OFFLINE=1`. It returns the workspace root.
- `commit(ws, message="change", files=None) -> sha` writes `files` (text, bytes, or None to delete) and commits the
  workspace directory (never `.wynd/`).
- `write_files(ws, files)` writes without committing; `git(cwd, *args) -> stdout`; `repo_root` is this repository.
"""

import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "workspaces"
REPO_ROOT = Path(__file__).resolve().parents[3]
COPY_IGNORE = shutil.ignore_patterns(".wynd", "__pycache__", ".pytest_cache", ".env")
# Local config so the user's global git settings (signing, hooks) never reach test repositories.
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


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def git():
    return run_git


@pytest.fixture
def write_files():
    return write_tree


@pytest.fixture
def make_repo(tmp_path, monkeypatch, shared_venvs):
    monkeypatch.setenv("UV_OFFLINE", "1")
    numbers = itertools.count(1)

    def make(
        name: str | None = None,
        *,
        source: Path | None = None,
        files: dict[str, str | bytes | None] | None = None,
        subdir: str = "",
    ) -> Path:
        repo = tmp_path / f"repo{next(numbers)}"
        ws = repo / subdir if subdir else repo
        origin = Path(source) if source is not None else FIXTURES / name if name is not None else None
        if origin is not None:
            shutil.copytree(origin, ws, ignore=COPY_IGNORE)
        ws.mkdir(parents=True, exist_ok=True)
        write_tree(ws, files)
        run_git(repo, "init", "-q", "-b", "main")
        for key, value in GIT_CONFIG.items():
            run_git(repo, "config", key, value)
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "--allow-empty", "-m", "initial")
        (ws / ".wynd").mkdir(exist_ok=True)
        (ws / ".wynd" / "venvs").symlink_to(shared_venvs, target_is_directory=True)
        return ws

    return make


@pytest.fixture
def commit():
    def commit(ws: Path, message: str = "change", files: dict[str, str | bytes | None] | None = None) -> str:
        write_tree(ws, files)
        run_git(ws, "add", "-A", "--", ".", ":(exclude).wynd")
        run_git(ws, "commit", "-q", "--allow-empty", "-m", message)
        return run_git(ws, "rev-parse", "HEAD").strip()

    return commit
