"""The build job, split into prepare and finalize for Kubernetes (PLAN §6.5; owner PROC-BUILD, M2).

`prepare_build_job` validates, checks the design phase, computes closure-HEAD `C`, gates on passing tests at `C`,
chooses the base, assigns edge venvs, resolves host-side, builds step wheels, writes the env manifest,
`process.lock.yaml`, the Dockerfile and `image.ref` into the build context (`ctx.scratch/"ctx"` or
`$WYND_BUILD_CONTEXT_DIR`). `run_build_job` = prepare -> `open_image_builder().build(...)` -> finalize (digest,
optional push, `artefacts.put_build`, outcome artefacts). `finalize_build_job` is the phased finalize: an external
BuildKit has built (and pushed) `image.ref` and left `buildkit-metadata.json` in the context.

Job inputs (as sent by `JobService.submit_build`, `$DRAFTS/06 §5.8`): `{process, registry: <image registry name> | None,
push: bool, platform?: str}`.
"""

from __future__ import annotations

import json
import os
import platform as host
import shutil
from collections.abc import Callable, Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from wynd.spec.env_manifest import EnvManifest
from wynd.spec.lockfiles import BaseChoice, FragmentRecord, LockedWheel, branch_key, dump_lock
from wynd.spec.plan import PlanVenv
from wynd.spec.workspace import ENV_MANIFEST_FILE, PROCESS_LOCK_FILE, slug
from wynd.spec.yamlio import load_model

from .. import git
from ..errors import DesignPhase, ResolutionError, WyndProcessError
from ..fragments import merge_process_fragments, provider_fragment, requirement_set
from ..hashing import process_hash, step_hash
from ..jobs import JobOutcome
from ..venvs import VenvGroup, detect_runtime_source, group_key, venv_groups
from .dockerfile import manifest_label, render_dockerfile
from .imagebuilder import METADATA_FILE, ImageBuildRequest, open_image_builder
from .lockfile import make_process_lock
from .resolve import UvResolver
from .variant import choose_base
from .wheels import build_step_wheel

if TYPE_CHECKING:
    from wynd.spec.plan import RunPlan

    from ..jobs import JobContext
    from ..loader import LoadedProcess
    from ..validation import ValidationReport
    from ..venvs import RuntimeSource
    from ..workspace import Workspace
    from .resolve import Resolver

IMAGE_VENV_ROOT = "/opt/wynd/venvs"
IMAGE_REF_FILE = "image.ref"
CONTEXT_ONLY = (IMAGE_REF_FILE, METADATA_FILE)       # in the build context, not in the stored build dir
HOST_PLATFORMS = {"x86_64": "linux/amd64", "amd64": "linux/amd64", "arm64": "linux/arm64", "aarch64": "linux/arm64"}


class Prepared(BaseModel):
    """Result of the prepare phase, stored as `JobRecord.artefacts["prepared"]` between phases."""
    process: str
    commit: str                                   # closure HEAD `C`: keys the build, the tag and the test gate
    source_sha: str                               # commit checked out by the job (same closure content)
    process_hash: str
    context_dir: str                              # the complete Docker build context
    image: str                                    # local tag wynd/<slug(pid)>:<C[:12]>
    image_ref: str                                # full target reference (the registry ref when pushing)
    registry: str | None = None                   # image registry name when pushing
    push: bool = False
    platform: str
    base: BaseChoice
    system_packages: list[str] = []
    fragments: list[FragmentRecord] = []
    tests: dict[str, Any]                         # {passed, failed, total, source: "ran"|"registry"}


class BuildFailed(WyndProcessError):
    """The build cannot proceed; `report` (validation or test report JSON) is attached to the job."""

    def __init__(self, message: str, report: dict[str, Any] | None = None) -> None:
        self.report = report
        super().__init__(message)


