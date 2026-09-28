"""The optimise report: units, applicability, changes, goldens (PLAN §6.1, §15 item 55; `$DRAFTS/08 §3.6`).

`WYND_UPDATE_GOLDEN=1` rewrites the goldens.
"""

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from wynd.process.optimise import (
    ProcessStats,
    TierStats,
    UnitStats,
    build_report,
    edges_lock_of,
    render_report_text,
)
from wynd.process.workspace import load_workspace
from wynd.spec.lockfiles import EdgesLock

HERE = Path(__file__).parent
COMMIT = "3f2a9c1d0e5b7a8c9d0e1f2a3b4c5d6e7f8a9b0c"
NOW = datetime(2026, 10, 2, 9, 0, tzinfo=UTC)
UNITS = ["read", "extract", "classify", "review", "review.judge", "edge:extract.done[save]",
         "edge:review.judge.done[ok]"]


def golden(name: str, text: str) -> None:
    path = HERE / name
    if os.environ.get("WYND_UPDATE_GOLDEN") == "1":
        path.write_text(text)
    assert text == path.read_text(), f"golden {name} differs (WYND_UPDATE_GOLDEN=1 rewrites it)"


def report(lp, stats, edges_lock=None):
    lock = edges_lock_of(lp) if edges_lock is None else edges_lock
    return build_report(lp, lock, stats, commit=COMMIT, now=NOW)


def by_unit(r):
    return {u.unit: u for u in r.units}


def test_demote_report_goldens(intake, stats_of):
    r = report(intake, stats_of("demote"))
    golden("golden_report.json", json.dumps(r.model_dump(mode="json"), indent=2) + "\n")
    golden("golden_report.txt", render_report_text(r))


def test_units_follow_the_definition(intake, stats_of):
    r = report(intake, stats_of("demote"))
    assert [u.unit for u in r.units] == UNITS                 # steps (child units after their node), then branches
    units = by_unit(r)
    assert (units["read"].kind, units["read"].package, units["read"].lock_path) == (
        "deterministic", "./steps/read", "processes/intake/steps/read/step.lock.yaml")
    assert (units["read"].provider, units["read"].tier, units["read"].recommendation) == (None, None, None)
    assert units["read"].duration_p50_ms == 16.5
    assert (units["review"].kind, units["review"].package, units["review"].lock_path) == (
        "process", "process:review", None)
    assert units["classify"].lock_path == "shared/steps/classify/step.lock.yaml"
    assert (units["review.judge"].tier, units["review.judge"].lock_path) == (
        "cheap", "processes/review/steps/judge/step.lock.yaml")    # no tier in the lock -> cheap
    edge = units["edge:extract.done[save]"]
    assert (edge.kind, edge.package, edge.lock_path, edge.provider, edge.tier) == (
        "edge", None, "processes/intake/edges.lock.yaml", "claude-code", "cheap")
    assert units["edge:review.judge.done[ok]"].tier == "standard"   # read from the child's own edges.lock.yaml
    assert r.window == {"max_runs": None, "runs_considered": 25, "live_calls": 75}
    assert r.generated_at == "2026-10-02T09:00:00Z"
    assert r.rules["min_samples"] == 20


def test_changes_cover_only_the_process_own_units(intake, stats_of):
    r = report(intake, stats_of("demote"))
    classify = by_unit(r)["classify"].recommendation
    assert (classify.action, classify.rule, classify.applicable) == ("demote", "D1", False)
    assert classify.not_applicable_reason == (
        "shared step shared:classify: changing its tier changes every process that uses it")
    assert [c.model_dump(mode="json") for c in r.changes] == [{
        "unit": "extract", "lock_path": "processes/intake/steps/extract/step.lock.yaml", "lock_key": None,
        "from_tier": "standard", "to_tier": "cheap", "rule": "D1",
        "evidence": {"n": 25.0, "fail_rate": 0.0, "vfail_rate": 0.0, "mean_attempts": 1.0,
                     "cost_per_exec_usd": pytest.approx(0.003)},
    }]


def test_an_edge_change_names_its_lock_key(intake, stats_of):
    r = report(intake, stats_of("edge"))
    edge = [c for c in r.changes if c.lock_key is not None]
    assert [c.model_dump(mode="json", exclude={"evidence"}) for c in edge] == [{
        "unit": "edge:extract.done[save]", "lock_path": "processes/intake/edges.lock.yaml",
        "lock_key": "extract.done[save]", "from_tier": "cheap", "to_tier": "standard", "rule": "P1",
    }]
    assert edge[0].evidence["n"] == 25
    assert edge[0].evidence["fail_rate"] == pytest.approx(0.08)


