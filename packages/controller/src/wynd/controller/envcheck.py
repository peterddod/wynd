"""`EnvService` (PLAN §8.1, §3.10; `$DRAFTS/06 §5.11`). Stub; CTL-CORE.

`resolve` precedence (highest first): `os.environ` > extra files > `root/.env` > `registry.secrets()`.
`resolve_image` = `resolve()` plus `WYND_REGISTRY_JSON` (compact JSON of `registry_snapshot(lp, registry)`) when the
snapshot is non-empty. `manifest` is the build's manifest if built, else `assemble_env_manifest`.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import EnvCheckDTO
    from wynd.spec.env_manifest import EnvManifest


class EnvService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def resolve(self, extra_files: Sequence[Path] = ()) -> dict[str, str]:
        raise NotImplementedError("PLAN §8.1")

    def resolve_image(self, pid: str, extra_files: Sequence[Path] = ()) -> dict[str, str]:
        raise NotImplementedError("PLAN §8.1")

    def manifest(self, pid: str, commit: str | None = None) -> EnvManifest:
        """The build's manifest for (pid, commit or the process HEAD) if built, else `assemble_env_manifest`."""
        raise NotImplementedError("PLAN §8.1")

    def check(
        self, pid: str, *, extra_files: Sequence[Path] = (), mode: Literal["local", "image"] = "local"
    ) -> EnvCheckDTO:
        raise NotImplementedError("PLAN §8.1")

    def check_release(self, release: Release) -> EnvCheckDTO:
        """`unbound`: required vars with no binding; `missing`: `from_env` bindings whose source is unset."""
        raise NotImplementedError("PLAN §8.1")
