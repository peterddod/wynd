"""Env fragments and env vars (PLAN §3.10; $DRAFTS/01 §6.2)."""

from collections.abc import Iterable
from typing import Literal

from wynd.spec.base import EnvName, SpecModel
from wynd.spec.errors import Diagnostic, SpecError


class EnvVar(SpecModel):
    name: EnvName
    description: str = ""
    secret: bool = False
    required: bool = True
    default: str | None = None  # documented default; satisfies `required`
    one_of: str | None = None  # alternatives group name (see EnvGroup)
    # "step:<id>" | "tool:<step id>/<tool>" | "mcp:<server>" | "provider:<name>" | "edge:<pid>:<branch_key>.<field>"
    # | "storage" | "runtime"
    used_by: list[str] = []


Mode = Literal["local", "image"]
_MODES: tuple[Mode, ...] = ("local", "image")


class EnvGroup(SpecModel):
    description: str = ""
    modes: list[Mode] = ["local", "image"]  # modes where the group is required


class EnvFragment(SpecModel):
    deps: list[str] = []  # PEP 508 requirement strings (minimums)
    system: list[str] = []  # OS package names, used verbatim by apt-get / apk
    requires: Literal["glibc"] | None = None
    vars: list[EnvVar] = []
    groups: dict[str, EnvGroup] = {}


def merge_env_vars(vars: Iterable[EnvVar]) -> list[EnvVar]:
    """Merge by name, sorted by name ($DRAFTS/01 §6.2); SpecError E-ENV-CONFLICT on differing defaults.

    First non-empty description; secret and required if any says so; the one agreed default; first `one_of`;
    `used_by` is the sorted union."""
    merged: dict[str, EnvVar] = {}
    conflicts: list[Diagnostic] = []
    for var in vars:
        current = merged.get(var.name)
        if current is None:
            merged[var.name] = var.model_copy(update={"used_by": sorted(set(var.used_by))})
            continue
        if current.default is not None and var.default is not None and current.default != var.default:
            users = ", ".join(sorted({*current.used_by, *var.used_by})) or "unknown"
            conflicts.append(
                Diagnostic(
                    "error", "E-ENV-CONFLICT",
                    f"env var {var.name} has conflicting defaults {current.default!r} and {var.default!r} "
                    f"(used by {users})",
                )
            )
            continue
        merged[var.name] = current.model_copy(
            update={
                "description": current.description or var.description,
                "secret": current.secret or var.secret,
                "required": current.required or var.required,
                "default": current.default if current.default is not None else var.default,
                "one_of": current.one_of if current.one_of is not None else var.one_of,
                "used_by": sorted({*current.used_by, *var.used_by}),
            }
        )
    if conflicts:
        raise SpecError(conflicts)
    return [merged[name] for name in sorted(merged)]


def merge_fragments(frags: Iterable[EnvFragment]) -> EnvFragment:
    """deps order-preserving unique; system sorted; requires glibc if any; vars merged; groups merged (modes union)."""
    frags = list(frags)
    deps: list[str] = []
    for frag in frags:
        deps.extend(dep for dep in frag.deps if dep not in deps)
    groups: dict[str, EnvGroup] = {}
    for frag in frags:
        for name, group in frag.groups.items():
            current = groups.get(name)
            if current is None:
                groups[name] = group.model_copy()
                continue
            modes = {*current.modes, *group.modes}
            groups[name] = EnvGroup(
                description=current.description or group.description, modes=[m for m in _MODES if m in modes]
            )
    return EnvFragment(
        deps=deps,
        system=sorted({pkg for frag in frags for pkg in frag.system}),
        requires="glibc" if any(frag.requires == "glibc" for frag in frags) else None,
        vars=merge_env_vars(var for frag in frags for var in frag.vars),
        groups=groups,
    )
