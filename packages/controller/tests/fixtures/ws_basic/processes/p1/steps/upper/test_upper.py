"""Tests for upper: one per proto-step example."""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step

Upper = load_step(Path(__file__).parent)


def test_example_1(tmp_path):
    result = run_step(Upper, {"text": "hello world"}, workspace=tmp_path)
    expect(result, exit="done", outputs={"text": "HELLO WORLD"})


def test_example_2(tmp_path):
    result = run_step(Upper, {"text": "   "}, workspace=tmp_path)
    expect(result, exit="empty")
