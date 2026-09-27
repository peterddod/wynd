"""PROC-BUILD test support: a recording resolver and image builder, a build `JobContext`, and the `gated` workspace
(a deterministic-only process with one agentic branch and a child process).

- `FakeResolver.compile` pins every requirement without `==` to `<name>==1.0.0` (sorted, unique) and raises
  `ResolutionError` for a musl resolve that includes a name listed in `musl_fail`; `build_wheel` writes a small
  deterministic zip named after the project's `[project] name` holding every staged file.
- `FakeImageBuilder` records every call in order in `calls`, writes `buildkit-metadata.json` like buildx and knows the
  refs in `present`.
- `build_ctx(ws, runs, registry, inputs)` is the `JobContext` the harness would build for a build job whose checkout
  is `ws` itself (state dir `ws/.wynd`).
"""

from __future__ import annotations

import json
import re
import tomllib
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from support.proc_env_workspaces import lock_only, to_yaml

from wynd.process.build.imagebuilder import METADATA_FILE, ImageBuildResult
from wynd.process.errors import ResolutionError
from wynd.process.jobs import JobContext, JobRecord
from wynd.spec.lockfiles import EdgeLockEntry, EdgesLock, check_hash, dump_lock

CONFIG_DIGEST = "sha256:" + "c" * 64
BUILD_DIGEST = "sha256:" + "d" * 64
PUSH_DIGEST = "sha256:" + "e" * 64
IMAGE_BYTES = 123456


class FakeResolver:
    def __init__(self, musl_fail: tuple[str, ...] = ()) -> None:
        self.musl_fail = musl_fail
        self.calls: list[dict[str, Any]] = []

    def compile(self, requirements, *, universal, python_version="3.12", python_platform=None, only_binary=False,
                find_links=()) -> list[str]:
        self.calls.append({"op": "compile", "requirements": list(requirements), "universal": universal,
                           "python_platform": python_platform, "only_binary": only_binary,
                           "find_links": [str(p) for p in find_links]})
        names = [re.split(r"[<>=!~;\[ ]", req, maxsplit=1)[0].lower() for req in requirements]
        if python_platform and any(name in self.musl_fail for name in names):
            raise ResolutionError(f"No solution found when resolving dependencies for {python_platform}\n"
                                  "  Because there is no musllinux wheel, we can conclude the requirements are "
                                  "unsatisfiable.")
        return sorted({req if "==" in req else f"{name}==1.0.0" for req, name in zip(requirements, names)})

    def build_wheel(self, src: Path, out_dir: Path) -> Path:
        self.calls.append({"op": "build_wheel", "src": str(src)})
        name = tomllib.loads((Path(src) / "pyproject.toml").read_text())["project"]["name"]
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        wheel = out_dir / f"{name.replace('-', '_')}-0.1.0-py3-none-any.whl"
        with zipfile.ZipFile(wheel, "w") as zf:
            for path in sorted(p for p in Path(src).rglob("*") if p.is_file() and p.name != "pyproject.toml"):
                zf.writestr(zipfile.ZipInfo(path.relative_to(src).as_posix(), (2020, 2, 2, 0, 0, 0)),
                            path.read_bytes())
        return wheel


class FakeImageBuilder:
    name = "fake"

    def __init__(self, present: tuple[str, ...] = ("wynd-base:0.1.0-slim", "wynd-base:0.1.0-alpine")) -> None:
        self.present = set(present)
        self.calls: list[tuple] = []
        self.requests: list[Any] = []

    def build(self, req, log) -> ImageBuildResult:
        self.requests.append(req)
        self.calls.append(("build", req.tags))
        (Path(req.context) / METADATA_FILE).write_text(json.dumps(
            {"containerimage.digest": BUILD_DIGEST, "containerimage.config.digest": CONFIG_DIGEST}))
        self.present.update(req.tags)
        return ImageBuildResult(tags=tuple(req.tags), image_id=CONFIG_DIGEST, digest=BUILD_DIGEST,
                                size_bytes=None if req.push else IMAGE_BYTES)

    def exists(self, ref: str) -> bool:
        return ref in self.present

    def push(self, ref: str, log) -> str:
        self.calls.append(("push", ref))
        return PUSH_DIGEST

    def tag(self, src: str, dst: str) -> None:
        self.calls.append(("tag", src, dst))
        self.present.add(dst)

    def login(self, registry_host: str, username: str, password: str) -> None:
        self.calls.append(("login", registry_host, username, password))

    def native_platform(self) -> str:
        return "linux/arm64"


def build_ctx(ws: Path, runs: Any, registry: Any, inputs: dict[str, Any], *, job_id: str = "job_build_1",
              lines: list[str] | None = None) -> JobContext:
    from wynd.process.git import rev_parse

    head = rev_parse(ws, "HEAD")
    now = datetime.now(UTC)
    job = JobRecord(id=job_id, job_kind="build", process=inputs["process"], ref=head, base_commit=head,
                    inputs=inputs, status="running", runner="inprocess",
                    handler="wynd.process.build.job:run_build_job", created_at=now, updated_at=now)
    state = ws / ".wynd"
    scratch = state / "jobs" / job_id / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    sink = lines if lines is not None else []
    return JobContext(job=job, inputs=inputs, session=None, worktree=ws, workspace=ws, workspace_root=ws,
                      state_dir=state, scratch=scratch, runs=runs, registry=registry, log=sink.append,
                      commit=lambda message, paths=None: None, save_session=lambda session: None)


# --- the gated workspace: deterministic steps only, one agentic branch, a child process -----------------------------

CHECK = "The note is worth keeping."
GATED = {
    "kind": "process", "name": "gated", "entry": "read", "inputs": {"src": "path"}, "outputs": {"n": "integer"},
    "steps": {"read": {"use": "./steps/read"}, "save": {"use": "./steps/save"}, "sub": {"use": "process:helper"}},
    "edges": [
        {"from": "read.done", "kind": "agentic", "to": [
            {"step": "save", "name": "keep", "check": CHECK},
            {"step": "$exit.done", "with": {"n": "0"}},
        ]},
        {"from": "save.done", "to": "sub"},
        {"from": "sub.done", "to": "$exit.done", "with": {"n": "steps.sub.outputs.n"}},
    ],
}
HELPER = {
    "kind": "process", "name": "helper", "entry": "count", "outputs": {"n": "integer"},
    "steps": {"count": {"use": "./steps/count"}},
    "edges": [{"from": "count.done", "to": "$exit.done", "with": {"n": "1"}}],
}
STEP_PY = '"""A step body (never imported by these tests)."""\n'


def gated_files(gated: dict[str, Any] | None = None, *, read_fragment: dict[str, Any] | None = None) -> dict[str, str]:
    lock = EdgesLock(edges={"read.done[keep]": EdgeLockEntry(check_hash=check_hash(CHECK, None), tier="standard")})
    files = {
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/gated/process.yaml": to_yaml(gated or GATED),
        "processes/gated/edges.lock.yaml": dump_lock(lock),
        "processes/helper/process.yaml": to_yaml(HELPER),
        **lock_only("processes/gated/steps/read", fragment=read_fragment or {"deps": ["pypdf>=6,<7"]}),
        **lock_only("processes/gated/steps/save"),
        **lock_only("processes/helper/steps/count"),
    }
    for pkg in ("processes/gated/steps/read", "processes/gated/steps/save", "processes/helper/steps/count"):
        name = pkg.rsplit("/", 1)[1]
        files[f"{pkg}/{name}.py"] = STEP_PY
        files[f"{pkg}/test_{name}.py"] = "def test_nothing():\n    pass\n"
    return files
