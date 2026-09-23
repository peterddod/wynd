"""Validator golden cases (PLAN §6.6; `$DRAFTS/04 §18.3`).

Each `fixtures/cases/<case>/` holds the files it adds to or replaces in `fixtures/base/` (a clean workspace) and an
`expected.json`: the process to validate and EVERY diagnostic of the report as `{code, severity, process, path,
file}` (`path` = `format_loc(loc)`), compared order-insensitively. So each case also proves nothing else fires.
"""

import json
from pathlib import Path

import pytest

from wynd.process.validation import validate_process
from wynd.process.workspace import load_workspace
from wynd.runtime.providers import ProviderInfo
from wynd.spec.errors import format_loc
from wynd.spec.fragments import EnvFragment

FIXTURES = Path(__file__).parent / "fixtures"
BASE = FIXTURES / "base"
CASES = sorted(p.name for p in (FIXTURES / "cases").iterdir() if p.is_dir())
KINDS = {"claude-code": "agent", "anthropic": "model", "fake": "agent"}


def catalog(name: str) -> ProviderInfo:
    """A fixed provider catalog (KeyError for anything else, like `provider_info`)."""
    return ProviderInfo(name, KINDS[name], {}, EnvFragment())


def case_files(case: str) -> dict[str, bytes]:
    root = FIXTURES / "cases" / case
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "expected.json" and "__pycache__" not in path.parts
    }


def rows(diagnostics) -> list[tuple]:
    return sorted((d.code, d.severity, d.process, format_loc(d.loc), d.file) for d in diagnostics)


@pytest.mark.parametrize("case", CASES)
def test_golden(case, make_repo):
    expected = json.loads((FIXTURES / "cases" / case / "expected.json").read_text())
    ws = make_repo(source=BASE, files=case_files(case))
    report = validate_process(load_workspace(ws), expected["process"], providers=catalog)
    want = sorted((e["code"], e["severity"], e["process"], e["path"], e["file"]) for e in expected["diagnostics"])
    assert rows(report.diagnostics) == want
    assert report.ok == all(e["severity"] != "error" for e in expected["diagnostics"])


def test_every_case_is_listed():
    assert "clean" in CASES and len(CASES) >= 20
    for case in CASES:
        assert (FIXTURES / "cases" / case / "expected.json").is_file(), case


def test_messages_and_positions(make_repo):
    """Templates are filled from the code table and located in the document (spot checks)."""
    ws = make_repo(source=BASE, files=case_files("e204"))
    [d] = validate_process(load_workspace(ws), "main", providers=catalog).diagnostics
    assert d.message == "edge from 'first.oops': step 'first' has no exit 'oops' (exits: done, error)"
    assert (d.file, d.line, d.column) == ("processes/main/process.yaml", 15, 11)

    ws = make_repo(source=BASE, files=case_files("i201"))
    report = validate_process(load_workspace(ws), "main", providers=catalog)
    assert [d.message for d in report.diagnostics] == [
        "branch 'first.done[0]' → 'check' lies on a cycle; max_traversals defaulted to 10",
        "branch 'check.bad[0]' → 'first' lies on a cycle; max_traversals defaulted to 10",
    ]

    ws = make_repo(source=BASE, files=case_files("e212"))
    report = validate_process(load_workspace(ws), "main", providers=catalog)
    assert sorted(d.message for d in report.diagnostics) == [
        "branch to 'second': with: binds unknown input(s) val",
        "branch to 'second': with: does not bind required input(s) value",
    ]

    ws = make_repo(source=BASE, files=case_files("w202"))
    report = validate_process(load_workspace(ws), "main", providers=catalog)
    provisional = [d for d in report.diagnostics if d.code in ("E212", "E-REF-FIELD")]
    assert provisional and all(
        d.severity == "warning" and d.message.endswith(" [provisional: interface inferred from examples]")
        for d in provisional
    )


def test_lint_findings_are_errors_with_file_positions(make_repo):
    ws = make_repo(source=BASE, files=case_files("lint"))
    report = validate_process(load_workspace(ws), "main", providers=catalog)
    assert not report.ok
    found = {(d.code, d.file, d.line) for d in report.diagnostics}
    assert found == {
        ("L001", "processes/main/steps/second/helpers.py", 6),
        ("L002", "processes/main/steps/second/helpers.py", 2),
        ("L003", "processes/main/steps/second/second.py", 10),
        ("L004", "processes/main/steps/second/second.py", 13),
        ("L005", "processes/main/steps/second/second.py", 19),
    }
    assert all(d.severity == "error" and d.process == "main" for d in report.diagnostics)
