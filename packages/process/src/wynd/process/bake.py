"""Bake a process into a single executable (PLAN §6.1, §15 item 47; owner PROC-BAKE, M2; `$DRAFTS/04 §8.9`).

stdlib zipapp + extract-once bootstrap (`templates/bake_main.py`); bakeable iff no `system:` packages, no shell steps
and one resolvable environment. Shares the build prepare phase, so a bake is commit-pinned and test-gated: the bake
reads the build context `prepare_build_job` wrote (`process.lock.yaml`, `process.env.yaml`, `dist/`).

Archive layout:
```
site/                        uv pip install --target site --no-deps <one resolved environment> <runtime> dist/*.whl
_wynd_bake/plan.json         the lock's image-mode RunPlan with every step in the single venv "bake"
_wynd_bake/process.env.yaml  the env manifest
_wynd_bake/meta.json         {"id": sha256(of the above)[:16], "process", "commit", "platform", "wynd_version"}
__main__.py                  templates/bake_main.py
```
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sysconfig
import zipapp
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from wynd.spec.env_manifest import EnvManifest
from wynd.spec.lockfiles import ProcessLock, load_process_lock
from wynd.spec.plan import PlanVenv, RunPlan
from wynd.spec.workspace import ENV_MANIFEST_FILE, PROCESS_LOCK_FILE, slug
from wynd.spec.yamlio import load_model

from . import _proc
from .errors import ResolutionError, WyndProcessError
from .venvs import PYTHON, RuntimeSource, detect_runtime_source

if TYPE_CHECKING:
    from collections.abc import Callable

    from .build.job import Prepared
    from .build.resolve import Resolver
    from .jobs import JobContext, JobOutcome

BAKE_VENV = "bake"
BAKE_DIR = "_wynd_bake"
INTERPRETER = f"/usr/bin/env python{PYTHON}"
TEMPLATE = Path(__file__).parent / "templates" / "bake_main.py"
RUNTIME_DISTS = frozenset({"wynd-spec", "wynd-runtime"})
_NAME = re.compile(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)")


class NotBakeable(WyndProcessError):
    """The process needs an image: system packages, shell steps, or dependency sets that do not share one
    environment."""


def run_bake_job(ctx: JobContext) -> JobOutcome:
    """Job handler `bake` (inputs `{process}`): the build prepare phase, then `bake_process`, then the `.pyz` joins
    the build of that commit in the artefact store (`BuildInfo.bake`)."""
    from .build.job import prepare_build_job
    from .jobs import JobOutcome

    try:
        prepared = prepare_build_job(replace(ctx, inputs={"process": ctx.inputs["process"], "registry": None,
                                                          "push": False}))
        pyz = bake_process(ctx, prepared)
    except WyndProcessError as err:                   # validation, design phase, failing tests, not bakeable
        report = getattr(err, "report", None)         # the build's failure report (test/validation JSON), if any
        return JobOutcome(status="failed", error=str(err), report=report if isinstance(report, dict) else None)
    context = build_context_dir(ctx)
    lock = load_process_lock(context / PROCESS_LOCK_FILE)
    path = store_bake(ctx, lock, context, pyz)
    ctx.log(f"baked {lock.process} at {lock.commit[:12]}: {path}")
    return JobOutcome(status="succeeded",
                      artefacts={"path": str(path), "size_bytes": path.stat().st_size, "commit": lock.commit})


def bake_process(
    ctx: JobContext,
    prepared: Prepared,
    *,
    resolver: Resolver | None = None,
    runtime: RuntimeSource | None = None,
) -> Path:
    """Build `<ctx.scratch>/bake/<slug(pid)>.pyz` from the prepared build context; `NotBakeable` when the process
    needs an image."""
    if resolver is None:
        from .build.resolve import UvResolver

        resolver = UvResolver()
    runtime = runtime or detect_runtime_source(os.environ)
    context = build_context_dir(ctx)
    lock = load_process_lock(context / PROCESS_LOCK_FILE)
    check_bakeable(lock)

    out = Path(ctx.scratch) / "bake"
    shutil.rmtree(out, ignore_errors=True)
    stage = out / "stage"
    (stage / BAKE_DIR).mkdir(parents=True)
    install_site(stage / "site", lock, context, resolver=resolver, runtime=runtime, log=ctx.log)
    (stage / BAKE_DIR / "plan.json").write_text(bake_plan(lock.plan).model_dump_json(indent=2) + "\n")
    shutil.copyfile(context / ENV_MANIFEST_FILE, stage / BAKE_DIR / ENV_MANIFEST_FILE)
    meta = {"id": tree_digest(stage)[:16], "process": lock.process, "commit": lock.commit,
            "platform": sysconfig.get_platform(), "wynd_version": lock.runtime_version}
    (stage / BAKE_DIR / "meta.json").write_text(json.dumps(meta, indent=2) + "\n")
    shutil.copyfile(TEMPLATE, stage / "__main__.py")

    target = out / f"{slug(lock.process)}.pyz"
    zipapp.create_archive(stage, target, interpreter=INTERPRETER, compressed=True)
    ctx.log(f"wrote {target.name} ({target.stat().st_size} bytes, id {meta['id']})")
    return target


def build_context_dir(ctx: JobContext) -> Path:
    """Where the prepare phase wrote the build context (PLAN §6.5)."""
    return Path(os.environ.get("WYND_BUILD_CONTEXT_DIR") or Path(ctx.scratch) / "ctx")


def check_bakeable(lock: ProcessLock) -> None:
    if lock.system_packages:
        raise NotBakeable(f"not bakeable: process {lock.process} needs system packages "
                          f"({', '.join(lock.system_packages)}); use wynd build")
    shell = sorted(sid for sid, step in lock.plan.steps.items() if step.kind == "shell")
    if shell:
        raise NotBakeable(f"not bakeable: shell steps need an image ({', '.join(shell)}); use wynd build")


def install_site(
    site: Path,
    lock: ProcessLock,
    context: Path,
    *,
    resolver: Resolver,
    runtime: RuntimeSource,
    log: Callable[[str], None] | None = None,
) -> None:
    """Resolve the union of every venv's requirements plus the runtime as one environment for this host, then install
    it, the runtime and the step wheels into `site` without further resolution."""
    runtime_reqs = runtime_requirements(runtime)
    wanted = sorted({req for venv in lock.venvs for req in venv.requirements if not is_runtime(req)})
    try:
        pins = resolver.compile([*wanted, *runtime_reqs], universal=False)
    except ResolutionError as err:
        raise NotBakeable(f"not bakeable: steps' dependency sets conflict ({err}); use wynd build") from None
    work = site.parent.parent                                  # outside the staged archive tree
    requirements = work / "requirements.txt"
    requirements.write_text("".join(f"{pin}\n" for pin in pins if not is_runtime(pin)))
    wheels = [str(context / wheel.wheel) for _, wheel in sorted(lock.wheels.items())]
    uv = _proc.require_tool("uv")
    _proc.run([uv, "pip", "install", "--target", str(site), "--python", PYTHON, "--no-deps",
               "-r", str(requirements), *runtime_reqs, *wheels], cwd=work, log=log)


def runtime_requirements(runtime: RuntimeSource) -> list[str]:
    """wynd-spec and wynd-runtime as installable (never editable) requirements."""
    if runtime.editable is None:
        return [f"wynd-spec=={runtime.version}", f"wynd-runtime=={runtime.version}"]
    spec, rt = runtime.editable
    return [f"wynd-spec @ {Path(spec).absolute().as_uri()}", f"wynd-runtime @ {Path(rt).absolute().as_uri()}"]


def is_runtime(requirement: str) -> bool:
    """A wynd-spec/wynd-runtime pin (in any form uv prints, incl. an editable workspace line)."""
    if requirement.lstrip().startswith("-e"):
        return True
    match = _NAME.match(requirement)
    return match is not None and re.sub(r"[-_.]+", "-", match.group(1)).lower() in RUNTIME_DISTS


def bake_plan(plan: RunPlan) -> RunPlan:
    """The image-mode plan with every step (and agentic edge) served by the single venv "bake"; `main` sets its
    interpreter to the running one."""
    steps = {sid: step.model_copy(update={"venv": BAKE_VENV}) for sid, step in plan.steps.items()}
    return plan.model_copy(update={
        "venv_root": "",
        "venvs": [PlanVenv(id=BAKE_VENV, steps=sorted(steps))],
        "steps": steps,
        "edge_venvs": {key: BAKE_VENV for key in plan.edge_venvs},
    })


def tree_digest(root: Path) -> str:
    """sha256 hex over the sorted relative paths and contents of every file under `root`."""
    digest = hashlib.sha256()
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest())
    return digest.hexdigest()


def store_bake(ctx: JobContext, lock: ProcessLock, context: Path, pyz: Path) -> Path:
    """Put the `.pyz` into the artefact store's build of `lock.commit`; an existing build there (an image) keeps its
    files and image fields. `BuildInfo.bake` is the file name inside the build dir; returns its absolute path."""
    from .artefacts import BUILD_FILE, BuildInfo, open_artefact_store

    store = open_artefact_store(ctx.state_dir, os.environ)
    existing = store.get_build(lock.process, lock.commit)
    staged = Path(ctx.scratch) / "bake" / "build"
    shutil.copytree(context, staged)
    if existing is not None:
        for item in store.local_dir(lock.process, lock.commit).iterdir():
            if item.name == BUILD_FILE or (staged / item.name).exists():
                continue
            if item.is_dir():
                shutil.copytree(item, staged / item.name)
            else:
                shutil.copy2(item, staged / item.name)
    shutil.copy2(pyz, staged / pyz.name)
    if existing is not None:
        info = existing.model_copy(update={"bake": pyz.name})
    else:
        info = BuildInfo(
            process=lock.process, commit=lock.commit, source_sha=lock.source_sha, job_id=ctx.job.id,
            process_hash=lock.process_hash, image=None, image_id=None, image_digest=None,
            base=lock.base.model_dump(mode="json"), manifest=load_model(context / ENV_MANIFEST_FILE, EnvManifest),
            bake=pyz.name, created_at=datetime.now(UTC), dir="",
        )
    stored = store.put_build(staged, info)
    return Path(stored.dir) / pyz.name
