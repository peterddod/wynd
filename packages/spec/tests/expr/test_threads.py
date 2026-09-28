"""Thread safety: one shared parser and parse cache, 8 threads x 1000 parse+evaluate of distinct expressions give the
same results as computed directly ($DRAFTS/01 §7.1, §12)."""

import threading
from concurrent.futures import ThreadPoolExecutor

from wynd.spec.expr.errors import ExprSyntaxError
from wynd.spec.expr.evaluator import evaluate, parse

THREADS = 8
PER_THREAD = 1000


def expression(t: int, i: int) -> str:
    return f'if steps.fix.runs + {t} * {i} > {i} then "t{t}-{i}" else steps.read.outputs.text'


def expected(t: int, i: int) -> list[str]:
    out = [f"t{t}-{i}" if 1 + t * i > i else "INVOICE 42"]
    if i % 50 == 0:
        out.append(f"1:{len(f'steps.x{t}_{i} <') + 1}: unexpected end of expression; expected a value")
    return out


def test_parse_and_evaluate_from_threads(scope):
    parse.cache_clear()
    start = threading.Barrier(THREADS)

    def work(t: int) -> list[str]:
        start.wait()
        out = []
        for i in range(PER_THREAD):
            out.append(evaluate(expression(t, i), scope))
            if i % 50 == 0:  # syntax errors build their messages from the parser state too
                try:
                    parse(f"steps.x{t}_{i} <")
                    out.append("parsed")
                except ExprSyntaxError as e:
                    out.append(str(e))
        return out

    with ThreadPoolExecutor(max_workers=THREADS) as pool:
        results = list(pool.map(work, range(THREADS)))

    for t, got in enumerate(results):
        assert got == [item for i in range(PER_THREAD) for item in expected(t, i)]
