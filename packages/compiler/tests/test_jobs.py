"""The compile job handler (`$DRAFTS/05 §5`, PLAN §7 item 3) run by the PLAN §3.18 reference harness, with the pipeline
faked: one squash commit on the base, WIP commits and resume as the same job requeued, nothing-to-compile, failure
still publishes the branch, replay results recorded on the result commit, the user's tree untouched, usage, cassette
size warnings, the real dependency wiring and the commit message."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from support.proc_git_harness import process_yaml, proto_yaml, requeue, run_job

from wynd.compiler import jobs, llm, pipeline
from wynd.compiler.llm import LLMResult
from wynd.compiler.report import CompileReport, ReportStep, ReportUsage, render_commit_message, summarize
from wynd.compiler.session import Answer, CompileSession, Question, SessionState
from wynd.process import testing
from wynd.process.git import closure_head, rev_parse
from wynd.process.git import git as git_out
from wynd.process.jobs import JobRecord, new_job_record
from wynd.runtime.storage.local import FileRunRegistry
from wynd.runtime.usage import Usage

HANDLER = "wynd.compiler.jobs:run_compile_job"
REAL_DEFAULT_DEPS = jobs.default_deps
FILES = {
    "wynd.yaml": "process_roots: [processes]\n",
    "processes/mini/process.yaml": process_yaml("mini", {"read": "./steps/read", "write": "./steps/write"}),
    "processes/mini/proto/read.yaml": proto_yaml("read"),
    "processes/mini/proto/write.yaml": proto_yaml("write"),
    "processes/other/process.yaml": process_yaml("other", {"x": "./steps/x"}),
    "processes/other/proto/x.yaml": proto_yaml("x"),
}
READ = "processes/mini/steps/read"
WRITE = "processes/mini/steps/write"


class Harness:
    def __init__(self, ws, runs, monkeypatch):
        self.ws, self.runs, self.monkeypatch = ws, runs, monkeypatch
        self.test_runs: list[dict] = []
        self.replay_passes = True
        self.deps_calls: list = []

    def submit(self, **inputs) -> str:
        record = new_job_record("compile", "HEAD", {"process": "mini", "target_branch": "main", **inputs},
                                ws_root=self.ws, runner="reference", handler=HANDLER)
        self.runs.create(record.model_dump(mode="json"))
        return record.id

    def run(self, job_id: str, compile_closure) -> JobRecord:
        self.monkeypatch.setattr(pipeline, "compile_closure", compile_closure)
        return run_job(self.runs, job_id, jobs.run_compile_job, ws_root=self.ws)

    def fake_run_tests(self, ws, pid, *, mode, commit, runs, venv_root, scratch, env=None, log=None):
        self.test_runs.append({"pid": pid, "mode": mode, "commit": commit, "runs": runs, "venv_root": venv_root,
                               "head": rev_parse(ws.root, "HEAD"), "ws": ws.root})
        suite = testing.SuiteResult(subject="process:mini", hash="sha256:x", passed=self.replay_passes,
                                    counts={"passed": 1}, cases=[])
        now = datetime.now(UTC)
        return testing.TestReport(process=pid, commit=commit, process_hash="sha256:x", mode=mode,
                                  passed=self.replay_passes, suites=[suite], started_at=now, finished_at=now,
                                  recorded=True)


@pytest.fixture
def h(make_repo, tmp_path, monkeypatch) -> Harness:
    harness = Harness(make_repo(files=FILES, subdir="ws"), FileRunRegistry(tmp_path / "registry"), monkeypatch)
    monkeypatch.setattr(jobs, "default_deps", lambda ctx, session: harness.deps_calls.append(ctx) or "the deps")
    monkeypatch.setattr(testing, "run_tests", harness.fake_run_tests)
    return harness


def write_package(checkout, pkg: str, source: str = "print('compiled')\n") -> None:
    (checkout / pkg).mkdir(parents=True, exist_ok=True)
    name = pkg.rsplit("/", 1)[-1]
    (checkout / pkg / f"{name}.py").write_text(source)
    (checkout / pkg / "step.lock.yaml").write_text(f"wynd: 1\nname: {name}\nkind: deterministic\n")


def compiled(session: CompileSession, node: str, pkg: str, *, tests=(2, 0)) -> ReportStep:
    entry = session.data.report.step_entry("mini", node)
    entry.step, entry.package, entry.action, entry.reason = node, pkg, "compiled", "not compiled yet"
    entry.decision = {"kind": "deterministic", "rule": 1, "why": "A pure function of the text."}
    entry.tests = {"passed": tests[0], "failed": tests[1]}
    return entry


def skipped(session: CompileSession, node: str) -> None:
    entry = session.data.report.step_entry("mini", node)
    entry.step, entry.action, entry.reason = node, "skipped", "proto-step unchanged"


def proposal(n: int = 1) -> Question:
    return Question(id=f"write.example{n}", kind="example_proposal", process="mini", step="write", package="write",
                    text=f"What if the text is empty? ({n})", proposed={"inputs": {"text": ""}, "exit": "done",
                                                                        "outputs": {"text": ""}},
                    expects="decision", default="accept", fingerprint="", asked_at="", asked_in_job="")


def commits(ws, base: str, tip: str) -> list[str]:
    return git_out(ws, "rev-list", f"{base}..{tip}").split()


def show(git, ws, rev: str) -> tuple[str, list[str]]:
    message = git(ws, "log", "-1", "--format=%B", rev)
    files = git(ws, "show", "--name-only", "--format=", rev).split()
    return message, files


# --- the job ----------------------------------------------------------------------------------------------------------

def test_a_done_compile_is_one_commit_on_the_base_and_records_replay_results(h, git, tree_state):
    job_id = h.submit()
    base = rev_parse(h.ws, "HEAD")
    before = tree_state(h.ws)
    seen = {}

    def compile_closure(session, env):
        seen.update(checkout=env.checkout, worktree=env.worktree, scratch=env.scratch, deps=env.deps)
        write_package(env.checkout, READ)
        compiled(session, "read", READ)
        assert env.commit("pipeline checkpoint") is not None        # the pipeline may commit on the way
        env.checkpoint(session.data)
        write_package(env.checkout, WRITE)
        compiled(session, "write", WRITE, tests=(3, 0))
        (env.checkout / "processes/other/proto/x.yaml").write_text("outside the closure\n")
        return SessionState.DONE

    record = h.run(job_id, compile_closure)
    branch = f"wynd/compile/mini/{job_id}"
    assert (record.status, record.result_branch, record.error) == ("succeeded", branch, None)
    tip = rev_parse(h.ws, branch)
    assert record.result_commit == tip and commits(h.ws, base, tip) == [tip]
    assert rev_parse(h.ws, f"{branch}~1") == base
    message, files = show(git, h.ws, branch)
    report = CompileReport.model_validate(record.report)
    assert message == render_commit_message(report).rstrip("\n") + f"\n\nWynd-Job: {job_id}\n\n"
    assert message.startswith("wynd compile: mini\n\nCompiled 2 steps: 2 deterministic. All 5 step tests pass.\n")
    assert files == [f"ws/{READ}/read.py", f"ws/{READ}/step.lock.yaml", f"ws/{WRITE}/step.lock.yaml",
                     f"ws/{WRITE}/write.py"]
    assert (report.status, report.commit, report.branch, report.session, report.jobs) == (
        "succeeded", tip, branch, job_id, [job_id])
    assert record.artefacts == {"steps_compiled": 2, "steps_skipped": 0, "split": []}
    assert (record.session["state"], record.questions) == ("done", [])
    assert record.session["events"][-1]["type"] == "commit"
    [call] = h.test_runs
    assert (call["pid"], call["mode"], call["runs"], call["head"]) == ("mini", "replay", h.runs, tip)
    assert call["commit"] == closure_head(h.ws, ["processes/mini", "wynd.yaml"], ref=tip) == tip
    assert call["venv_root"] == h.ws / ".wynd" / "venvs"
    assert seen["checkout"] == seen["worktree"] / "ws" and seen["deps"] == "the deps"
    assert seen["scratch"] == h.ws / ".wynd" / "jobs" / job_id / "scratch" / "compile"
    assert tree_state(h.ws) == before and rev_parse(h.ws, "main") == base    # the user's tree is untouched


def test_questions_stop_with_a_wip_commit_and_the_requeued_job_finishes_it(h, git, tree_state):
    job_id = h.submit()
    base = rev_parse(h.ws, "HEAD")
    before = tree_state(h.ws)

    def first(session, env):
        write_package(env.checkout, READ)
        compiled(session, "read", READ)
        assert session.ask(proposal()) is None
        return SessionState.AWAITING_INPUT

    waiting = h.run(job_id, first)
    assert (waiting.status, waiting.result_branch) == ("awaiting_input", f"wynd/compile/mini/{job_id}")
    assert [q["id"] for q in waiting.questions] == ["write.example1"]
    assert waiting.session["state"] == "awaiting_input" and h.test_runs == []
    wip = waiting.result_commit
    message, files = show(git, h.ws, wip)
    assert message.startswith(f"wynd compile (in progress): mini\n\nWynd-Session: {job_id}\nWynd-Process: mini\n"
                              f"Wynd-Base: {base}\n")
    assert files == [f"ws/{READ}/read.py", f"ws/{READ}/step.lock.yaml"]
    assert rev_parse(h.ws, f"{wip}~1") == base

    requeue(h.runs, job_id, wip, {**waiting.inputs, "answers": {"write.example1": "yes"}})
    resumed = {}

    def second(session, env):
        resumed.update(read=(env.checkout / READ / "read.py").exists(), answer=session.ask(proposal()),
                       jobs=list(session.data.jobs))
        write_package(env.checkout, WRITE)
        compiled(session, "write", WRITE)
        skipped(session, "read")
        return SessionState.DONE

    done = h.run(job_id, second)
    assert resumed == {"read": True, "jobs": [job_id],
                       "answer": Answer(text="yes", decision="confirm", example=proposal().proposed)}
    assert (done.status, done.result_branch, done.attempt) == ("succeeded", waiting.result_branch, 2)
    tip = done.result_commit
    assert commits(h.ws, base, tip) == [tip]                            # squashed: the WIP commit is gone
    assert show(git, h.ws, tip)[1] == [f"ws/{READ}/read.py", f"ws/{READ}/step.lock.yaml",
                                       f"ws/{WRITE}/step.lock.yaml", f"ws/{WRITE}/write.py"]
    assert done.session["questions"][0]["answered_by"] == "user" and done.questions == []
    assert [c["head"] for c in h.test_runs] == [tip]
    assert tree_state(h.ws) == before


def test_a_requeue_with_questions_still_open_does_not_compile(h):
    job_id = h.submit()

    def ask_two(session, env):
        write_package(env.checkout, READ)
        session.ask(proposal(1))
        session.ask(proposal(2))
        return SessionState.AWAITING_INPUT

    waiting = h.run(job_id, ask_two)
    requeue(h.runs, job_id, waiting.result_commit, {**waiting.inputs, "answers": {"write.example1": "reject"}})

    def never(session, env):
        raise AssertionError("the pipeline must not run while questions are pending")

    again = h.run(job_id, never)
    assert (again.status, again.result_commit) == ("awaiting_input", waiting.result_commit)
    assert [q["id"] for q in again.questions] == ["write.example2"]
    assert again.session["questions"][0]["answer"]["decision"] == "reject"


def test_accept_proposals_from_the_inputs_answers_proposals_as_they_are_asked(h):
    job_id = h.submit(accept_proposals=True)
    answers = []

    def compile_closure(session, env):
        answers.append(session.ask(proposal()))
        return SessionState.DONE

    record = h.run(job_id, compile_closure)
    assert answers[0].decision == "confirm" and record.status == "succeeded"
    assert record.session["options"]["accept_proposals"] is True


def test_nothing_to_compile_succeeds_without_a_commit(h):
    job_id = h.submit()

    def unchanged(session, env):
        skipped(session, "read")
        skipped(session, "write")
        return SessionState.DONE

    record = h.run(job_id, unchanged)
    assert (record.status, record.result_commit, record.result_branch) == ("succeeded", None, None)
    assert record.report["summary"] == "Nothing to compile: every step is unchanged."
    assert record.artefacts == {"steps_compiled": 0, "steps_skipped": 2, "split": []}
    assert h.test_runs == []


def test_a_failed_compile_still_publishes_its_branch(h, git):
    job_id = h.submit()
    base = rev_parse(h.ws, "HEAD")

    def fails(session, env):
        write_package(env.checkout, READ)
        compiled(session, "read", READ)
        env.commit("pipeline checkpoint")
        session.data.report.integration_tests = {"passed": 1, "failed": 1, "cases": [{"name": "example_2"}]}
        session.data.report.error = "process example 2 failed"
        return SessionState.FAILED

    record = h.run(job_id, fails)
    assert (record.status, record.error["message"]) == ("failed", "process example 2 failed")
    tip = rev_parse(h.ws, record.result_branch)
    assert record.result_commit == tip and commits(h.ws, base, tip) == [tip]
    assert show(git, h.ws, tip)[0].startswith("wynd compile: mini\n\nCompiled 1 step: 1 deterministic. "
                                              "1 of 2 step tests and 2 process tests fail.")
    assert record.report["status"] == "failed" and record.session["state"] == "failed"
    assert h.test_runs == []


def test_a_failing_replay_on_the_result_commit_fails_the_job(h):
    h.replay_passes = False
    job_id = h.submit()

    def done(session, env):
        write_package(env.checkout, READ)
        compiled(session, "read", READ)
        return SessionState.DONE

    record = h.run(job_id, done)
    assert record.status == "failed" and record.result_branch == f"wynd/compile/mini/{job_id}"
    assert record.error["message"] == "replay tests failed on the result commit: process:mini"
    assert record.session["state"] == "failed" and record.session["events"][-1]["type"] == "error"


def test_a_job_failed_after_answers_resumes_with_its_cumulative_answers(h):
    job_id = h.submit()

    def ask(session, env):
        write_package(env.checkout, READ)
        session.ask(proposal())
        return SessionState.AWAITING_INPUT

    waiting = h.run(job_id, ask)
    answers = {**waiting.inputs, "answers": {"write.example1": "yes"}}
    requeue(h.runs, job_id, waiting.result_commit, answers)

    def done(session, env):
        write_package(env.checkout, WRITE)
        compiled(session, "write", WRITE)
        return SessionState.DONE

    h.replay_passes = False
    failed = h.run(job_id, done)
    assert (failed.status, failed.session["state"]) == ("failed", "failed")

    h.replay_passes = True
    requeue(h.runs, job_id, failed.result_commit, answers)
    seen = []

    def again(session, env):
        seen.append(session.ask(proposal()).decision)
        return done(session, env)

    record = h.run(job_id, again)
    assert seen == ["confirm"]
    assert (record.status, record.session["state"], record.error) == ("succeeded", "done", None)


def test_large_cassettes_are_reported(h, commit):
    commit(h.ws, "tiny cassette limit", {"wynd.yaml": "process_roots: [processes]\ncassette_warn_mb: 0.001\n"})
    job_id = h.submit()

    def with_cassettes(session, env):
        write_package(env.checkout, READ)
        (env.checkout / READ / "cassettes").mkdir()
        (env.checkout / READ / "cassettes" / "a.json").write_text("x" * 1500)
        (env.checkout / READ / "cassettes" / "b.json").write_text("x" * 500)
        compiled(session, "read", READ)
        write_package(env.checkout, WRITE)
        compiled(session, "write", WRITE)
        return SessionState.DONE

    record = h.run(job_id, with_cassettes)
    assert record.report["warnings"] == ["cassettes for read are 0.0 MB (limit 0.001 MB)"]
    assert [s["cassette_bytes"] for s in record.report["steps"]] == [2000, 0]


def test_the_outcome_carries_this_attempts_usage(h):
    job_id = h.submit()
    call = Usage(input_tokens=10, output_tokens=2, cost_usd=0.25, latency_ms=100, calls=1)

    def spends(session, env):
        session.record_usage(kind="decide", node="read", tier="strong", usage=call)
        session.record_usage(kind="infer_schema", node="read", tier="standard", usage=call)
        report = session.data.report
        report.usage.recording = report.usage.recording + Usage(input_tokens=5, calls=3, cost_usd=0.01)
        session.ask(proposal())
        return SessionState.AWAITING_INPUT

    first = h.run(job_id, spends)
    assert first.usage.calls == 5 and first.usage.input_tokens == 25
    assert set(first.usage.by) == {"claude-code/strong", "claude-code/standard", "recording"}
    assert first.usage.by["recording"].calls == 3
    assert first.result_commit is None                                  # nothing written: no WIP commit
    requeue(h.runs, job_id, first.ref, {**first.inputs, "answers": {"write.example1": "accept"}})

    def spends_once(session, env):
        session.ask(proposal())
        session.record_usage(kind="write_deterministic", node="write", tier="strong", usage=call)
        return SessionState.DONE

    second = h.run(job_id, spends_once)
    assert second.usage.calls == 1 and set(second.usage.by) == {"claude-code/strong"}   # only this attempt's
    assert second.session["usage"]["calls"] == 3                                        # the session keeps totals


def test_attempt_usage_is_the_difference_keyed_by_provider_and_tier():
    one = Usage(input_tokens=1, calls=1, cost_usd=1.0)
    before = ReportUsage(total=one, by_tier={"strong": one})
    after = ReportUsage(total=one + one + one, by_tier={"strong": one + one, "cheap": one},
                        recording=Usage(calls=2, output_tokens=7))
    usage = jobs.attempt_usage(before, after, "fake")
    assert (usage.calls, usage.input_tokens, usage.output_tokens, usage.cost_usd) == (4, 2, 7, 2.0)
    assert {k: v.calls for k, v in usage.by.items()} == {"fake/strong": 1, "fake/cheap": 1, "recording": 2}
    assert jobs.attempt_usage(after, after, "fake").by == {}


# --- the real dependency wiring -------------------------------------------------------------------------------------

class FakeInner:
    def __init__(self):
        self.calls = 0

    def call(self, kind, *, node, system, prompt, response_model, tier, thinking):
        self.calls += 1
        return LLMResult(response_model(text=f"{kind}:{node}"),
                         Usage(input_tokens=7, calls=1, cost_usd=0.5), memo_hit=False)


def test_default_deps_wire_the_process_boundaries_and_meter_under_the_memo(monkeypatch, tmp_path):
    from pydantic import BaseModel

    from wynd.process import testing as ptesting
    from wynd.process import venvs
    from wynd.process.build.resolve import UvResolver
    from wynd.runtime.mcp import list_tools

    class Reply(BaseModel):
        text: str

    inner = FakeInner()
    made = {}
    monkeypatch.setattr(llm, "default_llm", lambda registry, workdir: made.update(registry=registry,
                                                                                  workdir=workdir) or inner)
    monkeypatch.setattr(UvResolver, "compile", lambda self, reqs, **kw: made.update(resolve=(list(reqs), kw)) or [
        "pypdf==6.19.0", "wynd-spec==0.1.0", "-e file:///repo/packages/runtime", "Wynd_Runtime==0.1.0"])
    ctx = SimpleNamespace(registry="the registry", state_dir=tmp_path / ".wynd", scratch=tmp_path / "scratch",
                          log=lambda line: None)
    session = CompileSession.new(session_id="job_1", process="mini", base_commit="b" * 40,
                                 options=jobs.CompileOptions())
    deps = REAL_DEFAULT_DEPS(ctx, session)
    assert made == {"registry": "the registry", "workdir": tmp_path / "scratch" / "llm"}
    assert (deps.registry, deps.run_step_suite, deps.run_process_examples) == (
        "the registry", ptesting.run_step_suite, ptesting.run_process_examples)
    assert (deps.describe, deps.list_mcp_tools) == (venvs.describe, list_tools)
    assert deps.lock_requirements(["pypdf>=6"]) == ["pypdf==6.19.0"]
    assert made["resolve"] == (["pypdf>=6"], {"universal": True, "python_version": "3.12"})
    assert deps.now().tzinfo is not None

    for _ in range(2):
        result = deps.llm.call("decide", node="read", system="s", prompt="p", response_model=Reply,
                               tier=llm.Tier.STRONG, thinking="medium")
        assert result.value == Reply(text="decide:read")
    assert inner.calls == 1                                              # the second call is a memo hit
    usage = session.data.report.usage
    assert (usage.total.calls, usage.by_tier["strong"].calls, usage.by_kind["decide"].calls) == (1, 1, 1)
    assert session.data.report.steps[0].usage.compiler.input_tokens == 7
    assert [e["step"] for e in session.data.memo.calls.values()] == ["read"]


def test_step_python_uses_the_shared_venv_root(monkeypatch, tmp_path):
    from wynd.process import venvs

    monkeypatch.setattr(llm, "default_llm", lambda registry, workdir: FakeInner())
    monkeypatch.setattr(venvs, "step_python", lambda reqs, *, venv_root, log=None: venv_root / "v" / "bin" / "python")
    ctx = SimpleNamespace(registry=None, state_dir=tmp_path / ".wynd", scratch=tmp_path, log=print)
    deps = REAL_DEFAULT_DEPS(ctx, CompileSession.new(session_id="j", process="p", base_commit="b",
                                                     options=jobs.CompileOptions()))
    assert deps.step_python(["pypdf>=6"]) == tmp_path / ".wynd" / "venvs" / "v" / "bin" / "python"


def test_the_compiler_provider_is_chosen_by_env(monkeypatch):
    assert jobs.compiler_provider() == "claude-code"
    monkeypatch.setenv("WYND_COMPILER_PROVIDER", "fake")
    assert jobs.compiler_provider() == "fake"


# --- report and commit message --------------------------------------------------------------------------------------

def dogfood_report() -> CompileReport:
    report = CompileReport(process="process_supplier_invoice", session="job_1", base_commit="4f1c2e9d")
    steps = [
        ("read", "read_pdf", "compiled", {"kind": "deterministic", "rule": 1,
                                          "why": "Extracting the text layer of a PDF is a pure function of the file."}),
        ("extract", "extract_invoice_fields", "compiled",
         {"kind": "agentic", "rule": 3, "tier": "cheap", "why": "Code handles 4/6 examples.",
          "split": {"deterministic": "extract", "agentic": "extract_agentic"}}),
        ("fix", "fix_fields", "compiled", {"kind": "agentic", "rule": 2, "tier": "cheap",
                                           "why": "Correcting mis-read fields needs reading the context."}),
        ("save", "save_record", "skipped", None),
    ]
    for node, step, action, decision in steps:
        entry = report.step_entry("process_supplier_invoice", node)
        entry.step, entry.action, entry.decision = step, action, decision
        entry.reason = "proto-step unchanged" if action == "skipped" else "not compiled yet"
        entry.tests = {"passed": 3, "failed": 0} if action == "compiled" else None
    report.integration_tests = {"passed": 2, "failed": 0, "cases": []}
    report.process_changes = [{"type": "split", "process": "process_supplier_invoice", "node": "extract",
                               "added_node": "extract_agentic", "edges_added": ["a", "b", "c"]}]
    report.proto_changes = [{"proto": "processes/process_supplier_invoice/proto/extract_invoice_fields.yaml",
                             "examples_added": 2}]
    return report


def test_the_commit_message_lists_every_step_and_ends_with_the_trailers():
    assert render_commit_message(dogfood_report()) == """\
