"""CTL-JOBS test helpers: a small git workspace, runners over real file stores, and a `ControllerContext`.

`WORKSPACE` (files) holds processes that validate cleanly — `alpha` (two local proto steps, read -> write) and
`beta` (a local step, root step `shared:util`, child `process:alpha`) — and `broken` (an unrouted exit, E208).
`CLOSURES` are their reference closures. Every repository gets `.gitignore` with `.wynd/` next to `wynd.yaml`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wynd.controller.controller import ControllerContext
from wynd.controller.git import GitLock
from wynd.process.jobs import JobRecord, wait_for
from wynd.runtime.storage import stores_from_env

CONTROLLER_TESTS = Path(__file__).resolve().parents[1]


def proto_yaml(name: str) -> str:
    return (
        f"kind: proto_step\nname: {name}\ninstruction: Make the text better.\n"
        "inputs:\n  text: string\noutputs:\n  text: string\n"
    )


def chain_process(name: str, steps: Mapping[str, str]) -> str:
    """A process running `steps` in order, passing `text` along, ending in `$exit.done`."""
    keys = list(steps)
    lines = ["kind: process", f"name: {name}", f"entry: {keys[0]}", "inputs:", "  text: string", "outputs:",
             "  text: string", "steps:"]
    lines += [f"  {key}: {{use: '{use}'}}" for key, use in steps.items()]
    lines.append("edges:")
    for a, b in zip(keys, [*keys[1:], "$exit.done"]):
        lines += [f"  - from: {a}.done", f"    to: {b}", f"    with: {{text: steps.{a}.outputs.text}}"]
    return "\n".join(lines) + "\n"


WORKSPACE: dict[str, str] = {
    "wynd.yaml": "process_roots: [processes]\nstep_roots:\n  shared: shared/steps\n",
    ".gitignore": ".wynd/\n",
    "README.md": "A workspace for the job tests.\n",
    "processes/alpha/process.yaml": chain_process("alpha", {"read": "./steps/read", "write": "./steps/write"}),
    "processes/alpha/proto/read.yaml": proto_yaml("read"),
    "processes/alpha/proto/write.yaml": proto_yaml("write"),
    "processes/beta/process.yaml": chain_process(
        "beta", {"pre": "./steps/pre", "tool": "shared:util", "sub": "process:alpha"}
    ),
    "processes/beta/proto/pre.yaml": proto_yaml("pre"),
    "shared/steps/util/proto.yaml": proto_yaml("util"),
    "processes/broken/process.yaml": "kind: process\nname: broken\nentry: only\nsteps:\n  only: {use: ./steps/only}\n",
    "processes/broken/proto/only.yaml": proto_yaml("only"),
}

CLOSURES: dict[str, list[str]] = {
    "alpha": ["processes/alpha", "wynd.yaml"],
    "beta": ["processes/alpha", "processes/beta", "shared/steps/util", "wynd.yaml"],
}


def run_git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def write_files(root: Path, files: Mapping[str, str | None]) -> None:
    """Write text files under `root`; None deletes a file or directory."""
    for rel, content in files.items():
        path = root / rel
        if content is None and path.is_dir():
            shutil.rmtree(path)
        elif content is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)


def commit(ws: Path, message: str, files: Mapping[str, str | None] | None = None) -> str:
    """Write `files` (workspace-relative), stage everything under the workspace and commit; -> the new sha."""
    if files:
        write_files(ws, files)
    run_git(ws, "add", "-A", ".")
    run_git(ws, "commit", "--quiet", "--allow-empty", "-m", message)
    return run_git(ws, "rev-parse", "HEAD")


def make_workspace(tmp_path: Path, *, subdir: str = "", files: Mapping[str, str] | None = None) -> Path:
    """A git repository at `tmp_path/"repo"` (branch main, one commit) with the workspace at `subdir`."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "--quiet", "-b", "main")
    ws = repo / subdir if subdir else repo
    write_files(ws, WORKSPACE if files is None else files)
    commit(ws, "initial workspace")
    return ws


def open_stores(ws: Path, env: Mapping[str, str] | None = None):
    return stores_from_env(os.environ if env is None else env, data_dir=ws / ".wynd")


def make_runner(ws: Path, cls: type, handlers: Mapping[str, str], *, env: Mapping[str, str] | None = None) -> Any:
    env = dict(os.environ if env is None else env)
    return cls(env=env, workspace_root=ws, state_dir=ws / ".wynd", stores=open_stores(ws, env), handlers=handlers)


def make_context(ws: Path, runner: Any, *, env: Mapping[str, str] | None = None) -> ControllerContext:
    """A context holding only what `JobService` and integration use (no artefact store, docs or providers)."""
    return ControllerContext(
        root=ws,
        state_dir=ws / ".wynd",
        subdir=run_git(ws, "rev-parse", "--show-prefix").rstrip("/"),
        env=dict(os.environ if env is None else env),
        stores=runner.stores,
        artefacts=None,
        docs=None,
        runner=runner,
        git_lock=GitLock(ws / ".wynd" / "locks" / "git.lock"),
        load_provider=lambda name: None,
        clock=lambda: datetime.now(UTC),
    )


def settle(runner: Any, job_id: str, timeout_s: float = 60) -> JobRecord:
    """Wait until the job is finished or awaiting input."""
    return wait_for(runner, job_id, poll_s=0.02, timeout_s=timeout_s)


def wait_status(runner: Any, job_id: str, status: str, timeout_s: float = 60) -> JobRecord:
    """Poll until the job reaches `status` (and, for running jobs, records its pid)."""
    deadline = time.monotonic() + timeout_s
    while True:
        record = runner.status(job_id)
        if record.status == status and (status != "running" or record.pid is not None):
            return record
        assert time.monotonic() < deadline, f"job {job_id} stayed {record.status}, expected {status}"
        time.sleep(0.02)


def wait_log(ws: Path, job_id: str, text: str, timeout_s: float = 60) -> None:
    """Poll until the job log contains `text`."""
    deadline = time.monotonic() + timeout_s
    while text not in log_text(ws, job_id):
        assert time.monotonic() < deadline, f"job {job_id} never logged {text!r}"
        time.sleep(0.02)


def checkout_path(ws: Path, job_id: str, attempt: int = 1) -> Path:
    return ws / ".wynd" / "jobs" / job_id / f"checkout-{attempt}"


def log_text(ws: Path, job_id: str) -> str:
    path = ws / ".wynd" / "jobs" / job_id / "job.log"
    return path.read_text() if path.exists() else ""


def file_at(ws: Path, ref: str, rel: str) -> str:
    """The content of workspace-relative `rel` at `ref`."""
    prefix = run_git(ws, "rev-parse", "--show-prefix")
    return run_git(ws, "show", f"{ref}:{prefix}{rel}")
