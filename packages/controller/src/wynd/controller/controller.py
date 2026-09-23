"""`ControllerContext` and `Controller` (PLAN §8; `$DRAFTS/06 §5.1`).

`Controller.open(root=None, *, environ=None, load_dotenv=True)` picks every backend by env var or entry point:
`stores_from_env(env, data_dir=root/".wynd")`, `open_artefact_store(state_dir, env)`,
`open_job_runner(env.get("WYND_JOB_RUNNER", "subprocess"), **kw)`. Serving and triggers are lazy
(`ControllerContext.serving`/`.triggers` are `functools.cached_property`s calling `open_serving_backend`/
`open_trigger_backend` with the backend factory keywords); `open` never touches them. `Controller.__init__` constructs
every service as `Service(ctx, self)`.

Field changes from the draft that PLAN §8 forces: `serving`/`triggers` are cached properties, not fields, so the
context keeps the `env` they are selected from; `git: Git` is `git_lock: GitLock` (controller `git.py` holds only
`GitLock`; all git work calls `wynd.process.git`). This module and `envfile.py` are the only places that read the
process environment (`test_no_env_branches.py`).
"""

from __future__ import annotations

import functools
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from wynd.controller.errors import NotAWorkspace

if TYPE_CHECKING:
    from wynd.controller.chat.engine import ChatService
    from wynd.controller.design import DesignService
    from wynd.controller.envcheck import EnvService
    from wynd.controller.git import GitLock
    from wynd.controller.jobs.service import JobService
    from wynd.controller.models import Meta
    from wynd.controller.processes import ProcessService
    from wynd.controller.registries import RegistryService
    from wynd.controller.releases.serving import ServingBackend
    from wynd.controller.releases.service import ReleaseService
    from wynd.controller.releases.triggers import TriggerBackend
    from wynd.controller.runs.service import RunService
    from wynd.controller.serving import ServeService
    from wynd.controller.store import DocStore
    from wynd.controller.uploads import UploadService
    from wynd.process.artefacts import ArtefactStore
    from wynd.process.jobs import JobRunner
    from wynd.process.workspace import Tree, Workspace
    from wynd.runtime.storage import Stores

STATE_SUBDIRS = ("locks", "jobs", "serve", "uploads", "chats")


@dataclass(frozen=True)
class ControllerContext:
    root: Path                              # workspace root (the directory holding wynd.yaml)
    state_dir: Path                         # root / ".wynd" (= WYND_DATA_DIR for the local storage backends)
    subdir: str                             # workspace path inside its git repo: "" or e.g. "examples/invoices"
    env: Mapping[str, str]                  # the environ every backend was selected from
    stores: Stores                          # runtime: workspaces, traces, runs (RunRegistry), registry (user Registry)
    artefacts: ArtefactStore                # process: .wynd/build/<pid>/<commit>/ or env-selected
    docs: DocStore                          # FileDocStore(state_dir / "controller"): releases, fires, chats
    runner: JobRunner
    git_lock: GitLock                       # GitLock(state_dir / "locks" / "git.lock")
    load_provider: Callable[[str], object]  # wynd.runtime.providers.load_provider
    clock: Callable[[], datetime]           # tz-aware UTC

    def workspace(self, tree: Tree | None = None) -> Workspace:
        """`load_workspace(root, tree)`, reloaded on every call; `tree=CommitTree(root, sha)` reads a commit."""
        from wynd.controller.errors import translated
        from wynd.process.workspace import load_workspace

        with translated():
            return load_workspace(self.root, tree)

    @functools.cached_property
    def serving(self) -> ServingBackend:
        """`open_serving_backend(env.get("WYND_SERVING_BACKEND", "docker"), **kw)` on first access."""
        from wynd.controller.releases.serving import open_serving_backend

        return open_serving_backend(self.env.get("WYND_SERVING_BACKEND", "docker"), **self._backend_kw())

    @functools.cached_property
    def triggers(self) -> TriggerBackend:
        """`open_trigger_backend(env.get("WYND_TRIGGER_BACKEND", "scheduler"), **kw)` on first access."""
        from wynd.controller.releases.triggers import open_trigger_backend

        return open_trigger_backend(self.env.get("WYND_TRIGGER_BACKEND", "scheduler"), **self._backend_kw())

    def _backend_kw(self) -> dict[str, Any]:
        """The backend factory keywords (PLAN §8)."""
        return {"env": self.env, "workspace_root": self.root, "state_dir": self.state_dir, "stores": self.stores}


