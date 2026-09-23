"""Storage backends selected by environment (SPEC §7.1, PLAN §3.14; algorithm `$DRAFTS/02 §9.4`).

Each interface is resolved from its selector variable through the `wynd.storage` entry-point group; this module never
imports a backend at import time.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from wynd.runtime.storage.base import REGISTRY_SECTIONS, Registry, RunRegistry, TraceSink, WorkspaceStore
from wynd.spec.fragments import EnvVar

__all__ = [
    "REGISTRY_SECTIONS",
    "STORAGE_ENV",
    "Registry",
    "RunRegistry",
    "StorageConfigError",
    "Stores",
    "TraceSink",
    "WorkspaceStore",
    "registry_from_env",
    "stores_from_env",
]


class StorageConfigError(RuntimeError):
    """A storage selector names no installed backend, a required location is unset, or a backend is read-only."""


@dataclass(frozen=True)
class Stores:
    workspaces: WorkspaceStore
    traces: TraceSink
    runs: RunRegistry
    registry: Registry


def stores_from_env(env: Mapping[str, str] | None = None, *, data_dir: str | Path | None = None) -> Stores:
    raise NotImplementedError("PLAN §3.14")


def registry_from_env(env: Mapping[str, str] | None = None) -> Registry:
    raise NotImplementedError("PLAN §3.14")


STORAGE_ENV: list[EnvVar] = [
    EnvVar(
        name="WYND_DATA_DIR",
        description="Root of run workspaces, traces and the run registry (the base image sets /var/lib/wynd).",
        required=False,
        used_by=["storage"],
    ),
    EnvVar(
        name="WYND_WORKSPACE_STORE",
        description="Run workspace backend, <scheme>[:<location>].",
        required=False,
        default="file",
        used_by=["storage"],
    ),
    EnvVar(
        name="WYND_TRACE_SINK",
        description="Trace backend, <scheme>[:<location>].",
        required=False,
        default="jsonl",
        used_by=["storage"],
    ),
    EnvVar(
        name="WYND_RUN_REGISTRY",
        description="Run and job registry backend, <scheme>[:<location>].",
        required=False,
        default="file",
        used_by=["storage"],
    ),
    EnvVar(
        name="WYND_REGISTRY",
        description="User registry backend, <scheme>[:<location>] (the base image sets env: read WYND_REGISTRY_JSON).",
        required=False,
        default="file",
        used_by=["storage"],
    ),
    EnvVar(
        name="WYND_HOME",
        description="Location of the file user registry (mcp.json, providers.json, registries.json, secrets.env).",
        required=False,
        default="~/.wynd",
        used_by=["storage"],
    ),
]
