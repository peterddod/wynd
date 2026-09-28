"""ShellStep (PLAN §5.2): argv from `command()`, exit-code mapping, `outputs()`, env/stdin/cwd, PATH prepend."""

import json
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from support.rt_step_doubles import chain, make_params
from wynd.runtime.shell import ShellResult
from wynd.runtime.step import ShellStep
from wynd.spec.lockfiles import RetryPolicy


class Pattern(BaseModel):
    pattern: str
    path: str


class Counted(BaseModel):
    exit: Literal["done"] = "done"
    count: int


class NoMatch(BaseModel):
    exit: Literal["no_match"] = "no_match"
    returncode: int
    stdout: str


class GrepCount(ShellStep):
    exit_codes = {0: "done", 1: "no_match", "*": "error"}
    Input = Pattern
    Output = Counted | NoMatch

    def command(self, input):
        return ["grep", "-c", input.pattern, input.path]

    def outputs(self, exit, result):
        if exit == "done":
            return Counted(count=int(result.stdout))
        return super().outputs(exit, result)


class Script(BaseModel):
    script: str


class Captured(BaseModel):
    exit: Literal["done"] = "done"
    returncode: int
    stdout: str
    stderr: str


def _sh(script: str, *, exit_codes=None, output=Captured):
    attrs = {"Input": Script, "Output": output, "command": lambda self, input: ["/bin/sh", "-c", script]}
    if exit_codes is not None:
        attrs["exit_codes"] = exit_codes
    return type("Sh", (ShellStep,), attrs)


def test_exit_codes_map_to_exits(tmp_path):
    notes = tmp_path / "notes.txt"
    notes.write_text("apple\nbanana\napple pie\n")
    done = chain(GrepCount, make_params(tmp_path, {"pattern": "apple", "path": str(notes)}, kind="shell"))
    assert (done.exit, done.outputs) == ("done", {"count": 2})
    miss = chain(GrepCount, make_params(tmp_path, {"pattern": "cherry", "path": str(notes)}, kind="shell"))
    assert (miss.exit, miss.outputs) == ("no_match", {"returncode": 1, "stdout": "0\n"})


def test_error_exit_code_is_shell_exit_with_tails(tmp_path):
    result = chain(GrepCount, make_params(tmp_path, {"pattern": "x", "path": str(tmp_path / "absent")}, kind="shell"))
    out = result.outputs
    assert (result.exit, out["cause"], out["type"]) == ("error", "shell_exit", None)
    assert out["message"].startswith("exit code 2: ") and "absent" in out["message"]
    assert out["partial_outputs"]["returncode"] == 2
    assert "absent" in out["partial_outputs"]["stderr"]


def test_tails_are_capped(tmp_path):
    step = _sh("head -c 5000 /dev/zero | tr '\\0' e >&2; exit 9")
    result = chain(step, make_params(tmp_path, {"script": ""}, kind="shell"))
    assert result.outputs["cause"] == "shell_exit"
    assert len(result.outputs["partial_outputs"]["stderr"]) == 2000
    assert result.outputs["message"] == "exit code 9: " + "e" * 2000


def test_digit_string_exit_code_keys(tmp_path):
    class Three(BaseModel):
        exit: Literal["three"] = "three"

    step = _sh("exit 3", exit_codes={"0": "done", "3": "three", "*": "error"}, output=Captured | Three)
    assert chain(step, make_params(tmp_path, {"script": ""}, kind="shell")).exit == "three"


def test_default_outputs_fill_stdout_stderr_returncode(tmp_path):
    result = chain(_sh("echo out; echo err >&2"), make_params(tmp_path, {"script": ""}, kind="shell"))
    assert result.outputs == {"returncode": 0, "stdout": "out\n", "stderr": "err\n"}


def test_custom_outputs_can_parse_stdout_json(tmp_path):
    class Parsed(BaseModel):
        exit: Literal["done"] = "done"
        total: float
        lines: int

    class Report(ShellStep):
        Input = Script
        Output = Parsed

        def command(self, input):
            return ["/bin/sh", "-c", input.script]

        def outputs(self, exit, result: ShellResult):
            return Parsed(**json.loads(result.stdout))

    result = chain(Report, make_params(tmp_path, {"script": 'echo \'{"total": 12.5, "lines": 3}\''}, kind="shell"))
    assert result.outputs == {"total": 12.5, "lines": 3}


def test_default_outputs_refuse_models_with_other_fields(tmp_path):
    step = _sh("echo 1", output=Counted)
    result = chain(step, make_params(tmp_path, {"script": ""}, kind="shell"))
    assert (result.outputs["cause"], result.outputs["type"]) == ("exception", "NotImplementedError")
    assert "outputs() must be defined" in result.outputs["message"] and "count" in result.outputs["message"]


def test_command_must_be_defined_and_be_an_argv(tmp_path):
    no_command = type("NoCommand", (ShellStep,), {"Input": Script, "Output": Captured})
    result = chain(no_command, make_params(tmp_path, {"script": ""}, kind="shell"))
    assert (result.outputs["type"], result.outputs["cause"]) == ("NotImplementedError", "exception")
    string = type("String", (ShellStep,), {"Input": Script, "Output": Captured,
                                           "command": lambda self, input: "echo hi"})
    result = chain(string, make_params(tmp_path, {"script": ""}, kind="shell"))
    assert result.outputs["type"] == "TypeError"
    assert "never a shell string" in result.outputs["message"]


def test_child_sees_wynd_input_empty_stdin_and_the_workspace_cwd(tmp_path):
    workspace = tmp_path / "ws"
    script = 'printf "%s\\n" "$WYND_INPUT"; cat; pwd -P'
    result = chain(_sh(script), make_params(workspace, {"script": script}, kind="shell"))
    wynd_input, cwd = result.outputs["stdout"].splitlines()
    assert json.loads(wynd_input) == {"script": script}
    assert Path(cwd) == workspace.resolve()


def test_console_scripts_of_the_step_environment_are_on_path(tmp_path, monkeypatch):
    """The worker is the venv's python; its bin dir (here: this interpreter's) is prepended to PATH."""
    monkeypatch.setenv("PATH", str(tmp_path / "nothing-here"))
    bin_dir = Path(sys.executable).parent
    found = chain(_sh("command -v pytest"), make_params(tmp_path, {"script": ""}, kind="shell"))
    assert Path(found.outputs["stdout"].strip()) == bin_dir / "pytest"

    class Version(ShellStep):
        Input = Script
        Output = Captured

        def command(self, input):
            return ["pytest", "--version"]

    ran = chain(Version, make_params(tmp_path, {"script": ""}, kind="shell"))
    assert ran.exit == "done", ran.outputs
    assert "pytest" in ran.outputs["stdout"] + ran.outputs["stderr"]


def test_shell_exit_is_retried_with_run_retries(tmp_path):
    step = _sh('echo x >> tries; test "$(wc -l < tries)" -ge 2')
    result = chain(step, make_params(tmp_path, {"script": ""}, kind="shell", retries=RetryPolicy(run=1)))
    assert (result.exit, result.attempts) == ("done", 2)
