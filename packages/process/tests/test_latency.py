"""Data-driven latency warnings for `latency: fast` processes (PLAN §3.2, §6.3 pass 8; `$DRAFTS/08 §3.5`).

Uses the optimise fixtures: the workspace `optimise/fixtures/ws` and the synthetic traces `optimise/fixtures/*.jsonl`
(`fast`: 6 runs, extract calls 2.6–3.1 s at standard, runs of 10.5–11.0 s, 2 s of overhead per transition).
"""

import ast
import json
from dataclasses import replace
from pathlib import Path

import pytest

import wynd.process.latency as latency_module
from wynd.process.latency import CODES, latency_findings, latency_warnings
from wynd.process.optimise import RULES_V1, ProcessStats, TierStats, UnitStats, build_report, collect_stats
from wynd.process.validation import validate_process
from wynd.process.workspace import load_workspace
from wynd.spec.lockfiles import EdgesLock

FIXTURES = Path(__file__).parent / "optimise" / "fixtures"
PROCESS = "processes/intake/process.yaml"
LATENCY_CODES = {"W-LATENCY-CALL", "W-LATENCY-RUN", "W-LATENCY-DISPATCH"}


def fast_stats(runs: int | None = None) -> ProcessStats:
    by_run: dict[str, list[dict]] = {}
    for line in (FIXTURES / "fast.jsonl").read_text().splitlines():
        event = json.loads(line)
        by_run.setdefault(event["run_id"], []).append(event)
    return collect_stats("intake", list(by_run)[:runs], by_run.__getitem__)


def workspace(make_repo, *, latency: str | None = "fast", files: dict[str, str] | None = None):
    text = (FIXTURES / "ws" / PROCESS).read_text()
    if latency is not None:
        text = text.replace("provider: claude-code\n", f"provider: claude-code\nlatency: {latency}\n")
    return load_workspace(make_repo(source=FIXTURES / "ws", files={PROCESS: text, **(files or {})}))


@pytest.fixture
def fast(make_repo):
    return workspace(make_repo).load_process("intake")


def test_slow_calls_runs_and_dispatch_warn_in_a_fast_process(fast):
    found = latency_warnings(fast, fast_stats())
    assert [(d.code, d.severity, d.message) for d in found] == [
        ("W-LATENCY-CALL", "warning",
         "extract: p95 model-call latency 3.1s over 6 live calls (claude-code/standard) exceeds the latency: fast "
         "budget of 1.5s; consider a lower tier or a ModelProvider"),
        ("W-LATENCY-RUN", "warning", "p95 run duration 11.0s over 6 runs exceeds 10s"),
        ("W-LATENCY-DISPATCH", "warning",
         "p95 executor overhead 2000ms per transition exceeds 50ms (step-to-step latency should be milliseconds)"),
    ]
    call, run, _ = found
    assert (call.file, call.process, call.loc) == (PROCESS, "intake", ("steps", "extract"))
    assert call.line is not None
    assert (run.file, run.loc) == (PROCESS, ())


@pytest.mark.parametrize("latency", [None, "normal"])
def test_nothing_warns_unless_the_process_is_fast(make_repo, latency):
    lp = workspace(make_repo, latency=latency).load_process("intake")
    assert latency_warnings(lp, fast_stats()) == []


def test_each_code_needs_latency_min_samples(fast):
    assert latency_warnings(fast, fast_stats(4)) == []
    assert {d.code for d in latency_warnings(fast, fast_stats(5))} == LATENCY_CODES


def test_only_calls_at_the_current_tier_count(make_repo):
    lp = workspace(make_repo, files={
        "processes/intake/steps/extract/step.lock.yaml":
            "wynd: 1\nname: extract\nkind: agentic\nentrypoint: extract:Extract\ntier: cheap\n",
    }).load_process("intake")
    assert {d.code for d in latency_warnings(lp, fast_stats())} == {"W-LATENCY-RUN", "W-LATENCY-DISPATCH"}


def test_thresholds_come_from_the_rules(fast):
    rules = replace(RULES_V1, fast_call_p95_ms=3100.0, fast_run_p95_ms=12000.0, fast_dispatch_p95_ms=2500.0)
    assert latency_warnings(fast, fast_stats(), rules) == []
    rules = replace(RULES_V1, fast_call_p95_ms=3000.0)
    message = latency_warnings(fast, fast_stats(), rules)[0].message
    assert "exceeds the latency: fast budget of 3s;" in message


def test_branch_and_child_units_are_located_at_their_node(fast):
    slow = [2000.0] * 5
    stats = ProcessStats(process="intake", units={
        "edge:extract.done[save]": UnitStats(unit="edge:extract.done[save]", kind="edge", by_tier={
            "claude-code/cheap": TierStats(provider="claude-code", tier="cheap", call_latency_ms=slow)}),
        "review.judge": UnitStats(unit="review.judge", kind="agentic", by_tier={
            "claude-code/cheap": TierStats(provider="claude-code", tier="cheap", call_latency_ms=slow)}),
    })
    found = latency_findings(fast, stats, EdgesLock())
    assert [(f.code, f.unit, f.loc) for f in found] == [
        ("W-LATENCY-CALL", "review.judge", ("steps", "review")),
        ("W-LATENCY-CALL", "edge:extract.done[save]", ("edges", 1, "to", 0)),
    ]


def test_the_validator_reports_them_when_given_stats(make_repo):
    ws = workspace(make_repo)
    codes = [d.code for d in validate_process(ws, "intake", stats=fast_stats()).diagnostics]
    assert LATENCY_CODES <= set(codes)
    assert not LATENCY_CODES & {d.code for d in validate_process(ws, "intake").diagnostics}


def test_the_report_lists_them_with_their_unit(fast):
    report = build_report(fast, EdgesLock(), fast_stats(), commit="0" * 40)
    assert [(w["code"], w["unit"]) for w in report.warnings] == [
        ("W-LATENCY-CALL", "extract"), ("W-LATENCY-RUN", ""), ("W-LATENCY-DISPATCH", "")]
    assert all(w["message"] for w in report.warnings)


def test_every_emitted_code_is_in_the_code_table():
    tree = ast.parse(Path(latency_module.__file__).read_text())
    literals = {node.value for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.startswith("W-")}
    assert literals == set(CODES) == LATENCY_CODES
    assert {severity for severity, _ in CODES.values()} == {"warning"}
