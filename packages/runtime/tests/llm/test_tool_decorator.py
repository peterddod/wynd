"""`@tool`, `tool_spec`, `step_tools`, `shell.allow` and `env` (PLAN §5.1, §5.2; `$DRAFTS/03 §9.1`)."""

from __future__ import annotations

import json
from datetime import date
from typing import Annotated

import pytest
from pydantic import BaseModel, Field, ValidationError

from wynd.runtime.agentic.errors import MissingEnvVar
from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.tools import BUILTINS, env, handles_for, shell, step_tools, tool, tool_spec, web_search
from wynd.runtime.tools.decorator import ToolDecl, ToolSpec
from wynd.spec.lockfiles import ToolSnapshot


class Rate(BaseModel):
    currency: str
    rate: float
    on: date


@tool
def lookup_rate(currency: str, on: date | None = None) -> Rate:
    """Look up the exchange rate of a currency."""
    return Rate(currency=currency, rate=1.25, on=on or date(2026, 10, 1))


@tool(name="crm-lookup", effects=["network"], idempotent=True, env=["CRM_TOKEN"])
def crm(customer_id: Annotated[str, Field(description="The CRM customer id")], limit: int = 3) -> list[str]:
    """Find a customer's open orders."""
    return [f"{customer_id}-{i}" for i in range(limit)]


def test_bare_decorator_marks_and_keeps_the_function_callable():
    assert lookup_rate.__wynd_tool__ == ToolDecl(name=None, effects=(), idempotent=False, env=())
    assert lookup_rate("GBP").rate == 1.25


def test_parameterised_decorator_records_name_effects_idempotent_env():
    spec = tool_spec(crm, "builtin")
    assert (spec.name, spec.effects, spec.idempotent, spec.env) == ("crm-lookup", ("network",), True, ("CRM_TOKEN",))
    assert spec.source == "builtin" and spec.server is None and spec.fn is crm
    assert crm("c1", limit=2) == ["c1-0", "c1-1"]


def test_schema_from_signature_defaults_descriptions_and_extra_forbid():
    spec = tool_spec(crm, "builtin")
    schema = spec.input_schema
    assert schema["required"] == ["customer_id"]
    assert schema["properties"]["customer_id"]["description"] == "The CRM customer id"
    assert schema["properties"]["limit"] == {"default": 3, "title": "Limit", "type": "integer"}
    assert schema["additionalProperties"] is False
    assert spec.description == "Find a customer's open orders."
    with pytest.raises(ValidationError):
        spec.args_model.model_validate({"customer_id": "c1", "unknown": 1})
    assert spec.args_model.model_validate({"customer_id": "c1", "limit": "5"}).limit == 5


def test_closures_are_tools():
    prefix = "order"

    @tool(effects=["filesystem"])
    def label(n: int) -> str:
        """Label an order number."""
        return f"{prefix}-{n}"

    [handle] = handles_for([label])
    assert handle.name == "label" and handle.local is True
    assert handle.invoke({"n": 7}).text == "order-7"
    assert label(8) == "order-8"


def test_return_serialisation():
    @tool
    def plain() -> str:
        """Return text."""
        return 'unquoted "text"'

    @tool
    def untyped():
        """No return annotation."""
        return {"b": 1, "a": [date(2026, 1, 2)]}

    @tool
    def huge() -> str:
        """Too much text."""
        return "x" * 100_010

    handles = {h.name: h for h in handles_for([lookup_rate, crm, plain, untyped, huge])}
    assert handles["lookup_rate"].invoke({"currency": "GBP"}).text == (
        '{"currency": "GBP", "on": "2026-10-01", "rate": 1.25}'
    )
    assert json.loads(handles["crm-lookup"].invoke({"customer_id": "c", "limit": 1}).text) == ["c-0"]
    assert handles["plain"].invoke({}).text == 'unquoted "text"'
    assert handles["untyped"].invoke({}).text == '{"a": ["2026-01-02"], "b": 1}'
    assert handles["huge"].invoke({}).text == "x" * 100_000 + "…[truncated 10 chars]"


def test_a_missing_docstring_is_a_definition_error():
    @tool
    def nodoc(x: int) -> int:
        return x

    with pytest.raises(StepDefinitionError, match="tool nodoc needs a docstring"):
        tool_spec(nodoc, "builtin")


def test_decorator_argument_errors():
    with pytest.raises(StepDefinitionError, match="unknown tool effect 'net'"):
        tool(effects=["net"])
    with pytest.raises(StepDefinitionError, match="tool name 'has space'"):
        tool(name="has space")

    @tool
    def star(*values: int) -> int:
        """Star arguments."""
        return 0

    with pytest.raises(StepDefinitionError, match=r"\*args/\*\*kwargs"):
        tool_spec(star, "builtin")
    with pytest.raises(StepDefinitionError, match="is not decorated with @tool"):
        tool_spec(len, "builtin")


