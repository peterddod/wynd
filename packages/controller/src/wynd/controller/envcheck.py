"""`EnvService` (PLAN §8.1, §3.10; `$DRAFTS/06 §5.11`).

`resolve` precedence (highest first): the controller's environ (`ctx.env`, `os.environ` unless the controller was
opened with another mapping) > extra files (in order) > `root/.env` > `registry.secrets()`; it refreshes MCP OAuth
tokens first (`RegistryService.refresh_tokens`, a no-op without OAuth entries).
`resolve_image` = `resolve()` plus `WYND_REGISTRY_JSON` (compact JSON of `registry_snapshot(lp, registry)`) when the
snapshot is non-empty. `manifest` is the build's manifest if built, else `assemble_env_manifest`. Values are never
reported, only names.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from wynd.controller.envfile import ENV_FILE, read_env_file
from wynd.controller.errors import translated

if TYPE_CHECKING:
    from wynd.controller.api.models_web import Release
    from wynd.controller.controller import Controller, ControllerContext
    from wynd.controller.models import EnvCheckDTO
    from wynd.spec.env_manifest import EnvManifest

REGISTRY_JSON = "WYND_REGISTRY_JSON"


class EnvService:
    def __init__(self, ctx: ControllerContext, ctl: Controller) -> None:
        self.ctx = ctx
        self.ctl = ctl

    def resolve(self, extra_files: Sequence[Path] = ()) -> dict[str, str]:
        """Refreshes expiring MCP OAuth tokens first (they are registry secrets), so every run, env check and
        container start sees current ones."""
        self.ctl.registries.refresh_tokens()
        values = dict(self.ctx.stores.registry.secrets())
        values.update(read_env_file(self.ctx.root / ENV_FILE))
        for path in reversed(list(extra_files)):
            values.update(read_env_file(Path(path)))
        values.update(self.ctx.env)
        return values

    def resolve_image(self, pid: str, extra_files: Sequence[Path] = ()) -> dict[str, str]:
        from wynd.process.envmanifest import registry_snapshot

        values = self.resolve(extra_files)
        with translated():
            snapshot = registry_snapshot(self.ctx.workspace().load_process(pid), self.ctx.stores.registry)
        if snapshot:
            values[REGISTRY_JSON] = json.dumps(snapshot, separators=(",", ":"), sort_keys=True)
        return values

    def manifest(self, pid: str, commit: str | None = None) -> EnvManifest:
        """The build's manifest for (pid, commit or the process HEAD) if built, else `assemble_env_manifest` (of the
        commit when given, else of the working tree)."""
        from wynd.controller.status import process_head
        from wynd.process.envmanifest import assemble_env_manifest
        from wynd.process.workspace import CommitTree

        with translated():
            found = process_head(self.ctx, pid) if commit is None else None
            head = commit or (None if found is None else found[0])
            build = None if head is None else self.ctx.artefacts.get_build(pid, head)
            if build is not None:
                return build.manifest
            tree = None if commit is None else CommitTree(self.ctx.root, commit)
            return assemble_env_manifest(self.ctx.workspace(tree), pid, self.ctx.stores.registry, commit=commit)

    def check(
        self, pid: str, *, extra_files: Sequence[Path] = (), mode: Literal["local", "image"] = "local"
    ) -> EnvCheckDTO:
        """`check_env` of `manifest(pid)` against `resolve` (local) or `resolve_image` (image)."""
        environ = self.resolve_image(pid, extra_files) if mode == "image" else self.resolve(extra_files)
        return check_manifest(self.manifest(pid), environ, mode=mode)

    def check_release(self, release: Release) -> EnvCheckDTO:
        """`unbound`: required vars with no binding (as `check_env` counts them in image mode: required without a
        default, or every member of a `one_of` group required in image mode none of whose members is bound);
        `missing`: `from_env` bindings whose source is unset in `resolve()`."""
        from wynd.controller.api.models_web import FromEnvBinding
        from wynd.controller.models import EnvCheckDTO
        from wynd.spec.fragments import EnvGroup

        manifest = self.manifest(release.process_id, release.commit)
        environ = self.resolve()
        unbound: list[str] = []
        groups: dict[str, list[str]] = {}
        for var in manifest.vars:
            if var.one_of is not None:
                groups.setdefault(var.one_of, []).append(var.name)
            elif var.required and var.default is None and var.name not in release.env:
                unbound.append(var.name)
        for group, names in groups.items():
            required = "image" in manifest.groups.get(group, EnvGroup()).modes      # check_env's default group
            if required and not any(name in release.env for name in names):
                unbound += names
        missing = [name for name, binding in release.env.items()
                   if isinstance(binding, FromEnvBinding) and not environ.get(binding.from_env)]
        return EnvCheckDTO(ok=not unbound and not missing, missing=missing, unbound=unbound)


def check_manifest(manifest: EnvManifest, environ: dict[str, str], *, mode: Literal["local", "image"]) -> EnvCheckDTO:
    """The env gate: spec `check_env` as the web/CLI DTO."""
    from wynd.controller.models import EnvCheckDTO
    from wynd.spec.env_manifest import check_env

    return EnvCheckDTO.from_check(manifest, check_env(manifest, environ, mode=mode))
