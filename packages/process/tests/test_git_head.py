"""Process HEAD and history queries (PLAN §3.19, SPEC §6.5 "HEAD of the process"): refs, `closure_head`,
`count_touching`, `commits_touching` (per commit, following merges) and `log_paths`."""

import subprocess
from datetime import datetime

import pytest
from support.proc_git_harness import CLOSURES, WORKSPACE, process_yaml, proto_yaml

from wynd.process import git as gitmod
from wynd.process.errors import GitError
from wynd.process.git import (
    closure_head,
    commits_touching,
    count_touching,
    current_branch,
    is_ancestor,
    log_paths,
    merge_base,
    prefix,
    reference_closure,
    rev_parse,
    toplevel,
)
from wynd.process.workspace import CommitTree, load_workspace

BETA = CLOSURES["beta"]


def edited(name: str, n: int) -> str:
    return proto_yaml(name, f"Edit number {n}.")


def process_head(ws, pid, ref="HEAD"):
    """"HEAD of the process" at `ref`: the closure computed at that commit, then its last touching commit."""
    return closure_head(ws, reference_closure(load_workspace(ws, CommitTree(ws, ref)), pid), ref)


def test_refs_and_repository_paths(make_repo, commit, git):
    ws = make_repo(files=WORKSPACE, subdir="examples/ws")
    repo = ws.parents[1]
    first = git(repo, "rev-parse", "HEAD").strip()
    second = commit(ws, "second", {"README.md": "more\n"})
    assert toplevel(ws) == repo.resolve()
    assert prefix(ws) == "examples/ws/"
    assert prefix(repo) == ""
    assert rev_parse(ws, "HEAD") == rev_parse(ws, "main") == rev_parse(ws, second[:10]) == second
    assert rev_parse(ws, "HEAD~1") == first
    with pytest.raises(GitError, match="'nope' is not a commit"):
        rev_parse(ws, "nope")
    assert current_branch(ws) == "main"
    assert is_ancestor(ws, first, second) and not is_ancestor(ws, second, first)
    git(repo, "checkout", "-q", "-b", "side", first)
    third = commit(ws, "side", {"README.md": "side\n"})
    assert merge_base(ws, second, third) == first
    git(repo, "checkout", "-q", "--detach", "HEAD")
    assert current_branch(ws) is None


def test_closure_head_moves_only_when_the_closure_changes(make_repo, commit):
    ws = make_repo(files=WORKSPACE)
    initial = rev_parse(ws, "HEAD")
    assert closure_head(ws, BETA) == initial
    commit(ws, "another process", {"processes/alpha/proto/read.yaml": edited("read", 1)})
    commit(ws, "an unreferenced root step", {"shared/steps/unused/proto.yaml": edited("unused", 1)})
    commit(ws, "outside every closure", {"README.md": "changed\n"})
    assert closure_head(ws, BETA) == initial
    via_root_step = commit(ws, "a root step the child references", {"shared/steps/util/proto.yaml": edited("util", 1)})
    assert closure_head(ws, BETA) == via_root_step
    via_child = commit(ws, "the child process", {"processes/team/gamma/proto/calc.yaml": edited("calc", 1)})
    assert closure_head(ws, BETA) == via_child
    via_config = commit(ws, "wynd.yaml", {"wynd.yaml": WORKSPACE["wynd.yaml"] + "cassette_warn_mb: 6\n"})
    assert closure_head(ws, BETA) == via_config
    assert closure_head(ws, BETA, ref=via_child) == via_child
    assert closure_head(ws, BETA, ref=f"{via_root_step}~1") == initial
    assert closure_head(ws, CLOSURES["alpha"], ref=via_root_step) != initial


def test_process_head_uses_the_closure_at_that_commit(make_repo, commit):
    ws = make_repo(files=WORKSPACE)
    dropped = commit(ws, "beta drops its child", {
        "processes/beta/process.yaml": process_yaml("beta", {"own": "./steps/own", "fetch": "shared:net/fetch"}),
    })
    commit(ws, "the former child changes", {"processes/team/gamma/proto/calc.yaml": edited("calc", 2)})
    assert process_head(ws, "beta") == dropped
    assert closure_head(ws, BETA) != dropped                    # the old closure would still see the child


