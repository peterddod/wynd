"""attempts: run_attempt with faked boundaries (lint/static short-circuit, interface mismatch, WYND-EXPECT
classification, memo, record-mode cassettes) and once for real (describe + pytest in this interpreter)."""

import json
import sys
from datetime import datetime
from pathlib import Path

from wynd.compiler.attempts import classify, promote_cassettes, run_attempt
from wynd.compiler.attempts import test_key as make_test_key
from wynd.compiler.pipeline import CompileDeps, CompileEnv
from wynd.compiler.testgen import step_tests
from wynd.process.testing import SuiteResult, TestCase
from wynd.spec.interface import interface_from_fields
from wynd.spec.lockfiles import StepLock
from wynd.spec.proto_step import Example
from wynd.spec.typelang import parse_type
from wynd.spec.yamlio import parse_model

INPUTS = {"text": parse_type("string")}
OUTPUTS = {"done": {"amount": parse_type("number"), "currency": parse_type("string")}, "empty": {}}
EXPECTED = interface_from_fields(INPUTS, OUTPUTS)

MODULE = '''\
"""Parse an amount of money."""
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class ParseAmount(DeterministicStep):
    class Input(BaseModel):
        text: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        amount: float
        currency: str

    class Empty(BaseModel):
        exit: Literal["empty"] = "empty"

    Output = Done | Empty

    def run(self, input: Input) -> Output:
        text = input.text.strip()
        if not text:
            return self.Empty()
        if text.startswith("£"):
            return self.Done(amount=float(text[1:]), currency="GBP")
        number, _, code = text.partition(" ")
        if code and number.replace(".", "", 1).isdigit():
            return self.Done(amount=float(number), currency=code)
        raise NotImplementedError("amount in words")
'''
EXAMPLES = [
    Example(inputs={"text": "£12.50"}, outputs={"amount": 12.5, "currency": "GBP"}),
    Example(inputs={"text": "3 EUR"}, outputs={"amount": 3.0, "currency": "GBP"}),          # wrong on purpose
    Example(inputs={"text": "twelve pounds fifty"}, outputs={"amount": 12.5, "currency": "GBP"}),
    Example(inputs={"text": ""}, exit="empty"),
]
TEST_FILE = step_tests("ParseAmount", EXAMPLES, INPUTS, source="proto/parse_amount.yaml")
FILES = {"parse_amount.py": MODULE, "test_parse_amount.py": TEST_FILE}


def expect_message(expected_exit, actual_exit, mismatches=(), error=None) -> str:
    payload = {"expected_exit": expected_exit, "actual_exit": actual_exit, "mismatches": list(mismatches),
               "error": error}
    return f"tmp_path = ...\n>       expect(result, ...)\nE       AssertionError: WYND-EXPECT {json.dumps(payload)}\n" \
           f"E       exit: expected {expected_exit!r}"


def suite(*cases: TestCase, problem: str | None = None) -> SuiteResult:
    failed = sum(c.outcome in ("failed", "error") for c in cases)
    return SuiteResult(subject="pkg", hash="", passed=not failed and problem is None,
                       counts={"passed": len(cases) - failed, "failed": failed, "error": 0, "skipped": 0},
                       cases=list(cases), problem=problem)


def case(name: str, outcome: str = "passed", message: str | None = None) -> TestCase:
    return TestCase(name=name, outcome=outcome, message=message, duration_ms=1)


DEFERRED = case("test_example_3", "failed", expect_message(
    "done", "error", error={"cause": "exception", "message": "amount in words", "error_type": "NotImplementedError"}))
WRONG = case("test_example_2", "failed", expect_message(
    "done", "done", [{"field": "currency", "expected": "GBP", "actual": "EUR"}]))


