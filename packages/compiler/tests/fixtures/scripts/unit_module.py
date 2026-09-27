# Module source returned by the scripted write_deterministic call in unit.yaml.
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import DeterministicStep


class ParseAmount(DeterministicStep):
    class Input(BaseModel):
        text: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        amount: float

    Output = Done

    def run(self, input: Input) -> Output:
        return self.Done(amount=float(input.text.strip().lstrip("£")))