def test_uncommitted_paths_have_no_head(make_repo, write_files, tmp_path, git):
    ws = make_repo(files=WORKSPACE)
    write_files(ws, {
        "processes/eps/process.yaml": process_yaml("eps", {"own": "./steps/own"}),
        "processes/eps/proto/own.yaml": proto_yaml("own"),
    })
    assert closure_head(ws, ["processes/eps"]) is None
    assert closure_head(ws, reference_closure(load_workspace(ws), "eps")) == rev_parse(ws, "HEAD")  # via wynd.yaml
    assert closure_head(ws, []) is None
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    git(fresh, "init", "-q", "-b", "main")
    (fresh / "wynd.yaml").write_text("")
    assert closure_head(fresh, ["wynd.yaml"]) is None
    assert log_paths(fresh, ["wynd.yaml"], 5) == []
    with pytest.raises(GitError):
        closure_head(ws, BETA, ref="no-such-ref")


def test_commits_touching_counts_every_commit_including_reverts(make_repo, commit):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    commit(ws, "outside", {"processes/alpha/proto/read.yaml": edited("read", 1)})
    change = commit(ws, "change util", {"shared/steps/util/proto.yaml": edited("util", 1)})
    revert = commit(ws, "revert util", {"shared/steps/util/proto.yaml": WORKSPACE["shared/steps/util/proto.yaml"]})
    tip = rev_parse(ws, "HEAD")
    assert commits_touching(ws, base, tip, BETA) == [(revert, "revert util"), (change, "change util")]
    assert count_touching(ws, base, tip, BETA) == 2
    assert commits_touching(ws, base, tip, CLOSURES["alpha"])[0][1] == "outside"
    assert commits_touching(ws, change, tip, BETA) == [(revert, "revert util")]
    assert commits_touching(ws, base, tip, []) == [] and count_touching(ws, base, tip, []) == 0


def test_commits_touching_follows_merged_side_branches(make_repo, commit, git):
    ws = make_repo(files=WORKSPACE)
    base = rev_parse(ws, "HEAD")
    git(ws, "checkout", "-q", "-b", "side")
    change = commit(ws, "side change", {"processes/beta/proto/own.yaml": edited("own", 1)})
    revert = commit(ws, "side revert", {"processes/beta/proto/own.yaml": WORKSPACE["processes/beta/proto/own.yaml"]})
    git(ws, "checkout", "-q", "main")
    commit(ws, "main moves", {"README.md": "main\n"})
    git(ws, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    touching = commits_touching(ws, base, rev_parse(ws, "HEAD"), BETA)
    assert {sha for sha, _ in touching} == {change, revert}


def test_log_paths_lists_the_newest_commits_touching_paths(make_repo, commit):
    ws = make_repo(files=WORKSPACE)
    shas = [commit(ws, f"edit {n}", {"processes/beta/proto/own.yaml": edited("own", n)}) for n in (1, 2, 3)]
    commit(ws, "elsewhere", {"README.md": "x\n"})
    entries = log_paths(ws, BETA, 2)
    assert [e["sha"] for e in entries] == [shas[2], shas[1]]
    assert [e["subject"] for e in entries] == ["edit 3", "edit 2"]
    first = entries[0]
    assert set(first) == {"sha", "short", "subject", "author", "at"}
    assert shas[2].startswith(first["short"]) and first["author"] == "Wynd Test"
    assert isinstance(first["at"], datetime) and first["at"].tzinfo is not None
    assert len(log_paths(ws, BETA, 10)) == 4                    # the three edits and the initial commit
    assert log_paths(ws, [], 10) == []


def test_git_errors_carry_the_command_and_stderr(make_repo):
    ws = make_repo(files=WORKSPACE)
    with pytest.raises(GitError, match=r"git cat-file -p nope failed in .*: fatal"):
        gitmod.git(ws, "cat-file", "-p", "nope")
    assert gitmod.git(ws, "cat-file", "-p", "nope", check=False) == ""
    assert gitmod.git(ws, "hash-object", "--stdin", input=b"x\n").strip() == subprocess.run(
        ["git", "hash-object", "--stdin"], input=b"x\n", capture_output=True, check=True
    ).stdout.decode().strip()
    assert gitmod.git(ws, "var", "GIT_AUTHOR_IDENT", env={"GIT_AUTHOR_NAME": "Someone"}).startswith("Someone <")
