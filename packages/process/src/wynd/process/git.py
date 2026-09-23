"""Every git primitive: closure, process HEAD, dirt, worktrees, commits, branches, integration (PLAN §3.18, §3.19;
owner PROC-GIT).

All calls are subprocess `git -C <ws_root>` with workspace-relative pathspecs (a workspace may be a subdirectory of
its repository); every path this module returns is workspace-relative. `.wynd/` never counts as a change and is never
staged. `IntegrationResult` is the W0-complete contract; `integrate` follows `$DRAFTS/04 §13.8` with the per-commit
rule over the union closure (base, target tip, branch tip).
"""

from __future__ import annotations

import os
import posixpath
import shutil
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel

from wynd.spec.workspace import STATE_DIR, WORKSPACE_FILE

from ._proc import require_tool
from .errors import GitError, LoadError
from .workspace import CommitTree, load_workspace

if TYPE_CHECKING:
    from .jobs import JobKind
    from .workspace import Workspace

RESULT_BRANCH_PREFIX = {"compile": "wynd/compile", "test_live": "wynd/test-live", "optimise": "wynd/optimise"}
# Used for commits (and rebases) only when the repository configures no identity of its own.
FALLBACK_IDENTITY = {"user.name": "Wynd", "user.email": "wynd@localhost"}
EXCLUDE_STATE = f":(exclude){STATE_DIR}"


class IntegrationResult(BaseModel):
    mode: Literal["fast_forward", "rebased", "pr_branch", "noop"]
    branch: str
    target: str
    head: str | None = None
    skipped_commits: int = 0
    conflicts: list[str] = []
    reason: str | None = None
    at: datetime
    pr_url: str | None = None             # always None in v1 (no PR is opened; `reason` carries the gh hint)


# --- running git ----------------------------------------------------------------------------------------------------

def git(
    cwd: Path, *args: str, input: bytes | None = None, check: bool = True, env: Mapping[str, str] | None = None
) -> str:
    """`git -C <cwd> <args>` stdout. `env` is overlaid on the current environment. With `check`, a non-zero exit
    raises `GitError` carrying git's stderr."""
    proc = _run(cwd, *args, input=input, env=env)
    if check and proc.returncode != 0:
        raise _error(cwd, args, proc)
    return proc.stdout.decode("utf-8", errors="replace")


