"""Drift tests for the sample data: the committed PDFs and every example text are generated from SAMPLES.

Run from the repo root: uv run pytest examples/invoices/tools
"""
import importlib.util
from pathlib import Path

import pytest
import yaml
from pypdf import PdfReader

TOOLS = Path(__file__).resolve().parent
PROCESS_DIR = TOOLS.parent / "processes" / "process_supplier_invoice"
EXAMPLES_DIR = PROCESS_DIR / "examples"
PROTO_DIR = PROCESS_DIR / "proto"

# extract_invoice_fields examples, in order, are the texts of these samples.
EXTRACT_SAMPLES = ["acme_inv_1042.pdf", "globex_inv_77810.pdf", "initech_inv_5521.pdf",
                   "hooli_credit_note_311.pdf", "shipping_notice.pdf"]


def _load_generator():
    # --import-mode=importlib does not put this directory on sys.path.
    spec = importlib.util.spec_from_file_location("make_sample_pdfs", TOOLS / "make_sample_pdfs.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


gen = _load_generator()


def read_yaml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def pdf_text(path: Path) -> tuple[str, int]:
    """(text, pages) exactly as the read_pdf step computes them."""
    reader = PdfReader(path, strict=True)
    return "\n".join((page.extract_text() or "").rstrip("\n") for page in reader.pages), len(reader.pages)


def sample_name(pdf_path: str) -> str:
    """`examples/<name>` (relative to the process dir) -> its SAMPLES key."""
    assert pdf_path.startswith("examples/"), pdf_path
    name = pdf_path.removeprefix("examples/")
    assert name in gen.SAMPLES, f"{pdf_path} is not a generated sample"
    return name


def test_generator_reproduces_committed_pdfs(tmp_path, monkeypatch):
    assert gen.OUT == EXAMPLES_DIR
    monkeypatch.setattr(gen, "OUT", tmp_path)
    gen.main()
    generated = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    committed = {p.name: p.read_bytes() for p in EXAMPLES_DIR.glob("*.pdf")}
    assert sorted(committed) == sorted(generated) == sorted(gen.SAMPLES)
    for name, data in generated.items():
        assert committed[name] == data, f"{name} drifted: run `uv run python tools/make_sample_pdfs.py`"


@pytest.mark.parametrize("name", sorted(gen.SAMPLES))
def test_pypdf_extracts_sample_text(name):
    assert pdf_text(EXAMPLES_DIR / name) == (gen.text_of(name), 1)


def test_read_pdf_examples_match_samples():
    examples = read_yaml(PROTO_DIR / "read_pdf.yaml")["examples"]
    assert any(ex["exit"] == "done" for ex in examples)
    for i, ex in enumerate(examples):
        pdf_path = ex["inputs"]["pdf_path"]
        if ex["exit"] == "error":
            assert not (PROCESS_DIR / pdf_path).exists(), f"examples[{i}] expects a missing file"
            continue
        assert ex["outputs"] == {"pages": 1, "text": gen.text_of(sample_name(pdf_path))}, f"examples[{i}]"


def test_extract_examples_are_the_sample_texts():
    examples = read_yaml(PROTO_DIR / "extract_invoice_fields.yaml")["examples"]
    assert [ex["inputs"]["invoice_text"] for ex in examples] == [gen.text_of(n) for n in EXTRACT_SAMPLES]


def test_example_texts_quoting_a_sample_equal_it():
    """An example text that starts with a sample's first line must be that sample's whole text."""
    texts_by_header: dict[str, set[str]] = {}
    for name, lines in gen.SAMPLES.items():
        texts_by_header.setdefault(lines[0], set()).add(gen.text_of(name))
    quoting = set()
    for path in sorted(PROTO_DIR.glob("*.yaml")):
        for i, ex in enumerate(read_yaml(path)["examples"]):
            for text in (ex["inputs"].get("invoice_text"), ex.get("outputs", {}).get("text")):
                if text is None or text.split("\n", 1)[0] not in texts_by_header:
                    continue
                assert text in texts_by_header[text.split("\n", 1)[0]], f"{path.name} examples[{i}] drifted"
                quoting.add(path.stem)
    assert quoting == {"read_pdf", "extract_invoice_fields", "fix_fields"}


def test_process_examples_use_samples_and_tmp_dirs():
    doc = read_yaml(PROCESS_DIR / "process.yaml")
    assert doc["examples"]
    for i, ex in enumerate(doc["examples"]):
        assert (EXAMPLES_DIR / sample_name(ex["inputs"]["pdf_path"])).is_file()
        # every declared output directory is redirected into the example's temporary dir
        assert ex["env"].keys() == doc["env"]["vars"].keys(), f"examples[{i}]"
        assert all(value.startswith("{tmp}/") for value in ex["env"].values()), f"examples[{i}]"


@pytest.mark.parametrize("blank", ["", "   "])
def test_render_pdf_rejects_blank_lines(blank):
    with pytest.raises(ValueError, match="blank lines"):
        gen.render_pdf(["Total: 5.00", blank])


def test_render_pdf_rejects_non_cp1252():
    with pytest.raises(UnicodeEncodeError):
        gen.render_pdf(["Total: ₹500"])


def test_render_pdf_escapes_string_delimiters(tmp_path):
    lines = ["Amount due (USD): 1\\2", "£ and € are WinAnsi"]
    path = tmp_path / "escaped.pdf"
    path.write_bytes(gen.render_pdf(lines))
    assert pdf_text(path) == ("\n".join(lines), 1)