class Fakes:
    def __init__(self, result: SuiteResult | None = None, interface=EXPECTED, describe_error: str | None = None):
        self.result = result or suite(*(case(f"test_example_{n}") for n in range(1, 5)))
        self.interface = interface
        self.describe_error = describe_error
        self.suite_calls = []
        self.describe_calls = []

    def step_python(self, requirements):
        return Path("/venvs/x/bin/python")

    def describe(self, python, pkg_dir, entrypoint):
        self.describe_calls.append((python, pkg_dir, entrypoint))
        if self.describe_error:
            raise RuntimeError(self.describe_error)
        return {"kind": "deterministic", "interface": self.interface.model_dump(mode="json")}

    def run_step_suite(self, pkg_dir, **kwargs):
        self.suite_calls.append((pkg_dir, kwargs))
        if kwargs["record_dir"] is not None:                        # what a record run leaves behind
            kwargs["record_dir"].mkdir(parents=True)
            (kwargs["record_dir"] / "k.json").write_text(json.dumps({"wynd_cassette": 1, "key": "k"}))
        return self.result


def make_env(tmp_path: Path, fakes) -> CompileEnv:
    deps = CompileDeps(llm=None, registry=None, step_python=fakes.step_python, lock_requirements=lambda r: [],
                       run_step_suite=fakes.run_step_suite, run_process_examples=None, describe=fakes.describe,
                       list_mcp_tools=lambda entry: [], now=datetime.now)
    return CompileEnv(checkout=tmp_path / "ws", worktree=tmp_path / "ws", scratch=tmp_path / "scratch", deps=deps,
                      checkpoint=lambda data: None, commit=lambda message: None, log=lambda line: None)


def pkg_dir(tmp_path: Path) -> Path:
    path = tmp_path / "ws" / "processes" / "p" / "steps" / "parse_amount"
    path.mkdir(parents=True)
    (path.parents[1] / "process.yaml").write_text("kind: process\n")
    return path


def attempt(env, pkg, files=FILES, **kwargs):
    kwargs.setdefault("kind", "deterministic")
    kwargs.setdefault("entrypoint", "parse_amount:ParseAmount")
    kwargs.setdefault("requirements", [])
    kwargs.setdefault("expected", EXPECTED)
    kwargs.setdefault("mode", "replay")
    kwargs.setdefault("n", 1)
    return run_attempt(env, pkg, files, **kwargs)


def test_all_passing(tmp_path):
    fakes = Fakes()
    env, pkg = make_env(tmp_path, fakes), pkg_dir(tmp_path)
    result = attempt(env, pkg, provider="fake")
    assert result.all_passed and (result.handled, result.deferred, result.wrong) == (4, 0, 0)
    assert result.cases == {1: "handled", 2: "handled", 3: "handled", 4: "handled"}
    assert result.cassettes is None and result.tier is None and result.failures == []
    assert result.description["interface"] == EXPECTED.model_dump(mode="json")
    assert (pkg / "parse_amount.py").read_text() == MODULE
    lock = parse_model((pkg / "step.lock.yaml").read_text(), StepLock)           # synthesised for the test run
    assert (lock.kind, lock.entrypoint, lock.name) == ("deterministic", "parse_amount:ParseAmount", "parse_amount")
    [(suite_pkg, kwargs)] = fakes.suite_calls
    out = tmp_path / "scratch" / "attempts" / "parse_amount" / "1"
    assert suite_pkg == pkg and kwargs["mode"] == "replay" and kwargs["record_dir"] is None
    assert kwargs["junit"] == out / "junit.xml" and kwargs["basetemp"] == out / "pytest"
    run_env = kwargs["env"]
    assert run_env["WYND_EVENTS_FILE"] == str(out / "events.jsonl")
    assert run_env["WYND_DEFAULT_PROVIDER"] == "fake"
    literals = json.loads(run_env["WYND_CASSETTE_LITERALS"])
    assert literals[str(pkg.parents[1])] == "<process>" and literals[str(tmp_path / "ws")] == "<ws>"


