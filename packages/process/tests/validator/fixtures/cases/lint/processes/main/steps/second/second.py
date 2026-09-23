import functools
from typing import Literal

from pydantic import BaseModel

from wynd.runtime.step import DeterministicStep

from .helpers import shout

SEEN = dict()


@functools.lru_cache(maxsize=None)
def cached(value: str) -> str:
    return shout(value)


class Second(DeterministicStep):
    history = []

    class Input(BaseModel):
        value: str

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        result: str

    def run(self, input: Input) -> Output:
        return self.Output(result=cached(input.value))