def prepare_build_job(ctx: JobContext) -> Prepared:
    pid = ctx.inputs["process"]
    registry_name = ctx.inputs.get("registry")
    push = bool(ctx.inputs.get("push")) and registry_name is not None
    if ctx.inputs.get("push") and registry_name is None:
        raise BuildFailed("push requested without an image registry")
    entry = _registry_entry(ctx, registry_name) if push else None

    from ..validation import validate
    from ..workspace import load_workspace

    ws = load_workspace(ctx.workspace)
    lp = ws.load_process(pid)
    report = validate(lp)
    if not report.ok:
        errors = [d for d in report.diagnostics if d.severity == "error"]
        raise BuildFailed(f"process '{pid}' failed validation: {len(errors)} error(s); first: {errors[0].format()}",
                          report=report.model_dump(mode="json"))
    try:
        env = merge_process_fragments(lp)
    except DesignPhase as err:
        raise BuildFailed(f"{err} (run wynd compile {pid})") from None

    commit = git.closure_head(ctx.workspace, git.reference_closure(ws, pid))
    if commit is None:
        raise BuildFailed(f"process '{pid}' has no commit")
    source_sha = git.rev_parse(ctx.workspace, "HEAD")
    phash = process_hash(ws.tree, lp)
    tests = _test_gate(ctx, ws, pid, commit, phash)

    runtime = detect_runtime_source(os.environ)
    resolver = UvResolver()
    platform = ctx.inputs.get("platform") or native_platform()
    context = Path(os.environ.get("WYND_BUILD_CONTEXT_DIR") or ctx.scratch / "ctx")
    if context.exists():
        shutil.rmtree(context)
    (context / "venvs").mkdir(parents=True)
    (context / "dist").mkdir()
    find_links = runtime_find_links(runtime, ctx.scratch / "runtime-wheels", resolver, ctx.log)

    plan, groups = image_plan(lp, report, venv_groups(env), ws.root)
    base = choose_base(lp.doc.env.base, env, groups, resolver=resolver, platform=platform, version=runtime.version,
                       find_links=find_links)
    if base.reason:
        ctx.log(base.reason)
    pins = _resolve(groups, resolver, runtime.version, find_links, ctx.log)
    for key, pinned in pins.items():
        (context / "venvs" / key).mkdir()
        (context / "venvs" / key / "requirements.txt").write_text("".join(f"{pin}\n" for pin in pinned))

    wheels: dict[str, LockedWheel] = {}
    for sid, pkg in sorted(lp.closure_packages().items()):
        ctx.log(f"building the wheel of {sid}")
        wheel = build_step_wheel(ws.tree, pkg, pkg.lock, out=context / "dist", resolver=resolver,
                                 stage_root=ctx.scratch / "wheels")
        wheels[sid] = LockedWheel(dir=pkg.dir, hash=step_hash(ws.tree, pkg), wheel=f"dist/{wheel.name}")

    from ..envmanifest import assemble_env_manifest

    manifest = assemble_env_manifest(ws, pid, ctx.registry, commit=commit)
    (context / ENV_MANIFEST_FILE).write_text(dump_lock(manifest))

    image = f"wynd/{slug(pid)}:{commit[:12]}"
    prepared = Prepared(
        process=pid, commit=commit, source_sha=source_sha, process_hash=phash, context_dir=str(context),
        image=image, image_ref=f"{entry.url}/{slug(pid)}:{commit[:12]}" if entry is not None else image,
        registry=registry_name if push else None, push=push, platform=platform, base=base,
        system_packages=list(env.system), fragments=list(env.fragments), tests=tests,
    )
    lock = make_process_lock(prepared, base, platform, groups, pins, wheels, plan=plan)
    (context / PROCESS_LOCK_FILE).write_text(dump_lock(lock))
    (context / "Dockerfile").write_text(render_dockerfile(lock) + manifest_label(manifest))
    (context / IMAGE_REF_FILE).write_text(prepared.image_ref + "\n")
    ctx.log(f"prepared the build context of {prepared.image_ref} in {context}")
    return prepared


def finalize_build_job(ctx: JobContext) -> JobOutcome:
    """Phased finalize: the external builder built `image.ref` (pushing it when `prepared.push`)."""
    prepared = Prepared.model_validate(ctx.job.artefacts["prepared"])
    meta = _metadata(Path(prepared.context_dir))
    digest = meta.get("containerimage.digest")
    pushed = [f"{prepared.image_ref}@{digest}"] if prepared.push and digest else []
    return _finalize(ctx, prepared, image_id=meta.get("containerimage.config.digest"), digest=digest, pushed=pushed,
                     image_bytes=None)


