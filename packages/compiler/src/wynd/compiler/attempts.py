"""One attempt: write files, static checks, interface check in the step venv, the package's test suite, and
per-example classification from the `WYND-EXPECT` lines (`$DRAFTS/05 §7.6`, PLAN §7 item 6).

Attempt tests run with `WYND_CASSETTE_MODE=record|replay`, `WYND_CASSETTE_RECORD_DIR=<scratch>/attempts/<node>/<n>/
cassettes`, `WYND_DEFAULT_PROVIDER=<root provider>` and `WYND_EVENTS_FILE=<scratch>/…/events.jsonl`.

Classification of an example's test: passed → `handled`; failed with `actual_exit == "error"`, `expected_exit !=
"error"` and error type `NotImplementedError` → `deferred`; anything else (wrong exit or outputs, another exception,
a collection/import error, a timeout, a missing test) → `wrong`. Only failing attempts are memoised (by test key):
a passing attempt leads to completion and its cassettes are needed.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from wynd.spec.hashing import canonical_json
from wynd.spec.interface import Interface, interfaces_equivalent
from wynd.spec.workspace import CASSETTES_DIR, PROCESS_FILE, STEP_LOCK_FILE, STEPS_DIR

if TYPE_CHECKING:
    from wynd.compiler.pipeline import CompileEnv
    from wynd.compiler.tools import ToolCatalog
    from wynd.process.testing import SuiteResult
    from wynd.spec.lockfiles import StepKind

EXAMPLE_TEST = re.compile(r"^test_example_(\d+)$")
EXPECT_LINE = re.compile(r"WYND-EXPECT (\{.*\})")
MESSAGE_LIMIT = 8 * 1024
SUITE_TIMEOUT_S = 1800


@dataclass
class FailureInfo:
    """One failing case as shown to the revise prompt ($DRAFTS/05 §13.4 "Failures section")."""
    test: str                                   # test function name, or "(static checks)" / "(interface)" / ...
    example: int | None                         # example number, None when not one example's test
    outcome: Literal["deferred", "wrong"]
    expected_exit: str | None = None            # from WYND-EXPECT when present
    actual_exit: str | None = None
    mismatches: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None         # {cause, message, error_type}
    message: str = ""                           # the failure text (tail kept, capped)


@dataclass
class AttemptResult:
    n: int
    kind: StepKind
    tier: str | None
    cases: dict[int, Literal["handled", "deferred", "wrong"]]     # example number -> class
    failures: list[FailureInfo]                                   # for the revise prompt
    static_errors: list[str]
    all_passed: bool
    handled: int
    deferred: int
    wrong: int
    cassettes: Path | None                                        # snapshot of this attempt's recordings
    events_file: Path | None
    duration_ms: int
    description: dict[str, Any] | None = None   # the step venv's `wynd.runtime.describe` JSON (lock interface source)


def attempt_dir(env: CompileEnv, node: str, n: int) -> Path:
    return env.scratch / "attempts" / node / str(n)


def test_key(files: dict[str, str], mode: str, requirements: Sequence[str]) -> str:
    """sha256 of {files: {relpath: sha256(content)}, mode, requirements}, 32 hex chars ($DRAFTS/05 §6.6)."""
    digests = {path: hashlib.sha256(text.encode()).hexdigest() for path, text in files.items()}
    payload = {"files": digests, "mode": mode, "requirements": sorted(requirements)}
    return hashlib.sha256(canonical_json(payload)).hexdigest()[:32]


def run_attempt(env: CompileEnv, pkg_dir: Path, files: dict[str, str], *, kind: StepKind, entrypoint: str,
                requirements: list[str], expected: Interface, mode: Literal["replay", "record"], n: int,
                catalog: ToolCatalog | None = None, upstream_nodes: Sequence[str] | None = None,
                effects: Sequence[str] | None = None, provider: str | None = None,
                memo: dict[str, dict[str, Any]] | None = None, node: str | None = None) -> AttemptResult:
    """Write `files` into `pkg_dir` (a `step.lock.yaml` is synthesised from the static report when `files` has
    none), lint + astcheck, `describe` in the step venv and compare with `expected`, then the package's tests.
    `catalog` defaults to the runtime builtins; `upstream_nodes` None skips the context-upstream check; `effects`
    None skips the subprocess check; `memo` is the session's `memo.tests`; `node` names the scratch dir (default
    the package name)."""
    from wynd.compiler.astcheck import check, lint_errors
    from wynd.compiler.codegen import BASES
    from wynd.compiler.tools import builtin_catalog

    started = time.monotonic()
    catalog = catalog or builtin_catalog()
    out = attempt_dir(env, node or pkg_dir.name, n)
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    pkg_dir.mkdir(parents=True, exist_ok=True)

    module, class_name = entrypoint.split(":")
    module_file = f"{module.replace('.', '/')}.py"
    source = files.get(module_file)
    if source is None:
        source = (pkg_dir / module_file).read_text(encoding="utf-8")
    numbers = _example_numbers(files, pkg_dir)
    static = check(source, class_name=class_name, base=BASES[kind], catalog=catalog,
                   upstream_nodes=None if upstream_nodes is None else list(upstream_nodes), effects=effects)
    errors = lint_errors(source, module_file) + static.errors

    written = dict(files)
    if STEP_LOCK_FILE not in written and not errors:
        written[STEP_LOCK_FILE] = _provisional_lock(pkg_dir.name, kind, entrypoint, static, catalog, effects)
    for rel, text in written.items():
        path = pkg_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    tier = _tier(written.get(STEP_LOCK_FILE)) if kind == "agentic" else None

    def all_wrong(test: str, message: str, description: dict | None = None) -> AttemptResult:
        return AttemptResult(
            n=n, kind=kind, tier=tier, cases={k: "wrong" for k in numbers},
            failures=[FailureInfo(test=test, example=None, outcome="wrong", message=_cap(message))],
            static_errors=errors, all_passed=False, handled=0, deferred=0, wrong=len(numbers), cassettes=None,
            events_file=None, duration_ms=_ms(started), description=description,
        )

    if errors:
        return all_wrong("(static checks)", "static checks failed:\n" + "\n".join(errors))
    key = test_key(written, mode, requirements)
    if memo is not None and key in memo:
        return replace(_from_memo(memo[key]), n=n)

    python = env.deps.step_python(requirements)
    try:
        description = env.deps.describe(python, pkg_dir, entrypoint)
    except Exception as err:  # noqa: BLE001 — an import or definition failure of generated code is a wrong attempt
        return all_wrong("(import)", str(err))
    diffs = interfaces_equivalent(expected, Interface.model_validate(description["interface"]))
    if diffs:
        return all_wrong("(interface)", "the step's models differ from the models block:\n" + "\n".join(diffs),
                         description)

    events_file = out / "events.jsonl"
    record_dir = out / CASSETTES_DIR if mode == "record" else None
    suite_env = {**os.environ, "WYND_EVENTS_FILE": str(events_file),
                 "WYND_CASSETTE_LITERALS": json.dumps(_literals(env, pkg_dir))}
    if provider is not None:
        suite_env["WYND_DEFAULT_PROVIDER"] = provider
    suite = env.deps.run_step_suite(pkg_dir, python=python, mode=mode, env=suite_env, junit=out / "junit.xml",
                                    basetemp=out / "pytest", record_dir=record_dir, timeout_s=SUITE_TIMEOUT_S)
    cases, failures = classify(suite, numbers)
    counts = {c: sum(1 for v in cases.values() if v == c) for c in ("handled", "deferred", "wrong")}
    result = AttemptResult(
        n=n, kind=kind, tier=tier, cases=cases, failures=failures, static_errors=[],
        all_passed=suite.passed and not failures and counts["handled"] == len(numbers),
        handled=counts["handled"], deferred=counts["deferred"], wrong=counts["wrong"],
        cassettes=record_dir, events_file=events_file if events_file.is_file() else None,
        duration_ms=_ms(started), description=description,
    )
    if memo is not None and not result.all_passed:
        memo[key] = _to_memo(result)
    return result


def classify(suite: SuiteResult, numbers: Sequence[int]) -> tuple[dict[int, str], list[FailureInfo]]:
    """Per-example classes and the failures to show the revise prompt (other failing tests count as failures but
    classify no example)."""
    cases: dict[int, str] = {}
    failures: list[FailureInfo] = []
    for case in suite.cases:
        match = EXAMPLE_TEST.match(case.name)
        number = int(match.group(1)) if match else None
        if case.outcome == "passed":
            if number is not None:
                cases[number] = "handled"
            continue
        info = _failure(case.name, number, case.outcome, case.message or "")
        failures.append(info)
        if number is not None:
            cases[number] = info.outcome
    missing = [k for k in numbers if k not in cases]
    for k in missing:
        cases[k] = "wrong"
    if missing:
        reason = suite.problem or "the test did not run"
        failures.append(FailureInfo(test="(suite)", example=None, outcome="wrong",
                                    message=_cap(f"examples {', '.join(map(str, missing))} did not run: {reason}")))
    elif suite.problem and not failures:
        failures.append(FailureInfo(test="(suite)", example=None, outcome="wrong", message=_cap(suite.problem)))
    return dict(sorted(cases.items())), failures


def _failure(test: str, number: int | None, outcome: str, message: str) -> FailureInfo:
    found = EXPECT_LINE.search(message) if outcome == "failed" else None
    if found is None:
        return FailureInfo(test=test, example=number, outcome="wrong", message=_cap(message))
    payload = json.loads(found.group(1))
    error = payload.get("error")
    deferred = (payload.get("actual_exit") == "error" and payload.get("expected_exit") != "error"
                and (error or {}).get("error_type") == "NotImplementedError")
    return FailureInfo(test=test, example=number, outcome="deferred" if deferred else "wrong",
                       expected_exit=payload.get("expected_exit"), actual_exit=payload.get("actual_exit"),
                       mismatches=list(payload.get("mismatches") or []), error=error, message=_cap(message))


def promote_cassettes(result: AttemptResult, pkg_dir: Path) -> list[Path]:
    """Copy an accepted attempt's recordings into `<pkg>/cassettes/` (stale entries removed)."""
    from wynd.runtime.cassettes import promote

    if result.cassettes is None:
        return []
    return promote(result.cassettes, pkg_dir / CASSETTES_DIR)


