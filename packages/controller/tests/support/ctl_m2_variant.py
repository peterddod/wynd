"""CTL-M2 Docker test helpers: the fake-provider dogfood variant and a one-step MCP process, built into images through
the real controller (`Controller.open` + the in-process job runner).

- `dogfood_copy(dest, venvs, provider="fake")`: a temp git copy of `examples/invoices` (no `.wynd`, `.env` or
  cassettes) with `provider:` switched, `.gitignore` for `.wynd/` and `.env`, `.wynd/venvs` linked to `venvs`.
- `mcp_workspace(dest, venvs, server)`: process `mcp_lookup` whose one agentic step `lookup` uses the MCP server
  `server` (tool `get_issue` of `wynd.runtime.mcp.fake_server`), with the snapshot in its `step.lock.yaml`.
- `build_image(ctl, pid)`: records a passing test result at the closure HEAD (the build gate reuses it; the tests of
  these fixtures are not what is under test) and runs the build job; -> the image reference.
- `EXAMPLE_SEQUENCES` / `step_triples(events, replace)`: the `(step, exit, outputs)` sequence of a run.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[4]
INVOICES = REPO / "examples" / "invoices"
PID = "process_supplier_invoice"
PROCESS_REL = Path("processes") / PID
FAKE_SCRIPT = INVOICES / "tests" / "fixtures" / "fake_provider.json"
COPY_IGNORE = shutil.ignore_patterns(".wynd", "__pycache__", ".pytest_cache", ".env", "cassettes")
GIT_IDENTITY = ["-c", "user.name=Wynd Test", "-c", "user.email=test@wynd.invalid", "-c", "commit.gpgsign=false"]
MCP_PID = "mcp_lookup"


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *GIT_IDENTITY, *args], check=True, capture_output=True,
                          text=True).stdout


def commit_all(ws: Path, message: str, venvs: Path) -> None:
    (ws / ".gitignore").write_text(".wynd/\n.env\n__pycache__/\n.pytest_cache/\n")
    git(ws, "init", "-q", "-b", "main")
    git(ws, "add", "-A")
    git(ws, "commit", "-q", "-m", message)
    (ws / ".wynd").mkdir()
    (ws / ".wynd" / "venvs").symlink_to(venvs, target_is_directory=True)


def dogfood_copy(dest: Path, venvs: Path, provider: str = "fake") -> Path:
    ws = dest / "invoices"
    shutil.copytree(INVOICES, ws, ignore=COPY_IGNORE)
    process_yaml = ws / PROCESS_REL / "process.yaml"
    text, count = re.subn(r"^provider: .*$", f"provider: {provider}", process_yaml.read_text(), flags=re.MULTILINE)
    assert count == 1
    process_yaml.write_text(text)
    commit_all(ws, f"sample workspace, provider {provider}", venvs)
    return ws


LOOKUP_MODULE = '''\
"""Wynd step lookup (agentic, MCP)."""
from typing import Literal

from pydantic import BaseModel

from wynd.runtime import AgenticStep, McpServer


class Lookup(AgenticStep):
    """Look up the issue with the given number and return its title."""

    mcp = [McpServer("{server}", allow=["get_issue"])]

    class Input(BaseModel):
        number: int

    class Output(BaseModel):
        exit: Literal["done"] = "done"
        title: str

    def run(self, input: Input) -> Output: ...
'''

STEP_PYPROJECT = '''\
[project]
name = "mcp_lookup-lookup"
version = "0.1.0"
requires-python = ">=3.12"
dependencies = []

[build-system]
requires = ["hatchling>=1.27"]
build-backend = "hatchling.build"
'''


def mcp_workspace(dest: Path, venvs: Path, server: str) -> Path:
    from wynd.runtime.mcp.fake_server import DEFAULT_TOOLS
    from wynd.runtime.mcp.snapshot import McpToolSpec, snapshot
    from wynd.runtime.mcp import McpServer
    from wynd.spec.lockfiles import StepLock, dump_lock

    specs = [McpToolSpec(name=t["name"], description=t.get("description", ""), input_schema=t["inputSchema"],
                         annotations=t.get("annotations", {})) for t in DEFAULT_TOOLS]
    snap = snapshot(McpServer(server, allow=["get_issue"]), specs)
    lock = StepLock(name="lookup", kind="agentic", entrypoint="lookup:Lookup", tier="cheap", thinking="low",
                    effects=["network"], mcp=[snap])
    process = {
        "kind": "process", "name": MCP_PID, "provider": "fake", "entry": "lookup",
        "inputs": {"number": "integer"}, "outputs": {"done": {"title": "string"}},
        "steps": {"lookup": {"use": "./steps/lookup"}},
        "edges": [{"from": "lookup.done", "to": "$exit.done", "with": {"title": "steps.lookup.outputs.title"}}],
    }
    ws = dest / "mcp-ws"
    step = ws / "processes" / MCP_PID / "steps" / "lookup"
    step.mkdir(parents=True)
    (ws / "wynd.yaml").write_text("process_roots: [processes]\n")
    (ws / "processes" / MCP_PID / "process.yaml").write_text(yaml.safe_dump(process, sort_keys=False))
    (step / "lookup.py").write_text(LOOKUP_MODULE.replace("{server}", server))
    (step / "pyproject.toml").write_text(STEP_PYPROJECT)
    (step / "step.lock.yaml").write_text(dump_lock(lock))
    commit_all(ws, "mcp lookup process", venvs)
    return ws


def build_image(ctl: Any, pid: str, *, timeout: float = 1800) -> str:
    from wynd.controller.status import process_head
    from wynd.process.hashing import process_hash
    from wynd.runtime.storage.models import TestResult

    head, _ = process_head(ctl.ctx, pid)
    ws = ctl.ctx.workspace()
    key = f"process:{pid}:{process_hash(ws.tree, ws.load_process(pid))}"
    ctl.ctx.stores.runs.put_test_result(TestResult(commit=head, key=key, passed=True, counts={"passed": 1},
                                                   ran_at=datetime.now(UTC)))
    job = ctl.jobs.submit_build(pid, push=False)
    job = ctl.jobs.wait(job.id, timeout=timeout)
    if job.status != "succeeded":
        text, _, _ = ctl.ctx.runner.logs(job.id)
        raise AssertionError(f"build of {pid} {job.status}: {job.error}\n{text[-4000:]}")
    return job.build.image


def step_triples(events: list[dict], replace: dict[str, str]) -> list[tuple[str, str, Any]]:
    """`(step, exit, outputs)` of every `step.end`, with each `replace` key (paths, run ids) substituted in the
    outputs' JSON."""
    triples = []
    for event in events:
        if event["type"] != "step.end":
            continue
        text = json.dumps(event["outputs"], sort_keys=True)
        for old, new in sorted(replace.items(), key=lambda item: -len(item[0])):
            text = text.replace(old, new)
        triples.append((event["step"], event["exit"], json.loads(text)))
    return triples
