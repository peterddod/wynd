"""Wynd step normalise_name (deterministic)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wynd.runtime import DeterministicStep


class NormaliseName(DeterministicStep):
    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")
        name: str

    class Done(BaseModel):
        model_config = ConfigDict(extra="forbid")
        exit: Literal["done"] = "done"
        name: str

    Output = Done

    def run(self, input: Input) -> Output:
        return self.Done(name=" ".join(part.capitalize() for part in input.name.split()))
