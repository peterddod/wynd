"""`wynd optimise` (PLAN §3.22, §9, §14 OPT-CTL; `$DRAFTS/08 §3.8–§3.9`): the text and JSON report, `--apply` with
`--unit` filtering, waiting, integrating and the exit codes, against a fake controller whose `optimise_report` /
`submit_optimise` are scripted; plus the report of a real controller over the fixture workspace."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

import wynd.controller.optimise as optimise
from wynd.controller.errors import NotFound
from wynd.controller.models import Integration, Job, JobError
from wynd.process.jobs import JobUsage
from wynd.process.optimise import RULES_V1, OptimiseReport, render_report_text

NOW = datetime(2026, 9, 28, 12, 0, tzinfo=UTC)
PID = "brief"
STEP_LOCK = "processes/brief/steps/draft/step.lock.yaml"
BRANCH = "wynd/optimise/brief/job_1"
RESULT = {
    "branch": BRANCH, "commit": "b" * 40, "report_commit": "c" * 40,
    "applied": [{"unit": "draft", "from_tier": "standard", "to_tier": "cheap", "rule": "D1",
                 "tests": {"passed": 2, "failed": 0}}],
    "rejected": [{"unit": "edge:draft.done[ok]", "from_tier": "standard", "to_tier": "cheap",
                  "reason": "lock is now cheap, report assumed standard"}],
    "process_tests": {"passed": 1, "failed": 0},
}


def stats(tier: str, n: int) -> dict:
    return {"provider": "fake", "tier": tier, "n": n, "attempts_sum": n, "calls": n, "input_tokens": 100 * n,
            "output_tokens": 10 * n, "cost_usd": 0.001 * n}


def unit(name: str, kind: str, rule: str | None) -> dict:
    agentic = rule is not None
    rec = None
    if agentic:
        rec = {"action": "demote" if rule == "D1" else "keep", "rule": rule, "from_tier": "standard",
               "to_tier": "cheap" if rule == "D1" else None, "reason": "within bounds", "applicable": rule == "D1"}
    return {"unit": name, "kind": kind, "package": None if kind == "edge" else "./steps/draft",
            "lock_path": STEP_LOCK, "provider": "fake" if agentic else None, "tier": "standard" if agentic else None,
            "current": stats("standard", 25) if agentic else None,
            "by_tier": {"fake/standard": stats("standard", 25)} if agentic else {},
            "duration_p50_ms": 1000.0, "duration_p95_ms": 1500.0, "recommendation": rec}


REPORT = OptimiseReport.model_validate({
    "process": PID, "commit": "c" * 40, "generated_at": "2026-09-28T12:00:00Z",
    "window": {"max_runs": 200, "runs_considered": 25, "live_calls": 50}, "rules": asdict(RULES_V1),
    "units": [unit("read", "deterministic", None), unit("draft", "agentic", "D1"),
              unit("edge:draft.done[ok]", "edge", "D1")],
    "warnings": [], "totals": [{"provider": "fake", "tier": "standard", "calls": 50, "input_tokens": 5000,
                                "output_tokens": 500, "cost_usd": 0.05}],
    "changes": [
        {"unit": name, "lock_path": STEP_LOCK, "lock_key": key, "from_tier": "standard", "to_tier": "cheap",
         "rule": "D1", "evidence": {"n": 25, "fail_rate": 0.0, "vfail_rate": 0.0, "mean_attempts": 1.0}}
        for name, key in (("draft", None), ("edge:draft.done[ok]", "draft.done[ok]"))],
})


def job(status: str = "succeeded", **fields) -> Job:
    return Job(id="job_1", kind="optimise", process_id=PID, ref="a" * 40, status=status, created_at=NOW,
               **fields)


FINISHED = job(branch=BRANCH, result_commit="b" * 40, report=RESULT,
               usage=JobUsage(input_tokens=300, output_tokens=30, calls=3, cost_usd=0.003))


class FakeJobs:
    def __init__(self, final: Job, mode: str = "fast_forward") -> None:
        self.final, self.mode = final, mode
        self.waited: list[str] = []
        self.integrated: list[str] = []

    def get(self, job_id: str) -> Job:
        return job("queued")

    def wait(self, job_id: str, *, on_log=None, **kw) -> Job:
        self.waited.append(job_id)
        if on_log is not None:
            on_log("12:00:00 draft: standard -> cheap (D1)\n")
        return self.final

    def integrate(self, job_id: str) -> Job:
        self.integrated.append(job_id)
        result = Integration(mode=self.mode, branch=BRANCH, target="main", head="b" * 40, at=NOW)
        return self.final.model_copy(update={"integration": result})


@pytest.fixture
def fake(monkeypatch, use_controller):
    """A controller with scripted jobs; `optimise_report`/`submit_optimise` record their calls. `submit` keeps the
    report's changes of `units` and raises `NothingToApply` when none is left, as the real one does."""
    state = SimpleNamespace(calls=[], ctl=use_controller(SimpleNamespace(jobs=FakeJobs(FINISHED))))

    def report(ctl, pid, *, max_runs=200, min_runs=20):
        state.calls.append(("report", pid, max_runs, min_runs))
        if pid != PID:
            raise NotFound(f"unknown process '{pid}'")
        return REPORT

    def submit(ctl, pid, *, units=None, max_runs=200, min_runs=20):
        state.calls.append(("submit", pid, units, max_runs, min_runs))
        changes = [c for c in REPORT.changes if units is None or c.unit in units]
        submitted = REPORT.model_copy(update={"changes": changes})
        if not changes:
            raise optimise.NothingToApply(f"nothing to apply: no applicable tier change in {pid}",
                                          details=submitted.model_dump(mode="json"))
        return "job_1", submitted

    monkeypatch.setattr(optimise, "optimise_report", report)
    monkeypatch.setattr(optimise, "submit_optimise", submit)
    return state


