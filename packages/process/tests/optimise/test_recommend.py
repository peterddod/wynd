"""Rule set v1: one test per rule id plus the threshold boundaries (PLAN §6.1; `$DRAFTS/08 §3.4`)."""

from dataclasses import replace

import pytest

from wynd.process.optimise import RULES_V1, TierStats, UnitStats, recommend


def ts(tier, n, *, fails=0, vfails=0, attempts=None, provider="claude-code"):
    return TierStats(provider=provider, tier=tier, n=n, fails=fails, vfail_execs=vfails,
                     attempts_sum=n if attempts is None else attempts)


def unit(*tiers):
    return UnitStats(unit="extract", kind="agentic", by_tier={f"{t.provider}/{t.tier}": t for t in tiers})


def rec(tier, *tiers, rules=RULES_V1):
    return recommend(unit(*tiers), "claude-code", tier, rules)


def test_r0_without_stats_or_below_min_samples():
    r = rec("standard")
    assert (r.action, r.rule, r.from_tier, r.to_tier, r.applicable) == ("keep", "R0", "standard", None, False)
    assert r.reason == "only 0 live executions at standard; need 20"
    r = rec("standard", ts("standard", 19))
    assert (r.rule, r.reason) == ("R0", "only 19 live executions at standard; need 20")
    assert rec("standard", ts("standard", 20)).rule == "D1"
    assert rec("standard", ts("standard", 10), rules=replace(RULES_V1, min_samples=10)).rule == "D1"


def test_r0_reads_only_the_current_provider():
    assert rec("cheap", ts("cheap", 50, fails=50, provider="anthropic")).rule == "R0"


def test_p1_promotes_one_tier():
    r = rec("cheap", ts("cheap", 20, fails=5, vfails=5, attempts=30))
    assert (r.action, r.rule, r.from_tier, r.to_tier, r.applicable) == ("promote", "P1", "cheap", "standard", True)
    assert r.reason == "5 failures, 25.0% validation failures, 1.50 attempts over 20 executions at cheap"
    assert rec("standard", ts("standard", 20, fails=5)).to_tier == "strong"


@pytest.mark.parametrize(("fails", "vfails", "rule"), [
    (1, 0, "P1"),        # fail rate exactly 0.05 promotes
    (0, 4, "P1"),        # validation-failure rate exactly 0.20 promotes
    (0, 3, "K1"),        # 0.15: within bounds (and cheap cannot be demoted)
])
def test_p1_boundaries(fails, vfails, rule):
    assert rec("cheap", ts("cheap", 20, fails=fails, vfails=vfails)).rule == rule


def test_p2_flags_failures_at_the_strongest_tier():
    r = rec("strong", ts("strong", 20, fails=2))
    assert (r.action, r.rule, r.to_tier, r.applicable) == ("flag", "P2", None, False)
    assert r.reason == "failing at the strongest tier: improve examples/instruction or split the step"


def test_h1_keeps_when_the_tier_below_failed_before():
    r = rec("standard", ts("standard", 20), ts("cheap", 5, fails=5, vfails=5))
    assert (r.action, r.rule, r.to_tier, r.applicable) == ("keep", "H1", None, False)
    assert r.reason == "cheap failed before: fail 100.0%, vfail 100.0% over 5"


def test_h1_needs_enough_samples_below_on_the_same_provider():
    assert rec("standard", ts("standard", 20), ts("cheap", 4, fails=4)).rule == "D1"
    assert rec("standard", ts("standard", 20), ts("cheap", 5, fails=5, provider="anthropic")).rule == "D1"
    assert rec("standard", ts("standard", 20), ts("cheap", 5)).rule == "D1"      # below was clean


def test_d1_demotes_one_tier():
    r = rec("standard", ts("standard", 60))
    assert (r.action, r.rule, r.from_tier, r.to_tier, r.applicable) == ("demote", "D1", "standard", "cheap", True)
    assert r.reason == "0 failures, 0.0% validation failures, 1.00 attempts over 60 executions at standard"
    assert rec("strong", ts("strong", 20)).to_tier == "standard"


@pytest.mark.parametrize(("n", "fails", "vfails", "attempts", "rule"), [
    (50, 0, 1, 50, "D1"),     # validation-failure rate exactly 0.02 still demotes
    (50, 0, 2, 50, "K1"),     # 0.04 does not
    (20, 0, 0, 21, "D1"),     # mean attempts exactly 1.05 still demotes
    (20, 0, 0, 22, "K1"),     # 1.10 does not
    (100, 1, 0, 100, "K1"),   # any failure blocks a demotion (fail rate 0.01 is below P1)
])
def test_d1_boundaries(n, fails, vfails, attempts, rule):
    assert rec("standard", ts("standard", n, fails=fails, vfails=vfails, attempts=attempts)).rule == rule


def test_k1_keeps_clean_cheap_units():
    r = rec("cheap", ts("cheap", 40))
    assert (r.action, r.rule, r.reason, r.applicable) == ("keep", "K1", "within bounds", False)
