"""Fixtures for the wynd.compiler tests (owner CMP-A): workspaces in temporary git repositories.

- `make_repo(source=None, *, files=None, subdir="")` copies the directory `source` (e.g. `fixtures_dir / "ws_mini"`
  or `repo_root / "examples/invoices"`) into a fresh repository under `tmp_path`, at `subdir` inside it, writes
  `files` over it (text, bytes, or None to delete), commits everything on `main`, links `<ws>/.wynd/venvs` to the
  session's `shared_venvs` and sets `UV_OFFLINE=1`. It returns the workspace root.
- `commit(ws, message="change", files=None) -> sha` writes `files` and commits the workspace directory (never
  `.wynd/`).
- `tree_state(ws) -> (status, {path: sha256})` snapshots a working tree (`git status --porcelain` and the hash of
  every file, both outside `.git`/`.wynd`), for "the user's working tree is untouched" assertions.
- `git(cwd, *args) -> stdout`; `repo_root` is this repository; `fixtures_dir` is `tests/fixtures`.
"""

import hashlib
import itertools
import shutil
import subprocess
from pathlib import Path

import pytest

FIXTURES = Path(__file__).parent / "fixtures"
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


def snapshot(ws: Path) -> tuple[str, dict[str, str]]:
    repo = Path(run_git(ws, "rev-parse", "--show-toplevel").strip())
    files = {
        str(p.relative_to(repo)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(repo.rglob("*"))
        if p.is_file() and not p.is_symlink() and not {".git", ".wynd"} & set(p.relative_to(repo).parts)
    }
    status = run_git(repo, "status", "--porcelain", "--untracked-files=all").splitlines()
    return "\n".join(line for line in status if ".wynd/" not in line), files


@pytest.fixture
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def git():
    return run_git


@pytest.fixture
def tree_state():
    return snapshot


@pytest.fixture
def make_repo(tmp_path, monkeypatch, shared_venvs):
    monkeypatch.setenv("UV_OFFLINE", "1")
    numbers = itertools.count(1)

    def make(
        source: Path | None = None,
        *,
        files: dict[str, str | bytes | None] | None = None,
        subdir: str = "",
    ) -> Path:
        repo = tmp_path / f"repo{next(numbers)}"
        ws = repo / subdir if subdir else repo
        if source is not None:
            shutil.copytree(source, ws, ignore=COPY_IGNORE)
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
