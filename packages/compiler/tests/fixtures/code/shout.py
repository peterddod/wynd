"""Wynd step shout (shell)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict

from wynd.runtime import ShellResult, ShellStep


class Shout(ShellStep):
    class Input(BaseModel):
        model_config = ConfigDict(extra="forbid")
        text: str

    class Done(BaseModel):
        model_config = ConfigDict(extra="forbid")
        exit: Literal["done"] = "done"
        text: str

    Output = Done

    exit_codes = {0: "done", "*": "error"}

    def command(self, input: Input) -> list[str]:
        return ["awk", "BEGIN { print toupper(ARGV[1]) }", input.text]

    def outputs(self, exit: str, result: ShellResult) -> Output:
        return self.Done(text=result.stdout.rstrip("\n"))