def _run(
    cwd: Path, *args: str, input: bytes | None = None, env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[bytes]:
    stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    return subprocess.run(
        [require_tool("git"), "-C", str(cwd), *args],
        capture_output=True,
        env={**os.environ, **env} if env is not None else None,
        **stdin,
    )


def _error(cwd: Path, args: Sequence[str], proc: subprocess.CompletedProcess[bytes]) -> GitError:
    detail = proc.stderr.decode("utf-8", errors="replace").strip()
    return GitError(f"git {' '.join(args)} failed in {cwd}" + (f": {detail}" if detail else ""))


def _first_line(proc: subprocess.CompletedProcess[bytes]) -> str:
    text = (proc.stderr or proc.stdout).decode("utf-8", errors="replace").strip()
    return text.splitlines()[0] if text else f"exit status {proc.returncode}"


def _identity(cwd: Path) -> list[str]:
    """`-c` options for FALLBACK_IDENTITY keys the repository's configuration does not set."""
    options: list[str] = []
    for key, value in FALLBACK_IDENTITY.items():
        if _run(cwd, "config", "--get", key).returncode != 0:
            options += ["-c", f"{key}={value}"]
    return options


def _unborn(cwd: Path) -> bool:
    return _run(cwd, "rev-parse", "--verify", "--quiet", "HEAD").returncode != 0


def _pathspecs(paths: Sequence[str]) -> list[str]:
    """`paths` as pathspecs that never reach the workspace's `.wynd/`. The exclude pathspec is added only when a path
    covers the whole workspace: next to it, git (2.50, 2.55) `add` skips a new file named by a plain pathspec."""
    specs = [p for p in paths if p.rstrip("/") != STATE_DIR and not p.startswith(f"{STATE_DIR}/")]
    if any(p.rstrip("/") == "." for p in specs):
        specs.append(EXCLUDE_STATE)
    return specs


# --- repository and refs --------------------------------------------------------------------------------------------

def toplevel(path: Path) -> Path:
    return Path(git(path, "rev-parse", "--show-toplevel").strip())


def prefix(ws_root: Path) -> str:
    """The workspace's path inside its repository: "" or e.g. "examples/invoices/" (git's --show-prefix)."""
    return git(ws_root, "rev-parse", "--show-prefix").strip()


def rev_parse(cwd: Path, ref: str) -> str:
    """Full sha of the commit `ref` names; `GitError` when it names none."""
    proc = _run(cwd, "rev-parse", "--verify", "--quiet", "--end-of-options", f"{ref}^{{commit}}")
    if proc.returncode != 0:
        raise GitError(f"'{ref}' is not a commit in the repository of {cwd}")
    return proc.stdout.decode().strip()


def current_branch(cwd: Path) -> str | None:
    """The checked-out branch; None when HEAD is detached."""
    proc = _run(cwd, "symbolic-ref", "--short", "--quiet", "HEAD")
    match proc.returncode:
        case 0:
            return proc.stdout.decode().strip()
        case 1:
            return None
    raise _error(cwd, ("symbolic-ref", "--short", "--quiet", "HEAD"), proc)


def is_ancestor(cwd: Path, a: str, b: str) -> bool:
    proc = _run(cwd, "merge-base", "--is-ancestor", a, b)
    match proc.returncode:
        case 0:
            return True
        case 1:
            return False
    raise _error(cwd, ("merge-base", "--is-ancestor", a, b), proc)


def merge_base(cwd: Path, a: str, b: str) -> str:
    return git(cwd, "merge-base", a, b).strip()


# --- closure, process HEAD, dirt, history ---------------------------------------------------------------------------

def reference_closure(ws: Workspace, pid: str) -> list[str]:
    """`wynd.yaml` + the process dir + every root-step package dir it references + each child process's closure,
    transitively; workspace-relative, sorted, ancestors only (PLAN §3.19)."""
    paths = {WORKSPACE_FILE}
    for lp in ws.load_process(pid).closure_processes().values():
        paths.add(lp.dir)
        paths.update(rs.package.dir for rs in lp.steps.values() if rs.ref_kind == "root" and rs.package is not None)
    return [p for p in sorted(paths) if not any(p.startswith(f"{q}/") for q in paths)]


def closure_head(ws_root: Path, paths: Sequence[str], ref: str = "HEAD") -> str | None:
    """The last commit at or before `ref` touching `paths`; None when none does (or HEAD has no commit yet)."""
    if not paths:
        return None
    proc = _run(ws_root, "log", "-1", "--format=%H", ref, "--", *paths)
    if proc.returncode != 0:
        if ref == "HEAD" and _unborn(ws_root):
            return None
        raise _error(ws_root, ("log", "-1", ref, "--", *paths), proc)
    return proc.stdout.decode().strip() or None


def dirty_paths(ws_root: Path, paths: Sequence[str] | None) -> list[str]:
    """Staged, unstaged and untracked-but-not-ignored changes under `paths` (None = the whole workspace directory),
    workspace-relative and sorted; `.wynd/` never counts."""
    specs = _pathspecs(["."] if paths is None else paths)
    if not specs:
        return []
    out = git(ws_root, "status", "--porcelain=v1", "-z", "--untracked-files=all", "--", *specs)
    found: set[str] = set()
    entries = iter(out.split("\0"))
    for entry in entries:
        if not entry:
            continue
        found.add(entry[3:])
        if entry[0] in "RC":                  # rename/copy: the source path follows
            found.add(next(entries))
    ws_prefix = prefix(ws_root)
    return sorted(p[len(ws_prefix):] for p in found if p.startswith(ws_prefix))


def count_touching(ws_root: Path, a: str, b: str, paths: Sequence[str]) -> int:
    """Number of commits in `a..b` touching `paths`."""
    if not paths:
        return 0
    return int(git(ws_root, "rev-list", "--count", "--full-history", f"{a}..{b}", "--", *paths).strip())


def log_paths(ws_root: Path, paths: Sequence[str], limit: int) -> list[dict[str, Any]]:
    """The newest `limit` commits touching `paths`: `{sha, short, subject, author, at}` (`at` the author date)."""
    if not paths or _unborn(ws_root):
        return []
    out = git(ws_root, "log", f"--max-count={limit}", "--format=%H%x00%h%x00%s%x00%an%x00%aI%x1e", "--", *paths)
    commits = []
    for record in out.split("\x1e"):
        if not record.strip():
            continue
        sha, short, subject, author, at = record.strip("\n").split("\0")
        commits.append({"sha": sha, "short": short, "subject": subject, "author": author,
                        "at": datetime.fromisoformat(at)})
    return commits


def commits_touching(ws_root: Path, base: str, tip: str, paths: Sequence[str]) -> list[tuple[str, str]]:
    """`(sha, subject)` of every commit in `base..tip` touching `paths`, newest first. Per commit, not a net diff: a
    change and its revert both count; side branches of merges are followed."""
    if not paths:
        return []
    out = git(ws_root, "log", "--full-history", "--format=%H%x00%s", f"{base}..{tip}", "--", *paths)
    return [tuple(line.split("\0", 1)) for line in out.splitlines() if line]


# --- worktrees ------------------------------------------------------------------------------------------------------

def add_worktree(repo: Path, path: Path, sha: str) -> None:
    """A detached worktree of `repo` at `sha` (parent directories are created)."""
    git(repo, "worktree", "add", "--quiet", "--detach", str(Path(path).absolute()), sha)


def remove_worktree(repo: Path, path: Path) -> None:
    """Remove the worktree at `path` whatever its state, and prune stale worktree records."""
    path = Path(path).absolute()
    _run(repo, "worktree", "remove", "--force", str(path))
    if path.exists():
        shutil.rmtree(path)
    git(repo, "worktree", "prune")


def worktree_for_branch(repo: Path, branch: str) -> Path | None:
    """The worktree that has `branch` checked out, if any."""
    for block in git(repo, "worktree", "list", "--porcelain").split("\n\n"):
        lines = block.splitlines()
        if f"branch refs/heads/{branch}" in lines and not any(line.startswith("prunable") for line in lines):
            return Path(next(line for line in lines if line.startswith("worktree "))[len("worktree "):])
    return None


# --- commits and branches -------------------------------------------------------------------------------------------

def commit_paths(
    checkout_ws: Path, message: str, paths: Sequence[str] | None = None, *, trailers: Mapping[str, str] = {}
) -> str | None:
    """Stage `paths` (None = the whole workspace directory) incl. deletions and commit everything staged on the
    checkout's HEAD, with `trailers` appended to the message. Identity "Wynd <wynd@localhost>" unless the repository
    configures one. Returns the new sha, or None when nothing is staged."""
    specs = _pathspecs(["."] if paths is None else _stageable(checkout_ws, paths))
    if specs:
        git(checkout_ws, "add", "-A", "--", *specs)
    if not _staged(checkout_ws):
        return None
    text = message.rstrip("\n")
    if trailers:
        text += "\n\n" + "\n".join(f"{key}: {value}" for key, value in trailers.items())
    git(checkout_ws, *_identity(checkout_ws), "commit", "--quiet", "--file=-", input=f"{text}\n".encode())
    return rev_parse(checkout_ws, "HEAD")


def commit_only(ws_root: Path, paths: Sequence[str], message: str) -> str | None:
    """Commit exactly `paths` (incl. deletions) in the main worktree, leaving any other staged change staged:
    `git add -A -- paths; git commit --only -- paths`. Returns the new sha, or None when they hold no change."""
    specs = _pathspecs(_stageable(ws_root, paths))
    if not specs:
        return None
    git(ws_root, "add", "-A", "--", *specs)
    if not _staged(ws_root, specs):
        return None
    text = message.rstrip("\n")
    git(ws_root, *_identity(ws_root), "commit", "--quiet", "--only", "--file=-", "--", *specs,
        input=f"{text}\n".encode())
    return rev_parse(ws_root, "HEAD")


def _stageable(cwd: Path, paths: Sequence[str]) -> list[str]:
    """The paths `git add` accepts: present on disk, or tracked (so their deletion can be staged)."""
    if not paths:
        return []
    tracked = [t for t in git(cwd, "ls-files", "-z", "--", *paths).split("\0") if t]
    return [
        p for p in paths
        if (cwd / p).exists() or any(t == p or t.startswith(f"{p.rstrip('/')}/") for t in tracked)
    ]


def _staged(cwd: Path, paths: Sequence[str] = ()) -> bool:
    args = ("diff", "--cached", "--quiet", "--", *paths)
    proc = _run(cwd, *args)
    if proc.returncode not in (0, 1):
        raise _error(cwd, args, proc)
    return proc.returncode == 1


def merge_ff_only(cwd: Path, rev: str) -> None:
    """Fast-forward the branch checked out at `cwd` to `rev`; `GitError` (listing any local changes that would be
    overwritten) when that is not a fast-forward or would clobber the worktree."""
    git(cwd, "merge", "--ff-only", "--quiet", rev)


def update_ref(cwd: Path, ref: str, new: str, old: str | None = None) -> None:
    """Point `ref` at `new`; with `old`, only if it still points at `old` (compare-and-swap, `GitError` otherwise)."""
    git(cwd, "update-ref", ref, new, *([old] if old is not None else []))


def result_branch(kind: JobKind, pid: str, job_id: str) -> str:
    """`wynd/<compile|test-live|optimise>/<pid>/<job id>`; build and bake never produce commits (ValueError)."""
    if kind not in RESULT_BRANCH_PREFIX:
        raise ValueError(f"{kind} jobs produce no commits and have no result branch")
    return f"{RESULT_BRANCH_PREFIX[kind]}/{pid}/{job_id}"


def set_branch(repo: Path, branch: str, sha: str, *, expected_old: str | None = None) -> None:
    update_ref(repo, f"refs/heads/{branch}", sha, expected_old)


def delete_branch(repo: Path, branch: str) -> None:
    git(repo, "branch", "--quiet", "-D", branch)


# --- integration ----------------------------------------------------------------------------------------------------

def integrate(
    ws_root: Path, *, process_id: str, base_sha: str, branch: str, target_branch: str, scratch_dir: Path
) -> IntegrationResult:
    """Bring result `branch` (forked from `base_sha`) into `target_branch` (SPEC §6.5, PLAN §3.18).

    Fast-forward when the target has not moved past the branch; otherwise rebase in a scratch worktree when no commit
    in `base..target` touches the union of the process's reference closures at base, target tip and branch tip;
    otherwise leave the branch for review (`pr_branch`). The target is only ever fast-forwarded: `merge --ff-only` in
    the worktree that has it checked out, else a compare-and-swap `update-ref`. The branch is deleted once integrated.
    """
    ws_root = Path(ws_root)
    repo = toplevel(ws_root)
    ws_prefix = prefix(ws_root)
    result = rev_parse(repo, f"refs/heads/{branch}")
    tip = rev_parse(repo, f"refs/heads/{target_branch}")
    hint = f"; review it: git push origin {branch} && gh pr create --head {branch}"

    def outcome(mode: str, **fields: Any) -> IntegrationResult:
        return IntegrationResult(mode=mode, branch=branch, target=target_branch, at=datetime.now(UTC), **fields)

    if is_ancestor(repo, result, tip):
        return outcome("noop", head=tip, reason=f"{branch} is already contained in {target_branch}")

    mode, new, skipped = "fast_forward", result, 0
    if not is_ancestor(repo, tip, result):
        at_base, at_tip, at_result = (_closure_at(ws_root, sha, process_id) for sha in (base_sha, tip, result))
        if at_tip is None:
            return outcome("pr_branch", reason=f"process {process_id} no longer exists at {target_branch}{hint}")
        closure = sorted({*(at_base or ()), *at_tip, *(at_result or ())})
        touching = commits_touching(ws_root, base_sha, tip, closure)
        if touching:
            sha, subject = touching[0]
            return outcome(
                "pr_branch",
                conflicts=_touched_paths(ws_root, base_sha, tip, closure),
                reason=f"{len(touching)} commit(s) on {target_branch} since {base_sha[:12]} touch {process_id}'s "
                       f"reference closure, e.g. {sha[:12]} {subject!r}{hint}",
            )
        work = Path(scratch_dir) / "rebase"
        if work.exists():
            remove_worktree(repo, work)
        add_worktree(repo, work, result)
        try:
            proc = _run(work, *_identity(repo), "rebase", "--quiet", "--onto", tip, base_sha)
            if proc.returncode != 0:
                unmerged = git(work, "diff", "--name-only", "--diff-filter=U", check=False).splitlines()
                _run(work, "rebase", "--abort")
                return outcome("pr_branch", conflicts=_ws_relative(unmerged, ws_prefix),
                               reason=f"rebase onto {target_branch} failed: {_first_line(proc)}{hint}")
            new = rev_parse(work, "HEAD")
        finally:
            remove_worktree(repo, work)
        set_branch(repo, branch, new, expected_old=result)
        mode, skipped = "rebased", int(git(repo, "rev-list", "--count", f"{base_sha}..{tip}").strip())

    checked_out = worktree_for_branch(repo, target_branch)
    if checked_out is None:
        proc = _run(repo, "update-ref", f"refs/heads/{target_branch}", new, tip)
        if proc.returncode != 0:
            return outcome("pr_branch", reason=f"{target_branch} moved during integration: {_first_line(proc)}{hint}")
    else:
        proc = _run(checked_out, "merge", "--ff-only", "--quiet", new)
        if proc.returncode != 0:
            overwritten = [line.strip() for line in proc.stderr.decode().splitlines() if line.startswith("\t")]
            return outcome("pr_branch", conflicts=_ws_relative(overwritten, ws_prefix),
                           reason=f"{target_branch} is checked out at {checked_out} and cannot fast-forward: "
                                  f"{_first_line(proc)}{hint}")
    delete_branch(repo, branch)
    return outcome(mode, head=new, skipped_commits=skipped)


def _closure_at(ws_root: Path, sha: str, pid: str) -> list[str] | None:
    """The reference closure of `pid` at commit `sha`; None when the process does not exist there. A document that
    does not load at `sha` still leaves what is known: `wynd.yaml` and the process dir."""
    try:
        ws = load_workspace(ws_root, CommitTree(ws_root, sha))
    except LoadError:
        return [WORKSPACE_FILE]
    if pid not in ws.processes:
        return None
    try:
        return reference_closure(ws, pid)
    except LoadError:
        return [WORKSPACE_FILE, ws.processes[pid].dir]


def _touched_paths(ws_root: Path, base: str, tip: str, paths: Sequence[str]) -> list[str]:
    out = git(ws_root, "log", "--full-history", "--format=", "--name-only", "--relative", f"{base}..{tip}", "--",
              *paths)
    return sorted({line for line in out.splitlines() if line})


def _ws_relative(repo_paths: Sequence[str], ws_prefix: str) -> list[str]:
    """Repository-relative paths made workspace-relative (`../x` for paths outside the workspace)."""
    base = ws_prefix.rstrip("/") or "."
    return sorted(posixpath.relpath(p, base) for p in repo_paths if p)
