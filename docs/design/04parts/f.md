## 12. Jobs: `JobRunner` interface, records, handlers, session hook (§6.6)

`wynd.process.jobs` holds the **interface and data model** only. Implementations are in the controller (06): in-process (M1), subprocess (M2), and Kubernetes (post-M5, `wynd.kube`). The controller draft also owns the job harness (`controller.jobs.harness.execute_job`) and the checkout backends. This section specifies the contract the harness must honour and the git primitives it calls (§13).

```python
JobKind  = Literal["compile", "test_live", "build", "bake", "optimise"]
JobState = Literal["queued", "running", "awaiting_input", "succeeded", "failed", "cancelled"]
TERMINAL: frozenset[JobState] = frozenset({"awaiting_input", "succeeded", "failed", "cancelled"})

class Artefact(BaseModel):
    kind: Literal["branch", "commit", "build_dir", "image", "bake", "report", "log"]
    name: str
    ref: str                                   # branch name / sha / path / image ref
    digest: str | None = None

class JobUsage(BaseModel):                     # §15 observability
    wall_s: float = 0.0
    cpu_s: float = 0.0                         # children rusage
    model: dict[str, dict[str, float]] = {}    # "<provider>/<tier>" → {input_tokens, output_tokens, cost_usd, calls}

class JobRecord(BaseModel):                    # RunRegistry kind "job", id = job id
    id: str                                    # new_job_id(): "20260922-221503-a1b2c3"
    kind: JobKind
    process: str
    ref: str                                   # as submitted ("HEAD", a branch, a sha)
    base_sha: str                              # resolved at submit; the job's checkout is exactly this commit
    target_branch: str | None                  # branch to integrate into (the branch `ref` named, or the one checked out when ref == "HEAD")
    workspace_rel: str                         # workspace root relative to the repo toplevel ("" or "examples/invoices")
    inputs: dict[str, Any]
    state: JobState
    parent_job: str | None = None              # resubmission chain (answers to awaiting_input)
    session: dict | None = None                # serialised compiler session (opaque here; compiler-owned format)
    questions: list[dict] = []                 # pending questions when awaiting_input (copied from the session for listing)
    result_branch: str | None = None
    result_sha: str | None = None
    artefacts: list[Artefact] = []
    report: dict = {}                          # kind-specific (compile report, TestReport, BuildInfo summary)
    integration: dict | None = None            # IntegrationResult once integrated (§13.8)
    error: str | None = None
    usage: JobUsage = JobUsage()
    created_at: datetime; started_at: datetime | None = None; finished_at: datetime | None = None

class JobRunner(Protocol):                     # §6.6 exactly, plus nothing
    def submit(self, kind: JobKind, ref: str, inputs: Mapping[str, Any]) -> str: ...   # → job id
    def status(self, job_id: str) -> JobRecord: ...
    def logs(self, job_id: str, offset: int = 0) -> tuple[list[str], int]: ...          # (lines, next offset)
    def artefacts(self, job_id: str) -> list[Artefact]: ...

@dataclass
class JobContext:                              # filled by the harness (06 execute_job)
    job: JobRecord
    inputs: dict[str, Any]                     # = job.inputs (06)
    session: dict | None                       # parent job's session when resuming (06), else None
    workspace_root: Path                       # the USER's workspace (hosts .wynd/venvs, .wynd/build, registries)
    checkout: Path                             # repo toplevel of the job checkout at job.base_sha
    workspace: Path                            # checkout / job.workspace_rel
    registry: RunRegistry
    log: Callable[[str], None]
    commit: Callable[..., str | None]          # commit(message, paths=None) → sha | None (nothing to commit); harness adds trailer
    save_session: Callable[[dict], None]       # checkpoint mid-job (crash safety)

class JobOutcome(BaseModel):
    state: Literal["succeeded", "failed", "awaiting_input"]
    commit: str | None = None                  # sha to publish as the result branch
    artefacts: list[Artefact] = []
    report: dict = {}
    session: dict | None = None                # required when awaiting_input
    questions: list[dict] = []
    error: str | None = None
    model_usage: dict[str, dict[str, float]] = {}

JobHandler = Callable[[JobContext], JobOutcome]

# Helpers (GIT, M1)
def new_job_id(now: datetime | None = None) -> str: ...        # "%Y%m%d-%H%M%S-" + token_hex(3); sortable
def new_job_record(kind: JobKind, ref: str, inputs: Mapping[str, Any], *, ws_root: Path,
                   parent: JobRecord | None = None) -> JobRecord: ...
    # validates inputs["process"] exists in load_workspace(ws_root); base_sha = rev_parse(ref) (parent.base_sha when
    # resuming); target_branch = current_branch() if ref == "HEAD" else (ref if it names a local branch else None)
def wait_for(runner: JobRunner, job_id: str, *, poll_s: float = 0.5, timeout_s: float | None = None) -> JobRecord: ...
def answers_inputs(parent: JobRecord, answers: Mapping[str, str]) -> dict[str, Any]: ...
    # {**parent.inputs, "resume": parent.id, "answers": {**parent.inputs.get("answers", {}), **answers}}
```

