"""Subprocess helpers for external executables (PLAN §6.1; owner PROC-WS).

`run` streams output lines to `log`; `require_tool` resolves `git`, `uv` (`WYND_UV`) or `docker` and raises
`ToolMissing` with an install hint.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO

from .errors import ToolMissing

# Tools whose executable can be overridden by an environment variable (PLAN §3.23).
TOOL_ENV = {"uv": "WYND_UV"}
INSTALL_HINTS = {
    "git": "install git (https://git-scm.com/downloads)",
    "git-lfs": "install git-lfs (https://git-lfs.com)",
    "uv": "install uv (https://docs.astral.sh/uv/getting-started/installation/) or set WYND_UV to its path",
    "docker": "install Docker with buildx (https://docs.docker.com/get-docker/)",
}


def run(
    args: Sequence[str],
    *,
    cwd: str | Path,
    env: Mapping[str, str] | None = None,
    log: Callable[[str], None] | None = None,
    check: bool = True,
    input: str | bytes | None = None,
) -> str:
    """Run `args` in `cwd` and return its stdout.

    `env` is the child's complete environment (None: inherit). Every stdout and stderr line is passed to `log` as it
    arrives (without the newline). With `check`, a non-zero exit raises `subprocess.CalledProcessError` carrying the
    stdout and stderr text; a missing executable raises `ToolMissing`.
    """
    argv = [str(arg) for arg in args]
    try:
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=dict(env) if env is not None else None,
            stdin=subprocess.PIPE if input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except FileNotFoundError:
        if not Path(cwd).is_dir():
            raise
        raise ToolMissing(argv[0], INSTALL_HINTS.get(Path(argv[0]).name, "")) from None

    lock = threading.Lock()
    stdout: list[str] = []
    stderr: list[str] = []
    threads = [threading.Thread(target=_pump, args=(proc.stderr, stderr, log, lock), daemon=True)]
    if input is not None:
        data = input.encode() if isinstance(input, str) else input
        threads.append(threading.Thread(target=_feed, args=(proc.stdin, data), daemon=True))
    for thread in threads:
        thread.start()
    _pump(proc.stdout, stdout, log, lock)
    for thread in threads:
        thread.join()
    returncode = proc.wait()
    out = "".join(stdout)
    if check and returncode != 0:
        raise subprocess.CalledProcessError(returncode, argv, output=out, stderr="".join(stderr))
    return out


def _pump(stream: IO[bytes], sink: list[str], log: Callable[[str], None] | None, lock: threading.Lock) -> None:
    for raw in iter(stream.readline, b""):
        line = raw.decode("utf-8", errors="replace")
        sink.append(line)
        if log is not None:
            with lock:
                log(line.rstrip("\r\n"))
    stream.close()


def _feed(stream: IO[bytes], data: bytes) -> None:
    try:
        stream.write(data)
    except BrokenPipeError:  # the child exited without reading all of its input
        pass
    finally:
        try:
            stream.close()
        except BrokenPipeError:
            pass


def require_tool(name: str) -> str:
    """Absolute path of executable `name` (`uv` honours `WYND_UV`); `ToolMissing` with an install hint otherwise."""
    override = os.environ.get(TOOL_ENV[name], "") if name in TOOL_ENV else ""
    path = shutil.which(override or name)
    if path is None:
        hint = INSTALL_HINTS.get(name, f"install '{name}' and make sure it is on PATH")
        if override:
            hint = f"{TOOL_ENV[name]}={override} does not name an executable; {hint}"
        raise ToolMissing(name, hint)
    return path
