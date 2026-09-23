"""Dirt and commits (PLAN §3.19, §15 item 12): `dirty_paths` over the whole workspace directory or a closure, and the
two ways wynd commits — `commit_paths` in job checkouts, `commit_only` for design commits in the main worktree."""

import pytest
from support.proc_git_harness import CLOSURES, WORKSPACE, proto_yaml

from wynd.process.git import commit_only, commit_paths, dirty_paths, rev_parse

GITIGNORE = "*.log\nbuild/\n"


def name_status(git, ws, sha, *options):
    return sorted(line for line in git(ws, "show", "--name-status", "--no-renames", "--format=", *options, sha).splitlines() if line)


def test_whole_workspace_dirt_sees_every_change_but_not_state_or_ignored_files(make_repo, write_files, git):
    ws = make_repo(files={**WORKSPACE, ".gitignore": GITIGNORE})
    assert dirty_paths(ws, None) == []
    write_files(ws, {
        "processes/alpha/proto/read.yaml": proto_yaml("read", "Modified."),      # modified
        "notes/todo.txt": "untracked, outside every closure\n",                    # untracked, in a new dir
        "shared/steps/unused/new.py": "x = 1\n",                                   # untracked
        "processes/beta/proto/own.yaml": None,                                     # deleted
        "run.log": "ignored\n",
        "build/out.bin": "ignored\n",
        ".wynd/jobs/j1/job.log": "state\n",
        ".wynd/tmp/x.txt": "state\n",
    })
    (ws / "staged.txt").write_text("staged\n")
    git(ws, "add", "staged.txt")
    assert dirty_paths(ws, None) == [
        "notes/todo.txt",
        "processes/alpha/proto/read.yaml",
        "processes/beta/proto/own.yaml",
        "shared/steps/unused/new.py",
        "staged.txt",
    ]


def test_closure_scoped_dirt_ignores_other_paths(make_repo, write_files):
    ws = make_repo(files=WORKSPACE)
    write_files(ws, {"processes/alpha/proto/read.yaml": "changed\n", "README.md": "changed\n"})
    assert dirty_paths(ws, CLOSURES["beta"]) == []
    write_files(ws, {"shared/steps/util/extra.py": "x = 1\n", "processes/team/gamma/proto/calc.yaml": "changed\n"})
    assert dirty_paths(ws, CLOSURES["beta"]) == [
        "processes/team/gamma/proto/calc.yaml", "shared/steps/util/extra.py",
    ]
    assert dirty_paths(ws, []) == []


def test_subdirectory_workspace_dirt_is_workspace_relative_and_stays_inside(make_repo, write_files, git):
    ws = make_repo(files=WORKSPACE, subdir="examples/ws")
    repo = ws.parents[1]
    write_files(repo, {"outside.txt": "not in the workspace\n", "examples/other/x.txt": "sibling\n"})
    assert dirty_paths(ws, None) == []
    git(ws, "mv", "README.md", "READ_ME.md")
    assert dirty_paths(ws, None) == ["README.md", "READ_ME.md"]              # a staged rename: both paths


def test_commit_paths_stages_the_given_paths_including_deletions(make_repo, write_files, git):
    ws = make_repo(files=WORKSPACE, subdir="examples/ws")
    base = rev_parse(ws, "HEAD")
    write_files(ws, {
        "processes/beta/steps/own/own.py": "print('own')\n",
        "processes/beta/proto/own.yaml": None,
        "processes/alpha/proto/read.yaml": "not staged\n",
        "shared/steps/never/proto.yaml": None,                                     # never existed: ignored
    })
    sha = commit_paths(ws, "compile beta", ["processes/beta", "shared/steps/never", "wynd.yaml"],
                       trailers={"Wynd-Job": "job_1", "Wynd-Process": "beta"})
    assert sha == rev_parse(ws, "HEAD") and rev_parse(ws, "HEAD~1") == base
    assert name_status(git, ws, sha, "--relative") == [
        "A\tprocesses/beta/steps/own/own.py", "D\tprocesses/beta/proto/own.yaml",
    ]
    assert git(ws, "log", "-1", "--format=%B", sha) == "compile beta\n\nWynd-Job: job_1\nWynd-Process: beta\n\n"
    assert git(ws, "log", "-1", "--format=%(trailers:key=Wynd-Job,valueonly)", sha).strip() == "job_1"
    assert dirty_paths(ws, None) == ["processes/alpha/proto/read.yaml"]
    assert commit_paths(ws, "nothing", ["processes/beta"]) is None


