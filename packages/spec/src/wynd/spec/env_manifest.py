"""`process.env.yaml` and the pure env check run in-image and locally (PLAN §3.10; $DRAFTS/01 §6.8)."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wynd.spec.base import ProcessId, SpecModel
from wynd.spec.errors import Diagnostic
from wynd.spec.fragments import EnvGroup, EnvVar, Mode
from wynd.spec.yamlio import load_model


class EnvManifest(SpecModel):
    wynd: Literal[1] = 1
    process: ProcessId
    commit: str | None = None
    vars: list[EnvVar]  # sorted by name
    groups: dict[str, EnvGroup] = {}


@dataclass(frozen=True)
class EnvCheck:
    ok: bool  # no error diagnostics
    diagnostics: list[Diagnostic]


def _users(var: EnvVar) -> str:
    return f" (used by {', '.join(var.used_by)})" if var.used_by else ""


def check_env(manifest: EnvManifest, environ: Mapping[str, str], *, mode: Mode) -> EnvCheck:
    """E-ENV-MISSING / E-ENV-ONE-OF / W-ENV-ONE-OF; a var is set when present and non-empty; never prints values."""
    diagnostics: list[Diagnostic] = []
    groups: dict[str, list[EnvVar]] = {}
    for index, var in enumerate(manifest.vars):
        if var.one_of is not None:
            groups.setdefault(var.one_of, []).append(var)
            continue
        if var.required and var.default is None and not environ.get(var.name):
            diagnostics.append(
                Diagnostic("error", "E-ENV-MISSING", f"required env var {var.name} is not set{_users(var)}",
                           loc=("vars", index))
            )
    for name, members in groups.items():
        if any(environ.get(var.name) for var in members):
            continue
        group = manifest.groups.get(name, EnvGroup())
        names = ", ".join(var.name for var in members)
        users = sorted({user for var in members for user in var.used_by})
        detail = f"; {group.description}" if group.description else ""
        used = f" (used by {', '.join(users)})" if users else ""
        if mode in group.modes:
            diagnostics.append(
                Diagnostic("error", "E-ENV-ONE-OF", f"set one of {names} (group {name}{detail}){used}",
                           loc=("groups", name))
            )
        else:
            diagnostics.append(
                Diagnostic("warning", "W-ENV-ONE-OF",
                           f"none of {names} is set (group {name}{detail}); required in "
                           f"{' and '.join(group.modes) or 'no'} mode, optional in {mode} mode{used}",
                           loc=("groups", name))
            )
    return EnvCheck(ok=not any(d.severity == "error" for d in diagnostics), diagnostics=diagnostics)


def load_env_manifest(path: Path) -> EnvManifest:
    return load_model(path, EnvManifest)
