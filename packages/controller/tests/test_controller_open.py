"""`Controller.open`, `ControllerContext` and `Controller.meta/close` (PLAN §8; `$DRAFTS/06 §5.1`).

The job runner and `GitLock` belong to CTL-JOBS (same sub-wave) and are doubled here: `open_job_runner` records the
factory keywords, `GitLock` is the conftest `FakeGitLock`.
"""

from __future__ import annotations

import os

import pytest

import wynd.controller.jobs.runners as runners
import wynd.controller.releases.serving as serving
import wynd.controller.releases.triggers as triggers
from wynd.controller import __version__
from wynd.controller.controller import Controller
from wynd.controller.errors import NotAWorkspace
from wynd.controller.store import FileDocStore
from wynd.process.artefacts import LocalArtefactStore
from wynd.spec.base import DEFAULT_PROVIDER


class Backend:
    def __init__(self, name: str, **kw) -> None:
        self.name, self.kw, self.closed = name, kw, False

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def opened(monkeypatch, fake_git_lock):
    """Records every backend factory call made through the controller."""
    calls: list[tuple[str, str, dict]] = []

    def factory(group: str):
        def open_backend(name: str, **kw) -> Backend:
            calls.append((group, name, kw))
            return Backend(name, **kw)
        return open_backend

    monkeypatch.setattr(runners, "open_job_runner", factory("runner"))
    monkeypatch.setattr(serving, "open_serving_backend", factory("serving"))
    monkeypatch.setattr(triggers, "open_trigger_backend", factory("triggers"))
    return calls


def environ(**extra: str) -> dict[str, str]:
    """The process environment without the runner selector the root conftest sets, plus `extra`."""
    return {**{k: v for k, v in os.environ.items() if k != "WYND_JOB_RUNNER"}, **extra}


def test_open_wires_every_backend_from_the_environ(subdir_workspace, opened):
    env = environ(WYND_JOB_RUNNER="inprocess")
    ctl = Controller.open(subdir_workspace, environ=env, load_dotenv=False)
    ctx = ctl.ctx

    assert (ctx.root, ctx.state_dir, ctx.subdir) == (subdir_workspace, subdir_workspace / ".wynd", "examples/ws")
    assert ctx.env is env
    assert all((ctx.state_dir / d).is_dir() for d in ("locks", "jobs", "serve", "uploads", "chats"))
    assert isinstance(ctx.artefacts, LocalArtefactStore) and ctx.artefacts.state_dir == ctx.state_dir
    assert isinstance(ctx.docs, FileDocStore) and ctx.docs.root == ctx.state_dir / "controller"
    assert ctx.git_lock.path == ctx.state_dir / "locks" / "git.lock"
    assert ctx.stores.registry.location() == os.environ["WYND_HOME"]
    assert ctx.clock().tzinfo is not None

    [(group, name, kw)] = opened                                     # serving and triggers stay untouched
    assert (group, name) == ("runner", "inprocess") and ctx.runner.name == "inprocess"
    assert kw == {"env": env, "workspace_root": subdir_workspace, "state_dir": ctx.state_dir, "stores": ctx.stores}

    for service in (ctl.processes, ctl.design, ctl.jobs, ctl.runs, ctl.env, ctl.serve, ctl.uploads, ctl.registries,
                    ctl.releases, ctl.chats):
        assert (service.ctx, service.ctl) == (ctx, ctl)


def test_serving_and_triggers_are_opened_lazily_once(workspace, opened):
    ctx = Controller.open(workspace, environ=environ(WYND_TRIGGER_BACKEND="kube"), load_dotenv=False).ctx
    assert [c[0] for c in opened] == ["runner"]
    assert ctx.serving is ctx.serving and ctx.triggers is ctx.triggers
    assert [(group, name) for group, name, _ in opened] == [("runner", "subprocess"), ("serving", "docker"),
                                                            ("triggers", "kube")]
    assert opened[1][2] == {"env": ctx.env, "workspace_root": workspace, "state_dir": ctx.state_dir,
                            "stores": ctx.stores}


def test_the_workspace_is_found_upwards_or_from_wynd_workspace(workspace, opened, tmp_path):
    nested = workspace / "processes" / "p1" / "steps"
    assert Controller.open(nested, environ=environ(), load_dotenv=False).ctx.root == workspace
    env = environ(WYND_WORKSPACE=str(workspace / "processes"))
    assert Controller.open(environ=env, load_dotenv=False).ctx.root == workspace
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(NotAWorkspace, match="no wynd.yaml found") as info:
        Controller.open(outside, environ=environ(), load_dotenv=False)
    assert (info.value.code, info.value.exit) == ("not_a_workspace", 3) and "wynd init" in info.value.hint


def test_load_dotenv_sets_only_unset_variables(workspace, opened, monkeypatch):
    (workspace / ".env").write_text("WYND_T_DOTENV=from-dotenv\nWYND_T_KEEP=from-dotenv\n")
    for name in ("WYND_T_DOTENV", "WYND_T_KEEP"):
        monkeypatch.setenv(name, "x")                                # monkeypatch restores both afterwards
    monkeypatch.delenv("WYND_T_DOTENV")
    Controller.open(workspace, load_dotenv=False)
    assert "WYND_T_DOTENV" not in os.environ
    ctl = Controller.open(workspace)
    assert (os.environ["WYND_T_DOTENV"], os.environ["WYND_T_KEEP"]) == ("from-dotenv", "x")
    assert ctl.ctx.env is os.environ


def test_workspace_is_reloaded_on_every_call(workspace, opened, write_files):
    ctx = Controller.open(workspace, environ=environ(), load_dotenv=False).ctx
    assert ctx.workspace().process_ids() == ["p1", "p2", "parent"]
    write_files(workspace, {"processes/p9/process.yaml": "kind: process\nname: p9\n"})
    assert "p9" in ctx.workspace().process_ids()


def test_meta(workspace, opened, git):
    ctl = Controller.open(workspace, environ=environ(WYND_CHAT_PROVIDER="fake"), load_dotenv=False)
    meta = ctl.meta()
    assert meta.version == __version__
    assert meta.workspace.model_dump() == {"root": str(workspace), "branch": "main",
                                           "head": git(workspace, "rev-parse", "HEAD").strip(),
                                           "process_roots": ["processes"], "step_roots": {"shared": "shared/steps"}}
    assert meta.default_provider == DEFAULT_PROVIDER and meta.default_max_traversals == 10
    assert meta.latency == ["fast", "normal"] and meta.limit_fields == ["max_traversals", "timeout", "retries"]
    assert "path" in meta.proto_types and meta.edge_kinds == ["deterministic", "agentic"]
    assert {"default", "coalesce", "len"} <= set(meta.expr_functions)
    assert (meta.llm.provider, meta.llm.ready) == ("fake", True)

    git(workspace, "checkout", "-q", "--detach")
    assert ctl.meta().workspace.branch is None
    missing = Controller.open(workspace, environ=environ(WYND_CHAT_PROVIDER="nope"), load_dotenv=False).meta()
    assert (missing.llm.provider, missing.llm.ready) == ("nope", False) and "not installed" in missing.llm.message


def test_close_closes_what_can_be_closed(workspace, opened):
    ctl = Controller.open(workspace, environ=environ(), load_dotenv=False)
    ctl.close()
    assert ctl.ctx.runner.closed is True