class Extract:
    """A step-like class (step_tools reads any class)."""

    class Customer(BaseModel):
        id: str

    tools = [web_search, shell.allow("pdftotext", "qpdf"), crm]

    def __init__(self) -> None:
        self.seen: list[str] = []

    @tool(effects=["network"])
    def find_customer(self, customer: Customer, verbose: bool = False) -> str:
        """Find a customer record."""
        self.seen.append(customer.id)
        return customer.id

    def helper(self) -> str:
        return "not a tool"

    @tool
    def count_seen(self) -> int:
        """How many customers were looked up."""
        return len(self.seen)


def test_step_tools_methods_then_tools():
    specs = step_tools(Extract)
    assert [(s.name, s.source) for s in specs] == [
        ("find_customer", "method"),
        ("count_seen", "method"),
        ("web_search", "builtin"),
        ("shell", "builtin"),
        ("crm-lookup", "builtin"),
    ]
    find = specs[0]
    assert list(find.input_schema["properties"]) == ["customer", "verbose"]     # self is not an argument
    assert find.args_model.model_validate({"customer": {"id": "c9"}}).customer == Extract.Customer(id="c9")
    step = Extract()
    assert step.find_customer(Extract.Customer(id="c1")) == "c1"                 # methods stay directly callable
    assert step.count_seen() == 1


def test_step_tools_rejects_bare_shell_and_undecorated_entries():
    class BareShell:
        tools = [shell]

    class Plain:
        tools = [len]

    with pytest.raises(StepDefinitionError, match=r'shell.allow\("<executable>", \.\.\.\)'):
        step_tools(BareShell)
    with pytest.raises(StepDefinitionError, match=r"tools\[0\] .* is not decorated with @tool"):
        step_tools(Plain)


@pytest.mark.parametrize("exe", ["python", "python3", "python3.12", "sh", "bash", "zsh", "dash", "fish", "node",
                                 "deno", "perl", "ruby", "php", "env", "xargs"])
def test_shell_allow_refuses_interpreters_and_shells(exe):
    with pytest.raises(StepDefinitionError, match="is an interpreter or shell"):
        shell.allow("pdftotext", exe)


def test_shell_allow_needs_executable_names():
    with pytest.raises(StepDefinitionError, match="at least one executable"):
        shell.allow()
    with pytest.raises(StepDefinitionError, match="must be an executable name, not a path"):
        shell.allow("/usr/bin/pdftotext")


def test_shell_allow_returns_a_decl_whose_snapshot_carries_allow():
    decl = shell.allow("pdftotext", "qpdf")
    assert isinstance(decl, ToolDecl) and decl.allow == ("pdftotext", "qpdf")
    spec = tool_spec(decl, "builtin")
    assert spec.name == "shell" and spec.allow == ("pdftotext", "qpdf")
    assert spec.input_schema == tool_spec(shell, "builtin").input_schema
    assert spec.snapshot() == ToolSnapshot(name="shell", source="library", effects=["shell"], idempotent=False,
                                           allow=["pdftotext", "qpdf"])


def test_snapshots_and_describe():
    by_name = {s.name: s for s in step_tools(Extract)}
    assert by_name["find_customer"].snapshot() == ToolSnapshot(name="find_customer", source="method",
                                                              effects=["network"])
    assert by_name["web_search"].snapshot() == ToolSnapshot(name="web_search", source="library", effects=["network"],
                                                           idempotent=True, env=["BRAVE_API_KEY"])
    assert by_name["crm-lookup"].describe() == {
        "name": "crm-lookup",
        "description": "Find a customer's open orders.",
        "effects": ["network"],
        "idempotent": True,
        "env": ["CRM_TOKEN"],
        "source": "builtin",
        "input_schema": by_name["crm-lookup"].input_schema,
    }


def test_builtins_catalogue():
    assert all(isinstance(s, ToolSpec) for s in BUILTINS)
    assert {s.name: (s.effects, s.idempotent, s.env) for s in BUILTINS} == {
        "http_get": (("network",), True, ()),
        "web_search": (("network",), True, ("BRAVE_API_KEY",)),
        "workspace_read": (("filesystem",), True, ()),
        "workspace_write": (("filesystem",), True, ()),
        "shell": (("shell",), False, ()),
        "now": ((), False, ()),
    }


def test_env_reads_the_process_environment(monkeypatch):
    monkeypatch.setenv("CRM_TOKEN", "t0k")
    assert env("CRM_TOKEN") == "t0k"
    monkeypatch.setenv("CRM_TOKEN", "")
    with pytest.raises(MissingEnvVar, match="CRM_TOKEN is not set; it is listed in process.env.yaml"):
        env("CRM_TOKEN")
    monkeypatch.delenv("CRM_TOKEN")
    with pytest.raises(MissingEnvVar) as info:
        env("CRM_TOKEN")
    assert info.value.name == "CRM_TOKEN"