def test_commit_paths_default_is_the_workspace_without_state(make_repo, write_files, git):
    ws = make_repo(files=WORKSPACE, subdir="examples/ws")
    write_files(ws.parents[1], {"outside.txt": "stays out\n"})
    write_files(ws, {"README.md": "changed\n", ".wynd/tmp/x.txt": "state\n"})
    sha = commit_paths(ws, "everything")
    assert git(ws, "show", "--name-only", "--format=", sha).split() == ["examples/ws/README.md"]
    assert commit_paths(ws, "again") is None


def test_commits_fall_back_to_the_wynd_identity(make_repo, write_files, git, monkeypatch, tmp_path):
    ws = make_repo(files=WORKSPACE)
    for var in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    git(ws, "config", "--unset", "user.name")
    git(ws, "config", "--unset", "user.email")
    write_files(ws, {"README.md": "one\n"})
    sha = commit_paths(ws, "no identity configured")
    assert git(ws, "log", "-1", "--format=%an <%ae>|%cn <%ce>", sha).strip() == (
        "Wynd <wynd@localhost>|Wynd <wynd@localhost>"
    )
    write_files(ws, {"README.md": "two\n"})
    assert git(ws, "log", "-1", "--format=%an", commit_only(ws, ["README.md"], "design")).strip() == "Wynd"
    git(ws, "config", "user.name", "Repo Person")
    git(ws, "config", "user.email", "person@example.com")
    write_files(ws, {"README.md": "three\n"})
    sha = commit_paths(ws, "identity configured")
    assert git(ws, "log", "-1", "--format=%an <%ae>", sha).strip() == "Repo Person <person@example.com>"


def test_commit_only_commits_exactly_its_paths_and_leaves_other_staged_changes(make_repo, write_files, git):
    ws = make_repo(files=WORKSPACE)
    write_files(ws, {
        "processes/alpha/proto/read.yaml": proto_yaml("read", "Design edit."),
        "processes/alpha/proto/extra.yaml": proto_yaml("extra"),
        "processes/alpha/proto/write.yaml": None,
        "processes/beta/proto/own.yaml": proto_yaml("own", "Someone else's staged edit."),
    })
    git(ws, "add", "processes/beta/proto/own.yaml")
    sha = commit_only(ws, ["processes/alpha/proto/read.yaml", "processes/alpha/proto/extra.yaml",
                           "processes/alpha/proto/write.yaml", "processes/alpha/proto/never.yaml"], "design: alpha")
    assert sha == rev_parse(ws, "HEAD")
    assert name_status(git, ws, sha) == [
        "A\tprocesses/alpha/proto/extra.yaml",
        "D\tprocesses/alpha/proto/write.yaml",
        "M\tprocesses/alpha/proto/read.yaml",
    ]
    assert git(ws, "diff", "--cached", "--name-only").split() == ["processes/beta/proto/own.yaml"]
    assert commit_only(ws, ["processes/alpha/proto/read.yaml"], "again") is None
    assert commit_only(ws, [], "empty") is None
    assert rev_parse(ws, "HEAD") == sha


@pytest.mark.parametrize("paths", [None, ["."]])
def test_commit_paths_never_stages_wynd_state(make_repo, write_files, git, paths):
    ws = make_repo(files=WORKSPACE)
    write_files(ws, {".wynd/jobs/j/job.log": "x\n", "README.md": "changed\n"})
    sha = commit_paths(ws, "commit", paths)
    assert git(ws, "show", "--name-only", "--format=", sha).split() == ["README.md"]
