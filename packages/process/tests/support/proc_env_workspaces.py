"""PROC-ENV test support: small workspaces written as file maps, a `validate` double and an in-memory user registry.

Workspaces are plain `{relative path: text}` maps handed to the PROC-WS `make_repo` fixture. Runnable step packages
get their `step.lock.yaml` from `finish_locks`, which describes each package in-process (the same code as `python -m
wynd.runtime.describe`) so the interface snapshots always match the code.
"""

from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
import yaml

from wynd.process.validation import ValidationReport
from wynd.runtime.describe import describe_package
from wynd.runtime.providers import ProviderInfo
from wynd.spec.expr.analysis import env_names
from wynd.spec.fragments import EnvFragment, EnvGroup, EnvVar
from wynd.spec.lockfiles import StepLock, branch_key, dump_lock
from wynd.spec.process_doc import expression_sites

# --- doubles --------------------------------------------------------------------------------------------------------


def fake_validate(lp, *, providers=None, stats=None) -> ValidationReport:
    """A PROC-VAL stand-in: loader diagnostics only, the loaded documents as the normalised ones, and `env_refs` from
    the `env.*` names in each edge branch's `with:` values."""
    closure = lp.closure_processes()
    env_refs: dict[str, list[str]] = {}
    for pid, proc in closure.items():
        edges = {edge.key: edge for edge in proc.doc.edges}
        for site in expression_sites(proc.doc):
            if site.role != "with":
                continue
            branch = edges[site.edge].to[site.branch]
            field = site.loc[5]
            for name in env_names([site.text]):
                env_refs.setdefault(name, []).append(f"edge:{pid}:{branch_key(site.edge, site.branch, branch.name)}"
                                                     f".{field}")
    return ValidationReport(
        process=lp.id,
        diagnostics=[d for proc in closure.values() for d in proc.diagnostics],
        normalized={pid: proc.doc for pid, proc in closure.items()},
        env_refs=env_refs,
    )


@pytest.fixture
def validate_double(monkeypatch):
    import wynd.process.validation as validation

    monkeypatch.setattr(validation, "validate", fake_validate)
    return fake_validate


class MemRegistry:
    """In-memory `Registry` (PLAN §3.14)."""

    def __init__(self, sections: dict[str, dict[str, dict]] | None = None) -> None:
        self.sections = {name: dict(entries) for name, entries in (sections or {}).items()}
        self._secrets: dict[str, str] = {}

    def list(self, section: str) -> dict[str, dict]:
        return dict(self.sections.get(section, {}))

    def get(self, section: str, name: str) -> dict | None:
        return self.sections.get(section, {}).get(name)

    def put(self, section: str, name: str, entry) -> None:
        self.sections.setdefault(section, {})[name] = dict(entry)

    def remove(self, section: str, name: str) -> bool:
        return self.sections.get(section, {}).pop(name, None) is not None

    def put_secret(self, name: str, value: str) -> None:
        self._secrets[name] = value

    def secrets(self) -> dict[str, str]:
        return dict(self._secrets)

    def location(self) -> str:
        return "memory"


SDK = "claude-agent-sdk>=0.2.157,<0.3"
FAKE_PROVIDERS = {
    "claude-code": ProviderInfo(
        name="claude-code", kind="agent", default_tiers={"cheap": "haiku"},
        env_fragment=EnvFragment(
            deps=[SDK], requires="glibc",
            vars=[
                EnvVar(name="CLAUDE_CODE_OAUTH_TOKEN", description="Claude Code token.", secret=True, required=False,
                       one_of="claude-code-auth", used_by=["provider:claude-code"]),
                EnvVar(name="ANTHROPIC_API_KEY", description="Anthropic API key.", secret=True, required=False,
                       one_of="claude-code-auth", used_by=["provider:claude-code"]),
            ],
            groups={"claude-code-auth": EnvGroup(description="One claude-code credential.", modes=["image"])},
        ),
    ),
    "anthropic": ProviderInfo(
        name="anthropic", kind="model", default_tiers={"cheap": "claude-haiku-4-5"},
        env_fragment=EnvFragment(vars=[EnvVar(name="ANTHROPIC_API_KEY", description="Anthropic API key.",
                                              secret=True, used_by=["provider:anthropic"])]),
    ),
    "fake": ProviderInfo(name="fake", kind="agent", default_tiers={"cheap": "fake"}, env_fragment=EnvFragment()),
}


def fake_providers(name: str) -> ProviderInfo:
    return FAKE_PROVIDERS[name]


