"""Type-check golden cases (PLAN §6.1 typecheck row, §6.3 pass 6).

Each `fixtures/cases/<case>/` holds the files it adds to or replaces in `fixtures/base/` (a clean, fully typed
workspace) and an `expected.json`: EVERY diagnostic of the report as `{code, severity, path, line, message}`, compared
order-insensitively, so each case also proves nothing else fires.
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
CASES = sorted(p.name for p in (FIXTURES / "cases").iterdir() if p.is_dir())
TYPE_CODES = {"E-TYPE-OP", "W-TYPE-NULL", "E-TYPE-ASSIGN", "W-TYPE-ASSIGN"}


def catalog(name: str) -> ProviderInfo:
    return ProviderInfo(name, "agent", {}, EnvFragment())


def case_files(case: str) -> dict[str, bytes]:
    root = FIXTURES / "cases" / case
    return {
        str(path.relative_to(root)): path.read_bytes()
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.name != "expected.json"
    }


def key(row: dict) -> tuple:
    return row["code"], row["severity"], row["path"], row["line"], row["message"]


@pytest.mark.parametrize("case", CASES)
def test_case(case, make_repo):
    expected = json.loads((FIXTURES / "cases" / case / "expected.json").read_text())
    ws = make_repo(source=FIXTURES / "base", files=case_files(case))
    report = validate_process(load_workspace(ws), expected["process"], providers=catalog)
    got = [
        {"code": d.code, "severity": d.severity, "path": format_loc(d.loc), "line": d.line, "message": d.message}
        for d in report.diagnostics
    ]
    assert sorted(map(key, got)) == sorted(map(key, expected["diagnostics"]))
    for d in report.diagnostics:
        assert (d.process, d.file) == ("main", "processes/main/process.yaml")
    assert report.ok == all(row["severity"] != "error" for row in expected["diagnostics"])


def test_every_code_has_a_case():
    seen = set()
    for case in CASES:
        rows = json.loads((FIXTURES / "cases" / case / "expected.json").read_text())["diagnostics"]
        seen |= {row["code"] for row in rows}
    assert TYPE_CODES <= seen
    assert "clean" in CASES
