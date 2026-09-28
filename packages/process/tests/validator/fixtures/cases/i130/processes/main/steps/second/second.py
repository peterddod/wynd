from typing import Literal

from pydantic import BaseModel

from wynd.runtime.step import DeterministicStep


class Second(DeterministicStep):
    class Input(BaseModel):
        value: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        result: str

    def run(self, input: Input) -> Output:
        return self.Output(result=f"done {input.value}")
