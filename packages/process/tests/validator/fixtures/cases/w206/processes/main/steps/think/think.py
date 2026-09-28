from typing import Literal

from pydantic import BaseModel

from wynd.runtime.step import AgenticStep


class Think(AgenticStep):
    """Answer the text."""

    class Input(BaseModel):
        text: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        result: str

    def run(self, input: Input) -> Output: ...
