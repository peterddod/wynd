"""The venv worker protocol (PLAN §3.12, §3.6; `$DRAFTS/02 §5`): real `sys.executable -m wynd.runtime.worker`
subprocesses running the real middleware chain, plus the in-process step-package loader."""

from __future__ import annotations

import io
import json
import logging
import os
import platform
import select
import signal
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.interface import interface_of
from wynd.runtime.policy import ExecPolicy
from wynd.runtime.worker.client import WorkerClient
from wynd.runtime.worker.loader import load_step_class, mount_step_package
from wynd.runtime.worker.protocol import (
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    NOT_INITIALISED,
    PARSE_ERROR,
    PROTOCOL_MISMATCH,
    UNKNOWN_STEP,
    InitStep,
    RunStepParams,
    RunStepResult,
)
from wynd.runtime.worker.server import OUTPUT_TAIL
from wynd.spec.hashing import interface_hash
from wynd.spec.lockfiles import RetryPolicy
from wynd.spec.workspace import step_module_name

READ_A = {
    "read.py": '''
        """Package a: its `read` module echoes the text."""
        from typing import Literal

        from pydantic import BaseModel

        from wynd.runtime import DeterministicStep


        class Read(DeterministicStep):
            """Read for process a."""

            class Input(BaseModel):
                text: str

            class Done(BaseModel):
                exit: Literal["done"] = "done"
                source: str
                text: str

            Output = Done

            def run(self, input):
                return self.Done(source="a", text=input.text)
    ''',
}

READ_B = {
    "read.py": '''
        """Package b: a different `read` module, with a relative import and two exits."""
        from typing import Literal

        from pydantic import BaseModel

        from wynd.runtime import DeterministicStep

        from .helpers import shout


        class Read(DeterministicStep):
            """Read for process b."""

            class Input(BaseModel):
                text: str

            class Done(BaseModel):
                exit: Literal["done"] = "done"
                source: str
                text: str

            class Empty(BaseModel):
                exit: Literal["empty"] = "empty"

            Output = Done | Empty

            def run(self, input):
                if not input.text:
                    return self.Empty()
                return self.Done(source="b", text=shout(input.text))
    ''',
    "helpers.py": '''
        def shout(text):
            return text.upper()
    ''',
}

KIT = {
    "kit.py": '''
        import os
        import subprocess
        import sys
        import time
        from typing import Literal

        from pydantic import BaseModel

        from wynd.runtime import DeterministicStep, ShellStep


        class Text(BaseModel):
            text: str = ""


        class Done(BaseModel):
            exit: Literal["done"] = "done"
            value: str = ""


        class Noisy(DeterministicStep):
            Input = Text
            Output = Done

            def run(self, input):
                print("printed " + input.text)
                os.write(1, b"raw fd write\\n")
                subprocess.run(["echo", "from a child"], check=True)
                sys.stderr.write("to stderr\\n")
                self.runtime.logger.warning("logged")
                return Done(value=sys.stdin.read())


        class Loud(DeterministicStep):
            Input = Text
            Output = Done

            def run(self, input):
                sys.stdout.write("x" * 100_000 + "END\\n")
                return Done()


        class Chatty(DeterministicStep):
            Input = Text
            Output = Done

            def run(self, input):
                self.runtime.logger.warning("started")
                time.sleep(0.6)
                return Done(value="finished")


        class Counter(DeterministicStep):
            Input = Text
            Output = Done

            def run(self, input):
                count = self.runtime.cache.get("count", 0) + 1
                self.runtime.cache.set("count", count)
                return Done(value=str(count))


        class Fails(DeterministicStep):
            Input = Text
            Output = Done

            def run(self, input):
                raise ValueError("bad " + input.text)


        class Grep(ShellStep):
            exit_codes = {0: "done", 1: "missing", "*": "error"}

            class Input(BaseModel):
                pattern: str

            class Found(BaseModel):
                exit: Literal["done"] = "done"
                stdout: str

            class Missing(BaseModel):
                exit: Literal["missing"] = "missing"
                returncode: int

            Output = Found | Missing

            def command(self, input):
                return ["grep", "-c", input.pattern, "data.txt"]
    ''',
}

BROKEN = {
    "broken.py": '''
        import wynd_missing_dependency  # not installed in any venv

        from wynd.runtime import DeterministicStep
    ''',
}

DRIFT_MESSAGE = "interface snapshot drift for step {}: run wynd validate --sync-interfaces"
SHELL_CODES = {"0": "done", "1": "missing", "*": "error"}


