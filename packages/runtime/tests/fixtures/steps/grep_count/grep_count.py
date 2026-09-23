"""Fixture step grep_count (shell): counts matching lines of a file with grep."""

from typing import Literal

from pydantic import BaseModel

from wynd.runtime import ShellStep


class GrepCount(ShellStep):
    """Count the lines of a file that match a pattern."""

    exit_codes = {0: "done", 1: "no_match", "*": "error"}

    class Input(BaseModel):
        pattern: str
        path: str

    class Done(BaseModel):
        exit: Literal["done"] = "done"
        count: int

    class NoMatch(BaseModel):
        exit: Literal["no_match"] = "no_match"
        returncode: int

    Output = Done | NoMatch

    def command(self, input: Input) -> list[str]:
        return ["grep", "-c", input.pattern, input.path]

    def outputs(self, exit, result):
        if exit == "done":
            return self.Done(count=int(result.stdout.strip()))
        return super().outputs(exit, result)