def _example_numbers(files: dict[str, str], pkg_dir: Path) -> list[int]:
    texts = [text for rel, text in files.items() if Path(rel).name.startswith("test_") and rel.endswith(".py")]
    if not texts:
        texts = [p.read_text(encoding="utf-8") for p in sorted(pkg_dir.glob("test_*.py"))]
    return sorted({int(m) for text in texts for m in re.findall(r"^def test_example_(\d+)\(", text, re.M)})


def _provisional_lock(name: str, kind: StepKind, entrypoint: str, static: Any, catalog: ToolCatalog,
                      effects: Sequence[str] | None) -> str:
    """What the runtime needs to run the tests (kind, entrypoint, tier, tools, MCP snapshots, effects)."""
    from wynd.compiler.lockfile import build_step_lock, render_step_lock

    lock = build_step_lock(name=name, kind=kind, entrypoint=entrypoint, step_id=name, proto=None, proto_hash=None,
                           interface=None, static=static, catalog=catalog, effects=effects or ())
    return render_step_lock(lock)


def _tier(lock_text: str | None) -> str | None:
    from wynd.spec.lockfiles import StepLock
    from wynd.spec.yamlio import parse_model

    if lock_text is None:
        return None
    return parse_model(lock_text, StepLock).tier or "cheap"