def write_package(root: Path, files: dict[str, str]) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    for name, source in files.items():
        (root / name).write_text(textwrap.dedent(source))
    return root


def entry(step_id: str, package_dir: Path, entrypoint: str, kind: str = "deterministic", **snapshot) -> InitStep:
    return InitStep(id=step_id, entrypoint=entrypoint, package_dir=str(package_dir), kind=kind, **snapshot)


def run_params(step_id: str, inputs: dict, workspace: Path, kind: str = "deterministic") -> dict:
    params = RunStepParams(
        run_id="run-test", step_path=step_id.rpartition("#")[2], step_run=1, step_id=step_id, inputs=inputs,
        workspace=str(workspace), policy=ExecPolicy(kind=kind, retries=RetryPolicy()),
    )
    return params.model_dump(mode="json")


def ignore(event: dict) -> None:
    return None


def run(client: WorkerClient, step_id: str, inputs: dict, workspace: Path) -> tuple[dict, list[dict]]:
    events: list[dict] = []
    result = client.call("run_step", run_params(step_id, inputs, workspace), on_event=events.append, timeout=60)
    return result, events


@pytest.fixture
def steps_namespace():
    """Forget the `wynd_steps` packages an in-process test mounted (their directories are per-test temp dirs)."""
    before = set(sys.modules)
    yield
    for name in set(sys.modules) - before:
        if name == "wynd_steps" or name.startswith("wynd_steps."):
            del sys.modules[name]


@pytest.fixture(scope="module")
def packages(tmp_path_factory) -> dict[str, Path]:
    root = tmp_path_factory.mktemp("packages")
    return {
        "a": write_package(root / "a" / "read", READ_A),
        "b": write_package(root / "b" / "read", READ_B),
        "kit": write_package(root / "kit", KIT),
        "broken": write_package(root / "broken", BROKEN),
    }


@pytest.fixture(scope="module")
def worker(packages, tmp_path_factory):
    kit = packages["kit"]
    steps = [
        entry("a#read", packages["a"], "read:Read"),
        entry("b#read", packages["b"], "read:Read"),
        entry("kit#noisy", kit, "kit:Noisy"),
        entry("kit#loud", kit, "kit:Loud"),
        entry("kit#chatty", kit, "kit:Chatty"),
        entry("kit#counter", kit, "kit:Counter"),
        entry("kit#fails", kit, "kit:Fails"),
        entry("broken#step", packages["broken"], "broken:Broken"),
    ]
    client = WorkerClient(sys.executable, steps, env=dict(os.environ), cwd=str(tmp_path_factory.mktemp("cwd")))
    client.start(timeout=60)
    yield client
    client.close()


def test_init_imports_every_step_and_reports_failures_per_step(worker):
    result = worker.init_result
    assert (result["protocol"], result["runtime_version"]) == (1, "0.1.0")
    assert (result["python"], result["pid"]) == (platform.python_version(), worker.pid)
    ok = {step_id for step_id, status in result["steps"].items() if status["ok"]}
    assert ok == {"a#read", "b#read", "kit#noisy", "kit#loud", "kit#chatty", "kit#counter", "kit#fails"}
    error = result["steps"]["broken#step"]["error"]
    assert (error["type"], error["message"]) == ("ModuleNotFoundError", "No module named 'wynd_missing_dependency'")
    assert "import wynd_missing_dependency" in error["traceback"]
    assert worker.alive


def test_two_read_packages_coexist_in_one_worker(worker, tmp_path):
    described = worker.call("describe", {"ids": ["a#read", "b#read"]}, on_event=ignore, timeout=30)["steps"]
    a, b = described["a#read"], described["b#read"]
    assert a["cls"] == f"wynd_steps.{step_module_name('a#read')}.read:Read"
    assert b["cls"] == f"wynd_steps.{step_module_name('b#read')}.read:Read"
    assert (a["doc"], b["doc"]) == ("Read for process a.", "Read for process b.")
    assert list(a["exits"]) == ["done", "error"] and list(b["exits"]) == ["done", "empty", "error"]
    assert (a["kind"], a["input_fields"], a["required_inputs"]) == ("deterministic", ["text"], ["text"])

    assert run(worker, "a#read", {"text": "hi"}, tmp_path)[0]["outputs"] == {"source": "a", "text": "hi"}
    assert run(worker, "b#read", {"text": "hi"}, tmp_path)[0]["outputs"] == {"source": "b", "text": "HI"}
    assert run(worker, "b#read", {"text": ""}, tmp_path)[0]["exit"] == "empty"