# --- building blocks ------------------------------------------------------------------------------------------------


def src(text: str) -> str:
    return textwrap.dedent(text).lstrip()


def to_yaml(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False)


def pyproject(name: str, deps: tuple[str, ...] = ()) -> str:
    listed = ", ".join(json.dumps(dep) for dep in deps)
    return src(f'''
        [project]
        name = "{name}"
        version = "0.1.0"
        requires-python = ">=3.12"
        dependencies = [{listed}]
        [build-system]
        requires = ["hatchling>=1.27"]
        build-backend = "hatchling.build"
    ''')


def lock_only(dir: str, **lock: Any) -> dict[str, str]:
    """A compiled package without code (enough for loading, merging, planning and manifests)."""
    name = dir.rsplit("/", 1)[1]
    fields = {"name": name, "kind": "deterministic", "entrypoint": f"{name}:{name.title()}", **lock}
    return {f"{dir}/pyproject.toml": pyproject(name, tuple(fields.get("fragment", {}).get("deps", ()))),
            f"{dir}/step.lock.yaml": dump_lock(StepLock.model_validate(fields))}


def finish_locks(ws: Path, locks: dict[str, dict[str, Any]]) -> None:
    """Write `<pkg>/step.lock.yaml` for each `{package dir: extra lock fields}` with the described interface (and the
    agentic context / shell exit codes) of the package's code."""
    for rel, extra in locks.items():
        pkg = ws / rel
        name = pkg.name
        entrypoint = extra.pop("entrypoint", None) or f"{name}:{name.title()}"
        described = _describe(pkg, entrypoint)
        fields: dict[str, Any] = {"name": name, "kind": described.kind, "entrypoint": entrypoint,
                                  "interface": described.interface.model_dump(mode="json")}
        if described.kind == "agentic":
            fields["context"] = described.context
        if described.kind == "shell":
            fields["shell"] = {"exit_codes": {int(k) if k.isdigit() else k: v
                                              for k, v in described.exit_codes.items()}}
        fields.update(extra)
        (pkg / "step.lock.yaml").write_text(dump_lock(StepLock.model_validate(fields)))


def _describe(pkg: Path, entrypoint: str):
    """`describe_package` without leaving `__pycache__` in the package (it would be committed and hashed)."""
    before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
    try:
        return describe_package(pkg, entrypoint)
    finally:
        sys.dont_write_bytecode = before


# --- the runnable "notes" workspace ---------------------------------------------------------------------------------

READ_PY = src('''
    """Read a note file."""
    from pathlib import Path
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import DeterministicStep


    class Read(DeterministicStep):
        """Read the note."""

        class Input(BaseModel):
            src: Path

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            text: str

        class Empty(BaseModel):
            exit: Literal["empty"] = "empty"

        Output = Done | Empty

        def run(self, input):
            text = input.src.read_text().strip()
            if not text:
                return self.Empty()
            return self.Done(text=text)
''')

SAVE_PY = src('''
    """Save a note in upper case."""
    from pathlib import Path
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import DeterministicStep


    class Save(DeterministicStep):
        """Save the note."""

        class Input(BaseModel):
            text: str
            dest: str

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            name: str
            words: int

        Output = Done

        def run(self, input):
            out = Path(input.dest)
            out.mkdir(parents=True, exist_ok=True)
            (out / "note.txt").write_text(input.text.upper())
            return self.Done(name="note.txt", words=len(input.text.split()))
''')

TEST_READ_PY = src('''
    from pathlib import Path

    from wynd.runtime.testing import expect, load_step, run_step

    Read = load_step(Path(__file__).parent)
    BASE = Path(__file__).resolve().parents[2]


    def test_example_1(tmp_path):
        result = run_step(Read, {"src": str(BASE / "data" / "hello.txt")}, workspace=tmp_path)
        expect(result, exit="done", outputs={"text": "hello world"})


    def test_example_2(tmp_path):
        result = run_step(Read, {"src": str(BASE / "data" / "blank.txt")}, workspace=tmp_path)
        expect(result, exit="empty")
''')

TEST_SAVE_PY = src('''
    from pathlib import Path

    from wynd.runtime.testing import expect, load_step, run_step

    Save = load_step(Path(__file__).parent)


    def test_example_1(tmp_path):
        result = run_step(Save, {"text": "hi there", "dest": str(tmp_path / "out")}, workspace=tmp_path)
        expect(result, exit="done", outputs={"name": "note.txt", "words": 2})
        assert (tmp_path / "out" / "note.txt").read_text() == "HI THERE"
''')

