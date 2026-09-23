"""Worktrees, refs and integration (PLAN §3.18 "Integration", SPEC §6.5; `$DRAFTS/04 §18.6` cases 1–7, in temporary
repositories). The user's checkout is only ever fast-forwarded; the per-commit rule decides between rebase and review.
"""

import pytest
from support.proc_git_harness import WORKSPACE, make_branch, process_yaml, proto_yaml

from wynd.process import git as gitmod
from wynd.process.errors import GitError
from wynd.process.git import (
    add_worktree,
    current_branch,
    delete_branch,
    dirty_paths,
    integrate,
    is_ancestor,
    merge_ff_only,
    remove_worktree,
    rev_parse,
    set_branch,
    update_ref,
    worktree_for_branch,
)

BRANCH = "wynd/compile/beta/job_20260923T100000000_abcdef"
OWN = "processes/beta/proto/own.yaml"
UTIL = "shared/steps/util/proto.yaml"
COMPILED = {OWN: proto_yaml("own", "Compiled edit."), "processes/beta/steps/own/own.py": "print('own')\n"}


def integrate_beta(ws, tmp_path, base, target="main"):
    return integrate(ws, process_id="beta", base_sha=base, branch=BRANCH, target_branch=target,
                     scratch_dir=tmp_path / "scratch")


def branch_exists(git, ws, branch=BRANCH):
    return git(ws, "branch", "--list", branch).strip() != ""


def worktrees(git, ws):
    return [line for line in git(ws, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]


def test_worktrees(make_repo, commit, git, tmp_path):
    ws = make_repo(files=WORKSPACE)
    first = rev_parse(ws, "HEAD")
    commit(ws, "second", {"README.md": "2\n"})
    wt = tmp_path / "jobs" / "job_1" / "checkout-1"
    add_worktree(ws, wt, first)
    assert rev_parse(wt, "HEAD") == first and current_branch(wt) is None
    assert (wt / "README.md").read_text() == WORKSPACE["README.md"]
    assert worktree_for_branch(ws, "main").resolve() == ws.resolve()
    assert worktree_for_branch(wt, "main").resolve() == ws.resolve()
    assert worktree_for_branch(ws, "nope") is None
    (wt / "README.md").write_text("modified in the checkout\n")
    (wt / "untracked.txt").write_text("x\n")
    remove_worktree(ws, wt)
    assert not wt.exists() and len(worktrees(git, ws)) == 1
    remove_worktree(ws, wt)                                  # already gone: nothing to do


def test_refs_compare_and_swap_and_fast_forward(make_repo, commit, git):
    ws = make_repo(files=WORKSPACE)
    first = rev_parse(ws, "HEAD")
    second = commit(ws, "second", {"README.md": "2\n"})
    set_branch(ws, "b", first)
    assert rev_parse(ws, "b") == first
    with pytest.raises(GitError):
        set_branch(ws, "b", second, expected_old=second)
    set_branch(ws, "b", second, expected_old=first)
    assert rev_parse(ws, "b") == second
    update_ref(ws, "refs/heads/c", first)
    with pytest.raises(GitError):
        update_ref(ws, "refs/heads/c", second, first[::-1])
    delete_branch(ws, "b")
    assert not branch_exists(git, ws, "b")
    ahead = make_branch(ws, "ahead", {"README.md": "3\n"})
    merge_ff_only(ws, "ahead")
    assert rev_parse(ws, "main") == ahead and (ws / "README.md").read_text() == "3\n"
    make_branch(ws, "diverged", {"README.md": "other\n"}, start=first)
    with pytest.raises(GitError, match="merge --ff-only"):
        merge_ff_only(ws, "diverged")


def test_1_fast_forward_updates_the_checked_out_target_and_deletes_the_branch(make_repo, git, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, COMPILED)
    (ws / "README.md").write_text("unrelated local edit\n")                  # does not block a fast-forward
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.head, r.branch, r.target) == ("fast_forward", result, BRANCH, "main")
    assert (r.skipped_commits, r.conflicts, r.reason, r.pr_url) == (0, [], None, None)
    assert rev_parse(ws, "main") == result
    assert (ws / OWN).read_text() == COMPILED[OWN]
    assert (ws / "README.md").read_text() == "unrelated local edit\n"
    assert dirty_paths(ws, None) == ["README.md"]
    assert not branch_exists(git, ws)


