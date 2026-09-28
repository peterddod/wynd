"""Canonical JSON and content hashes ($DRAFTS/01 §8, §12.8; PLAN §3.19). Tree-based step/process hashes are
wynd.process's (PROC-WS)."""

import hashlib
import math
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path, PurePosixPath

import pytest

from wynd.spec import (
    ProtoStep,
    RetryPolicy,
    TScalar,
    canonical_json,
    dependency_set_hash,
    hash_bytes,
    hash_obj,
    interface_from_fields,
    interface_hash,
    jsonable,
    normalize_requirement,
    parse_model,
    parse_type,
    proto_hash,
)

FIXTURES = Path(__file__).parent / "fixtures" / "proto"


class Colour(Enum):
    RED = "red"


def test_canonical_json():
    assert canonical_json({"b": 1, "a": [1.0, 1]}) == b'{"a":[1.0,1],"b":1}'
    assert canonical_json({"a": 1, "b": 2}) == canonical_json({"b": 2, "a": 1})
    assert canonical_json({"é": 1}) == '{"é":1}'.encode()
    assert canonical_json(1.0) != canonical_json(1)
    assert canonical_json(date(2026, 10, 1)) == b'"2026-10-01"'
    assert canonical_json(datetime(2026, 10, 1, 9, 30, tzinfo=timezone.utc)) == b'"2026-10-01T09:30:00+00:00"'
    assert canonical_json(PurePosixPath("a/b")) == b'"a/b"'
    assert canonical_json({0: "done", "*": "error"}) == b'{"*":"error","0":"done"}'
    assert canonical_json((1, 2)) == b"[1,2]"
    assert canonical_json(Colour.RED) == b'"red"'
    assert canonical_json(parse_type("list[date?]")) == b'"list[date?]"'
    assert canonical_json(RetryPolicy(run=1)) == b'{"run":1}'  # models: by alias, defaults excluded


@pytest.mark.parametrize("bad", [math.nan, math.inf, {1: "a", "1": "b"}, {(1, 2): "t"}, {1, 2}, object()])
def test_jsonable_rejects(bad):
    with pytest.raises((ValueError, TypeError)):
        jsonable(bad)


def test_hash_bytes_and_obj():
    assert hash_bytes(b"x") == "sha256:" + hashlib.sha256(b"x").hexdigest()
    assert hash_obj({"a": 1}) == hash_bytes(b'{"a":1}')


PROTO = """kind: proto_step
name: extract
instruction: |
  Extract the total.
inputs:
  text: string
outputs:
  total: number
  due: date
examples:
  - inputs: { text: "Total 3" }
    outputs: { total: 3, due: 2026-10-01 }
"""

REFORMATTED = """# comments and key order do not matter
name: extract
kind: proto_step
examples:
  - outputs:
      due: "2026-10-01"
      total: 3
    inputs:
      text: Total 3
outputs:
  done:
    due: date
    total: number
exits: [done]
inputs: {text: string}
instruction: "Extract the total.\\n"
"""


def test_proto_hash_ignores_form():
    base = parse_model(PROTO, ProtoStep)
    assert proto_hash(base) == proto_hash(parse_model(REFORMATTED, ProtoStep))
    assert proto_hash(base).startswith("sha256:")


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("Extract the total.", "Extract the grand total."),
        ("total: number\n  due", "total: integer\n  due"),
        ("total: 3,", "total: 4,"),
        ("examples:\n", "examples:\n  - inputs: { text: x }\n"),
        ("outputs:\n  total", "env: {deps: [pypdf]}\noutputs:\n  total"),
    ],
)
def test_proto_hash_changes_with_content(old, new):
    assert old in PROTO
    assert proto_hash(parse_model(PROTO.replace(old, new), ProtoStep)) != proto_hash(parse_model(PROTO, ProtoStep))


def test_proto_hash_of_fixture_is_stable():
    proto = parse_model((FIXTURES / "extract_invoice_fields.yaml").read_text(), ProtoStep)
    doc = proto.model_dump(mode="json", by_alias=True, exclude_defaults=True)
    assert proto_hash(proto) == hash_obj({"kind": "proto_step", "doc": doc})


def test_interface_hash_changes_when_a_field_is_added():
    small = interface_from_fields({"a": TScalar("string")}, {"done": {}})
    big = interface_from_fields({"a": TScalar("string"), "b": TScalar("string", True)}, {"done": {}})
    assert interface_hash(small) == interface_hash(interface_from_fields({"a": TScalar("string")}, {"done": {}}))
    assert interface_hash(small) != interface_hash(big)


def test_requirements():
    assert normalize_requirement("  PyPDF_2 >= 3 ,< 4 ") == "pypdf-2>= 3 ,< 4"
    assert normalize_requirement("requests  ==2.0") == "requests==2.0"
    assert normalize_requirement("Foo.Bar[extra]>=1; python_version  >= '3.12'") == (
        "foo-bar[extra]>=1; python_version >= '3.12'")
    same = dependency_set_hash(["requests==2.0", "pypdf-2>=3"])
    assert dependency_set_hash(["PyPDF_2>=3", "requests  ==2.0"]) == same
    assert dependency_set_hash(["a", "a", "A"]) == dependency_set_hash(["a"])
    assert dependency_set_hash(["a"], python="3.12") != dependency_set_hash(["a"])
    assert dependency_set_hash([]) == hash_obj({"requirements": [], "python": None})