def run_build_job(ctx: JobContext) -> JobOutcome:
    try:
        prepared = prepare_build_job(ctx)
    except BuildFailed as err:
        return JobOutcome(status="failed", error=str(err), report=err.report)
    builder = open_image_builder(os.environ)
    from ..base import ensure_base, registry_login

    ensure_base(prepared.base.image, prepared.base.version, prepared.base.variant, image_builder=builder, log=ctx.log)
    context = Path(prepared.context_dir)
    result = builder.build(ImageBuildRequest(context=context, dockerfile=context / "Dockerfile",
                                             tags=(prepared.image,), platforms=(prepared.platform,)), ctx.log)
    digest, pushed = result.digest, []
    if prepared.push:
        registry_login(builder, _registry_entry(ctx, prepared.registry), os.environ)
        builder.tag(prepared.image, prepared.image_ref)
        digest = builder.push(prepared.image_ref, ctx.log)
        pushed = [f"{prepared.image_ref}@{digest}"]
    return _finalize(ctx, prepared, image_id=result.image_id, digest=digest, pushed=pushed,
                     image_bytes=result.size_bytes)


def native_platform() -> str:
    """The host's `linux/<arch>`: images run on the builder's architecture unless the job names a platform."""
    machine = host.machine().lower()
    if machine not in HOST_PLATFORMS:
        raise BuildFailed(f"cannot tell the image platform of a {machine} host; pass a platform")
    return HOST_PLATFORMS[machine]


def runtime_find_links(
    runtime: RuntimeSource, dest: Path, resolver: Resolver, log: Callable[[str], None]
) -> list[Path]:
    """Editable runtime: wheels of `packages/spec` and `packages/runtime` built into `dest`, so `wynd-runtime==V`
    resolves before it is published (the image installs the base's vendored copies). Pinned runtime: none."""
    if runtime.editable is None:
        return []
    for source in runtime.editable:
        log(f"building the wheel of {source.name} for resolution")
        resolver.build_wheel(source, dest)
    return [dest]


def image_plan(
    lp: LoadedProcess, report: ValidationReport, groups: Sequence[VenvGroup], ws_root: Path
) -> tuple[RunPlan, list[VenvGroup]]:
    """The image-mode plan (venv id = group key) with the §6.4 edge venvs added, and every venv's group (the edge
    venvs' groups hold their provider's deps)."""
    from ..plan import assign_edge_venvs, build_plan

    keyed = [PlanVenv(id=group.key, steps=list(group.steps)) for group in groups]
    plan = build_plan(lp, report, mode="image", venv_root=IMAGE_VENV_ROOT, venvs=keyed, ws_root=ws_root)
    edge_venvs, extra = assign_edge_venvs(plan)
    plan = plan.model_copy(update={"venvs": [*plan.venvs, *extra], "edge_venvs": edge_venvs})
    known = {group.key: group for group in groups}
    for requirements in _edge_requirements(plan):
        known.setdefault(group_key(requirements), VenvGroup(group_key(requirements), requirements, ()))
    return plan, [known[venv.id] for venv in plan.venvs]


def _edge_requirements(plan: RunPlan) -> list[tuple[str, ...]]:
    """The requirement set of each agentic branch's provider (`EdgeLockEntry.provider or plan.provider`)."""
    sets = []
    for process in plan.processes.values():
        for edge in process.definition.edges:
            for index, branch in enumerate(edge.to):
                if branch.check is None:
                    continue
                entry = process.edges_lock.edges.get(branch_key(edge.from_, index, branch.name))
                fragment = provider_fragment((entry.provider if entry is not None else None) or plan.provider)
                sets.append(requirement_set(fragment.deps if fragment is not None else []))
    return sets


def _test_gate(ctx: JobContext, ws: Workspace, pid: str, commit: str, phash: str) -> dict[str, Any]:
    """A passing result recorded for `C` is reused; otherwise the replay tests run (and are recorded)."""
    recorded = ctx.runs.get_test_result(commit, f"process:{pid}:{phash}")
    if recorded is not None and recorded.passed:
        ctx.log(f"tests passed on {commit[:12]} (recorded result)")
        return _counts(recorded.counts, "registry")
    from ..testing import run_tests

    ctx.log(f"running the replay tests on {commit[:12]}")
    report = run_tests(ws, pid, mode="replay", commit=commit, runs=ctx.runs, venv_root=ctx.state_dir / "venvs",
                       scratch=ctx.scratch / "tests", log=ctx.log)
    counts = {key: sum(suite.counts.get(key, 0) for suite in report.suites)
              for key in ("passed", "failed", "error", "skipped")}
    if not report.passed:
        raise BuildFailed(f"tests fail on {commit}", report=report.model_dump(mode="json"))
    return _counts(counts, "ran")


