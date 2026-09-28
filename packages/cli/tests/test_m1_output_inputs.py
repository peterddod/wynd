"""Output formatting (`wynd.cli.output`) and command-line inputs/answers (`wynd.cli.inputs`; PLAN §9, §3.22;
`$DRAFTS/06 §9.1`, §10.6). `parse_inputs` runs against a real controller: the path-typed fields come from
`ProcessService.interface` of the fixture process `p3` (`doc: path`, `attachments: list[path]`, `cover: path?`)."""

from __future__ import annotations

import io
import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from wynd.cli.inputs import parse_answers, parse_inputs
from wynd.cli.output import fmt_age, fmt_duration, fmt_sha, fmt_usage, jsonable, print_json, table
from wynd.controller.errors import Invalid, NotFound
from wynd.controller.models import LocalTarget
from wynd.runtime.usage import Usage


# --- output -----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("ms", "text"), [
    (None, "-"), (0, "0ms"), (14.4, "14ms"), (999, "999ms"), (1000, "1.00s"), (1210, "1.21s"), (59_994, "59.99s"),
    (123_000, "2m03s"), (3_600_000, "60m00s"),
])
def test_fmt_duration(ms, text):
    assert fmt_duration(ms) == text


def test_fmt_usage_sha_and_age():
    usage = Usage(input_tokens=812, output_tokens=64, cost_usd=0.0011, calls=1)
    assert fmt_usage(usage, "haiku") == "haiku 812→64 tok $0.0011"
    assert fmt_usage(usage.model_dump(mode="json")) == "812→64 tok $0.0011"
    assert fmt_usage(Usage(input_tokens=3, output_tokens=1, calls=1)) == "3→1 tok"
    assert fmt_usage(Usage()) == "" and fmt_usage(None) == ""
    assert (fmt_sha("3f9c1e2aa0"), fmt_sha(None)) == ("3f9c1e2", "-")
    now = datetime(2026, 9, 22, 12, tzinfo=UTC)
    ages = [fmt_age(now - timedelta(seconds=s), now) for s in (5, 125, 7300, 200_000)]
    assert ages == ["5s", "2m", "2h", "2d"] and fmt_age(None, now) == "-"


def test_table_pads_columns_with_two_spaces():
    text = table(["JOB", "KIND", "REF"], [["job_1", "build", None], ["job_22", "test_live", "abc"]])
    assert text.splitlines() == [
        "JOB     KIND       REF",
        "job_1   build      -",
        "job_22  test_live  abc",
    ]


def test_print_json_is_one_document_and_wraps_lists(capsys):
    print_json([LocalTarget(), {"at": datetime(2026, 9, 22, tzinfo=UTC)}])
    assert json.loads(capsys.readouterr().out) == {"items": [{"kind": "local"}, {"at": "2026-09-22T00:00:00+00:00"}]}
    print_json(LocalTarget())
    assert json.loads(capsys.readouterr().out) == {"kind": "local"}
    assert jsonable({"t": (1, 2)}) == {"t": [1, 2]}


# --- inputs -----------------------------------------------------------------------------------------------------------

@pytest.fixture
def ctl(workspace, make_controller):
    return make_controller(workspace)


def test_values_are_yaml_scalars_and_later_sources_win(ctl, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "in.yaml").write_text("pages: 1\nnote: from the file\ndue: 2026-10-01\nkeep: file\n")
    inputs = parse_inputs(ctl, "p3", inputs_file="in.yaml", inputs_json='{"pages": 2, "note": "from json"}',
                          pairs=["pages=3", "flag=true", "ratio=0.5", "blank=", "spaces=   ", "raw={not: [yaml",
                                 "when=2026-10-01"])
    assert inputs == {"pages": 3, "note": "from json", "due": "2026-10-01", "keep": "file", "flag": True,
                      "ratio": 0.5, "blank": "", "spaces": "   ", "raw": "{not: [yaml", "when": "2026-10-01"}