NOTES_PROCESS = {
    "kind": "process",
    "name": "notes",
    "provider": "fake",
    "env": {"vars": {"OUT_DIR": "Where saved notes go."}},
    "entry": "read",
    "inputs": {"src": "path"},
    "outputs": {"done": {"name": "string", "words": "integer"}, "empty": {}},
    "examples": [
        {"inputs": {"src": "data/hello.txt"}, "env": {"OUT_DIR": "{tmp}/out"},
         "outputs": {"name": "note.txt", "words": 2}},
        {"inputs": {"src": "data/blank.txt"}, "exit": "empty"},
    ],
    "steps": {"read": {"use": "./steps/read"}, "save": {"use": "./steps/save"}},
    "edges": [
        {"from": "read.done", "to": "save", "with": {"text": "steps.read.outputs.text", "dest": "env.OUT_DIR"}},
        {"from": "read.empty", "to": "$exit.empty"},
        {"from": "save.done", "to": "$exit.done",
         "with": {"name": "steps.save.outputs.name", "words": "steps.save.outputs.words"}},
    ],
}
NOTES = "processes/notes"


def notes_files(process: dict[str, Any] | None = None) -> dict[str, str]:
    return {
        "wynd.yaml": "process_roots: [processes]\n",
        f"{NOTES}/process.yaml": to_yaml(process or NOTES_PROCESS),
        f"{NOTES}/data/hello.txt": "hello world\n",
        f"{NOTES}/data/blank.txt": "\n",
        f"{NOTES}/steps/read/pyproject.toml": pyproject("notes-read"),
        f"{NOTES}/steps/read/read.py": READ_PY,
        f"{NOTES}/steps/read/test_read.py": TEST_READ_PY,
        f"{NOTES}/steps/save/pyproject.toml": pyproject("notes-save"),
        f"{NOTES}/steps/save/save.py": SAVE_PY,
        f"{NOTES}/steps/save/test_save.py": TEST_SAVE_PY,
    }


def make_notes(make_repo, commit, process: dict[str, Any] | None = None, files: dict[str, str] | None = None) -> Path:
    """The runnable two-step workspace, committed, with real interface snapshots."""
    ws = make_repo(files={**notes_files(process), **(files or {})})
    finish_locks(ws, {f"{NOTES}/steps/read": {}, f"{NOTES}/steps/save": {}})
    commit(ws, "locks")
    return ws


# --- agentic steps behind edges (cassette misses) and {tmp} in a recorded request -----------------------------------

DRAFT_PY = src('''
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import AgenticStep


    class Draft(AgenticStep):
        """Summarise the text in one line."""

        class Input(BaseModel):
            text: str

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            summary: str

        Output = Done

        def run(self, input: Input) -> Done: ...
''')

FALLBACK_PY = src('''
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import DeterministicStep


    class Fallback(DeterministicStep):
        """A fixed summary."""

        class Input(BaseModel):
            text: str

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            summary: str

        Output = Done

        def run(self, input):
            return self.Done(summary="fallback")
''')

NOTE_PY = src('''
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import AgenticStep


    class Note(AgenticStep):
        """Write a one-line note about where the text will be stored."""

        class Input(BaseModel):
            dest: str
            text: str

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            line: str

        Output = Done

        def run(self, input: Input) -> Done: ...
''')

AGENTIC_LOCK = {"tier": "cheap", "thinking": "low", "retries": {"run": 2, "validation": 2, "tool": 1}}


