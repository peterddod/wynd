"""Performance smoke ($DRAFTS/01 §12.11): generous bounds, measured at roughly a tenth of them."""

import time

from wynd.spec.expr.evaluator import evaluate, parse


def test_cached_condition_evaluation(scope):
    text = "steps.validate.outputs.fixable and steps.fix.runs < 3"
    parse(text)
    started = time.perf_counter()
    for _ in range(10_000):
        evaluate(text, scope)
    assert time.perf_counter() - started < 0.5


def test_uncached_parse():
    texts = [f"steps.perf.outputs.x{i} or {i} > 1 and len(steps.perf.outputs.items) == {i}" for i in range(200)]
    started = time.perf_counter()
    for text in texts:
        parse(text)
    assert (time.perf_counter() - started) / len(texts) < 0.005