def test_run_step_round_trip(worker, tmp_path):
    result, events = run(worker, "a#read", {"text": "hello"}, tmp_path / "ws")
    parsed = RunStepResult.model_validate(result)
    assert (parsed.exit, parsed.outputs, parsed.attempts) == ("done", {"source": "a", "text": "hello"}, 1)
    assert (parsed.summary.step, parsed.summary.exit) == ("read", "done")
    assert parsed.summary.key_outputs == {"source": "a", "text": "hello"}
    assert parsed.timings.worker_ms >= 0 and parsed.timings.run_ms is not None
    assert (parsed.usage, parsed.model, parsed.replayed, parsed.validation_failures) == (None, None, False, 0)
    assert events == []
    assert (tmp_path / "ws").is_dir()


def test_stdout_pollution_never_breaks_the_protocol(worker, tmp_path):
    for text in ("one", "two"):
        result, events = run(worker, "kit#noisy", {"text": text}, tmp_path)
        assert (result["exit"], result["outputs"]) == ("done", {"value": ""})     # stdin reads return ""
        assert events[0] == {"type": "step.log", "level": "WARNING", "stream": "logger", "message": "logged"}
        output = [event for event in events if event.get("stream") == "output"]
        assert len(output) == 1 and len(events) == 2
        assert output[0]["type"] == "step.log" and output[0]["level"] == "INFO"
        message = output[0]["message"]
        for line in (f"printed {text}", "raw fd write", "from a child", "to stderr"):
            assert line in message
        assert message.count("printed") == 1                     # each run captures only its own output
    assert worker.call("ping", {}, on_event=ignore, timeout=10) == {"pid": worker.pid}


def test_captured_output_keeps_the_last_64_kib(worker, tmp_path):
    _, events = run(worker, "kit#loud", {}, tmp_path)
    (message,) = [event["message"] for event in events if event.get("stream") == "output"]
    assert len(message.encode()) == OUTPUT_TAIL
    assert message.endswith("xxxEND\n")


def test_logger_records_are_live_notifications(worker, tmp_path):
    received: list[tuple[float, dict]] = []
    result = worker.call(
        "run_step", run_params("kit#chatty", {}, tmp_path),
        on_event=lambda event: received.append((time.monotonic(), event)), timeout=30,
    )
    returned = time.monotonic()
    assert result["outputs"] == {"value": "finished"}
    ((at, event),) = received
    assert event["message"] == "started"
    assert returned - at > 0.3                  # sent while the step was still sleeping, not with the response


def test_cache_persists_per_step_across_runs(worker, tmp_path):
    counts = [run(worker, "kit#counter", {}, tmp_path)[0]["outputs"]["value"] for _ in range(3)]
    assert counts == ["1", "2", "3"]


def test_a_step_error_exit_is_a_successful_response(worker, tmp_path):
    result, _ = run(worker, "kit#fails", {"text": "input"}, tmp_path)
    outputs = result["outputs"]
    assert (result["exit"], outputs["cause"], outputs["type"]) == ("error", "exception", "ValueError")
    assert outputs["message"] == "ValueError: bad input"
    assert outputs["inputs"] == {"text": "input"} and "raise ValueError" in outputs["traceback"]


def test_a_step_that_failed_to_import_resolves_with_cause_import(worker, tmp_path):
    result, _ = run(worker, "broken#step", {"text": "x"}, tmp_path)
    outputs = result["outputs"]
    assert (result["exit"], result["attempts"], outputs["cause"]) == ("error", 0, "import")
    assert outputs["message"] == "ModuleNotFoundError: No module named 'wynd_missing_dependency'"
    assert (outputs["type"], outputs["inputs"], outputs["attempts"]) == ("ModuleNotFoundError", {"text": "x"}, 0)
    assert result["summary"]["key_outputs"]["cause"] == "import"
    described = worker.call("describe", {"ids": ["broken#step"]}, on_event=ignore, timeout=30)["steps"]
    assert described["broken#step"]["error"]["type"] == "ModuleNotFoundError"


def test_describe_null_lists_every_initialised_step(worker):
    described = worker.call("describe", {"ids": None}, on_event=ignore, timeout=30)["steps"]
    assert set(described) == set(worker.init_result["steps"])


