"""Offline doubles for compiler, controller, CLI and web tests (`$DRAFTS/05 §13.5, §16.2`).

Scripts are keyed by `(kind, node, n)`: the n-th call with a given (kind, node) gets the n-th entry, so they survive
prompt wording changes and break only when the pipeline's call sequence changes. A response's special key
`module_source_file` is resolved relative to the script file.

Script format (YAML or the loaded mapping):

    calls:
      - kind: write_deterministic
        node: parse
        response: {module_source_file: ../code/parse_amount_v1.py, deps: [], ...}
        usage: {input_tokens: 10}          # optional; default: one call, zero tokens
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar

import yaml

from wynd.process.jobs import JobContext, JobOutcome, JobRecord, new_job_record
from wynd.runtime.usage import Usage

if TYPE_CHECKING:
    from wynd.compiler.calls import CallKind
    from wynd.compiler.llm import CompilerLLM, LLMResult, Thinking, Tier
    from wynd.runtime.storage.base import Registry, RunRegistry

T = TypeVar("T")

HANDLERS = {"compile": "wynd.compiler.jobs:run_compile_job", "test_live": "wynd.process.testing:run_test_live_job"}


class ScriptMiss(Exception):
    """No scripted response for this call; names exactly which one is missing."""

    def __init__(self, kind: str, node: str, n: int, prompt_head: str):
        super().__init__(f"no scripted response for call {n} of ({kind}, {node}); prompt starts: {prompt_head}")
        self.kind = kind
        self.node = node
        self.n = n
        self.prompt_head = prompt_head


@dataclass(frozen=True)
class ScriptedCall:
    """One call a ScriptedLLM answered (tests assert on prompts and on the absence of repeated calls)."""
    kind: str
    node: str
    n: int
    system: str
    prompt: str
    tier: str
    thinking: str


class ScriptedLLM:
    """Implements CompilerLLM from a script (a YAML file path or the loaded mapping)."""

    def __init__(self, script: Path | dict):
        match script:
            case Path() | str():
                self.base = Path(script).parent
                data = yaml.safe_load(Path(script).read_text(encoding="utf-8")) or {}
            case dict():
                self.base = Path.cwd()
                data = script
        self.script = script
        self.entries: dict[tuple[str, str], list[dict[str, Any]]] = {}
        for entry in data.get("calls", []):
            self.entries.setdefault((entry["kind"], entry["node"]), []).append(entry)
        self.calls: list[ScriptedCall] = []

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        from wynd.compiler.llm import LLMResult

        n = 1 + sum(1 for c in self.calls if c.kind == kind and c.node == node)
        entries = self.entries.get((kind, node), [])
        if n > len(entries):
            raise ScriptMiss(kind, node, n, prompt[:200])
        self.calls.append(ScriptedCall(kind, node, n, system, prompt, str(tier), thinking))
        entry = entries[n - 1]
        response = dict(entry["response"])
        source_file = response.pop("module_source_file", None)
        if source_file is not None:
            response["module_source"] = (self.base / source_file).read_text(encoding="utf-8")
        usage = Usage.model_validate({"calls": 1, **(entry.get("usage") or {})})
        return LLMResult(response_model.model_validate(response), usage, memo_hit=False)

    def prompts(self, kind: str | None = None, node: str | None = None) -> list[str]:
        """The prompts of the answered calls, optionally filtered by kind and node."""
        return [c.prompt for c in self.calls if kind in (None, c.kind) and node in (None, c.node)]


class RecordingLLM:
    """Wraps a real CompilerLLM and writes every call as a script entry to `out` (live tests refresh the offline
    scripts with it)."""

    def __init__(self, inner: CompilerLLM, out: Path):
        self.inner = inner
        self.out = out
        self.recorded: list[dict[str, Any]] = []

    def call(self, kind: CallKind, *, node: str, system: str, prompt: str,
             response_model: type[T], tier: Tier, thinking: Thinking) -> LLMResult[T]:
        result = self.inner.call(kind, node=node, system=system, prompt=prompt, response_model=response_model,
                                 tier=tier, thinking=thinking)
        self.recorded.append({"kind": kind, "node": node, "response": result.value.model_dump(mode="json")})
        self.out.parent.mkdir(parents=True, exist_ok=True)
        text = yaml.safe_dump({"calls": self.recorded}, sort_keys=False, allow_unicode=True, width=120)
        self.out.write_text(text, encoding="utf-8")       # rewritten after every call: a partial run is kept
        return result


class FakeJobContext(JobContext):
    """A `wynd.process.jobs.JobContext` over a temporary git repo: a real `git worktree` under
    `<ws_root>/.wynd/jobs/<id>/checkout-<attempt>`, detached at the job's ref, with `commit` given the harness's
    semantics (PLAN §3.18): stage the reference closure (incl. deletions), trailer `Wynd-Job: <id>`.

    The job record is created in `runs` (default: a `FileRunRegistry` under `<ws_root>/.wynd/registry`). `finish`
    folds a handler's `JobOutcome` into the record like the harness (publishing `outcome.commit` as the result
    branch); `requeue` answers like `JobService.answer`: the same job, attempt + 1, checked out at the result commit,
    with the saved session and cumulative answers.
    """

    def __init__(self, ws_root: Path, process: str, *, inputs: Mapping[str, Any] | None = None, ref: str = "HEAD",
                 kind: str = "compile", registry: Registry | None = None, runs: RunRegistry | None = None):
        from wynd.process import git
        from wynd.runtime.storage.local import FileRegistry, FileRunRegistry

        ws_root = Path(ws_root).absolute()
        state_dir = ws_root / ".wynd"
        runs = runs if runs is not None else FileRunRegistry(state_dir / "registry")
        registry = registry if registry is not None else FileRegistry(state_dir / "home")
        all_inputs = {"process": process, **(inputs or {})}
        all_inputs.setdefault("target_branch", git.current_branch(ws_root) or "main")
        record = new_job_record(kind, ref, all_inputs, ws_root=ws_root, runner="fake",
                                handler=HANDLERS.get(kind, ""))
        runs.create(record.model_dump(mode="json"))
        self._attach(record, ws_root, runs, registry)

    def _attach(self, record: JobRecord, ws_root: Path, runs: RunRegistry, registry: Registry) -> None:
        from wynd.process import git

        state_dir = ws_root / ".wynd"
        job_dir = state_dir / "jobs" / record.id
        worktree = job_dir / f"checkout-{record.attempt}"
        repo = git.toplevel(ws_root)
        if worktree.exists():
            git.remove_worktree(repo, worktree)
        git.add_worktree(repo, worktree, record.ref)
        scratch = job_dir / "scratch"
        scratch.mkdir(parents=True, exist_ok=True)
        workspace = worktree / record.workspace_rel
        self.lines: list[str] = []
        self.sessions: list[dict] = []
        log_path = job_dir / "job.log"

        def log(text: str) -> None:
            self.lines.append(text)
            with open(log_path, "a", encoding="utf-8") as f:
                f.write(f"{datetime.now(UTC):%H:%M:%S} {text}\n")

        def commit(message: str, paths: Sequence[str] | None = None) -> str | None:
            if paths is None:
                paths = _closure_paths(workspace, record.process)
            return git.commit_paths(workspace, message, paths, trailers={"Wynd-Job": record.id})

        def save_session(session: dict) -> None:
            self.sessions.append(session)
            runs.update(record.id, {"session": session})

        JobContext.__init__(
            self, job=record, inputs=dict(record.inputs), session=record.session, worktree=worktree,
            workspace=workspace, workspace_root=ws_root, state_dir=state_dir, scratch=scratch, runs=runs,
            registry=registry, log=log, commit=commit, save_session=save_session,
        )

    def record(self) -> JobRecord:
        """The job record as stored now."""
        return JobRecord.model_validate(self.runs.get(self.job.id))

    def finish(self, outcome: JobOutcome) -> JobRecord:
        """Fold `outcome` into the record like the harness: status, report, questions, session (when given), result
        branch at `outcome.commit`; the checkout is removed unless the job failed."""
        from wynd.process import git

        fields: dict[str, Any] = {
            "status": outcome.status,
            "artefacts": {**self.job.artefacts, **outcome.artefacts},
            "report": outcome.report,
            "questions": outcome.questions,
            "error": {"message": outcome.error, "detail": None} if outcome.error else None,
            "finished_at": datetime.now(UTC),
        }
        if outcome.session is not None:
            fields["session"] = outcome.session
        if outcome.commit:
            branch = git.result_branch(self.job.job_kind, self.job.process, self.job.id)
            git.set_branch(self.worktree, branch, outcome.commit)
            fields |= {"result_branch": branch, "result_commit": outcome.commit}
        record = JobRecord.model_validate(self.runs.update(self.job.id, fields))
        if outcome.status != "failed":
            git.remove_worktree(git.toplevel(self.workspace_root), self.worktree)
        return record

    def requeue(self, answers: Mapping[str, str] | None = None, **inputs: Any) -> FakeJobContext:
        """The same job requeued (PLAN §3.18 "Answering"): attempt + 1, checkout at `result_commit or ref`, the
        stored session, `inputs["answers"]` merged cumulatively, other `inputs` overriding."""
        current = self.record()
        merged = {**current.inputs, **inputs,
                  "answers": {**current.inputs.get("answers", {}), **(answers or {})}}
        record = JobRecord.model_validate(self.runs.update(current.id, {
            "status": "queued", "attempt": current.attempt + 1, "ref": current.result_commit or current.ref,
            "inputs": merged,
        }))
        ctx = FakeJobContext.__new__(FakeJobContext)
        ctx._attach(record, self.workspace_root, self.runs, self.registry)
        return ctx


def _closure_paths(workspace: Path, pid: str) -> list[str]:
    """The reference closure of `pid` in the checkout's working tree and at its HEAD, so deletions are staged too
    (the harness's rule)."""
    from wynd.process import git
    from wynd.process.errors import WyndProcessError
    from wynd.process.workspace import CommitTree, load_workspace

    paths: set[str] = set()
    for at_head in (False, True):
        try:
            tree = CommitTree(workspace, "HEAD") if at_head else None
            paths.update(git.reference_closure(load_workspace(workspace, tree), pid))
        except WyndProcessError:
            continue
    return sorted(paths)
