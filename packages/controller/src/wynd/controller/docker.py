"""Docker CLI helpers (PLAN §8.1; `$DRAFTS/06 §5.12`). Stub; CTL-M2.

Every call is `subprocess` `["docker", ...]`; a missing binary or daemon raises `Unavailable`. Env values go through
the child `env=` with `-e VAR` (no value) so secrets never appear in argv. No mounts.
"""

from __future__ import annotations

import subprocess
from collections.abc import Mapping


def image_labels(image: str) -> dict[str, str]:
    raise NotImplementedError("PLAN §8.1")


def run_detached(
    image: str,
    *,
    name: str,
    env: Mapping[str, str],
    host_port: int | None,
    container_port: int,
    labels: Mapping[str, str],
    restart: str | None = None,
) -> str:
    """-> container id."""
    raise NotImplementedError("PLAN §8.1")


def host_port(name: str, container_port: int) -> int:
    raise NotImplementedError("PLAN §8.1")


def rm(name: str) -> None:
    """`docker rm -f` (idempotent)."""
    raise NotImplementedError("PLAN §8.1")


def logs_follow(name: str) -> subprocess.Popen:
    raise NotImplementedError("PLAN §8.1")


def logs_tail(name: str, n: int = 50) -> str:
    raise NotImplementedError("PLAN §8.1")
