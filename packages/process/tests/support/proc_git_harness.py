"""PROC-GIT test helpers: a multi-process workspace, compiled-step files, result branches made the way jobs make
them, and a reference job harness.

- `WORKSPACE` (files) holds processes `alpha` (local steps), `beta` (a local step, root step `shared:net/fetch`, child
  `process:team/gamma`), `team/gamma` (a local step, root step `shared:util`) and `delta` (the child twice plus
  `shared:util`: a diamond); `CLOSURES` are their reference closures.
- `make_branch(ws, branch, files)` commits `files` on a detached worktree forked from `start` and publishes the commit
  as `branch`, exactly as a job's result branch is made.
- `run_job(runs, job_id, handler, ws_root=…)` is a small reference implementation of the PLAN §3.18 harness contract
  (a test double of `wynd.controller.jobs.harness.execute_job`, phase "all") built only from `wynd.process.git`, so
  the job types and git primitives are proven together; `requeue` resubmits the same job as answering does.
"""

from __future__ import annotations

import shutil
import traceback
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from wynd.process import git
from wynd.process.jobs import JobContext, JobOutcome, JobRecord
from wynd.process.workspace import CommitTree, load_workspace
from wynd.spec.hashing import proto_hash
from wynd.spec.proto_step import ProtoStep
from wynd.spec.yamlio import parse_model


def proto_yaml(name: str, instruction: str = "Turn the text into better text.") -> str:
    return (
        f"kind: proto_step\nname: {name}\ninstruction: {instruction}\n"
        "inputs:\n  text: string\noutputs:\n  text: string\n"
    )


def process_yaml(name: str, steps: Mapping[str, str], edges: str = "") -> str:
    lines = ["kind: process", f"name: {name}", f"entry: {next(iter(steps))}", "steps:"]
    lines += [f"  {key}: {{use: '{use}'}}" for key, use in steps.items()]
    return "\n".join(lines) + "\n" + edges


WORKSPACE: dict[str, str] = {
    "wynd.yaml": "process_roots: [processes]\nstep_roots:\n  shared: shared/steps\n",
    "README.md": "A workspace for the git tests.\n",
    "processes/alpha/process.yaml": process_yaml("alpha", {"read": "./steps/read", "write": "./steps/write"}),
    "processes/alpha/proto/read.yaml": proto_yaml("read"),
    "processes/alpha/proto/write.yaml": proto_yaml("write"),
    "processes/beta/process.yaml": process_yaml(
        "beta", {"own": "./steps/own", "fetch": "shared:net/fetch", "sub": "process:team/gamma"}
    ),
    "processes/beta/proto/own.yaml": proto_yaml("own"),
    "processes/team/gamma/process.yaml": process_yaml("gamma", {"calc": "./steps/calc", "tool": "shared:util"}),
    "processes/team/gamma/proto/calc.yaml": proto_yaml("calc"),
    "processes/delta/process.yaml": process_yaml(
        "delta", {"one": "process:team/gamma", "two": "process:team/gamma", "tool": "shared:util"}
    ),
    "shared/steps/net/fetch/proto.yaml": proto_yaml("fetch"),
    "shared/steps/util/proto.yaml": proto_yaml("util"),
    "shared/steps/unused/proto.yaml": proto_yaml("unused"),
}

CLOSURES: dict[str, list[str]] = {
    "alpha": ["processes/alpha", "wynd.yaml"],
    "beta": ["processes/beta", "processes/team/gamma", "shared/steps/net/fetch", "shared/steps/util", "wynd.yaml"],
    "team/gamma": ["processes/team/gamma", "shared/steps/util", "wynd.yaml"],
    "delta": ["processes/delta", "processes/team/gamma", "shared/steps/util", "wynd.yaml"],
}


def compiled_step(pdir: str, name: str, proto_text: str | None = None) -> dict[str, str]:
    """Files of a compiled process-local step `name` under process dir `pdir`, its lock holding the proto's hash."""
    proto_text = proto_text or proto_yaml(name)
    digest = proto_hash(parse_model(proto_text, ProtoStep, f"{name}.yaml"))
    pkg = f"{pdir}/steps/{name}"
    return {
        f"{pdir}/proto/{name}.yaml": proto_text,
        f"{pkg}/pyproject.toml": f'[project]\nname = "{name}"\nversion = "0.1.0"\ndependencies = []\n',
        f"{pkg}/step.lock.yaml": (
            f"wynd: 1\nname: {name}\nkind: deterministic\nentrypoint: {name}:Step\nproto_hash: {digest}\n"
        ),
        f"{pkg}/{name}.py": '"""Never imported by these tests."""\n',
    }


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