def test_child_process_units_are_reported_but_not_applicable(intake):
    failing = TierStats(provider="claude-code", tier="cheap", n=20, fails=5, attempts_sum=20)
    clean = TierStats(provider="claude-code", tier="standard", n=20, attempts_sum=20)
    stats = ProcessStats(process="intake", units={
        "review.judge": UnitStats(unit="review.judge", kind="agentic", by_tier={"claude-code/cheap": failing}),
        "edge:review.judge.done[ok]": UnitStats(unit="edge:review.judge.done[ok]", kind="edge",
                                               by_tier={"claude-code/standard": clean}),
    })
    r = report(intake, stats)
    units = by_unit(r)
    reason = "belongs to child process review: optimise changes only the process's own steps and branches"
    judge = units["review.judge"].recommendation
    assert (judge.action, judge.to_tier, judge.applicable, judge.not_applicable_reason) == (
        "promote", "standard", False, reason)
    child_edge = units["edge:review.judge.done[ok]"].recommendation
    assert (child_edge.action, child_edge.to_tier, child_edge.applicable, child_edge.not_applicable_reason) == (
        "demote", "cheap", False, reason)
    assert r.changes == []
    assert "No applicable changes." in render_report_text(r)


def test_a_branch_without_a_lock_entry_uses_the_defaults_and_is_not_applicable(intake, stats_of):
    r = report(intake, stats_of("edge"), EdgesLock())
    edge = by_unit(r)["edge:extract.done[save]"]
    assert (edge.provider, edge.tier) == ("claude-code", "cheap")
    assert (edge.recommendation.rule, edge.recommendation.applicable) == ("P1", False)
    assert edge.recommendation.not_applicable_reason == (
        "no edges.lock.yaml entry for extract.done[save]: run wynd compile first")
    assert [c.unit for c in r.changes] == ["extract"]


def test_lock_provider_and_tier_select_the_current_stats(make_repo, stats_of):
    root = make_repo(source=HERE / "fixtures" / "ws", files={
        "processes/intake/steps/extract/step.lock.yaml":
            "wynd: 1\nname: extract\nkind: agentic\nentrypoint: extract:Extract\nprovider: anthropic\ntier: strong\n",
    })
    lp = load_workspace(root).load_process("intake")
    extract = by_unit(report(lp, stats_of("demote")))["extract"]
    assert (extract.provider, extract.tier, extract.current) == ("anthropic", "strong", None)
    assert list(extract.by_tier) == ["claude-code/standard"]
    assert extract.recommendation.rule == "R0"


def test_totals_sum_live_usage_per_provider_and_tier(intake, stats_of):
    totals = report(intake, stats_of("demote")).totals
    assert [(t["provider"], t["tier"], t["calls"], t["input_tokens"], t["output_tokens"]) for t in totals] == [
        ("claude-code", "cheap", 25, 25 * 400, 25 * 30),
        ("claude-code", "standard", 50, sum(900 + i for i in range(1, 26)) + 25 * 500, 25 * 90 + 25 * 40),
    ]
    assert totals[1]["cost_usd"] == pytest.approx(25 * 0.003 + 25 * 0.002)


def test_empty_stats_keep_every_agentic_unit(intake):
    r = report(intake, ProcessStats(process="intake"))
    assert [u.recommendation.rule for u in r.units if u.recommendation] == ["R0"] * 5
    assert r.totals == [] and r.changes == [] and r.warnings == []
    text = render_report_text(r)
    assert text.startswith("optimise intake @ 3f2a9c1  (0 runs, 0 live calls; rules v1, min 20)\n")
    assert "totals: none\n" in text


def test_the_dogfood_reports(make_repo, repo_root):
    """The sample process loads and reports (every agentic unit keeps with R0 when there are no runs)."""
    root = make_repo(source=repo_root / "examples" / "invoices")
    lp = load_workspace(root).load_process("process_supplier_invoice")
    r = report(lp, ProcessStats(process=lp.id))
    assert [u.unit for u in r.units][: len(lp.doc.steps)] == list(lp.doc.steps)
    assert all(u.recommendation.rule == "R0" for u in r.units if u.recommendation is not None)
    assert any(u.kind == "agentic" for u in r.units)
    assert r.changes == []