def test_classification_handled_deferred_wrong(tmp_path):
    result_suite = suite(case("test_example_1"), WRONG, DEFERRED,
                         case("test_example_4", "error", "ImportError: boom"),
                         case("test_file_contents", "failed", "assert False"))
    fakes = Fakes(result_suite)
    result = attempt(make_env(tmp_path, fakes), pkg_dir(tmp_path))
    assert result.cases == {1: "handled", 2: "wrong", 3: "deferred", 4: "wrong"}
    assert (result.handled, result.deferred, result.wrong, result.all_passed) == (1, 1, 2, False)
    by_test = {f.test: f for f in result.failures}
    assert by_test["test_example_3"].outcome == "deferred"
    assert by_test["test_example_3"].error["error_type"] == "NotImplementedError"
    assert by_test["test_example_2"].mismatches == [{"field": "currency", "expected": "GBP", "actual": "EUR"}]
    assert by_test["test_example_4"].message == "ImportError: boom" and by_test["test_example_4"].expected_exit is None
    assert by_test["test_file_contents"].example is None                 # counts as a failure, classifies nothing


def test_expected_error_is_wrong_not_deferred():
    message = expect_message("error", "done")
    cases, failures = classify(suite(case("test_example_1", "failed", message)), [1])
    assert cases == {1: "wrong"} and failures[0].expected_exit == "error"
    other = expect_message("done", "error", error={"cause": "exception", "message": "x", "error_type": "KeyError"})
    assert classify(suite(case("test_example_1", "failed", other)), [1])[0] == {1: "wrong"}


def test_missing_examples_are_wrong():
    cases, failures = classify(suite(case("test_example_1"), problem="pytest timed out after 1800s"), [1, 2])
    assert cases == {1: "handled", 2: "wrong"}
    assert failures[-1].test == "(suite)" and "timed out" in failures[-1].message


def test_lint_short_circuits(tmp_path):
    fakes = Fakes()
    module = MODULE.replace('"""Parse an amount of money."""\n', '"""Parse."""\nSEEN = []\n')
    result = attempt(make_env(tmp_path, fakes), pkg_dir(tmp_path), {**FILES, "parse_amount.py": module})
    assert result.cases == {n: "wrong" for n in (1, 2, 3, 4)} and result.wrong == 4
    assert any(" L002 " in e for e in result.static_errors)
    assert result.failures[0].test == "(static checks)"
    assert fakes.describe_calls == [] and fakes.suite_calls == []


def test_static_check_short_circuits(tmp_path):
    fakes = Fakes()
    result = attempt(make_env(tmp_path, fakes), pkg_dir(tmp_path), entrypoint="parse_amount:Other")
    assert result.static_errors == ["the step class must be named Other (found ParseAmount)"]
    assert fakes.suite_calls == []


def test_interface_mismatch(tmp_path):
    other = interface_from_fields(INPUTS, {"done": {"amount": parse_type("integer")}, "empty": {}})
    fakes = Fakes(interface=other)
    result = attempt(make_env(tmp_path, fakes), pkg_dir(tmp_path))
    assert result.wrong == 4 and result.failures[0].test == "(interface)"
    assert "differ from the models block" in result.failures[0].message
    assert "field 'amount'" in result.failures[0].message
    assert fakes.suite_calls == []


def test_describe_failure(tmp_path):
    fakes = Fakes(describe_error="cannot describe: ModuleNotFoundError: No module named 'pypdf'")
    result = attempt(make_env(tmp_path, fakes), pkg_dir(tmp_path))
    assert result.wrong == 4 and "pypdf" in result.failures[0].message and result.failures[0].test == "(import)"


def test_failing_attempts_are_memoised(tmp_path):
    fakes = Fakes(suite(case("test_example_1"), WRONG, DEFERRED, case("test_example_4")))
    env, pkg, memo = make_env(tmp_path, fakes), pkg_dir(tmp_path), {}
    first = attempt(env, pkg, memo=memo, requirements=["pypdf>=6"])
    assert len(memo) == 1 and len(fakes.suite_calls) == 1
    again = attempt(env, pkg, memo=memo, requirements=["pypdf>=6"], n=2)
    assert len(fakes.suite_calls) == 1 and fakes.describe_calls[1:] == []   # nothing ran again
    assert again.n == 2 and again.cases == first.cases and again.failures == first.failures
    attempt(env, pkg, memo=memo, requirements=["pypdf>=6"], mode="record")   # another key: runs
    assert len(fakes.suite_calls) == 2