def snapshots(steps_dir: Path) -> dict[str, str]:
    """The true interface hashes of two kit classes, imported in-process."""
    return {
        name: interface_hash(interface_of(load_step_class(f"snap#{name}", f"kit:{name}", steps_dir)).interface)
        for name in ("Counter", "Grep")
    }


def test_init_verifies_the_lock_snapshots(tmp_path, steps_namespace):
    kit = write_package(tmp_path / "kit", KIT)
    true = snapshots(kit)
    counter, grep = true["Counter"], true["Grep"]
    other = load_step_class("snap#read", "read:Read", write_package(tmp_path / "a", READ_A))
    stale = interface_hash(interface_of(other).interface)
    steps = [
        entry("ok#counter", kit, "kit:Counter", interface_hash=counter),
        entry("ok#grep", kit, "kit:Grep", "shell", interface_hash=grep, exit_codes=SHELL_CODES),
        entry("ok#unsnapshotted", kit, "kit:Grep", "shell"),
        entry("drift#interface", kit, "kit:Counter", interface_hash=stale),
        entry("drift#kind", kit, "kit:Counter", "shell", interface_hash=counter),
        entry("drift#context", kit, "kit:Counter", interface_hash=counter, context=["previous.outputs"]),
        entry("drift#exit_codes", kit, "kit:Grep", "shell", interface_hash=grep, exit_codes={"0": "done"}),
        entry("drift#two", kit, "kit:Grep", "agentic", interface_hash=stale),
    ]
    client = WorkerClient(sys.executable, steps, env=dict(os.environ))
    try:
        report = client.start(timeout=60)["steps"]
        assert all(report[step_id]["ok"] for step_id in ("ok#counter", "ok#grep", "ok#unsnapshotted"))
        expected = {"drift#interface": ["interface"], "drift#kind": ["kind"], "drift#context": ["context"],
                    "drift#exit_codes": ["exit_codes"], "drift#two": ["kind", "interface"]}
        for step_id, drift in expected.items():
            assert report[step_id] == {"ok": False, "error": {
                "type": "SnapshotDrift", "message": DRIFT_MESSAGE.format(step_id), "traceback": None, "drift": drift,
            }}
            events: list[dict] = []
            result = client.call("run_step", run_params(step_id, {"text": "x"}, tmp_path), on_event=events.append,
                                 timeout=30)
            outputs = result["outputs"]
            assert (result["exit"], outputs["cause"], outputs["type"]) == ("error", "import", "SnapshotDrift")
            assert outputs["message"] == DRIFT_MESSAGE.format(step_id) and events == []

        (tmp_path / "ws").mkdir()
        (tmp_path / "ws" / "data.txt").write_text("apple\nbanana\napple\n")
        params = run_params("ok#grep", {"pattern": "apple"}, tmp_path / "ws", "shell")
        found = client.call("run_step", params, on_event=ignore, timeout=30)
        assert (found["exit"], found["outputs"]) == ("done", {"stdout": "2\n"})
    finally:
        client.close()


class Raw:
    """A worker spoken to line by line, without WorkerClient (for protocol faults and process behaviour)."""

    def __init__(self) -> None:
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "wynd.runtime.worker"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            env={**os.environ, "PYTHONUNBUFFERED": "1"}, bufsize=0,
        )
        self.buffer = b""

    def send(self, message: dict | str) -> None:
        text = message if isinstance(message, str) else json.dumps(message)
        self.proc.stdin.write(text.encode() + b"\n")

    def request(self, method: str, params: dict | None = None, rid: int = 1) -> dict:
        self.send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
        return self.receive()

    def receive(self, timeout: float = 30) -> dict:
        fd = self.proc.stdout.fileno()
        while b"\n" not in self.buffer:
            ready, _, _ = select.select([fd], [], [], timeout)
            assert ready, "the worker did not answer"
            chunk = os.read(fd, 65536)
            if not chunk:
                raise EOFError("worker closed its protocol stream")
            self.buffer += chunk
        line, _, self.buffer = self.buffer.partition(b"\n")
        return json.loads(line)

    def close(self) -> int:
        if self.proc.poll() is None:
            self.proc.stdin.close()
        return self.proc.wait(timeout=10)


@pytest.fixture
def raw():
    worker = Raw()
    yield worker
    if worker.proc.poll() is None:
        worker.proc.kill()
        worker.proc.wait()


def error_code(reply: dict) -> int:
    return reply["error"]["code"]