Job inputs by kind:

| kind | inputs | handler (owner) | result branch |
|---|---|---|---|
| `compile` | `{"process", "answers"?: {qid: text}, "resume"?: job id, "accept_proposals"?: bool}` | `wynd.compiler.jobs:run_compile_job` (compiler) | `wynd/compile/<process>/<job-id>` |
| `test_live` | `{"process"}` | `wynd.process.testing:run_test_live_job` | `wynd/test-live/<process>/<job-id>` |
| `build` | `{"process", "push"?: registry name, "platform"?: "linux/amd64"}` | `wynd.process.build:run_build_job` | none |
| `bake` | `{"process"}` | `wynd.process.bake:run_bake_job` | none |
| `optimise` (M5) | `{"process", "apply"?: bool, "window_days"?: int}` | `wynd.compiler.optimise:run_optimise_job` (compiler) | `wynd/optimise/<process>/<job-id>` |

**Harness contract.** 06 implements it. It is listed here because handlers depend on it.

1. `record.state = "running"`, set `started_at`, and `registry.put("job", id, record)`.
2. Create the checkout at `record.base_sha`. The local backend runs `git.add_worktree(repo, <ws_root>/.wynd/jobs/<id>/checkout, base_sha)`. The clone-based backend (kube) clones and checks out.
3. Build the `JobContext`. When `record.parent_job` is set, `session` = the parent record's `session`.
4. `outcome = handler(ctx)`. Every `ctx.log` line is appended to `.wynd/jobs/<id>/job.log` (local) with a UTC timestamp. Subprocess output from `uv`, `docker` and `pytest` is streamed through `ctx.log`.
5. If `outcome.commit` is set, publish the branch with `git.result_branch(kind, process, id)`. Locally this is `git.set_branch(repo, branch, sha)`: worktrees share the repo's refs, which is how "push back into the same repo" happens. The clone backend runs `git push origin <sha>:refs/heads/<branch>`. Set `result_branch` and `result_sha`.
6. Copy `state`, `artefacts`, `report`, `session`, `questions`, `error` and usage into the record. Set `finished_at` and save.
7. Remove the checkout on success or `awaiting_input`. **Keep it on failure** for inspection, recording its path in `report.checkout`.
8. Any exception from the handler → `state = "failed"`, `error = f"{type(e).__name__}: {e}"`, traceback in the log.

**Session serialisation hook** (§6.6, §9):
- A compile handler that needs a human returns `JobOutcome(state="awaiting_input", session=…, questions=…)`. It may commit partial progress first. The record is terminal.
- Answering means `runner.submit("compile", parent.base_sha, answers_inputs(parent, answers))`. The new record carries `parent_job = parent.id`, and its handler receives the parent's session in `ctx.session`.
- The chain of records is the conversation. The web chat is a view over it (06 `compile_view.py`).
- Resubmitting against the parent's `base_sha` keeps the session consistent with the tree it was created on. Integration later handles any movement of the target branch (§13.8).

---

## 13. Git utilities (§6.5, §6.6, §11)

`wynd.process.git` wraps the `git` CLI through `_proc.run` (no GitPython). Every function takes explicit paths; nothing depends on the process cwd.

```python
class GitError(WyndProcessError):
    args_: list[str]; returncode: int; stderr: str

def git(cwd: Path, *args: str, input: bytes | None = None, check: bool = True, env: Mapping[str, str] | None = None) -> str
def toplevel(path: Path) -> Path                                        # rev-parse --show-toplevel
def rev_parse(cwd: Path, ref: str) -> str                               # rev-parse --verify <ref>^{commit}
def current_branch(cwd: Path) -> str | None                             # symbolic-ref --short -q HEAD
def is_ancestor(cwd: Path, a: str, b: str) -> bool                      # merge-base --is-ancestor
```