def _counts(counts: dict[str, int], source: str) -> dict[str, Any]:
    failed = counts.get("failed", 0) + counts.get("error", 0)
    return {"passed": counts.get("passed", 0), "failed": failed, "total": sum(counts.values()), "source": source}


def _resolve(
    groups: Sequence[VenvGroup], resolver: Resolver, version: str, find_links: Sequence[Path],
    log: Callable[[str], None],
) -> dict[str, list[str]]:
    pins = {}
    for group in groups:
        log(f"resolving venv {group.key} ({', '.join(group.requirements) or 'runtime only'})")
        try:
            pins[group.key] = resolver.compile(
                [*group.requirements, f"wynd-spec=={version}", f"wynd-runtime=={version}"], universal=True,
                python_version="3.12", find_links=find_links)
        except ResolutionError as err:
            tail = "\n".join(str(err).strip().splitlines()[-5:])
            steps = ", ".join(group.steps) or "none: an agentic edge's provider"
            raise BuildFailed(f"cannot resolve venv {group.key} (steps {steps}): {tail}") from None
    return pins


def _registry_entry(ctx: JobContext, name: str | None):
    from ..base import image_registry

    try:
        return image_registry(ctx.registry, name)
    except WyndProcessError as err:
        raise BuildFailed(str(err)) from None


def _metadata(context: Path) -> dict[str, Any]:
    path = context / METADATA_FILE
    return json.loads(path.read_text()) if path.is_file() else {}


def _finalize(
    ctx: JobContext, prepared: Prepared, *, image_id: str | None, digest: str | None, pushed: list[str],
    image_bytes: int | None,
) -> JobOutcome:
    """Store the build (the context minus `image.ref`/metadata) under `.wynd/build/<pid>/<C>/` and report it."""
    from ..artefacts import BuildInfo, open_artefact_store

    context = Path(prepared.context_dir)
    staging = ctx.state_dir / "tmp" / f"build-{ctx.job.id}"
    if staging.exists():
        shutil.rmtree(staging)
    staging.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(context, staging, ignore=shutil.ignore_patterns(*CONTEXT_ONLY))
    manifest = load_model(staging / ENV_MANIFEST_FILE, EnvManifest)
    info = BuildInfo(
        process=prepared.process, commit=prepared.commit, source_sha=prepared.source_sha, job_id=ctx.job.id,
        process_hash=prepared.process_hash, image=prepared.image_ref, image_id=image_id, image_digest=digest,
        pushed=pushed, base=prepared.base.model_dump(mode="json"), manifest=manifest,
        created_at=datetime.now(UTC), dir="",
    )
    stored = open_artefact_store(ctx.state_dir, os.environ).put_build(staging, info)
    build_dir = Path(stored.dir)
    sizes = {
        "build_dir_bytes": _size(build_dir.rglob("*")),
        "image_bytes": image_bytes,
        "wheels_bytes": _size((build_dir / "dist").glob("*.whl")),
    }
    ctx.log(f"built {prepared.image_ref}" + (f" ({digest})" if digest else ""))
    return JobOutcome(
        status="succeeded",
        artefacts={"commit": prepared.commit, "image": prepared.image_ref, "image_digest": digest,
                   "build_dir": str(build_dir), "pushed": bool(pushed), "tests": prepared.tests, "sizes": sizes},
        report={"commit": prepared.commit, "source_sha": prepared.source_sha, "image": prepared.image_ref,
                "image_id": image_id, "image_digest": digest, "pushed": pushed,
                "base": prepared.base.model_dump(mode="json"), "platform": prepared.platform},
    )


def _size(paths: Iterable[Path]) -> int:
    return sum(path.stat().st_size for path in paths if path.is_file())
