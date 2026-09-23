"""Loader and document-check diagnostics, pinned per fixture ($DRAFTS/01 §12.7).

Each `fixtures/<kind>/<case>.yaml` is loaded with the public loader for its kind (and, when it loads, checked with the
kind's document check); the formatted diagnostics must equal `<case>.expected.txt` line for line. Expression parsing is
the `expr_double` (conftest), so these pin how documents locate findings, not the expression language."""

from pathlib import Path

import pytest

from wynd.spec import (
    ProcessDoc,
    ProtoStep,
    SpecError,
    StepLock,
    WorkspaceConfig,
    check_process_doc,
    check_proto_step,
    parse_model,
)

FIXTURES = Path(__file__).parent / "fixtures"
KINDS = {
    "process": (ProcessDoc, check_process_doc),
    "proto": (ProtoStep, check_proto_step),
    "locks": (StepLock, None),
    "workspace": (WorkspaceConfig, None),
}
CASES = sorted(str(p.relative_to(FIXTURES)) for p in FIXTURES.glob("*/*.yaml"))


def diagnostics_of(rel: str) -> list:
    kind = rel.split("/")[0]
    model, check = KINDS[kind]
    try:
        doc = parse_model((FIXTURES / rel).read_text(encoding="utf-8"), model, rel)
    except SpecError as err:
        return err.diagnostics
    return check(doc) if check else []


@pytest.mark.parametrize("rel", CASES)
def test_fixture_diagnostics(rel, expr_double):
    expected = (FIXTURES / rel).with_suffix(".expected.txt").read_text(encoding="utf-8")
    assert "".join(d.format() + "\n" for d in diagnostics_of(rel)) == expected


def test_every_fixture_has_an_expectation():
    assert len(CASES) == 28
    assert all((FIXTURES / rel).with_suffix(".expected.txt").exists() for rel in CASES)


def test_check_table_covers_every_document_code(expr_double):
    codes = {d.code for d in diagnostics_of("process/check_table.yaml")}
    assert codes == {
        "E-ENTRY", "E-STEP-UNKNOWN", "E-EDGE-DUP", "E-EXIT-TARGET", "E-IGNORE-FORM", "E-BRANCH-NAME",
        "W-BRANCH-UNREACHABLE", "W-LIMITS-EXIT", "E-HANDLER-ROLE", "E-EXPR-FUNC", "E-EXAMPLE",
    }


def test_syntax_error_fails_load_through_load_process(tmp_path, expr_double):
    from wynd.spec import load_process

    path = tmp_path / "process.yaml"
    path.write_text((FIXTURES / "process/bad_expr.yaml").read_text())
    with pytest.raises(SpecError) as err:
        load_process(path)
    [diag] = err.value.diagnostics
    assert (diag.code, diag.file, diag.line, diag.column) == ("E-EXPR-SYNTAX", str(path), 17, 77)
    assert diag.span == (60, 64)

