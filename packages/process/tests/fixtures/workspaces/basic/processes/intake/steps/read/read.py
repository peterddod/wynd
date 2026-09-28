"""Fixture step: the text of a file (never imported by the loader tests)."""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from wynd.runtime.step import DeterministicStep


class Read(DeterministicStep):
    class Input(BaseModel):
        path: Path

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        text: str

    def run(self, input: Input) -> Output:
        return self.Output(text=input.path.read_text())
