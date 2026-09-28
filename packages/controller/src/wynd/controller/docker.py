"""Docker CLI helpers (PLAN §8.1; `$DRAFTS/06 §5.12`).

Every call is `subprocess` `["docker", ...]`; a missing binary or daemon raises `Unavailable`. Env values go through
the child `env=` with `-e VAR` (no value) so secrets never appear in argv. No mounts.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Mapping, Sequence

from wynd.controller.errors import NotFound, Unavailable

DAEMON_DOWN = ("Cannot connect to the Docker daemon", "error during connect", "Is the docker daemon running")
NO_SUCH = ("No such image", "No such object", "No such container")


def image_labels(image: str) -> dict[str, str]:
    out = _docker(["image", "inspect", "--format", "{{json .Config.Labels}}", image], missing=f"image {image}")
    return json.loads(out.strip() or "null") or {}


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
    args = ["run", "-d", "--name", name, "-p", f"127.0.0.1:{host_port or ''}:{container_port}"]
    for key, value in labels.items():
        args += ["--label", f"{key}={value}"]
    for var in sorted(env):
        args += ["-e", var]
    if restart is not None:
        args += ["--restart", restart]
    args.append(image)
    return _docker(args, env={**os.environ, **env}).strip()


def host_port(name: str, container_port: int) -> int:
    """The published host port of `container_port/tcp` (`docker port` prints e.g. `127.0.0.1:53817`)."""
    out = _docker(["port", name, f"{container_port}/tcp"], missing=f"container {name}")
    first = out.strip().splitlines()[0] if out.strip() else ""
    port = first.rpartition(":")[2]
    if not port.isdigit():
        raise Unavailable(f"container {name} publishes no host port for {container_port}/tcp")
    return int(port)


def rm(name: str) -> None:
    """`docker rm -f` (idempotent)."""
    try:
        _docker(["rm", "-f", name], missing=f"container {name}")
    except NotFound:
        return


def logs_follow(name: str) -> subprocess.Popen:
    try:
        return subprocess.Popen(["docker", "logs", "-f", name], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                text=True)
    except FileNotFoundError:
        raise Unavailable("docker is not installed (the docker CLI was not found on PATH)") from None


def logs_tail(name: str, n: int = 50) -> str:
    return _docker(["logs", "--tail", str(n), name], missing=f"container {name}", merge=True)


def _docker(
    args: Sequence[str], *, env: Mapping[str, str] | None = None, missing: str | None = None, merge: bool = False
) -> str:
    """Run `docker <args>` and return stdout (stdout + stderr with `merge`). A missing binary or daemon raises
    `Unavailable`; with `missing`, a "No such …" failure raises `NotFound(f"{missing} not found")`."""
    try:
        proc = subprocess.run(["docker", *args], env=None if env is None else dict(env), capture_output=True,
                              text=True, stdin=subprocess.DEVNULL)
    except FileNotFoundError:
        raise Unavailable("docker is not installed (the docker CLI was not found on PATH)") from None
    if proc.returncode == 0:
        return proc.stdout + proc.stderr if merge else proc.stdout
    stderr = proc.stderr.strip()
    if any(text in stderr for text in DAEMON_DOWN):
        raise Unavailable(f"the Docker daemon is not reachable: {stderr}", hint="start Docker and retry")
    if missing is not None and any(text in stderr for text in NO_SUCH):
        raise NotFound(f"{missing} not found")
    raise Unavailable(f"docker {args[0]} failed (exit {proc.returncode}): {stderr}")
