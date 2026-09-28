"""Wynd step store_note (deterministic)."""
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wynd.runtime import DeterministicStep


class StoreNote(DeterministicStep):
    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")
        note: str
        dest: Path

    class Done(BaseModel):
        model_config = ConfigDict(extra="forbid")
        exit: Literal["done"] = "done"
        path: Path

    Output = Done

    def run(self, input: Input) -> Output:
        input.dest.mkdir(parents=True, exist_ok=True)
        path = input.dest / f"{input.note}.txt"
        path.write_text(input.note + "\n")
        return self.Done(path=path)