### 13.1 Reference closure (§11)

```python
def reference_closure(ws: Workspace, pid: str) -> list[str]            # (06) workspace-relative POSIX, sorted, deduped
```

Closure of `P`:
- `wynd.yaml`, because it defines how `P`'s references resolve;
- `P.dir`;
- for each root-step binding, the step package dir (proto-only steps included: the dir holding `proto.yaml`);
- for each `process:` binding, the child's closure, recursively.

Local steps and protos are inside `P.dir`. Descendant paths already covered by an ancestor path in the list are dropped.

Dogfood: `["processes/process_supplier_invoice", "wynd.yaml"]`.

### 13.2 HEAD of the process

```python
def closure_head(ws_root: Path, paths: Sequence[str], ref: str = "HEAD") -> str | None
    # git -C ws_root log -1 --format=%H <ref> -- <paths…>     (None if never committed)
```

The closure is computed at `ref`, through a `CommitTree`, when `ref` is not the working tree. "HEAD of the process" (§6.5, §11) is `closure_head(ws.root, reference_closure(load_workspace(root, CommitTree(root, ref)), pid), ref)`.

### 13.3 Dirty checks

```python
def dirty_paths(ws_root: Path, paths: Sequence[str] | None = None) -> list[str]
    # git -C ws_root status --porcelain=v1 -z --untracked-files=all -- <paths or ".">
    # returns workspace-relative paths with staged, unstaged or untracked (non-ignored) changes
```

`wynd build`, `wynd bake` and `wynd compile` refuse when `dirty_paths(root, reference_closure(ws, pid))` is non-empty. The message lists the paths and says `commit or stash them; builds run from commits only`. See I-6 for why the check is restricted to the closure.

### 13.4 Worktrees

```python
def add_worktree(repo: Path, path: Path, sha: str) -> None      # git -C repo worktree add --detach <path> <sha>
def remove_worktree(repo: Path, path: Path) -> None             # git -C repo worktree remove --force <path>; worktree prune
def worktree_for_branch(repo: Path, branch: str) -> Path | None # parse `git worktree list --porcelain` for "branch refs/heads/<branch>"
```

Job checkouts live at `<ws_root>/.wynd/jobs/<job-id>/checkout` (06), inside the ignored `.wynd/`, so the user's `git status` is unaffected. A worktree is always a full repo checkout. The workspace inside it is `checkout / workspace_rel`, which matters for the dogfood at `examples/invoices/`.

### 13.5 Commits and branches

```python
def commit_paths(checkout_ws: Path, message: str, paths: Sequence[str] | None = None,
                 *, trailers: Mapping[str, str] = {}) -> str | None
    # git add -A -- <paths or "."> ; if `git diff --cached --quiet` → None
    # author = repo config user.name/email if set, else "Wynd <wynd@localhost>"; committer always "Wynd <wynd@localhost>"
    #   (passed via GIT_AUTHOR_*/GIT_COMMITTER_* env so jobs work with no git identity configured)
    # message + "\n\n" + "Wynd-Job: <id>" etc. from trailers
    # returns new HEAD sha (detached HEAD in the job checkout)
def result_branch(kind: JobKind, pid: str, job_id: str) -> str
    # {"compile": "wynd/compile", "test_live": "wynd/test-live", "optimise": "wynd/optimise"}[kind] + f"/{pid}/{job_id}"
    # (process ids contain "/" → nested ref dirs; leaves-don't-nest guarantees no ref/dir clash)
def set_branch(repo: Path, branch: str, sha: str, *, expected_old: str | None = None) -> None
    # git update-ref refs/heads/<branch> <sha> [<expected_old>]   (compare-and-swap when expected_old given)
def delete_branch(repo: Path, branch: str) -> None               # git branch -D
```

### 13.6 Intervening commits

```python
def commits_touching(ws_root: Path, base: str, tip: str, paths: Sequence[str]) -> list[tuple[str, str]]
    # git -C ws_root log --format=%H%x00%s <base>..<tip> -- <paths…>  → [(sha, subject)], newest first
```

This is per commit, not a net diff: a commit that touches the closure and a later one that reverts it both count. That is the literal reading of "every intervening commit is outside the closure".