def test_path_fields_are_made_absolute_against_the_cwd(ctl, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    inputs = parse_inputs(ctl, "p3", pairs=["doc=docs/a.pdf", "attachments=[x.pdf, /abs/y.pdf]", "cover=../c.png",
                                            "pages=2"])
    assert inputs == {"doc": str(tmp_path / "docs/a.pdf"), "attachments": [str(tmp_path / "x.pdf"), "/abs/y.pdf"],
                      "cover": str(tmp_path.parent / "c.png"), "pages": 2}
    other = tmp_path / "elsewhere"
    assert parse_inputs(ctl, "p3", pairs=["doc=a.pdf"], cwd=other) == {"doc": str(other / "a.pdf")}
    assert parse_inputs(ctl, "p3", pairs=["doc=/abs/a.pdf", "cover="]) == {"doc": "/abs/a.pdf", "cover": ""}
    assert parse_inputs(ctl, "p1", pairs=["text=rel/x"]) == {"text": "rel/x"}       # a string field is left alone


def test_inputs_from_stdin(ctl, monkeypatch):
    monkeypatch.setattr("sys.stdin", io.StringIO('{"pages": 7}'))
    assert parse_inputs(ctl, "p3", inputs_file="-") == {"pages": 7}


@pytest.mark.parametrize(("kwargs", "message"), [
    ({"pairs": ["novalue"]}, "--input expects KEY=VALUE"),
    ({"pairs": ["=x"]}, "--input expects KEY=VALUE"),
    ({"inputs_json": "{nope"}, "--inputs is not valid JSON"),
    ({"inputs_json": "[1, 2]"}, "--inputs must be a mapping"),
    ({"inputs_file": "missing.yaml"}, "cannot read missing.yaml"),
])
def test_bad_inputs_are_invalid(ctl, tmp_path, monkeypatch, kwargs, message):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(Invalid, match=message) as info:
        parse_inputs(ctl, "p3", **kwargs)
    assert info.value.exit == 2


def test_inputs_file_must_be_a_mapping_and_the_process_must_exist(ctl, tmp_path):
    (tmp_path / "list.yaml").write_text("- 1\n- 2\n")
    with pytest.raises(Invalid, match="must be a mapping"):
        parse_inputs(ctl, "p3", inputs_file=str(tmp_path / "list.yaml"))
    (tmp_path / "bad.yaml").write_text("a: [unclosed\n")
    with pytest.raises(Invalid, match="not valid JSON or YAML"):
        parse_inputs(ctl, "p3", inputs_file=str(tmp_path / "bad.yaml"))
    with pytest.raises(NotFound):
        parse_inputs(ctl, "nope", pairs=["a=1"])
    assert parse_inputs(ctl, "nope") == {}


# --- answers ----------------------------------------------------------------------------------------------------------

def test_answers_file_then_pairs(tmp_path):
    answers_file = tmp_path / "answers.yaml"
    answers_file.write_text(yaml.safe_dump({
        "extract.schema": True,
        "extract.example1": "accept",
        "extract.example2": "It should be not_an_invoice: we never pay without a due date.\n",
        "extract.example3": {"exit": "not_an_invoice", "outputs": {}},
        "extract.example4": 3,
        "extract.example5": None,
    }))
    answers = parse_answers(answers_file, ["extract.example1=reject", "q.x=a=b"])
    assert answers == {
        "extract.schema": "yes",
        "extract.example1": "reject",
        "extract.example2": "It should be not_an_invoice: we never pay without a due date.\n",
        "extract.example3": json.dumps({"exit": "not_an_invoice", "outputs": {}}),
        "extract.example4": "3",
        "extract.example5": "",
        "q.x": "a=b",
    }
    assert parse_answers() == {}
    (tmp_path / "empty.yaml").write_text("")
    assert parse_answers(tmp_path / "empty.yaml") == {}
    with pytest.raises(Invalid, match="--answer expects QID=TEXT"):
        parse_answers(pairs=["no-equals"])
    (tmp_path / "list.yaml").write_text("- a\n")
    with pytest.raises(Invalid, match="must be a mapping"):
        parse_answers(tmp_path / "list.yaml")
