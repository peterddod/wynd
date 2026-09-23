"""Wynd step upper (deterministic)."""
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class Upper(DeterministicStep):
    """Upper-case the text; blank text takes the empty exit."""

    class Input(BaseModel):
        text: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        text: str

    class Empty(BaseModel):
        exit: Literal["empty"] = "empty"

    Output = Done | Empty

    def run(self, input: Input) -> Done | Empty:
        if not input.text.strip():
            return self.Empty()
        return self.Done(text=input.text.upper())