wynd compile: process_supplier_invoice

Compiled 3 steps: 1 deterministic, 1 agentic, 1 split into deterministic + agentic; 1 skipped (unchanged). \
All 9 step tests and 2 process tests pass.

- read (read_pdf): deterministic, rule 1. Extracting the text layer of a PDF is a pure function of the file.
- extract (extract_invoice_fields): split, rule 3 (cheap tier). Code handles 4/6 examples.
- fix (fix_fields): agentic, rule 2 (cheap tier). Correcting mis-read fields needs reading the context.
- save (save_record): skipped (proto-step unchanged).

Process changes: added node extract_agentic and 3 edges (split of extract).
Proto-steps: 2 confirmed edge-case examples added to extract_invoice_fields.

Wynd-Session: job_1
Wynd-Process: process_supplier_invoice
Wynd-Base: 4f1c2e9d
"""


def test_the_summary_orders_kinds_and_a_set_summary_wins():
    report = dogfood_report()
    assert summarize(report).startswith("Compiled 3 steps: 1 deterministic, 1 agentic, 1 split")
    report.summary = "Custom."
    assert render_commit_message(report).split("\n")[2] == "Custom."
    empty = CompileReport(process="p", status="failed")
    assert summarize(empty) == "Nothing was compiled."


def test_the_report_json_uses_the_documented_names():
    data = dogfood_report().model_dump(mode="json", by_alias=True)
    assert list(data) == ["report_version", "process", "session", "jobs", "base_commit", "commit", "branch", "status",
                          "summary", "steps", "process_changes", "proto_changes", "integration_tests", "validation",
                          "questions", "usage", "warnings", "error"]
    assert "schema" in data["steps"][0] and set(data["usage"]) == {"total", "by_kind", "by_tier", "recording"}
    assert CompileReport.model_validate(data) == dogfood_report()


# --- git operations in the job worktree -----------------------------------------------------------------------------

def test_restore_paths_undoes_every_kind_of_change_under_the_paths_only(make_repo, commit, git):
    from wynd.compiler.gitops import restore_paths

    ws = make_repo(files=FILES)
    commit(ws, "compiled read", {f"{READ}/read.py": "v1\n", f"{READ}/step.lock.yaml": "lock\n"})
    (ws / READ / "read.py").write_text("half-written\n")                      # modified
    (ws / READ / "step.lock.yaml").unlink()                                  # deleted
    (ws / READ / "helpers.py").write_text("new\n")                           # untracked
    (ws / READ / "cassettes").mkdir()
    (ws / READ / "cassettes" / "c.json").write_text("{}\n")
    (ws / WRITE).mkdir(parents=True)
    (ws / WRITE / "write.py").write_text("staged\n")                         # staged, never committed
    git(ws, "add", "--", WRITE)
    (ws / "processes/other/proto/x.yaml").write_text("keep me\n")
    restore_paths(ws, [ws / READ, WRITE])
    assert (ws / READ / "read.py").read_text() == "v1\n"
    assert (ws / READ / "step.lock.yaml").read_text() == "lock\n"
    assert not (ws / READ / "helpers.py").exists() and not (ws / READ / "cassettes").exists()
    assert not (ws / WRITE).exists()
    assert git(ws, "status", "--porcelain").split("\n")[0] == " M processes/other/proto/x.yaml"
    restore_paths(ws, [])                                                   # nothing to do


def test_squash_to_keeps_the_changes_staged_on_the_base(make_repo, commit, git):
    from wynd.compiler.gitops import squash_to

    ws = make_repo(files=FILES)
    base = rev_parse(ws, "HEAD")
    commit(ws, "wip 1", {f"{READ}/read.py": "v1\n"})
    commit(ws, "wip 2", {f"{READ}/read.py": "v2\n", f"{WRITE}/write.py": "w\n"})
    squash_to(ws, base=base)
    assert rev_parse(ws, "HEAD") == base
    assert sorted(git(ws, "diff", "--cached", "--name-only").split()) == [f"{READ}/read.py", f"{WRITE}/write.py"]
    assert (ws / READ / "read.py").read_text() == "v2\n"
