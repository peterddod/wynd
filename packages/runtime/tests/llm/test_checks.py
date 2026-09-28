"""AgenticStep definition checks and `describe_agentic` (SPEC §3.2, §3.8; PLAN §5.2, §15 item 33;
`$DRAFTS/03 §6.1`).

The checks run from `AgenticStep.__init_subclass__`, so most cases define a class and expect the definition to fail.
"""

from __future__ import annotations

from typing import Literal

import pytest
from pydantic import BaseModel

from wynd.runtime.agentic.checks import check_agentic_class, describe_agentic, is_ellipsis_body, step_instruction
from wynd.runtime.errors import StepDefinitionError
from wynd.runtime.mcp import McpServer
from wynd.runtime.step import AgenticStep
from wynd.runtime.tools import now, shell, tool, web_search, workspace_read


class Text(BaseModel):
    text: str


class Done(BaseModel):
    exit: Literal["done"] = "done"
    total: float


def plain(self, input): ...


def with_docstring(self, input):
    """Completed by the loop."""
    ...


def with_pass(self, input):
    pass


def with_body(self, input):
    return 1


def docstring_only(self, input):
    """Nothing else."""


def two_statements(self, input):
    ...
    ...


@pytest.mark.parametrize(("fn", "expected"), [
    (plain, True),
    (with_docstring, True),
    (with_pass, False),
    (with_body, False),
    (docstring_only, False),
    (two_statements, False),
])
def test_is_ellipsis_body(fn, expected):
    assert is_ellipsis_body(fn) is expected


def test_is_ellipsis_body_of_an_indented_method():
    class Holder:
        def run(self, input: Text) -> Done:  # annotations and indentation do not matter
            ...

    assert is_ellipsis_body(Holder.run)


def test_a_valid_step_defines_cleanly():
    class Extract(AgenticStep):
        """Extract the total from the text."""

        Input = Text
        Output = Done
        context = ["process.goal", "previous.summary", "steps.classify.outputs.kind", "full_trace"]
        tools = [web_search, shell.allow("pdftotext")]
        mcp = [McpServer("github", allow=["get_issue"])]

        @tool
        def lookup(self, code: str) -> str:
            """Look up a code."""
            return code

        def run(self, input: Text) -> Done: ...

    check_agentic_class(Extract)          # idempotent: checking again is fine


def test_run_may_be_inherited():
    class Extract(AgenticStep):
        """Extract the total."""

        Input = Text
        Output = Done

    assert "run" not in Extract.__dict__


def test_missing_docstring():
    with pytest.raises(StepDefinitionError) as err:
        class Extract(AgenticStep):
            Input = Text
            Output = Done

            def run(self, input): ...

    assert "the class docstring is the instruction" in str(err.value)
    assert str(err.value).startswith("Extract is not a valid AgenticStep:")


def test_the_agentic_step_docstring_is_not_an_instruction_but_a_user_base_class_docstring_is():
    with pytest.raises(StepDefinitionError, match="docstring"):
        class Bare(AgenticStep):
            def run(self, input): ...

    class Base(AgenticStep):
        """Summarise the text in one sentence."""

    class Child(Base):
        def run(self, input): ...

    assert step_instruction(Child) == "Summarise the text in one sentence."


def test_step_instruction_is_the_cleaned_docstring():
    class Extract(AgenticStep):
        """Given an invoice, extract the total.

        Rules:
            - never guess the currency
        """

    assert step_instruction(Extract) == "Given an invoice, extract the total.\n\nRules:\n    - never guess the currency"


def test_concrete_run_is_rejected():
    with pytest.raises(StepDefinitionError) as err:
        class Extract(AgenticStep):
            """Extract the total."""

            def run(self, input):
                return Done(total=1.0)

    assert "AgenticStep.run must be `...` (completed by the loop)" in str(err.value)


def test_run_without_readable_source():
    source = 'class Extract(AgenticStep):\n    """Extract."""\n    def run(self, input): ...\n'
    with pytest.raises(StepDefinitionError) as err:
        exec(source, {"AgenticStep": AgenticStep})
    assert "cannot read the source of run(); ship .py sources in the wheel" in str(err.value)