def test_2_moved_target_outside_the_closure_is_rebased(make_repo, commit, git, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, COMPILED, message="wynd compile beta")
    commit(ws, "alpha moves", {"processes/alpha/proto/read.yaml": proto_yaml("read", "Elsewhere.")})
    tip = commit(ws, "readme moves", {"README.md": "moved\n"})
    stale = tmp_path / "scratch" / "rebase"
    stale.mkdir(parents=True)
    (stale / "left-over.txt").write_text("from a crashed attempt\n")
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.skipped_commits, r.conflicts, r.reason) == ("rebased", 2, [], None)
    head = rev_parse(ws, "main")
    assert r.head == head != result
    assert rev_parse(ws, "main~1") == tip and is_ancestor(ws, tip, head)
    assert git(ws, "log", "--format=%s", "main").split("\n")[:4] == [
        "wynd compile beta", "readme moves", "alpha moves", "initial",
    ]
    assert (ws / OWN).read_text() == COMPILED[OWN] and (ws / "README.md").read_text() == "moved\n"
    assert dirty_paths(ws, None) == []
    assert not branch_exists(git, ws) and not stale.exists() and len(worktrees(git, ws)) == 1


@pytest.mark.parametrize("path, content", [
    ("wynd.yaml", WORKSPACE["wynd.yaml"] + "cassette_warn_mb: 7\n"),         # via wynd.yaml
    ("processes/team/gamma/proto/calc.yaml", proto_yaml("calc", "Child.")),  # via a child process dir
    (UTIL, proto_yaml("util", "Root step of the child.")),                   # via a root step, transitively
    (OWN, proto_yaml("own", "Same file.")),                                  # the process dir itself
])
def test_3_moved_target_touching_the_closure_is_left_for_review(make_repo, commit, git, tmp_path, path, content):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, COMPILED)
    touch = commit(ws, "touches the closure", {path: content})
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.head, r.conflicts) == ("pr_branch", None, [path])
    assert r.reason.startswith(f"1 commit(s) on main since {base[:12]} touch beta's reference closure, e.g. "
                               f"{touch[:12]} 'touches the closure'")
    assert r.reason.endswith(f"git push origin {BRANCH} && gh pr create --head {BRANCH}")
    assert rev_parse(ws, BRANCH) == result and rev_parse(ws, "main") == touch


def test_3_the_closure_is_the_union_over_base_tip_and_branch(make_repo, commit, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    make_branch(ws, BRANCH, {"processes/beta/process.yaml": process_yaml(
        "beta", {"own": "./steps/own", "fetch": "shared:net/fetch", "sub": "process:team/gamma",
                 "extra": "shared:unused"})})                                  # the branch adds a reference
    commit(ws, "unused root step", {"shared/steps/unused/proto.yaml": proto_yaml("unused", "Now used.")})
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.conflicts) == ("pr_branch", ["shared/steps/unused/proto.yaml"])


def test_4_conflicting_local_changes_leave_the_branch_and_the_users_files(make_repo, git, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, COMPILED)
    (ws / OWN).write_text("an unsaved edit\n")
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.head, r.conflicts) == ("pr_branch", None, [OWN])
    assert f"main is checked out at {ws.resolve()} and cannot fast-forward: error: Your local changes" in r.reason
    assert (ws / OWN).read_text() == "an unsaved edit\n"
    assert rev_parse(ws, "main") == base and rev_parse(ws, BRANCH) == result


