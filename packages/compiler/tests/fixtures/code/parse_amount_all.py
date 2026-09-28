"""Wynd step parse_amount (deterministic: digits, and the few amounts in words it knows)."""
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wynd.runtime import DeterministicStep

NUMBER = re.compile(r"\d+(?:\.\d+)?")
WORDS = (("twelve pounds fifty", 12.5),)


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
        if match is not None:
            return self.Done(amount=float(match.group()))
        for words, amount in WORDS:
            if input.text.strip().lower() == words:
                return self.Done(amount=amount)
        raise NotImplementedError(f"no amount in {input.text!r}")