def test_passing_attempts_are_not_memoised(tmp_path):
    memo = {}
    attempt(make_env(tmp_path, Fakes()), pkg_dir(tmp_path), memo=memo)
    assert memo == {}


def test_test_key():
    key = make_test_key(FILES, "replay", ["b", "a"])
    assert len(key) == 32 and key == make_test_key(dict(reversed(FILES.items())), "replay", ["a", "b"])
    assert key != make_test_key(FILES, "record", ["a", "b"])
    assert key != make_test_key({**FILES, "parse_amount.py": MODULE + "\n"}, "replay", ["a", "b"])


AGENTIC_MODULE = '''\
"""Classify."""
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import AgenticStep
from wynd.runtime.tools import now


class ParseAmount(AgenticStep):
    """Parse the amount."""

    class Input(BaseModel):
        text: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        amount: float
        currency: str

    class Empty(BaseModel):
        exit: Literal["empty"] = "empty"

    Output = Done | Empty

    context = ["steps.read.outputs"]
    tools = [now]
    mcp = []

    def run(self, input: Input) -> Output: ...
'''


def test_record_mode_snapshots_cassettes_and_promotes(tmp_path):
    fakes = Fakes()
    env, pkg = make_env(tmp_path, fakes), pkg_dir(tmp_path)
    files = {**FILES, "parse_amount.py": AGENTIC_MODULE}
    result = attempt(env, pkg, files, kind="agentic", mode="record", upstream_nodes=["read"], n=3)
    assert result.all_passed and result.tier == "cheap"
    assert result.cassettes == tmp_path / "scratch" / "attempts" / "parse_amount" / "3" / "cassettes"
    assert fakes.suite_calls[0][1]["record_dir"] == result.cassettes
    lock = parse_model((pkg / "step.lock.yaml").read_text(), StepLock)
    assert lock.kind == "agentic" and lock.context == ["steps.read.outputs"] and [t.name for t in lock.tools] == ["now"]
    (pkg / "cassettes").mkdir()
    (pkg / "cassettes" / "stale.json").write_text(json.dumps({"wynd_cassette": 1}))
    written = promote_cassettes(result, pkg)
    assert written == [pkg / "cassettes" / "k.json"]
    assert sorted(p.name for p in (pkg / "cassettes").iterdir()) == ["k.json"]


def test_context_upstream_check(tmp_path):
    files = {**FILES, "parse_amount.py": AGENTIC_MODULE}
    result = attempt(make_env(tmp_path, Fakes()), pkg_dir(tmp_path), files, kind="agentic", upstream_nodes=[])
    assert result.static_errors and "not an upstream node" in result.static_errors[0]


def test_explicit_lock_is_used(tmp_path):
    lock = "wynd: 1\nname: parse_amount\nkind: agentic\nentrypoint: parse_amount:ParseAmount\ntier: standard\n"
    files = {**FILES, "parse_amount.py": AGENTIC_MODULE, "step.lock.yaml": lock}
    pkg = pkg_dir(tmp_path)
    result = attempt(make_env(tmp_path, Fakes()), pkg, files, kind="agentic")
    assert result.tier == "standard" and (pkg / "step.lock.yaml").read_text() == lock


def test_real_attempt_in_this_interpreter(tmp_path):
    """describe and the package's pytest run for real (this interpreter has wynd-runtime and pytest)."""
    from wynd.process.testing import run_step_suite
    from wynd.process.venvs import describe

    class Real(Fakes):
        def step_python(self, requirements):
            return Path(sys.executable)

    fakes = Real()
    env = make_env(tmp_path, fakes)
    env.deps.describe = describe
    env.deps.run_step_suite = run_step_suite
    result = attempt(env, pkg_dir(tmp_path))
    assert result.cases == {1: "handled", 2: "wrong", 3: "deferred", 4: "handled"}, result.failures
    assert not result.all_passed
    wrong = next(f for f in result.failures if f.example == 2)
    assert wrong.mismatches == [{"field": "currency", "expected": "GBP", "actual": "EUR"}]
    deferred = next(f for f in result.failures if f.example == 3)
    assert deferred.error["message"] == "NotImplementedError: amount in words"

