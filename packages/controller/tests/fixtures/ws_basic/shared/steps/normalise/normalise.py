"""Wynd step normalise (deterministic)."""
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class Normalise(DeterministicStep):
    """Trim and lower-case the name."""

    class Input(BaseModel):
        name: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        name: str

    def run(self, input: Input) -> Output:
        return self.Output(name=input.name.strip().lower())
