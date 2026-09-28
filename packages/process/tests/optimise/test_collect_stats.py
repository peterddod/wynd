"""`percentile` and `collect_stats` over synthetic §3.13 traces (PLAN §6.1; `$DRAFTS/08 §3.3`)."""

import math

import pytest

from wynd.process.optimise import collect_stats, percentile


def test_percentile_is_nearest_rank():
    assert percentile([], 0.95) is None
    assert percentile([7.0], 0.5) == 7.0
    assert percentile([7.0], 0.95) == 7.0
    twenty = [float(x) for x in range(20, 0, -1)]          # unsorted input
    assert percentile(twenty, 0.5) == 10.0
    assert percentile(twenty, 0.95) == 19.0
    assert percentile(twenty, 1.0) == 20.0
    assert percentile(twenty, 0.01) == 1.0


def test_clean_live_runs(stats_of):
    stats = stats_of("demote")
    assert stats.process == "intake"
    assert stats.runs == 25
    assert list(stats.units) == ["read", "extract", "edge:extract.done[save]", "classify"]

    read = stats.units["read"]
    assert (read.kind, read.executions, read.by_tier) == ("deterministic", 25, {})
    assert read.duration_ms == [10.0 + i * 0.5 for i in range(1, 26)]

    extract = stats.units["extract"]
    assert (extract.kind, extract.executions) == ("agentic", 25)
    assert list(extract.by_tier) == ["claude-code/standard"]
    ts = extract.by_tier["claude-code/standard"]
    assert (ts.provider, ts.tier, ts.n, ts.fails, ts.vfail_execs, ts.attempts_sum, ts.calls) == (
        "claude-code", "standard", 25, 0, 0, 25, 25)
    assert ts.input_tokens == sum(900 + i for i in range(1, 26))
    assert ts.output_tokens == 25 * 90
    assert math.isclose(ts.cost_usd, 25 * 0.003)
    assert (ts.fail_rate, ts.vfail_rate, ts.mean_attempts) == (0.0, 0.0, 1.0)
    assert math.isclose(ts.cost_per_exec_usd, 0.003)
    assert ts.call_latency_ms == [1800.0 + i * 20 for i in range(1, 26)]
    assert (ts.call_p50_ms, ts.call_p95_ms) == (2060.0, 2280.0)      # ranks 13 and 24 of 25
    assert (ts.exec_p50_ms, ts.exec_p95_ms) == (2160.0, 2380.0)      # call latency + 100 ms of step overhead

    edge = stats.units["edge:extract.done[save]"]
    assert (edge.kind, edge.executions, list(edge.by_tier)) == ("edge", 25, ["claude-code/cheap"])
    assert edge.by_tier["claude-code/cheap"].n == 25


def test_validation_failures_without_a_model_on_step_end_count_at_the_calls_tier(stats_of):
    ts = stats_of("promote").units["extract"].by_tier["claude-code/cheap"]
    assert ts.n == 20
    assert ts.fails == 5                          # runs 1-5: exit error, cause output_validation
    assert ts.vfail_execs == 6                    # ... plus run 6's one retry
    assert ts.attempts_sum == 5 * 3 + 2 + 14
    assert ts.calls == 5 * 3 + 2 + 14
    assert ts.input_tokens == 5 * 3 * 900 + (880 + 906) + sum(900 + i for i in range(7, 21))
    assert (ts.fail_rate, ts.vfail_rate) == (0.25, 0.3)
    assert len(ts.call_latency_ms) == 31          # every live call's latency, valid or not


def test_replayed_runs_count_executions_but_never_evidence(stats_of):
    stats = stats_of("replay_only")
    assert stats.runs == 25
    for name in ("extract", "classify", "edge:extract.done[save]"):
        unit = stats.units[name]
        assert unit.executions == 25
        assert len(unit.duration_ms) == 25
        assert unit.by_tier == {}