def test_tools_must_be_decorated():
    def helper(x: str) -> str:
        """Not a tool."""
        return x

    with pytest.raises(StepDefinitionError) as err:
        class Extract(AgenticStep):
            """Extract the total."""

            tools = [now, helper]

    assert "tools[1] ('test_tools_must_be_decorated.<locals>.helper') is not decorated with @tool" in str(err.value)


def test_bare_shell_needs_an_allowlist():
    with pytest.raises(StepDefinitionError) as err:
        class Extract(AgenticStep):
            """Extract the total."""

            tools = [shell]

    assert "shell.allow" in str(err.value)


def test_tool_names_are_unique_across_methods_builtins_and_mcp():
    with pytest.raises(StepDefinitionError) as err:
        class Triage(AgenticStep):
            """Triage the message."""

            tools = [web_search]
            mcp = [McpServer("github", allow=["get_issue", "list_issues"])]

            @tool
            def web_search(self, query: str) -> str:
                """Search the intranet."""
                return query

            @tool
            def github__get_issue(self, number: int) -> str:
                """A local issue lookup."""
                return str(number)

    text = str(err.value)
    assert "tool name 'web_search' is used more than once" in text
    assert "tool name 'github__get_issue' is used more than once" in text
    assert "github__list_issues" not in text


def test_mcp_entries_must_be_distinct_servers():
    with pytest.raises(StepDefinitionError) as err:
        class Triage(AgenticStep):
            """Triage the message."""

            mcp = [McpServer("github", allow=["get_issue"]), McpServer("github", allow=["list_issues"]), "linear"]

    text = str(err.value)
    assert "mcp: server 'github' is declared twice" in text
    assert "mcp[2] must be McpServer(name, allow=[...]), got str" in text


def test_context_entries_must_parse():
    with pytest.raises(StepDefinitionError) as err:
        class Extract(AgenticStep):
            """Extract the total."""

            context = ["process.goal", "previous.everything", "steps.read.summary.text", 3]

    text = str(err.value)
    assert "context[1]: invalid context entry 'previous.everything'" in text
    assert "context[2]: invalid context entry 'steps.read.summary.text'" in text
    assert "context[3] must be a string, got int" in text
    assert "context[0]" not in text


def test_tool_methods_must_not_be_named_like_hooks():
    with pytest.raises(StepDefinitionError) as err:
        class Extract(AgenticStep):
            """Extract the total."""

            @tool
            def pre(self) -> str:
                """Prepare."""
                return ""

    assert "@tool method pre() is named like a step hook (run/pre/post)" in str(err.value)


def test_every_problem_is_listed_at_once():
    with pytest.raises(StepDefinitionError) as err:
        class Extract(AgenticStep):
            tools = ["web_search"]
            context = ["nope"]

            def run(self, input):
                return None

    lines = str(err.value).splitlines()
    assert lines[0] == "Extract is not a valid AgenticStep:"
    assert len(lines) == 5
    assert all(line.startswith("  - ") for line in lines[1:])


def test_describe_agentic():
    class Triage(AgenticStep):
        """Triage a support message into a typed ticket."""

        Input = Text
        Output = Done
        context = ["process.goal", "previous.outputs"]
        tools = [workspace_read, web_search]
        mcp = [McpServer("github", allow=["list_issues", "get_issue"])]

        @tool(effects=["network"], idempotent=True, env=["CRM_TOKEN"])
        def lookup_customer(self, email: str) -> str:
            """Fetch the customer record for an email address."""
            return email

        def run(self, input: Text) -> Done: ...

    described = describe_agentic(Triage)
    assert set(described) == {"instruction", "context", "tools", "mcp", "completed_by_loop"}
    assert described["instruction"] == "Triage a support message into a typed ticket."
    assert described["context"] == ["process.goal", "previous.outputs"]
    assert described["mcp"] == [{"name": "github", "allow": ["list_issues", "get_issue"]}]
    assert described["completed_by_loop"] is True
    assert [(t["name"], t["source"]) for t in described["tools"]] == [
        ("lookup_customer", "method"), ("workspace_read", "builtin"), ("web_search", "builtin"),
    ]
    method = described["tools"][0]
    assert method["description"] == "Fetch the customer record for an email address."
    assert (method["effects"], method["idempotent"], method["env"]) == (["network"], True, ["CRM_TOKEN"])
    assert method["input_schema"]["properties"] == {"email": {"title": "Email", "type": "string"}}
    assert described["tools"][2]["env"] == ["BRAVE_API_KEY"]