def test_calls_before_init_are_refused(raw, tmp_path):
    assert raw.request("ping")["result"] == {"pid": raw.proc.pid}
    assert error_code(raw.request("describe", {"ids": None})) == NOT_INITIALISED
    assert error_code(raw.request("run_step", run_params("a#read", {}, tmp_path))) == NOT_INITIALISED
    assert error_code(raw.request("edge.check", {})) == NOT_INITIALISED


def test_malformed_requests_get_standard_errors(raw):
    raw.send("{not json")
    reply = raw.receive()
    assert (reply["id"], error_code(reply)) == (None, PARSE_ERROR)
    raw.send("[1, 2]")
    assert error_code(raw.receive()) == INVALID_REQUEST
    raw.send({"jsonrpc": "2.0", "id": 4})
    reply = raw.receive()
    assert (reply["id"], error_code(reply)) == (4, INVALID_REQUEST)
    raw.send({"jsonrpc": "2.0", "id": 5, "method": "ping", "params": [1]})
    assert error_code(raw.receive()) == INVALID_PARAMS
    assert error_code(raw.request("teleport", rid=6)) == METHOD_NOT_FOUND
    raw.send({"jsonrpc": "2.0", "method": "ping"})              # a notification gets no reply
    assert raw.request("ping", rid=7) == {"jsonrpc": "2.0", "id": 7, "result": {"pid": raw.proc.pid}}


def test_protocol_mismatch(raw):
    reply = raw.request("init", {"protocol": 2, "runtime_version": "0.1.0", "steps": []})
    assert error_code(reply) == PROTOCOL_MISMATCH
    assert "protocol mismatch" in reply["error"]["message"]


def test_bad_params_and_unknown_steps_after_init(raw, packages, tmp_path):
    steps = [entry("a#read", packages["a"], "read:Read").model_dump(mode="json")]
    init = raw.request("init", {"protocol": 1, "runtime_version": "0.1.0", "steps": steps})["result"]
    assert init["steps"] == {"a#read": {"ok": True}}
    assert error_code(raw.request("init", {"protocol": 1, "steps": [{"id": "x"}]})) == INVALID_PARAMS
    assert error_code(raw.request("run_step", {"step_id": "a#read"})) == INVALID_PARAMS
    unknown = raw.request("run_step", run_params("nope#read", {}, tmp_path))
    assert error_code(unknown) == UNKNOWN_STEP and "nope#read" in unknown["error"]["message"]
    assert error_code(raw.request("describe", {"ids": ["nope#read"]})) == UNKNOWN_STEP
    assert error_code(raw.request("describe", {"ids": "a#read"})) == INVALID_PARAMS
    assert raw.request("run_step", run_params("a#read", {"text": "ok"}, tmp_path))["result"]["exit"] == "done"


def test_shutdown_exits_zero(raw):
    assert raw.request("shutdown")["result"] == {}
    assert raw.proc.wait(timeout=10) == 0


def test_eof_exits_zero(raw):
    assert raw.request("ping")["result"]["pid"] == raw.proc.pid
    assert raw.close() == 0


def test_sigint_is_ignored(raw):
    assert raw.request("ping")["result"]["pid"] == raw.proc.pid
    raw.proc.send_signal(signal.SIGINT)
    time.sleep(0.3)
    assert raw.proc.poll() is None
    assert raw.request("ping", rid=2)["result"]["pid"] == raw.proc.pid
    assert raw.close() == 0


def test_client_warns_on_a_runtime_version_mismatch(packages, monkeypatch, caplog):
    monkeypatch.setattr("wynd.runtime.worker.client.__version__", "9.9.9")
    client = WorkerClient(sys.executable, [entry("a#read", packages["a"], "read:Read")], env=dict(os.environ))
    try:
        with caplog.at_level(logging.WARNING, logger="wynd.runtime.worker"):
            result = client.start(timeout=60)
        assert result["steps"] == {"a#read": {"ok": True}}
        assert "runs wynd-runtime 0.1.0; this process runs 9.9.9" in caplog.text
    finally:
        client.close()


