"""Wynd step count (deterministic)."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class Count(DeterministicStep):
    """Count the words of the text and save it as count.txt under dest."""

    class Input(BaseModel):
        text: str
        dest: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        words: int
        path: str

    def run(self, input: Input) -> Output:
        dest = Path(input.dest)
        dest.mkdir(parents=True, exist_ok=True)
        path = dest / "count.txt"
        path.write_text(input.text)
        return self.Output(words=len(input.text.split()), path=str(path))