### 13.7 Compile-job workspace changes

The compiler writes only inside the closure of the process being compiled (source packages, cassettes, confirmed examples appended to protos). `ctx.commit(message, paths=reference_closure(...))` stages only closure paths, so a compile commit can never leak outside the closure.

### 13.8 Integration (§6.5)

```python
class IntegrationResult(BaseModel):
    outcome: Literal["fast_forward", "rebased", "needs_review", "noop"]
    target_branch: str
    branch: str                                   # result branch (deleted after fast_forward/rebased; kept on needs_review)
    new_head: str | None = None
    reason: str | None = None

def integrate(ws_root: Path, *, process_id: str, base_sha: str, branch: str, target_branch: str,
              scratch_dir: Path) -> IntegrationResult
```

Algorithm. The user's checkout is never rebased or reset. It only ever sees a fast-forward, or nothing.

```text
repo = toplevel(ws_root); result = rev_parse(repo, branch); tip = rev_parse(repo, f"refs/heads/{target_branch}")
if is_ancestor(repo, result, tip): return noop                                   # already integrated
if tip == base_sha:
    new, outcome = result, "fast_forward"
else:
    closure = ∪ reference_closure(load_workspace(ws_root, CommitTree(ws_root, s)), process_id) for s in (base_sha, tip)
              (a commit where the process no longer exists contributes nothing; if it's missing at tip → needs_review
               "process <id> no longer exists at <target>")
    touching = commits_touching(ws_root, base_sha, tip, closure)
    if touching: return needs_review(reason=f"{len(touching)} commit(s) on {target_branch} since {base_sha[:12]} touch "
                                            f"{process_id}'s reference closure, e.g. {sha[:12]} {subject!r}; review {branch}")
    tmp = scratch_dir / "rebase"; add_worktree(repo, tmp, result)
    try:   git(tmp, "rebase", "--onto", tip, base_sha)                          # replays base..result onto tip (detached)
    except GitError as e: git(tmp, "rebase", "--abort", check=False); remove_worktree(repo, tmp)
                          return needs_review(reason=f"rebase conflict: {first_line(e.stderr)}")
    new = rev_parse(tmp, "HEAD"); remove_worktree(repo, tmp)
    set_branch(repo, branch, new)                                                # branch now holds the rebased commits
    outcome = "rebased"
# advance the target branch
wt = worktree_for_branch(repo, target_branch)
if wt is None: set_branch(repo, target_branch, new, expected_old=tip)           # CAS; GitError → needs_review "target moved"
else:
    try: git(wt, "merge", "--ff-only", new)                                      # updates the user's files; refuses to clobber
    except GitError as e: return needs_review(reason=f"{target_branch} is checked out at {wt} and cannot fast-forward: "
                                                     f"{first_line(e.stderr)}", branch kept)
delete_branch(repo, branch)
return IntegrationResult(outcome=outcome, target_branch=target_branch, branch=branch, new_head=new)
```

The same function integrates `test_live` and `optimise` results. The controller (06 `jobs/integrate.py`) should call this function rather than re-implementing it, and store the result in `JobRecord.integration`. On `needs_review`, the CLI prints the branch and suggests `gh pr create --head <branch>` (06).

---

## 14. Compile state (inputs to derived status, §11)

Status is derived per commit by the controller (06 `status.py`). This workstream supplies:

```python
class StepState(BaseModel):
    name: str; use: str; step_id: str | None
    proto_hash: str | None; locked_proto_hash: str | None; step_hash: str | None
    compiled: bool                                       # phase == "compiled"

class CompileState(BaseModel):
    design: bool                                         # any closure step not compiled, or any child in design
    reasons: list[str]                                   # "step extract: proto-step changed since compile", "child finance/x is in design", …
    steps: list[StepState]
    process_hash: str

def compile_state(ws: Workspace, pid: str) -> CompileState                      # (06)
```

Use `load_workspace(root, CommitTree(root, sha))` for per-commit status (§11: "a process can be released at commit X and in design at HEAD simultaneously").

Flag derivation:
- *design* = `CompileState.design`.
- *compiled* = not design **and** `tests_status(registry, ws, pid, closure_head).status == "passed"`.
- *built* = `artefacts.get_build(pid, closure_head)` is not `None`.
- *released* = the controller's `Release` records.

---