def make_branch(
    ws: Path, branch: str, files: Mapping[str, str | None], message: str = "compile result", start: str = "HEAD"
) -> str:
    """Commit `files` (workspace-relative) on a detached worktree at `start` and publish the commit as `branch`."""
    repo = git.toplevel(ws)
    work = repo.parent / "branch-worktrees" / branch.replace("/", "-")
    git.add_worktree(repo, work, git.rev_parse(ws, start))
    try:
        checkout_ws = work / git.prefix(ws)
        write_files(checkout_ws, files)
        sha = git.commit_paths(checkout_ws, message)
    finally:
        git.remove_worktree(repo, work)
    assert sha is not None, "make_branch needs a change to commit"
    git.set_branch(repo, branch, sha)
    return sha


Handler = Callable[[JobContext], JobOutcome]


def run_job(runs: Any, job_id: str, handler: Handler, *, ws_root: Path, registry: Any = None) -> JobRecord:
    """Run queued job `job_id` once: detached worktree at `job.ref` under `.wynd/jobs/<id>/checkout-<attempt>`,
    `ctx.commit` stages the process closure (at the checkout and its HEAD, so deletions count) with trailer
    `Wynd-Job: <id>`, the outcome's commit is published as the result branch, the checkout is removed unless the job
    failed."""
    job = JobRecord.model_validate(runs.get(job_id))
    runs.update(job_id, {"status": "running", "started_at": datetime.now(UTC)})
    repo = git.toplevel(ws_root)
    job_dir = ws_root / ".wynd" / "jobs" / job.id
    worktree = job_dir / f"checkout-{job.attempt}"
    scratch = job_dir / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    git.add_worktree(repo, worktree, job.ref)
    workspace = worktree / job.workspace_rel

    def log(line: str) -> None:
        with (job_dir / "job.log").open("a") as f:
            f.write(f"{datetime.now(UTC):%H:%M:%S} {line}\n")

    def commit(message: str, paths: list[str] | None = None) -> str | None:
        if paths is None:
            now = git.reference_closure(load_workspace(workspace), job.process)
            before = git.reference_closure(load_workspace(workspace, CommitTree(workspace, "HEAD")), job.process)
            paths = sorted({*now, *before})
        return git.commit_paths(workspace, message, paths, trailers={"Wynd-Job": job.id})

    ctx = JobContext(
        job=job, inputs=job.inputs, session=job.session, worktree=worktree, workspace=workspace,
        workspace_root=ws_root, state_dir=ws_root / ".wynd", scratch=scratch, runs=runs, registry=registry,
        log=log, commit=commit, save_session=lambda session: runs.update(job.id, {"session": session}),
    )
    try:
        outcome = handler(ctx)
    except Exception as err:
        log(traceback.format_exc())
        patch: dict[str, Any] = {"status": "failed", "error": {"message": f"{type(err).__name__}: {err}"}}
    else:
        patch = {
            "status": outcome.status, "artefacts": {**job.artefacts, **outcome.artefacts}, "report": outcome.report,
            "session": outcome.session, "questions": outcome.questions, "usage": outcome.usage.model_dump(),
            "error": {"message": outcome.error} if outcome.error else None,
        }
        if outcome.commit:
            branch = git.result_branch(job.job_kind, job.process, job.id)
            git.set_branch(repo, branch, outcome.commit)
            patch |= {"result_branch": branch, "result_commit": outcome.commit}
    if patch["status"] != "failed":
        git.remove_worktree(repo, worktree)
    return JobRecord.model_validate(runs.update(job.id, {**patch, "finished_at": datetime.now(UTC)}))


def requeue(runs: Any, job_id: str, ref: str, inputs: Mapping[str, Any]) -> None:
    """Answering resubmits the same job: queued again at `ref` with the merged inputs, next attempt."""
    job = JobRecord.model_validate(runs.get(job_id))
    runs.update(job_id, {"status": "queued", "ref": ref, "inputs": dict(inputs), "attempt": job.attempt + 1})
