"""Diagnostics (PLAN §3.2)."""

import re
from pathlib import Path

import pytest

from wynd.spec import Diagnostic, SpecError, format_loc
from wynd.spec.errors import CODES

PACKAGE = Path(__file__).resolve().parents[1] / "src" / "wynd" / "spec"


def test_format_loc():
    assert format_loc(("edges", 3, "to", 0, "with", "dest")) == "edges[3].to[0].with.dest"
    assert format_loc(()) == ""
    assert format_loc((0, "a")) == "[0].a"


def test_format_full_and_partial():
    full = Diagnostic("error", "E-TO", "bad", file="p.yaml", line=3, column=5, loc=("edges", 0))
    assert full.format() == "p.yaml:3:5: error[E-TO] edges[0]: bad"
    assert Diagnostic("warning", "W-X", "hmm").format() == "warning[W-X]: hmm"
    assert Diagnostic("info", "I201", "filled", file="p.yaml", loc=("a",)).format() == "p.yaml: info[I201] a: filled"


def test_format_appends_snippet_lines_indented():
    diag = Diagnostic("error", "E-EXPR-SYNTAX", "oops", line=1, column=2, snippet="a b\n  ^")
    assert diag.format() == "1:2: error[E-EXPR-SYNTAX]: oops\n    a b\n      ^"


def test_to_json_lists():
    diag = Diagnostic("error", "E-REF-FIELD", "m", loc=("edges", 1), span=(3, 7), process="p")
    assert diag.to_json() == {
        "severity": "error", "code": "E-REF-FIELD", "message": "m", "file": None, "line": None, "column": None,
        "loc": ["edges", 1], "process": "p", "span": [3, 7], "snippet": None,
    }
    assert Diagnostic("error", "C", "m").to_json()["span"] is None


def test_spec_error_is_a_value_error_with_formatted_lines():
    diags = [Diagnostic("error", "E-A", "one", line=1, column=1), Diagnostic("error", "E-B", "two")]
    with pytest.raises(ValueError) as err:
        raise SpecError(diags)
    assert err.value.diagnostics == diags
    assert str(err.value) == "1:1: error[E-A]: one\nerror[E-B]: two"



def test_every_emitted_code_is_in_the_spec_namespace():
    emitted = {
        (path.name, code)
        for path in PACKAGE.rglob("*.py")
        if path.name != "errors.py" or path.parent.name == "expr"
        for code in re.findall(r"""["']([EWI]-[A-Z][A-Z0-9-]*)["']""", path.read_text(encoding="utf-8"))
    }
    assert len({code for _, code in emitted}) >= 30
    assert sorted((name, code) for name, code in emitted if code not in CODES) == []
