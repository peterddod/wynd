"""`ControllerContext` and `Controller` (PLAN §8; `$DRAFTS/06 §5.1`). Stub; CTL-CORE.

`Controller.open(root=None, *, environ=None, load_dotenv=True)` picks every backend by env var or entry point:
`stores_from_env(env, data_dir=root/".wynd")`, `open_artefact_store(state_dir, env)`,
`open_job_runner(env.get("WYND_JOB_RUNNER", "subprocess"), **kw)`. Serving and triggers are lazy
(`ControllerContext.serving`/`.triggers` are `functools.cached_property`s calling `open_serving_backend`/
`open_trigger_backend` with the backend factory keywords); `open` never touches them. `Controller.__init__` constructs
every service as `Service(ctx, self)`.

Field changes from the draft that PLAN §8 forces: `serving`/`triggers` are cached properties, not fields, so the
context keeps the `env` they are selected from; `git: Git` is `git_lock: GitLock` (controller `git.py` holds only
`GitLock`; all git work calls `wynd.process.git`).
"""

from __future__ import annotations

import functools
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

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
        raise NotImplementedError("PLAN §8")

    @functools.cached_property
    def serving(self) -> ServingBackend:
        """`open_serving_backend(env.get("WYND_SERVING_BACKEND", "docker"), **kw)` on first access."""
        raise NotImplementedError("PLAN §8")

    @functools.cached_property
    def triggers(self) -> TriggerBackend:
        """`open_trigger_backend(env.get("WYND_TRIGGER_BACKEND", "scheduler"), **kw)` on first access."""
        raise NotImplementedError("PLAN §8")


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
        raise NotImplementedError("PLAN §8")

    @classmethod
    def open(
        cls, root: Path | None = None, *, environ: Mapping[str, str] | None = None, load_dotenv: bool = True
    ) -> Controller:
        """`$DRAFTS/06 §5.1` steps 1-9 (NotAWorkspace when no `wynd.yaml` is found)."""
        raise NotImplementedError("PLAN §8")

    def meta(self) -> Meta:
        """`llm.ready`/`llm.message` from `wynd.runtime.providers.check_provider(<default provider>)`."""
        raise NotImplementedError("PLAN §8")

    def close(self) -> None:
        """Stops threads started by services (the API lifespan calls it)."""
        raise NotImplementedError("PLAN §8")
