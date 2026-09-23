"""`process.env.yaml` and the pure env check run in-image and locally (PLAN §3.10; $DRAFTS/01 §6.8)."""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from wynd.spec.base import ProcessId, SpecModel
from wynd.spec.errors import Diagnostic
from wynd.spec.fragments import EnvGroup, EnvVar, Mode


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


def check_env(manifest: EnvManifest, environ: Mapping[str, str], *, mode: Mode) -> EnvCheck:
    """E-ENV-MISSING / E-ENV-ONE-OF / W-ENV-ONE-OF; a var is set when present and non-empty; never prints values."""
    raise NotImplementedError("PLAN §3.10")


def load_env_manifest(path: Path) -> EnvManifest:
    raise NotImplementedError("PLAN §3.10")