def agentic_files() -> dict[str, str]:
    """`review` routes `draft.error` to a fallback; `strict` leaves it to the error handler; `echo` passes a `{tmp}`
    path into an agentic step. All use the `fake` provider; `draft` lives in the `shared` step root."""
    review = {
        "kind": "process", "name": "review", "provider": "fake", "entry": "draft",
        "inputs": {"text": "string"}, "outputs": {"summary": "string"},
        "examples": [{"inputs": {"text": "a long text"}, "outputs": {"summary": "fallback"}}],
        "steps": {"draft": {"use": "shared:draft"}, "fallback": {"use": "./steps/fallback"}},
        "edges": [
            {"from": "draft.done", "to": "$exit.done", "with": {"summary": "steps.draft.outputs.summary"}},
            {"from": "draft.error", "to": "fallback", "with": {"text": "steps.draft.outputs.inputs.text"}},
            {"from": "fallback.done", "to": "$exit.done", "with": {"summary": "steps.fallback.outputs.summary"}},
        ],
    }
    strict = {
        "kind": "process", "name": "strict", "provider": "fake", "entry": "draft",
        "inputs": {"text": "string"}, "outputs": {"summary": "string"},
        "examples": [{"inputs": {"text": "a long text"}, "exit": "error"}],
        "steps": {"draft": {"use": "shared:draft"}},
        "edges": [{"from": "draft.done", "to": "$exit.done", "with": {"summary": "steps.draft.outputs.summary"}}],
    }
    echo = {
        "kind": "process", "name": "echo", "provider": "fake", "entry": "note",
        "inputs": {"dest": "string", "text": "string"}, "outputs": {"line": "string"},
        "examples": [{"inputs": {"dest": "{tmp}/out", "text": "hi"}, "outputs": {"line": "ok"}}],
        "steps": {"note": {"use": "./steps/note"}},
        "edges": [{"from": "note.done", "to": "$exit.done", "with": {"line": "steps.note.outputs.line"}}],
    }
    return {
        "wynd.yaml": "process_roots: [processes]\nstep_roots: {shared: shared/steps}\n",
        "shared/steps/draft/pyproject.toml": pyproject("shared-draft"),
        "shared/steps/draft/draft.py": DRAFT_PY,
        "processes/review/process.yaml": to_yaml(review),
        "processes/review/steps/fallback/pyproject.toml": pyproject("review-fallback"),
        "processes/review/steps/fallback/fallback.py": FALLBACK_PY,
        "processes/strict/process.yaml": to_yaml(strict),
        "processes/echo/process.yaml": to_yaml(echo),
        "processes/echo/steps/note/pyproject.toml": pyproject("echo-note"),
        "processes/echo/steps/note/note.py": NOTE_PY,
    }


def make_agentic(make_repo, commit) -> Path:
    ws = make_repo(files=agentic_files())
    finish_locks(ws, {
        "shared/steps/draft": dict(AGENTIC_LOCK),
        "processes/review/steps/fallback": {},
        "processes/echo/steps/note": dict(AGENTIC_LOCK),
    })
    commit(ws, "locks")
    return ws


# --- a parent whose child's own example fails -------------------------------------------------------------------------

DOUBLE_PY = src('''
    from typing import Literal

    from pydantic import BaseModel

    from wynd.runtime import DeterministicStep


    class Double(DeterministicStep):
        """Double a number."""

        class Input(BaseModel):
            n: int

        class Done(BaseModel):
            exit: Literal["done"] = "done"
            n: int

        Output = Done

        def run(self, input):
            return self.Done(n=input.n * 2)
''')

TEST_DOUBLE_PY = src('''
    from pathlib import Path

    from wynd.runtime.testing import expect, load_step, run_step

    Double = load_step(Path(__file__).parent)


    def test_example_1(tmp_path):
        expect(run_step(Double, {"n": 2}, workspace=tmp_path), exit="done", outputs={"n": 4})
''')


def family_files(child_expected: int = 5) -> dict[str, str]:
    """`parent` runs `process:child`; the child's own example expects `child_expected` for n=2 (4 is right)."""
    child = {
        "kind": "process", "name": "child", "entry": "double", "inputs": {"n": "integer"},
        "outputs": {"n": "integer"},
        "examples": [{"inputs": {"n": 2}, "outputs": {"n": child_expected}}],
        "steps": {"double": {"use": "./steps/double"}},
        "edges": [{"from": "double.done", "to": "$exit.done", "with": {"n": "steps.double.outputs.n"}}],
    }
    parent = {
        "kind": "process", "name": "parent", "entry": "sub", "inputs": {"n": "integer"},
        "outputs": {"n": "integer"},
        "examples": [{"inputs": {"n": 1}, "outputs": {"n": 2}}],
        "steps": {"sub": {"use": "process:child"}},
        "edges": [{"from": "sub.done", "to": "$exit.done", "with": {"n": "steps.sub.outputs.n"}}],
    }
    return {
        "wynd.yaml": "process_roots: [processes]\n",
        "processes/parent/process.yaml": to_yaml(parent),
        "processes/child/process.yaml": to_yaml(child),
        "processes/child/steps/double/pyproject.toml": pyproject("child-double"),
        "processes/child/steps/double/double.py": DOUBLE_PY,
        "processes/child/steps/double/test_double.py": TEST_DOUBLE_PY,
    }


def make_family(make_repo, commit, child_expected: int = 5) -> Path:
    ws = make_repo(files=family_files(child_expected))
    finish_locks(ws, {"processes/child/steps/double": {}})
    commit(ws, "locks")
    return ws
