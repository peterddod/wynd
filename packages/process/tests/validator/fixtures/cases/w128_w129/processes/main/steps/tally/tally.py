from typing import Literal

from pydantic import BaseModel

from wynd.runtime.step import DeterministicStep


class Tally(DeterministicStep):
    class Input(BaseModel):
        value: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        anything: str

    def run(self, input: Input) -> Output:
        return self.Output(anything=input.value)