# --- the report ------------------------------------------------------------------------------------------------------

def test_prints_the_text_report(cli, fake):
    result = cli("optimise", PID)
    assert result.stdout == render_report_text(REPORT)
    assert "demote -> cheap (D1)" in result.stdout
    assert fake.calls == [("report", PID, 200, 20)]


def test_json_prints_the_report_document(cli, fake):
    assert json.loads(cli("optimise", PID, "--json").stdout) == REPORT.model_dump(mode="json")


def test_the_run_window_options_reach_the_report(cli, fake):
    cli("optimise", PID, "--max-runs", "50", "--min-runs", "5")
    assert fake.calls == [("report", PID, 50, 5)]


def test_usage_errors_exit_2(cli, fake):
    cli("optimise", PID, "--unit", "draft", code=2)                   # --unit needs --apply
    cli("optimise", PID, "--no-wait", code=2)
    cli("optimise", PID, "--min-runs", "0", code=2)
    assert fake.calls == []


def test_an_unknown_process_exits_3(cli, fake):
    result = cli("optimise", "nope", code=3)
    assert "error: unknown process 'nope'" in result.stderr


# --- --apply ---------------------------------------------------------------------------------------------------------

def test_apply_submits_waits_prints_the_result_and_integrates(cli, fake):
    result = cli("optimise", PID, "--apply")
    assert fake.calls == [("submit", PID, None, 200, 20)]
    assert fake.ctl.jobs.waited == ["job_1"] and fake.ctl.jobs.integrated == ["job_1"]
    assert "submitted job_1 (optimise): draft standard -> cheap, edge:draft.done[ok] standard -> cheap" in \
        result.stderr
    assert "draft: standard -> cheap (D1)" in result.stderr                    # the job log
    lines = result.stdout.splitlines()
    assert lines[0].split() == ["UNIT", "CHANGE", "RESULT", "TESTS", "REASON"]
    assert lines[1].split() == ["draft", "standard", "->", "cheap", "applied", "2/2", "-"]
    assert lines[2].split()[:6] == ["edge:draft.done[ok]", "standard", "->", "cheap", "rejected", "-"]
    assert lines[2].endswith("lock is now cheap, report assumed standard")
    assert lines[3:] == ["process examples  1/1", "usage: 300→30 tok $0.0030", "fast-forwarded main to bbbbbbb"]


def test_apply_unit_keeps_only_those_units_changes(cli, fake):
    result = cli("optimise", PID, "--apply", "--unit", "draft")
    assert fake.calls == [("submit", PID, ["draft"], 200, 20)]
    assert "submitted job_1 (optimise): draft standard -> cheap\n" in result.stderr

    fake.calls.clear()
    cli("optimise", PID, "--apply", "--unit", "draft", "--unit", "edge:draft.done[ok]", "--min-runs", "5")
    assert fake.calls == [("submit", PID, ["draft", "edge:draft.done[ok]"], 200, 5)]


def test_nothing_to_apply_exits_0_without_a_job(cli, fake):
    result = cli("optimise", PID, "--apply", "--unit", "read")
    assert result.stdout == f"nothing to apply: no applicable tier change in {PID}\n"
    assert fake.ctl.jobs.waited == []

    document = json.loads(cli("optimise", PID, "--apply", "--unit", "read", "--json").stdout)
    assert document["process"] == PID and document["changes"] == []


def test_a_failed_job_exits_1_and_is_not_integrated(cli, fake):
    fake.ctl.jobs.final = job("failed", report={**RESULT, "branch": None, "commit": None},
                              error=JobError(message="process examples failed live after the tier changes"))
    result = cli("optimise", PID, "--apply", code=1)
    assert fake.ctl.jobs.integrated == []
    assert "job job_1 failed: process examples failed live after the tier changes" in result.stderr


def test_a_branch_left_for_review_exits_5(cli, fake):
    fake.ctl.jobs.mode = "pr_branch"
    result = cli("optimise", PID, "--apply", code=5)
    assert f"left for review: {BRANCH}" in result.stdout


def test_no_integrate_leaves_the_branch(cli, fake):
    result = cli("optimise", PID, "--apply", "--no-integrate")
    assert fake.ctl.jobs.integrated == []
    assert f"succeeded; branch {BRANCH} not integrated (wynd jobs integrate job_1)" in result.stdout


def test_no_wait_prints_the_job_id(cli, fake):
    assert cli("optimise", PID, "--apply", "--no-wait").stdout == "job_1\n"
    assert json.loads(cli("optimise", PID, "--apply", "--no-wait", "--json").stdout)["status"] == "queued"
    assert fake.ctl.jobs.waited == []


def test_apply_json_prints_the_integrated_job(cli, fake):
    document = json.loads(cli("optimise", PID, "--apply", "--json").stdout)
    assert document["id"] == "job_1" and document["report"]["applied"][0]["unit"] == "draft"
    assert document["integration"]["mode"] == "fast_forward"


# --- a real controller -----------------------------------------------------------------------------------------------

def test_the_report_of_a_real_workspace(cli, workspace, make_controller, use_controller, git):
    use_controller(make_controller(workspace))
    head = git(workspace, "rev-parse", "HEAD").strip()
    out = cli("optimise", "p1").stdout
    assert out.startswith(f"optimise p1 @ {head[:7]}  (0 runs, 0 live calls; rules v1, min 20)\n")
    assert out.endswith("No applicable changes.\n")
    assert cli("optimise", "p1", "--apply").stdout == "nothing to apply: no applicable tier change in p1\n"
    cli("optimise", "p1", "--apply", "--unit", "nope", code=2)