def test_edge_checks(stats_of):
    stats = stats_of("edge")
    edge = stats.units["edge:extract.done[save]"]
    assert (edge.kind, edge.executions) == ("edge", 25)
    ts = edge.by_tier["claude-code/cheap"]
    assert ts.n == 25                             # the two failed checks come from their model.call events
    assert ts.fails == 2                          # a negative verdict (runs 3-10) is not a failure
    assert ts.vfail_execs == 2
    assert ts.attempts_sum == 2 * 3 + 23
    assert ts.calls == 2 * 3 + 23
    assert math.isclose(ts.cost_usd, 29 * 0.001)
    assert stats.runs == 25


def test_nested_steps_and_branches_are_keyed_by_path(stats_of):
    stats = stats_of("edge")
    review = stats.units["review"]
    assert (review.kind, review.executions, review.by_tier) == ("process", 8, {})   # usage but no model: no tier
    judge = stats.units["review.judge"]
    assert (judge.kind, judge.executions, list(judge.by_tier)) == ("agentic", 8, ["claude-code/cheap"])
    child_edge = stats.units["edge:review.judge.done[ok]"]
    assert (child_edge.kind, child_edge.executions) == ("edge", 8)
    assert child_edge.by_tier["claude-code/standard"].n == 8
    assert "edge:judge.done[ok]" not in stats.units


def test_dispatch_overhead_uses_top_level_steps_only(stats_of):
    stats = stats_of("nested")
    assert stats.runs == 1
    assert stats.run_duration_ms == [4755.0]
    # read 10 + extract 1900 + review 1955 (which contains judge and the child's check) + top-level check 850;
    # 40 ms left over 3 top-level transitions
    assert stats.dispatch_overhead_ms == [pytest.approx(40.0 / 3)]


def test_incomplete_runs_count_for_units_only(read_runs):
    ids, read = read_runs("demote")
    truncated = {ids[0]: [e for e in read(ids[0]) if e["type"] != "run.end"]}
    stats = collect_stats("intake", [ids[0]], truncated.__getitem__)
    assert (stats.runs, stats.run_duration_ms, stats.dispatch_overhead_ms) == (0, [], [])
    assert stats.units["extract"].executions == 1
    assert stats.units["extract"].by_tier["claude-code/standard"].n == 1


def test_only_the_given_runs_are_read(read_runs, stats_of):
    ids, _ = read_runs("demote")
    stats = stats_of("demote", ids[:3])
    assert stats.runs == 3
    assert stats.units["extract"].by_tier["claude-code/standard"].n == 3
    assert stats_of("demote", []).units == {}


def test_an_agentic_run_without_model_calls_is_not_evidence():
    events = [
        {"v": 1, "seq": 1, "ts": "2026-09-01T09:00:00Z", "run_id": "r", "type": "step.start", "step": "extract",
         "name": "extract", "process": "intake", "span": 1, "parent": None, "id": "intake#extract",
         "kind": "agentic", "run": 1},
        {"v": 1, "seq": 2, "ts": "2026-09-01T09:00:00Z", "run_id": "r", "type": "step.end", "step": "extract",
         "span": 1, "parent": None, "run": 1, "kind": "agentic", "exit": "error",
         "outputs": {"cause": "config", "message": "unknown provider"}, "attempts": 0, "validation_failures": 0,
         "timings": {"duration_ms": 3.0}, "usage": None, "model": None, "replayed": False},
        {"v": 1, "seq": 3, "ts": "2026-09-01T09:00:00Z", "run_id": "r", "type": "run.end", "exit": "error",
         "status": "failed", "duration_ms": 5.0},
    ]
    stats = collect_stats("intake", ["r"], lambda _: events)
    unit = stats.units["extract"]
    assert (unit.executions, unit.duration_ms, unit.by_tier) == (1, [3.0], {})
    assert stats.dispatch_overhead_ms == [2.0]


def test_serialised_stats_hide_samples_and_show_derived_values(stats_of):
    data = stats_of("demote").units["extract"].model_dump(mode="json")
    assert "duration_ms" not in data
    ts = data["by_tier"]["claude-code/standard"]
    assert "call_latency_ms" not in ts and "exec_duration_ms" not in ts
    assert ts["call_p95_ms"] == 2280.0
    assert ts["fail_rate"] == 0.0