class Controller:
    ctx: ControllerContext
    processes: ProcessService
    design: DesignService
    jobs: JobService
    runs: RunService
    env: EnvService
    serve: ServeService
    uploads: UploadService
    registries: RegistryService
    releases: ReleaseService
    chats: ChatService

    def __init__(self, ctx: ControllerContext) -> None:
        """Stores `ctx` and constructs every service as `Service(ctx, self)`."""
        from wynd.controller.chat.engine import ChatService
        from wynd.controller.design import DesignService
        from wynd.controller.envcheck import EnvService
        from wynd.controller.jobs.service import JobService
        from wynd.controller.processes import ProcessService
        from wynd.controller.registries import RegistryService
        from wynd.controller.releases.service import ReleaseService
        from wynd.controller.runs.service import RunService
        from wynd.controller.serving import ServeService
        from wynd.controller.uploads import UploadService

        self.ctx = ctx
        self.processes = ProcessService(ctx, self)
        self.design = DesignService(ctx, self)
        self.jobs = JobService(ctx, self)
        self.runs = RunService(ctx, self)
        self.env = EnvService(ctx, self)
        self.serve = ServeService(ctx, self)
        self.uploads = UploadService(ctx, self)
        self.registries = RegistryService(ctx, self)
        self.releases = ReleaseService(ctx, self)
        self.chats = ChatService(ctx, self)

    @classmethod
    def open(
        cls, root: Path | None = None, *, environ: Mapping[str, str] | None = None, load_dotenv: bool = True
    ) -> Controller:
        """`$DRAFTS/06 §5.1` steps 1-9 (NotAWorkspace when no `wynd.yaml` is found). The workspace is searched
        upwards from `root`, else `WYND_WORKSPACE`, else the cwd."""
        from wynd.controller.envfile import load_into_environ
        from wynd.controller.git import GitLock
        from wynd.controller.jobs.runners import open_job_runner
        from wynd.controller.store import FileDocStore
        from wynd.process.artefacts import open_artefact_store
        from wynd.process.errors import GitError
        from wynd.process.git import prefix
        from wynd.runtime.providers import load_provider
        from wynd.runtime.storage import stores_from_env
        from wynd.spec.workspace import STATE_DIR, find_workspace_root

        env = os.environ if environ is None else environ
        start = Path(root) if root is not None else Path(env.get("WYND_WORKSPACE") or Path.cwd())
        found = find_workspace_root(start)
        if found is None:
            raise NotAWorkspace(f"no wynd.yaml found in {start.absolute()} or any parent directory",
                                hint="create a workspace with `wynd init`")
        state_dir = found / STATE_DIR
        for name in STATE_SUBDIRS:
            (state_dir / name).mkdir(parents=True, exist_ok=True)
        if load_dotenv:
            load_into_environ(found)
        try:
            subdir = prefix(found).rstrip("/")
        except GitError:
            subdir = ""                                 # not a git repository: loading reports E101
        stores = stores_from_env(env, data_dir=state_dir)
        runner = open_job_runner(env.get("WYND_JOB_RUNNER", "subprocess"), env=env, workspace_root=found,
                                 state_dir=state_dir, stores=stores)
        ctx = ControllerContext(
            root=found,
            state_dir=state_dir,
            subdir=subdir,
            env=env,
            stores=stores,
            artefacts=open_artefact_store(state_dir, env),
            docs=FileDocStore(state_dir / "controller"),
            runner=runner,
            git_lock=GitLock(state_dir / "locks" / "git.lock"),
            load_provider=functools.partial(load_provider, registry=stores.registry),
            clock=lambda: datetime.now(UTC),
        )
        return cls(ctx)

    def meta(self) -> Meta:
        """`llm.ready`/`llm.message` from `wynd.runtime.providers.check_provider(<chat provider>)`, where the chat
        provider is `WYND_CHAT_PROVIDER` (default: the default provider)."""
        from wynd.controller import __version__
        from wynd.controller.models import LlmStatus, Meta, WorkspaceInfo
        from wynd.process.errors import GitError
        from wynd.process.git import current_branch, rev_parse
        from wynd.runtime.providers import check_provider
        from wynd.spec.base import (
            BASES,
            DEFAULT_MAX_TRAVERSALS,
            DEFAULT_PROVIDER,
            EDGE_KINDS,
            LATENCIES,
            LIMIT_FIELDS,
            PROTO_TYPES,
        )
        from wynd.spec.expr.evaluator import BUILTINS

        ctx = self.ctx
        config = ctx.workspace().config
        try:
            head: str | None = rev_parse(ctx.root, "HEAD")
        except GitError:
            head = None                                 # no commit yet
        provider = ctx.env.get("WYND_CHAT_PROVIDER") or DEFAULT_PROVIDER
        ready, message = check_provider(provider)
        return Meta(
            version=__version__,
            workspace=WorkspaceInfo(
                root=str(ctx.root), branch=current_branch(ctx.root), head=head,
                process_roots=list(config.process_roots),
                step_roots={alias: root.path for alias, root in config.step_roots.items()},
            ),
            proto_types=list(PROTO_TYPES),
            bases=list(BASES),
            latency=list(LATENCIES),
            edge_kinds=list(EDGE_KINDS),
            expr_functions=sorted(BUILTINS),
            limit_fields=list(LIMIT_FIELDS),
            default_max_traversals=DEFAULT_MAX_TRAVERSALS,
            default_provider=DEFAULT_PROVIDER,
            llm=LlmStatus(provider=provider, ready=ready, message=message),
        )

    def close(self) -> None:
        """Stops threads started by services (the API lifespan calls it): every service or runner that has a
        `close()`."""
        for part in (self.ctx.runner, self.jobs, self.runs, self.serve, self.releases, self.chats):
            close = getattr(part, "close", None)
            if callable(close):
                close()
