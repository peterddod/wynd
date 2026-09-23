"""Tests for count: one per proto-step example."""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step

Count = load_step(Path(__file__).parent)


def test_example_1(tmp_path):
    result = run_step(Count, {"text": "HELLO WORLD", "dest": str(tmp_path / "out")}, workspace=tmp_path)
    expect(result, exit="done", outputs={"words": 2})
    assert (tmp_path / "out" / "count.txt").read_text() == "HELLO WORLD"
