"""testgen: golden step test files, and a generated file actually run against a small step package."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from wynd.compiler.schemas import canonical
from wynd.compiler.testgen import step_tests
from wynd.spec.hashing import proto_hash
from wynd.spec.proto_step import Example, load_proto_step
from wynd.spec.typelang import parse_type

GOLDEN = Path(__file__).parent / "fixtures" / "golden" / "testgen"
PROTOS = Path(__file__).resolve().parents[3] / "examples/invoices/processes/process_supplier_invoice/proto"


def golden(name: str) -> str:
    return (GOLDEN / f"{name}.py.golden").read_text()


def dogfood(name: str, **kwargs) -> str:
    proto = load_proto_step(PROTOS / f"{name}.yaml")
    examples = [canonical(e, proto.inputs, proto.outputs) for e in proto.examples]
    class_name = "".join(p.title() for p in name.split("_"))
    return step_tests(class_name, examples, proto.inputs, source=f"proto/{name}.yaml",
                      proto_hash=proto_hash(proto), **kwargs)


def fields(spec: dict) -> dict:
    return {name: parse_type(t) for name, t in spec.items()}


PARSE_EXAMPLES = [
    Example(inputs={"text": "£12.50"}, outputs={"amount": 12.5, "currency": "GBP"}),
    Example(inputs={"text": "12.50 GBP"}, outputs={"amount": 12.5, "currency": "GBP"}),
    Example(inputs={"text": "twelve pounds fifty"}, outputs={"amount": 12.5, "currency": "GBP"},
            description="What should happen when the amount is written in words?"),
    Example(inputs={"text": ""}, exit="empty"),
]


@pytest.mark.parametrize("name", ["read_pdf", "save_record", "fix_fields", "extract_invoice_fields"])
def test_dogfood_goldens(name):
    assert dogfood(name) == golden(name)


def test_split_deterministic_half_defers():
    text = step_tests("ParseAmount", PARSE_EXAMPLES, fields({"text": "string"}), source="proto/parse_amount.yaml",
                      deferred=[3], partner="parse_amount_agentic")
    assert text == golden("split_deterministic")


def test_free_text_fields_become_present():
    examples = [Example(inputs={"q": "x"}, outputs={"answer": "yes", "reason": "because"}),
                Example(inputs={"q": "y"}, outputs={"answer": "no"})]
    text = step_tests("Ask", examples, fields({"q": "string"}), source="proto/ask.yaml", free_text=["reason"])
    assert 'expect(result, exit="done", outputs={\'answer\': \'yes\'}, present=[\'reason\'])' in text
    assert 'expect(result, exit="done", outputs={\'answer\': \'no\'})' in text


def test_step_root_base_depth_and_error_example():
    examples = [Example(inputs={"file": "data/in.txt"}, outputs={"n": 1}), Example(inputs={"file": "/abs/x"},
                                                                                  exit="error")]
    text = step_tests("Count", examples, fields({"file": "path"}), source="shared:count/proto.yaml", step_root=True)
    assert "BASE = Path(__file__).resolve().parents[0]   # relative example paths resolve against the package " \
           "directory" in text
    assert "str(BASE / 'data/in.txt')" in text
    assert "{'file': '/abs/x'}" in text                        # absolute paths stay as given
    assert text.rstrip().endswith('expect(result, exit="error")')


def test_no_base_or_sub_tmp_when_unused():
    text = step_tests("ParseAmount", PARSE_EXAMPLES[:1], fields({"text": "string"}), source="proto/p.yaml")
    assert "BASE" not in text and "sub_tmp" not in text
    assert "from wynd.runtime.testing import expect, load_step, run_step\n" in text


def test_output_is_deterministic_and_valid_python():
    first = dogfood("save_record")
    assert first == dogfood("save_record")
    compile(first, "test_save_record.py", "exec")


STEP_MODULE = '''\
"""A tiny deterministic step for the testgen round trip."""
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
        if code:
            return self.Done(amount=float(number), currency=code)
        raise NotImplementedError("amount in words")
'''

STEP_LOCK = """\
wynd: 1
name: parse_amount
kind: deterministic
entrypoint: parse_amount:ParseAmount
"""


def test_generated_tests_run_against_a_step(tmp_path):
    """The generated file runs under pytest: handled examples pass, the split half's deferral is expected."""
    pkg = tmp_path / "parse_amount"
    pkg.mkdir()
    (pkg / "parse_amount.py").write_text(STEP_MODULE)
    (pkg / "step.lock.yaml").write_text(STEP_LOCK)
    (pkg / "test_parse_amount.py").write_text(
        step_tests("ParseAmount", PARSE_EXAMPLES, fields({"text": "string"}), source="proto/parse_amount.yaml",
                   deferred=[3], partner="parse_amount_agentic"))
    ini = tmp_path / "pytest.ini"
    ini.write_text("[pytest]\naddopts = -p no:cacheprovider --import-mode=importlib\n")
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-c", str(ini), "--rootdir", str(pkg), "--confcutdir", str(pkg),
         "--basetemp", str(tmp_path / "bt"), str(pkg)],
        capture_output=True, text=True, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "4 passed" in proc.stdout
