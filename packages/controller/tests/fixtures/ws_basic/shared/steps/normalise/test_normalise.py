"""Tests for normalise: one per proto-step example."""
from pathlib import Path

from wynd.runtime.testing import expect, load_step, run_step

Normalise = load_step(Path(__file__).parent)


def test_example_1(tmp_path):
    result = run_step(Normalise, {"name": "  Ada "}, workspace=tmp_path)
    expect(result, exit="done", outputs={"name": "ada"})
