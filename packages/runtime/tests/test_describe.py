"""`python -m wynd.runtime.describe <package_dir> <entrypoint>` (PLAN §5.1 describe row).

The package is mounted through the worker's loader, doubled here (RT-WORKER lands in wave 2b); `main` is exercised
in-process for the same reason."""

import json
from dataclasses import dataclass
from typing import Literal

import pytest
from pydantic import BaseModel

from support.rt_step_doubles import FIXTURES, FakeToolSpec, load_step_class, use_agentic_checks, use_loader
from wynd.runtime.describe import PackageDescription, describe_class, describe_package, main
from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.step import AgenticStep, ProcessStep
from wynd.spec.hashing import interface_hash
from wynd.spec.interface import interface_from_models
from wynd.spec.lockfiles import ToolSnapshot, load_step_lock


@pytest.fixture
def loader(monkeypatch):
    use_loader(monkeypatch)


def test_deterministic_package(loader):
    description = describe_package(FIXTURES / "echo", "echo:Echo")
    assert description.kind == "deterministic"
    assert description.doc == "Repeat the text in capitals and count its characters."
    assert description.interface.exits == ["done", "empty"]
    assert list(description.interface.input["properties"]) == ["text"]
    assert (description.context, description.tools, description.mcp, description.exit_codes) == ([], [], [], None)


def test_interface_is_the_snapshot_the_worker_verifies(loader):
    cls = load_step_class(str((FIXTURES / "echo").resolve()), "echo:Echo", FIXTURES / "echo")
    description = describe_package(FIXTURES / "echo", "echo:Echo")
    assert interface_hash(description.interface) == interface_hash(interface_from_models(cls.Input, cls.Output))


def test_shell_package_exit_codes_match_the_lock_shape(loader):
    description = describe_package(FIXTURES / "grep_count", "grep_count:GrepCount")
    assert description.kind == "shell"
    assert description.exit_codes == {"0": "done", "1": "no_match", "*": "error"}
    lock = load_step_lock(FIXTURES / "grep_count" / "step.lock.yaml")
    assert description.exit_codes == {str(code): exit for code, exit in lock.shell.exit_codes.items()}


@dataclass(frozen=True)
class Server:
    name: str
    allow: tuple[str, ...]


def test_agentic_description_snapshots_tools_mcp_and_context(monkeypatch):
    use_agentic_checks(monkeypatch)
    snapshots = [
        ToolSnapshot(name="lookup", source="method", effects=["network"], idempotent=True, env=["CRM_TOKEN"]),
        ToolSnapshot(name="shell", source="library", effects=["shell"], allow=["pdftotext"]),
        ToolSnapshot(name="now", source="library"),
    ]
    seen: list = []
    monkeypatch.setattr("wynd.runtime.tools.decorator.step_tools",
                        lambda cls: seen.append(cls) or [FakeToolSpec(s) for s in snapshots])

    class In(BaseModel):
        text: str

    class Out(BaseModel):
        exit: Literal["done"] = "done"
        total: float

    class Extract(AgenticStep):
        """Extract the total.

        Amounts are in pounds.
        """

        context = ["process.goal", "previous.outputs"]
        mcp = [Server("github", ("get_issue", "list_issues"))]
        Input = In
        Output = Out

        def run(self, input): ...

    description = describe_class(Extract)
    assert description.kind == "agentic"
    assert description.doc == "Extract the total.\n\nAmounts are in pounds."
    assert description.context == ["process.goal", "previous.outputs"]
    assert description.mcp == [{"server": "github", "allow": ["get_issue", "list_issues"]}]
    assert (description.tools, seen) == (snapshots, [Extract])
    assert PackageDescription.model_validate_json(description.model_dump_json()) == description


def test_process_steps_are_not_packages():
    class Child(ProcessStep):
        process_id = "child"

    with pytest.raises(StepDefinitionError, match="ProcessStep"):
        describe_class(Child)


def test_main_prints_the_json(loader, capsys):
    assert main([str(FIXTURES / "grep_count"), "grep_count:GrepCount"]) == 0
    out = capsys.readouterr().out
    description = PackageDescription.model_validate_json(out)
    assert description.kind == "shell"
    assert json.loads(out)["interface"]["outputs"]["no_match"]["properties"]["exit"]["const"] == "no_match"


def test_main_keeps_stdout_clean_when_the_module_prints(loader, tmp_path, capsys):
    package = tmp_path / "noisy"
    package.mkdir()
    (package / "noisy.py").write_text(
        "from typing import Literal\nfrom pydantic import BaseModel\nfrom wynd.runtime import DeterministicStep\n"
        "print('hello from import')\n\n"
        "class Noisy(DeterministicStep):\n"
        "    class Input(BaseModel):\n        n: int\n"
        "    class Output(BaseModel):\n        exit: Literal['done'] = 'done'\n"
        "    def run(self, input):\n        return self.Output()\n"
    )
    assert main([str(package), "noisy:Noisy"]) == 0
    captured = capsys.readouterr()
    assert PackageDescription.model_validate_json(captured.out).kind == "deterministic"
    assert "hello from import" in captured.err


def test_main_failures(loader, tmp_path, capsys):
    assert main([]) == 2
    assert "usage: python -m wynd.runtime.describe" in capsys.readouterr().err
    assert main([str(tmp_path), "absent:Step"]) == 1
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "ModuleNotFoundError" in captured.err