def _literals(env: CompileEnv, pkg_dir: Path) -> dict[str, str]:
    """`<process>` (process-local packages; a step-root package is its own base) and `<ws>`, with realpaths."""
    process_dir = pkg_dir.parents[1] if pkg_dir.parent.name == STEPS_DIR and (
        pkg_dir.parents[1] / PROCESS_FILE).is_file() else pkg_dir
    out: dict[str, str] = {}
    for path, placeholder in ((process_dir, "<process>"), (env.checkout, "<ws>")):
        out[str(path)] = placeholder
        out[os.path.realpath(path)] = placeholder
    return out


def _cap(text: str) -> str:
    return text if len(text) <= MESSAGE_LIMIT else "…" + text[-MESSAGE_LIMIT:]


def _ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _to_memo(result: AttemptResult) -> dict[str, Any]:
    data = asdict(result)
    data["cases"] = {str(k): v for k, v in result.cases.items()}
    for name in ("cassettes", "events_file"):
        data[name] = str(data[name]) if data[name] is not None else None
    return data


def _from_memo(data: dict[str, Any]) -> AttemptResult:
    data = dict(data)
    data["cases"] = {int(k): v for k, v in data["cases"].items()}
    data["failures"] = [FailureInfo(**f) for f in data["failures"]]
    for name in ("cassettes", "events_file"):
        data[name] = Path(data[name]) if data[name] is not None else None
    return AttemptResult(**data)
