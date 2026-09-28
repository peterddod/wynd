"""Wynd step parse_amount (deterministic half: numeric amounts; amounts in words are deferred)."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wynd.runtime import DeterministicStep

NUMBER = re.compile(r"\d+(?:\.\d+)?")


class ParseAmount(DeterministicStep):
    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")
        text: str

    class Done(BaseModel):
        model_config = ConfigDict(extra="forbid")
        exit: Literal["done"] = "done"
        amount: float

    Output = Done

    def run(self, input: Input) -> Output:
        match = NUMBER.search(input.text)
        if match is None:
            raise NotImplementedError(f"no digits in {input.text!r}")
        return self.Done(amount=float(match.group()))
