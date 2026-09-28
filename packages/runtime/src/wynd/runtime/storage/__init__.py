"""Storage backends selected by environment (SPEC §7.1, PLAN §3.14; algorithm `$DRAFTS/02 §9.4`).

Each interface is resolved from its selector variable through the `wynd.storage` entry-point group; this module never
imports a backend at import time. A selector is `<scheme>[:<location>]`; entry point `<interface>.<scheme>` names a
factory `(location, root, env) -> backend`, where `root` is the default location (`None` when it cannot be derived).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

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

_GROUP = "wynd.storage"
# interface -> (selector variable, default scheme)
_SELECTORS = {
    "workspace": ("WYND_WORKSPACE_STORE", "file"),
    "trace": ("WYND_TRACE_SINK", "jsonl"),
    "runs": ("WYND_RUN_REGISTRY", "file"),
    "registry": ("WYND_REGISTRY", "file"),
}
_DATA_SUBDIRS = {"workspace": "workspaces", "trace": "traces", "runs": "registry"}


class StorageConfigError(RuntimeError):
    """A storage selector names no installed backend, a required location is unset, or a backend is read-only."""


@dataclass(frozen=True)
class Stores:
    workspaces: WorkspaceStore
    traces: TraceSink
    runs: RunRegistry
    registry: Registry


def _default_root(interface: str, env: Mapping[str, str], data_dir: str | Path | None) -> Path | None:
    if interface == "registry":
        return Path(env.get("WYND_HOME") or Path.home() / ".wynd")
    data = env.get("WYND_DATA_DIR") or data_dir
    return Path(data) / _DATA_SUBDIRS[interface] if data else None


def _open(interface: str, env: Mapping[str, str], data_dir: str | Path | None) -> Any:
    var, default = _SELECTORS[interface]
    scheme, _, location = (env.get(var) or default).partition(":")
    found = list(metadata.entry_points(group=_GROUP, name=f"{interface}.{scheme}"))
    if not found:
        raise StorageConfigError(f"unknown {var} scheme {scheme!r} (no {_GROUP} entry point "
                                 f"'{interface}.{scheme}' is installed)")
    root = None if location else _default_root(interface, env, data_dir)
    return found[0].load()(location=location or None, root=root, env=env)


def stores_from_env(env: Mapping[str, str] | None = None, *, data_dir: str | Path | None = None) -> Stores:
    """`WYND_DATA_DIR` (else `data_dir`) roots the workspace, trace and run backends; `WYND_HOME` the user registry."""
    env = os.environ if env is None else env
    return Stores(
        workspaces=_open("workspace", env, data_dir),
        traces=_open("trace", env, data_dir),
        runs=_open("runs", env, data_dir),
        registry=_open("registry", env, data_dir),
    )


def registry_from_env(env: Mapping[str, str] | None = None) -> Registry:
    return _open("registry", os.environ if env is None else env, None)


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