def test_5_target_checked_out_nowhere_moves_by_compare_and_swap(make_repo, git, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, COMPILED)
    git(ws, "checkout", "-q", "-b", "elsewhere")
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.head) == ("fast_forward", result)
    assert rev_parse(ws, "main") == result and current_branch(ws) == "elsewhere"
    assert (ws / OWN).read_text() == WORKSPACE[OWN]                          # the checkout is untouched
    assert not branch_exists(git, ws)


def test_5_a_compare_and_swap_race_leaves_the_branch_for_review(make_repo, git, tmp_path, monkeypatch):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, COMPILED)
    git(ws, "checkout", "-q", "-b", "elsewhere")
    real = gitmod.worktree_for_branch
    raced = []

    def racing(repo, branch):                                                # someone moves main meanwhile
        raced.append(make_branch(ws, "racer", {"README.md": "raced\n"}, start=base))
        update_ref(ws, "refs/heads/main", raced[0])
        return real(repo, branch)

    monkeypatch.setattr(gitmod, "worktree_for_branch", racing)
    r = integrate_beta(ws, tmp_path, base)
    assert r.mode == "pr_branch" and r.reason.startswith("main moved during integration: ")
    assert rev_parse(ws, "main") == raced[0] and rev_parse(ws, BRANCH) == result


def test_6_an_integrated_branch_is_a_noop(make_repo, git, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, COMPILED)
    merge_ff_only(ws, BRANCH)
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.head, r.conflicts) == ("noop", result, [])
    assert rev_parse(ws, "main") == result


def test_7_a_change_and_its_revert_still_need_review(make_repo, commit, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, COMPILED)
    commit(ws, "touch util", {UTIL: proto_yaml("util", "Briefly different.")})
    commit(ws, "revert util", {UTIL: WORKSPACE[UTIL]})
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.conflicts) == ("pr_branch", [UTIL])
    assert r.reason.startswith("2 commit(s) on main")
    assert rev_parse(ws, BRANCH) == result


def test_a_rebase_conflict_outside_the_closure_is_aborted_and_left_for_review(make_repo, commit, git, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    result = make_branch(ws, BRANCH, {**COMPILED, "README.md": "the branch's readme\n"})
    tip = commit(ws, "readme", {"README.md": "main's readme\n"})
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.conflicts) == ("pr_branch", ["README.md"])
    assert r.reason.startswith("rebase onto main failed: ")
    assert rev_parse(ws, BRANCH) == result and rev_parse(ws, "main") == tip
    assert len(worktrees(git, ws)) == 1 and not (tmp_path / "scratch" / "rebase").exists()


def test_a_process_removed_at_the_target_is_left_for_review(make_repo, commit, tmp_path):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    make_branch(ws, BRANCH, COMPILED)
    commit(ws, "remove beta", {"processes/beta": None})
    r = integrate_beta(ws, tmp_path, base)
    assert r.mode == "pr_branch" and r.reason.startswith("process beta no longer exists at main")


def test_subdirectory_workspace_rebases_over_commits_outside_it(make_repo, commit, git, tmp_path):
    ws = make_repo(files=WORKSPACE, subdir="examples/ws")
    repo = ws.parents[1]
    base = rev_parse(ws, "HEAD")
    make_branch(ws, BRANCH, COMPILED)
    (repo / "ROOT.md").write_text("outside the workspace\n")
    git(repo, "add", "ROOT.md")
    git(repo, "commit", "-q", "-m", "root file")
    commit(ws, "alpha", {"processes/alpha/proto/read.yaml": proto_yaml("read", "Elsewhere.")})
    r = integrate_beta(ws, tmp_path, base)
    assert (r.mode, r.skipped_commits) == ("rebased", 2)
    assert (ws / OWN).read_text() == COMPILED[OWN] and (repo / "ROOT.md").is_file()
    moved = rev_parse(ws, "main")
    make_branch(ws, BRANCH, {OWN: proto_yaml("own", "Second compile.")})
    commit(ws, "touch", {OWN: proto_yaml("own", "Hand edit.")})
    again = integrate_beta(ws, tmp_path, moved)
    assert (again.mode, again.conflicts) == ("pr_branch", [OWN])              # workspace-relative
