"""Live compile of the `ws_mini` fixture (`$DRAFTS/05 §16.3`): the real compiler LLM (`$WYND_COMPILER_PROVIDER`, default
`claude-code`) and `provider: claude-code` steps, with `accept_proposals`. The job must succeed and its result must pass
`run_tests` in replay with live calls made impossible. Every compiler call is written to
`<tmp>/recorded_script.yaml` by `RecordingLLM`, so a developer can refresh the offline scripts from it.

Runs only with `WYND_LIVE=1`.
"""

import os

import pytest
from support.proc_git_harness import run_job

from wynd.compiler import jobs, llm
from wynd.compiler.testing import RecordingLLM
from wynd.process import testing
from wynd.process.jobs import new_job_record
from wynd.process.workspace import load_workspace
from wynd.runtime.storage.local import FileRunRegistry

pytestmark = pytest.mark.live


def test_ws_mini_compiles_live_and_replays(make_repo, commit, git, fixtures_dir, tmp_path, monkeypatch):
    ws = make_repo(fixtures_dir / "ws_mini")
    pdir = load_workspace(ws).process_dir("mini").relative_to(ws).as_posix()
    doc = (ws / pdir / "process.yaml").read_text()
    commit(ws, "compile from proto-steps only", {
        f"{pdir}/process.yaml": doc.replace("provider: fake", "provider: claude-code"),
        f"{pdir}/steps": None,
        f"{pdir}/cassettes": None,
    })
    script = tmp_path / "recorded_script.yaml"
    real_llm = llm.default_llm
    monkeypatch.setattr(llm, "default_llm", lambda registry, workdir: RecordingLLM(real_llm(registry, workdir), script))

    runs = FileRunRegistry(tmp_path / "registry")
    record = new_job_record("compile", "HEAD", {"process": "mini", "target_branch": "main", "accept_proposals": True},
                            ws_root=ws, runner="reference", handler="wynd.compiler.jobs:run_compile_job")
    runs.create(record.model_dump(mode="json"))
    done = run_job(runs, record.id, jobs.run_compile_job, ws_root=ws)
    assert done.status == "succeeded", (done.error, (done.report or {}).get("steps"))
    assert done.result_commit and script.read_text().strip()
    usage = done.report["usage"]["total"]
    print(f"\nws_mini live compile: {usage['calls']} compiler calls, cost {usage['cost_usd']} USD")

    git(ws, "merge", "--ff-only", "-q", done.result_branch)
    lp = load_workspace(ws).load_process("mini")
    kinds = {node: rs.package.lock.kind for node, rs in lp.steps.items() if rs.package and rs.package.lock}
    assert (kinds["shout"], kinds["classify"]) == ("shell", "agentic")
    assert all(rs.package.lock.compiled for rs in lp.steps.values() if rs.package and rs.package.proto)

    env = {**os.environ, "ANTHROPIC_API_KEY": "sk-invalid-replay-check"}
    replay = testing.run_tests(load_workspace(ws), "mini", mode="replay", commit=None, runs=None,
                               venv_root=ws / ".wynd" / "venvs", scratch=tmp_path / "replay", env=env)
    assert replay.passed, [(s.subject, s.problem, [c.message for c in s.cases if c.outcome != "passed"])
                           for s in replay.suites if not s.passed]
