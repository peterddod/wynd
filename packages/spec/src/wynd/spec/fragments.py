"""Env fragments and env vars (PLAN §3.10; $DRAFTS/01 §6.2)."""

from collections.abc import Iterable
from typing import Literal

from wynd.spec.base import EnvName, SpecModel


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
    """Merge by name, sorted by name ($DRAFTS/01 §6.2); SpecError E-ENV-CONFLICT on differing defaults."""
    raise NotImplementedError("PLAN §3.10")


def merge_fragments(frags: Iterable[EnvFragment]) -> EnvFragment:
    """deps order-preserving unique; system sorted; requires glibc if any; vars merged; groups merged (modes union)."""
    raise NotImplementedError("PLAN §3.10")
