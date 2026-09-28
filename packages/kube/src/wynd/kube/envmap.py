"""Env manifest -> container env, release ConfigMap data (`$DRAFTS/08 §5.7`, PLAN §11 item 5).

Secret vars always come from the per-process Secret `wynd-p-<process>` (`secretKeyRef`, optional = not required);
non-secret values go into the release ConfigMap.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wynd.spec.env_manifest import EnvManifest


def container_env(manifest: EnvManifest, *, secret_name: str, config_name: str) -> tuple[list[dict], list[dict]]:
    """(env, envFrom): one secretKeyRef entry per secret var of the manifest, and a configMapRef to config_name."""
    raise NotImplementedError("PLAN §11")


def config_data(manifest: EnvManifest, binding: Mapping[str, str], defaults: Mapping[str, str]) -> dict[str, str]:
    """ConfigMap data for the manifest's non-secret vars (incl. WYND_REGISTRY_JSON and WYND_MCP_<NAME>_URL).
    Value precedence: binding, then manifest default, then serving defaults."""
    raise NotImplementedError("PLAN §11")