def test_edge_check_is_dispatched_to_the_edges_handler(monkeypatch):
    """In-process: `edge.check` calls `wynd.runtime.edges.handle_edge_check`, whose `notify` calls become
    notifications of the request and whose `EdgeCheckError` becomes JSON-RPC -32001 with `data.cause`."""
    from wynd.runtime.executor.edges import EdgeCheckError
    from wynd.runtime.worker import server as worker_server

    out = io.StringIO()
    server = worker_server.WorkerServer(out)
    monkeypatch.setattr(worker_server, "_active", server)

    def handler(params: dict) -> dict:
        worker_server.notify({"type": "model.call", "step": "edge:validate.done[save]"})
        if params["call"]["check"] == "unanswerable":
            raise EdgeCheckError("validation", "no valid verdict after 2 retries")
        return {"verdict": {"take": True, "reason": "the totals match"}}

    monkeypatch.setattr("wynd.runtime.edges.handle_edge_check", handler)

    def edge_check(rid: int, check: str) -> dict:
        request = {"jsonrpc": "2.0", "id": rid, "method": "edge.check",
                   "params": {"call": {"check": check}, "cassette": {"mode": "replay"}, "workspace": "/ws"}}
        return server.handle_line(json.dumps(request))

    assert server.handle_line(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "init",
                                          "params": {"protocol": 1, "steps": []}}))["result"]["steps"] == {}
    assert edge_check(2, "totals match") == {
        "jsonrpc": "2.0", "id": 2, "result": {"verdict": {"take": True, "reason": "the totals match"}},
    }
    assert edge_check(3, "unanswerable") == {"jsonrpc": "2.0", "id": 3, "error": {
        "code": -32001, "message": "no valid verdict after 2 retries",
        "data": {"cause": "validation", "message": "no valid verdict after 2 retries"},
    }}
    worker_server.notify({"type": "model.call", "step": "late"})              # no request in flight: dropped
    notification = {"jsonrpc": "2.0", "method": "event",
                    "params": {"type": "model.call", "step": "edge:validate.done[save]"}}
    assert [json.loads(line) for line in out.getvalue().splitlines()] == [notification, notification]


# --- the step-package loader, in-process -------------------------------------------------------------------------

def test_mount_is_idempotent_and_keyed_by_step_id(tmp_path, steps_namespace):
    a = write_package(tmp_path / "a" / "read", READ_A)
    b = write_package(tmp_path / "b" / "read", READ_B)
    name = mount_step_package("p#read", a)
    assert name == f"wynd_steps.{step_module_name('p#read')}"
    assert mount_step_package("p#read", a) == name
    assert list(sys.modules[name].__path__) == [str(a.resolve())]
    first, second = load_step_class("p#read", "read:Read", a), load_step_class("q#read", "read:Read", b)
    assert first is not second
    assert first.__module__ == f"{name}.read"
    assert second.__module__ == f"wynd_steps.{step_module_name('q#read')}.read"
    assert first().run(first.Input(text="x")).source == "a"
    assert second().run(second.Input(text="x")).text == "X"


def test_mount_runs_a_package_init(tmp_path, steps_namespace):
    package = write_package(tmp_path / "pkg", {**READ_A, "__init__.py": "MARKER = 'initialised'\n"})
    name = mount_step_package("p#pkg", package)
    assert sys.modules[name].MARKER == "initialised"
    assert load_step_class("p#pkg", "read:Read", package).__module__ == f"{name}.read"


@pytest.mark.parametrize(
    ("entrypoint", "source", "message"),
    [
        ("read", "", "must be '<module>:<Class>'"),
        ("read:Missing", "", "is not a Step subclass"),
        ("read:BaseModel", "", "is not a Step subclass"),
        ("child:Child", "from wynd.runtime import ProcessStep\nclass Child(ProcessStep):\n    process_id = 'c'\n",
         "is a ProcessStep"),
        ("bad:Bad", "from pydantic import BaseModel\nfrom wynd.runtime import DeterministicStep\n"
                    "class Bad(DeterministicStep):\n    class Input(BaseModel):\n        x: int\n"
                    "    Output = Input\n    def run(self, input):\n        return None\n", "Bad"),
    ],
)
def test_load_step_class_rejects_bad_entrypoints(tmp_path, steps_namespace, entrypoint, source, message):
    files = dict(READ_A)
    if source:
        files[f"{entrypoint.partition(':')[0]}.py"] = source
    package = write_package(tmp_path / "pkg", files)
    with pytest.raises(StepDefinitionError, match=message):
        load_step_class("p#pkg", entrypoint, package)


def test_load_step_class_surfaces_import_errors(tmp_path, steps_namespace):
    package = write_package(tmp_path / "broken", BROKEN)
    with pytest.raises(ModuleNotFoundError, match="wynd_missing_dependency"):
        load_step_class("p#broken", "broken:Broken", package)
