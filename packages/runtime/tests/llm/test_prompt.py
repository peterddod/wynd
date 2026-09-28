"""The prompt both provider kinds send, and the validation-retry text (PLAN §5.5; `$DRAFTS/03 §6.3`)."""

from __future__ import annotations

from datetime import date
from typing import Literal

import pytest
from pydantic import BaseModel, ValidationError

from wynd.runtime.agentic.prompt import (
    RETRY_NO_OUTPUT,
    RETRY_TRUNCATED,
    RETRY_VALIDATION,
    SYSTEM_PREAMBLE,
    format_validation_errors,
    render_prompt,
)
from wynd.spec.interface import output_adapter

INSTRUCTION = """Given the text of a supplier invoice, extract the invoice number and total.
    If the text is not a supplier invoice, take the not_an_invoice exit."""


def test_system_prompt_is_the_preamble_plus_the_cleaned_instruction():
    system, _ = render_prompt(INSTRUCTION, {}, {"invoice_text": "x"})
    assert system == (
        "You are one step in an automated, tested process. No human is available to answer questions.\n"
        "Carry out the task below using only the tools provided, then reply with the step's structured output.\n"
        "The `exit` field selects the outcome: choose the exit that matches what happened and fill in exactly that "
        "exit's fields.\n"
        "\n"
        "# Task\n"
        "Given the text of a supplier invoice, extract the invoice number and total.\n"
        "If the text is not a supplier invoice, take the not_an_invoice exit."
    )
    assert system.startswith(SYSTEM_PREAMBLE)


def test_user_message_with_context():
    _, user = render_prompt(
        INSTRUCTION,
        {"process.goal": "Pay supplier invoices", "previous.outputs": {"pages": 2, "text": "Café — €12"}},
        {"invoice_text": "INVOICE INV-1042", "due": date(2026, 10, 1)},
    )
    assert user == (
        "# Context\n"
        "```json\n"
        "{\n"
        '  "previous.outputs": {\n'
        '    "pages": 2,\n'
        '    "text": "Café — €12"\n'
        "  },\n"
        '  "process.goal": "Pay supplier invoices"\n'
        "}\n"
        "```\n"
        "\n"
        "# Input\n"
        "```json\n"
        "{\n"
        '  "due": "2026-10-01",\n'
        '  "invoice_text": "INVOICE INV-1042"\n'
        "}\n"
        "```"
    )


@pytest.mark.parametrize("context", [{}, None])
def test_user_message_without_context(context):
    _, user = render_prompt(INSTRUCTION, context, {"b": 1, "a": [None, True]})
    assert user == '# Input\n```json\n{\n  "a": [\n    null,\n    true\n  ],\n  "b": 1\n}\n```'


def test_context_entries_that_are_unavailable_are_still_shown():
    _, user = render_prompt(INSTRUCTION, {"previous.summary": None}, {})
    assert user.startswith('# Context\n```json\n{\n  "previous.summary": null\n}\n```\n\n# Input\n')


def test_rendering_is_deterministic_under_key_order():
    one = render_prompt(INSTRUCTION, {"b": 1, "a": 2}, {"y": {"q": 1, "p": 2}, "x": 0})
    two = render_prompt(INSTRUCTION, {"a": 2, "b": 1}, {"x": 0, "y": {"p": 2, "q": 1}})
    assert one == two


def test_raw_mode_is_verbatim():
    assert render_prompt("  You are a compiler.\n", {"x": 1}, {"y": 2}, raw_prompt="Write the step.") == (
        "  You are a compiler.\n", "Write the step.")


class Line(BaseModel):
    amount: float


class Done(BaseModel):
    exit: Literal["done"] = "done"
    total: float
    lines: list[Line]


class NotAnInvoice(BaseModel):
    exit: Literal["not_an_invoice"] = "not_an_invoice"


ADAPTER = output_adapter(Done | NotAnInvoice)


def errors_of(value) -> ValidationError:
    with pytest.raises(ValidationError) as err:
        ADAPTER.validate_python(value)
    return err.value


def test_format_validation_errors_names_each_field_under_output():
    text = format_validation_errors(errors_of({"exit": "done", "total": "twelve", "lines": [{"amount": "x"}, {}]}))
    assert text.splitlines() == [
        "- output.done.total: Input should be a valid number, unable to parse string as a number (got 'twelve')",
        "- output.done.lines.0.amount: Input should be a valid number, unable to parse string as a number (got 'x')",
        "- output.done.lines.1.amount: Field required (got {})",
    ]


def test_format_validation_errors_for_a_missing_exit():
    text = format_validation_errors(errors_of({"total": 1}))
    assert text == "- output: Unable to extract tag using discriminator 'exit' (got {'total': 1})"


def test_format_validation_errors_truncates_the_input_and_limits_the_lines():
    long = "y" * 200
    err = errors_of({"exit": "done", "total": long, "lines": [{"amount": "x"} for _ in range(30)]})
    lines = format_validation_errors(err).splitlines()
    assert len(lines) == 20
    shown = "'" + "y" * 79                       # repr(input)[:80]
    assert lines[0] == f"- output.done.total: Input should be a valid number, unable to parse string as a number " \
                       f"(got {shown})"
    assert len(format_validation_errors(err, limit=3).splitlines()) == 3


def test_retry_texts():
    text = RETRY_VALIDATION.format(errors=format_validation_errors(errors_of({"total": 1})))
    assert text == (
        "Your structured output did not validate against the step's Output schema:\n"
        "- output: Unable to extract tag using discriminator 'exit' (got {'total': 1})\n"
        "Reply again with corrected structured output. Only call tools again if you need information you do not have."
    )
    assert RETRY_NO_OUTPUT == "You replied without structured output. Reply with the step's structured output now."
    assert RETRY_TRUNCATED == ("Your previous reply was cut off at the output token limit. Reply again, more "
                               "concisely, with the step's structured output.")
