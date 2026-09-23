"""Fixture step echo (deterministic): shouts its text; imports a sibling module relatively."""

from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep

from .helpers import shout


class Echo(DeterministicStep):
    """Repeat the text in capitals and count its characters."""

    class Input(BaseModel):
        text: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        text: str
        length: int

    class Empty(BaseModel):
        exit: Literal["empty"] = "empty"

    Output = Done | Empty

    def run(self, input: Input) -> Output:
        if not input.text:
            return self.Empty()
        return self.Done(text=shout(input.text), length=len(input.text))
